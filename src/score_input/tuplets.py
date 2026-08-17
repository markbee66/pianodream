"""修法二與三：把連音的時值修對。

從 `repair.py` 拆出來的。這兩種**真的改音符的時值**（跟改拍號不一樣，
改拍號只是換量尺），所以要排在拍號修法前面 —— 理由見 `repair.py` 開頭。

    fix_missing_tuplets   一個連音記號都沒有。小節剛好多出「一個音的長度」，
                          而且找得到三個等長的連續音符，就把那三個標成三連音
    fix_partial_tuplets   homr **認出了連音，但只標了其中幾個音**。旁邊還有一串
                          同樣時值卻沒標的音，補完之後總長剛好對得上，就補完

兩者都只在「補完之後總長剛好對得上」時才動手。湊不出整數就不改 ——
猜錯的節奏比留著錯誤更糟，因為它看起來是對的。
"""

from lxml import etree

from .timing import TOLERANCE, measure_duration as _duration_of, voice_notes
from .walk import _expected, _walk


def fix_missing_tuplets(root, initial=(4, 4)):
    """小節剛好多出「一個音的長度」時，找三個等長的連續音符標成三連音。

    三個等長音符寫成三連音之後，總長從 3d 變成 2d，剛好少掉一個 d。
    所以「多出來的量」正好等於要找的那個音符時值 —— 這個對應關係很緊，
    不太可能誤判。

    時值要乘 2/3，除不盡的話先把整個 part 的 divisions 乘 3。
    """
    changed = 0
    for part in root.findall("part"):
        targets = []
        for measure, divisions, signature, _ in _walk(part, initial):
            expected = _expected(signature)
            excess = _duration_of(measure, divisions) - expected
            if excess <= TOLERANCE:
                continue
            region = _find_triplet(measure, excess * divisions)
            if region:
                targets.append((measure, region))

        if not targets:
            continue
        # 時值要乘 2/3，除不盡就先把整個 part 的刻度乘 3（區間端點也要跟著放大）
        if _needs_finer_divisions(targets):
            _scale_divisions(part, 3)
            targets = [(m, (lo * 3, hi * 3)) for m, (lo, hi) in targets]
        for measure, region in targets:
            _mark_triplet(measure, region)
            changed += 1
    return changed


def _scan(measure):
    """走一遍小節，記下每個元素**開始時游標在哪**，以及小節總長。

    不能只看文件順序。homr 產出的大譜表是「上行一個音 → backup → 下行一個音」
    交錯排的，同一個聲部的相鄰音符在檔案裡根本不相鄰 —— 照文件順序找三連音
    永遠找不到（第一版就是這樣，Alkan 一個都沒抓到）。
    看游標位置才是看真正的時間軸。
    """
    cursor = longest = 0.0
    anchor = 0.0        # 最後一個「有推進游標」的音在哪開始
    marks = []
    for element in measure:
        if element.tag == "note":
            chord = element.find("chord") is not None
            grace = element.find("grace") is not None
            # 和弦音與裝飾音跟著前一個音，起點是那個音的起點而不是現在的游標 ——
            # 用游標的話它們會被算到下一拍去，縮放區間時就會漏掉
            marks.append((element, anchor if (chord or grace) else cursor, chord or grace))
            if chord or grace:
                continue
            anchor = cursor
            cursor += float(element.findtext("duration") or 0)
            longest = max(longest, cursor)
        elif element.tag in ("backup", "forward"):
            marks.append((element, cursor, False))
            duration = float(element.findtext("duration") or 0)
            cursor += -duration if element.tag == "backup" else duration
            longest = max(longest, cursor)
    return marks, longest


def _find_triplet(measure, excess_divisions):
    """找出時間軸上連續三個、每個都長 excess 的發音位置。

    三個等長音符寫成三連音之後總長從 3d 變成 2d，剛好少掉一個 d，
    所以「多出來的量」正好等於要找的那個音符時值。這個對應關係很緊。

    回傳 (區間起點, 區間終點)，找不到回 None。
    """
    if excess_divisions <= 1e-6:
        return None

    marks, total = _scan(measure)
    onsets = sorted({start for element, start, skip in marks
                     if element.tag == "note" and not skip})
    if len(onsets) < 3:
        return None

    bounds = onsets + [total]
    spans = [bounds[i + 1] - bounds[i] for i in range(len(onsets))]

    for i in range(len(onsets) - 2):
        if not all(abs(spans[i + k] - excess_divisions) < 1e-6 for k in range(3)):
            continue
        # **整串等長的音有幾個？** 只有剛好 3 個才可能是三連音。
        # 5 個等長的音是五連音、6 個是六連音 —— 從裡面挑 3 個標成三連音，
        # 小節的拍數是湊對了，音樂卻是錯的，而且從此再也看不出來。
        # 〈うまぴょい伝説〉第 1 小節就是這樣：譜上印著「5」，
        # 修完卻變成「三連音 + 兩個 32 分音符」，Gate B 反而不再抱怨。
        run = 0
        while i - run - 1 >= 0 and abs(spans[i - run - 1] - excess_divisions) < 1e-6:
            run += 1
        length = run + 3
        while i + length - run < len(spans) and \
                abs(spans[i + length - run] - excess_divisions) < 1e-6:
            length += 1
        if length != 3:
            continue
        return onsets[i], bounds[i + 3]
    return None


def _needs_finer_divisions(targets):
    """區間長度除不盡 3 的話，時值乘 2/3 會出現小數，得先把刻度放大。"""
    for measure, (lo, hi) in targets:
        span = (hi - lo) / 3.0
        if abs(span * 2 / 3 - round(span * 2 / 3)) > 1e-9:
            return True
    return False


def _scale_divisions(part, factor):
    """把整個 part 的時間刻度乘上 factor，所有時值一起放大。"""
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            node = attributes.find("divisions")
            if node is not None and node.text:
                node.text = str(int(round(float(node.text) * factor)))
        for tag in ("note", "backup", "forward"):
            for element in measure.findall(tag):
                node = element.find("duration")
                if node is not None and node.text:
                    node.text = str(int(round(float(node.text) * factor)))
        for direction in measure.findall("direction"):
            node = direction.find("offset")
            if node is not None and node.text:
                node.text = str(int(round(float(node.text) * factor)))


def _mark_triplet(measure, region):
    """把區間裡的東西全部縮成 2/3，音符再補上 <time-modification>。

    **backup / forward 也要一起縮。** 大譜表是交錯排的，backup 的長度就是上一個
    音的長度；只縮音符不縮 backup，兩行的時間軸就會錯開，修完反而更糟。
    """
    lo, hi = region
    marks, _ = _scan(measure)
    for element, start, _ in marks:
        if element.tag in ("backup", "forward"):
            # 跳躍看的是**落點**，不是起點。大譜表交錯排的時候，backup 是用來
            # 「跳回去寫另一行的同一拍」，所以它屬於落點那一拍。
            #
            # 兩個邊界剛好相反，用起點判斷一定會錯一個：
            #   區間**前**那個 backup   起點 = 區間起點，落點在區間外 → 不能縮
            #   區間**最後**那個 backup 起點 = 區間終點，落點在區間內 → 必須縮
            # 第一版用起點判斷，前面那個縮錯、後面那個漏縮，兩個錯誤還不會互相
            # 抵消（一個多 2 個刻度、一個少 2 個），怎麼調都對不上。
            offset = float(element.findtext("duration") or 0)
            target = start - offset if element.tag == "backup" else start + offset
            if not (lo - 1e-6 <= target < hi - 1e-6 and start <= hi + 1e-6):
                continue
        elif not (lo - 1e-6 <= start < hi - 1e-6):
            continue

        duration = element.find("duration")
        if duration is not None and duration.text:
            duration.text = str(int(round(float(duration.text) * 2 / 3)))

        if element.tag != "note" or element.find("time-modification") is not None:
            continue
        modification = etree.Element("time-modification")
        etree.SubElement(modification, "actual-notes").text = "3"
        etree.SubElement(modification, "normal-notes").text = "2"
        # <time-modification> 在 <note> 裡有固定的位置：type / dot 之後
        anchor = element.find("dot")
        if anchor is None:
            anchor = element.find("type")
        position = list(element).index(anchor) + 1 if anchor is not None else len(element)
        element.insert(position, modification)


#
# `fix_missing_tuplets()` 處理的是「一個連音記號都沒有」。這裡處理的是相反的
# 情況：homr **認出了連音，但只標了其中幾個音**。
#
#     〈山魔王的宮殿〉第 43 小節　divisions=12（四分音符 = 12）
#     voice 5 逐音展開：
#         dur=6              ← 普通八分音符
#         dur=4  3:2  ┐
#         dur=4  3:2  ├ 只有這三個被標成三連音
#         dur=4  3:2  ┘
#         dur=6 ×6           ← 其餘全是普通八分音符
#
# 譜上那個 `3` 底下是一條**橫跨十幾個音的長連桁**，整段都是三連音。
# 少標的結果是 voice 5 變成 5.0 拍而不是 4.0。
#
# 這一首壞掉的 10 個小節裡，42–48 那連續七個全部是這個型態 —— 而第 42 小節
# 正是全曲第一次出現連音記號的地方，前面 41 小節一個都沒有，所以開頭全對。
# 使用者說的「同樣的結構，開頭會成功，中間就壞掉」講的就是這件事。

#: 補完之後**還要是完整的連音群**才動手（3:2 就補 3 的倍數）。
#: 少了這一條就會退化成 `_find_triplet()` 踩過的坑：從五連音裡挑三個標成
#: 三連音，小節的拍數湊對了、音樂卻是錯的，而且從此再也看不出來。
TUPLET_WHOLE_GROUPS = True


def _tuplet_completion(notes, divisions, expected):
    """這個聲部的連音群補得完嗎？補得完就回 ([要標的音符], actual, normal)。

    判準一條一條都是必要的，少一條就會開始亂猜：

        1 這個聲部太長（連音只會讓時值變短，短的不是這個病）
        2 裡面已經有 <time-modification>，而且比例一致 —— 這是「譜上確實有
          連音記號」的證據，不是我們自己想像出來的
        3 沒標的那些音跟已標的**同一個 <type>**、而且長度都一樣
          （長連桁底下是一整串等長的音，這是它的定義）
        4 補完之後這個聲部的總長**剛好**等於拍號要求 —— 湊不出整數就不動
        5 補的數量是完整的連音群（見 TUPLET_WHOLE_GROUPS）
    """
    length = sum(float(n.findtext("duration") or 0) for n in notes)
    excess = length - expected * divisions
    if excess <= TOLERANCE * divisions:
        return None                              # 1

    marked = [n for n in notes if n.find("time-modification") is not None]
    if not marked:
        return None                              # 2
    ratios = set()
    for note in marked:
        modification = note.find("time-modification")
        try:
            ratios.add((int(modification.findtext("actual-notes")),
                        int(modification.findtext("normal-notes"))))
        except (TypeError, ValueError):
            return None
    if len(ratios) != 1:
        return None
    actual, normal = ratios.pop()
    if actual <= normal or normal <= 0:
        return None

    types = {n.findtext("type") for n in marked}
    # 只看**連著**已標音符的那一段等長音。長連桁是一條連續的線，中間隔了
    # 別的時值就是另一群了。
    run = _same_type_run(notes, marked, types)
    plain = [n for n in run if n.find("time-modification") is None]
    if not plain:
        return None
    lengths = {float(n.findtext("duration") or 0) for n in plain}
    if len(lengths) != 1:
        return None                              # 3
    unit = lengths.pop()
    if unit <= 0:
        return None

    # 標一個省下 unit * (1 - normal/actual)
    saved = unit * (actual - normal) / actual
    count = excess / saved
    if abs(count - round(count)) > 1e-6:
        return None                              # 4
    count = int(round(count))
    if not 0 < count <= len(plain):
        return None
    if TUPLET_WHOLE_GROUPS and count % actual:
        return None                              # 5

    # 從**緊接在已標音符後面**的那些開始補：長連桁是往右延伸的，
    # 而 homr 標到的通常是連音數字所在的第一群。不夠再往前補，
    # 一樣從離已標音符最近的那一個開始 —— 兩邊都保證補出來的是連續的一段。
    first, last = run.index(marked[0]), run.index(marked[-1])
    after = [n for n in run[last + 1:] if n.find("time-modification") is None]
    before = [n for n in run[:first] if n.find("time-modification") is None]
    chosen = after[:count]
    if len(chosen) < count:
        chosen = before[len(chosen) - count:] + chosen
    return chosen, actual, normal


def _same_type_run(notes, marked, types):
    """包住所有已標音符的那一段**連續同 type** 音符。"""
    positions = [notes.index(n) for n in marked]
    low, high = min(positions), max(positions)
    while low - 1 >= 0 and notes[low - 1].findtext("type") in types:
        low -= 1
    while high + 1 < len(notes) and notes[high + 1].findtext("type") in types:
        high += 1
    return notes[low:high + 1]


def fix_partial_tuplets(root, initial=(4, 4)):
    """把只標了一半的連音群補完。回傳補了幾個聲部。

    逐 voice 處理，不跨 voice —— 大譜表的兩行是各自獨立的時間軸，
    一邊的連音跟另一邊沒有關係。
    """
    changed = 0
    for part in root.findall("part"):
        plans = []
        for measure, divisions, signature, _ in _walk(part, initial):
            expected = _expected(signature)
            for notes in voice_notes(measure).values():
                plan = _tuplet_completion(notes, divisions, expected)
                if plan:
                    plans.append(plan)

        if not plans:
            continue
        # 時值要乘 normal/actual，除不盡就先把整個 part 的刻度放大。
        # 各群的比例可能不同，取乘積最保險（3:2 與 5:4 同時出現時 15 就夠）。
        factor = _tuplet_scale(plans)
        if factor > 1:
            _scale_divisions(part, factor)
        for notes, actual, normal in plans:
            for note in notes:
                _apply_tuplet(note, actual, normal)
            changed += 1
    return changed


def _tuplet_scale(plans):
    """時值乘 normal/actual 會出現小數的話，整個 part 的刻度要放大幾倍。"""
    factor = 1
    for notes, actual, normal in plans:
        for note in notes:
            value = float(note.findtext("duration") or 0) * factor * normal / actual
            if abs(value - round(value)) > 1e-9:
                factor *= actual
                break
    return factor


def _apply_tuplet(note, actual, normal):
    """把一個音的時值乘 normal/actual，並補上 <time-modification>。"""
    duration = note.find("duration")
    if duration is not None and duration.text:
        duration.text = str(int(round(float(duration.text) * normal / actual)))
    if note.find("time-modification") is not None:
        return
    modification = etree.Element("time-modification")
    etree.SubElement(modification, "actual-notes").text = str(actual)
    etree.SubElement(modification, "normal-notes").text = str(normal)
    # <time-modification> 在 <note> 裡有固定的位置：type / dot 之後
    anchor = note.find("dot")
    if anchor is None:
        anchor = note.find("type")
    position = list(note).index(anchor) + 1 if anchor is not None else len(note)
    note.insert(position, modification)
