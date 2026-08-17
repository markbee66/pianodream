"""修法一：拍號認錯了就改宣告。

從 `repair.py` 拆出來的三種修法之一。這一種**不動任何音符**，只換量尺 ——
所以它一定要排在時值修法後面（順序的道理見 `repair.py` 開頭）。

    André 小奏鳴曲   第 25 小節之後全部量到 2 拍，被宣告成 4/4
                     → 那裡開始是 Rondo，本來就是 2/4。**一個拍號認錯，33 個
                       小節陪葬**。跟標準答案比對，那 33 小節的音其實 100% 正確。

四條規則，證據強度由弱到強：

    fix_time_signatures        一段之內長度一致、卻跟宣告的不合（門檻 70%）
    align_to_known_signatures  眾數剛好是這首曲子別處用過的拍號（門檻 30%，
                               因為多了一個來自段落之外的證據）
    normalize_time_signatures  16/16 -> 4/4，長度完全等價，不是猜的
    dedupe_time_signatures     同一小節裡兩個互相矛盾的 <time>，留對的那個
"""

from collections import Counter

from lxml import etree

from .timing import TOLERANCE, measure_duration as _duration_of
from .walk import BEAT_TYPES, MAX_BEATS, _expected, _signature_for, _walk

# 一段裡有多少比例的小節長度一致，才認定「宣告的拍號是錯的」。
# 訂高一點：拍號改錯的話整段時間軸都會偏掉，寧可不改。
TIME_RUN_AGREE = 0.70
TIME_RUN_MIN = 4          # 至少要連續這麼多小節，才算得上「一段」

# `align_to_known_signatures()` 用的門檻，比上面寬鬆很多。
# 那條規則多了一個來自段落之外的證據（這個拍號是這首曲子別處用過的），
# 所以不需要段落自己也達到 70%。
TIME_KNOWN_AGREE = 0.30


def fix_time_signatures(root, initial=(4, 4)):
    """一整段小節長度一致、卻跟宣告的拍號不合，就改宣告。回傳改了幾處。"""
    changed = 0
    for part in root.findall("part"):
        rows = list(_walk(part, initial))
        # 依「宣告拍號的位置」切段 —— 一段之內拍號是同一個
        segments, current = [], []
        for row in rows:
            if row[3] and current:
                segments.append(current)
                current = []
            current.append(row)
        if current:
            segments.append(current)

        for segment in segments:
            if len(segment) < TIME_RUN_MIN:
                continue
            signature = segment[0][2]
            expected = _expected(signature)
            # 最後一小節常常是不完整的收尾，不列入統計
            body = segment[:-1] if len(segment) > TIME_RUN_MIN else segment
            lengths = [round(_duration_of(m, d), 4) for m, d, _, _ in body]
            if not lengths:
                continue

            common, count = Counter(lengths).most_common(1)[0]
            if count / len(lengths) < TIME_RUN_AGREE:
                continue                       # 長度本來就參差，不是拍號的問題
            if abs(common - expected) < TOLERANCE:
                continue                       # 宣告的拍號本來就對

            new_signature = _signature_for(common, signature[1])
            if new_signature is None:
                continue

            if _write_time(segment[0][0], new_signature):
                changed += 1
    return changed


def align_to_known_signatures(root):
    """段落的長度眾數剛好等於**這首曲子別處用過的拍號**時，改成那一個。回傳改了幾處。

    這一條要跑在**合併之後**。`fix_time_signatures()` 是逐頁跑的，一頁只有十來個
    小節，段落太短、統計不出東西；整首看才有足夠的樣本。

    判準跟 `fix_time_signatures()` 不一樣，而且刻意寬鬆得多（30% 而不是 70%），
    因為它多了一個**來自段落之外**的證據：這個拍號是這首曲子自己用過的。

        〈李斯特 鐘〉  第 1 小節宣告 7/8（3.5 個四分音符），管到第 60 小節
                      那 60 小節的長度眾數是 3.0（23 個），3.5 一個都沒有
                      而全曲第 100 小節印著 6/8 —— 3.0 正好就是它

    7/8 在鋼琴曲裡本來就罕見，而 6/8 是這首曲子確實在用的拍號。認錯的是 7/8。
    改完之後那 23 個小節不再被判成「拍數不對」，問題清單也才看得出真正的問題。

    兩道防線避免亂改：眾數要達到 30%，而且要比「符合目前宣告」的小節**多**。
    只有一個拍號宣告的曲子完全不受影響（沒有別處可以參照）。
    """
    changed = 0
    for part in root.findall("part"):
        rows = list(_walk(part))
        known = {}
        for measure, _, signature, declared in rows:
            if declared:
                known.setdefault(round(_expected(signature), 4), signature)
        if len(known) < 2:
            continue                    # 沒有別的拍號可以參照

        segments, current = [], []
        for row in rows:
            if row[3] and current:
                segments.append(current)
                current = []
            current.append(row)
        if current:
            segments.append(current)

        for segment in segments:
            if len(segment) < TIME_RUN_MIN:
                continue
            signature = segment[0][2]
            expected = round(_expected(signature), 4)
            lengths = [round(_duration_of(m, d), 4) for m, d, _, _ in segment]
            counts = Counter(lengths)
            common, hits = counts.most_common(1)[0]
            if abs(common - expected) < TOLERANCE:
                continue                # 宣告的本來就對
            if common not in known:
                continue                # 眾數不是這首用過的拍號，不動它
            if hits / len(lengths) < TIME_KNOWN_AGREE:
                continue
            if hits <= counts.get(expected, 0):
                continue                # 沒有比宣告的更有說服力

            if _write_time(segment[0][0], known[common]):
                changed += 1
    return changed


def normalize_time_signatures(root):
    """把寫得很怪、但長度等價的拍號換成慣用寫法。回傳改了幾處。

    homr 把〈うまぴょい伝説〉鋼琴版的 4/4 認成 **16/16**。
    16 個十六分音符 = 4 個四分音符，**長度完全一樣**，所以小節拍數檢查
    看不出任何問題（它比的是長度），Gate B 也不會抱怨 —— 只有跨頁比對拍號時
    才會冒出一句「各段的拍號不一致」。

    這裡只在「長度不變」的前提下換成慣用的分母，不會動到任何音符的時值。
    真正少見但合法的拍號（7/8、5/4）分子分母不會相等，不受影響。
    """
    changed = 0
    for time in root.iter("time"):
        try:
            beats = int(time.findtext("beats"))
            beat_type = int(time.findtext("beat-type"))
        except (TypeError, ValueError):
            continue
        if beat_type <= 4 or beats != beat_type:
            continue        # 只處理 8/8、16/16 這種分子分母相同的怪寫法

        quarters = beats * 4.0 / beat_type          # 一小節幾個四分音符
        for candidate in (4, 2, 8):
            value = quarters * candidate / 4.0
            if abs(value - round(value)) < 1e-9 and 1 <= round(value) <= MAX_BEATS:
                if (round(value), candidate) != (beats, beat_type):
                    time.findtext("beats")
                    time.find("beats").text = str(int(round(value)))
                    time.find("beat-type").text = str(candidate)
                    changed += 1
                break
    return changed


def dedupe_time_signatures(root):
    """同一小節裡有兩個互相矛盾的 <time> 時只留一個。回傳刪掉幾個。

    `_write_time()` 修的是「**我們自己**寫拍號時不要留下矛盾」，但 homr 產出的檔案
    本來就可能自帶矛盾 —— 〈Rush E〉第 1 頁的第 1 小節就是兩個 `<attributes>`：
    第一個裝 divisions / staves / time（4/4），第二個裝 key / time / clef（2/4）。

    這種矛盾自己不會報錯，但**誰算數要看讀取端怎麼掃**：

        repair._walk / measure_health   用 `attributes/time` -> 讀到 4/4，覺得沒問題
        validate                        逐一走過每個 attributes -> 被 2/4 蓋掉

    於是 repair 一處都沒改（`time_fixes` 是 0），validate 卻拿 2/4 去量 4 拍的小節，
    整頁 13 個小節報了 11 個錯、信心掉到 0.41。修 repair 或修 validate 都只是換一邊
    讀錯，真正的修法是**讓檔案自己不要互相矛盾**，所以這一步要跑在最前面。

    留哪一個不用猜：拿這一小節到下一個拍號宣告為止的實際長度去比，符合的小節多的
    那一個留下來；平手時留先出現的，跟 `_walk()` 既有的行為一致。
    """
    removed = 0
    for part in root.findall("part"):
        measures = part.findall("measure")

        # 先量好每一小節實際佔幾個四分音符，等一下要拿來投票
        divisions, lengths = 1.0, []
        for measure in measures:
            value = measure.findtext("attributes/divisions")
            if value:
                try:
                    divisions = float(value)
                except ValueError:
                    pass
            lengths.append(_duration_of(measure, divisions))

        for index, measure in enumerate(measures):
            found = [(block, element)
                     for block in measure.findall("attributes")
                     for element in block.findall("time")]
            if len(found) < 2:
                continue

            parsed = []
            for block, element in found:
                try:
                    signature = (int(element.findtext("beats")),
                                 int(element.findtext("beat-type")))
                except (TypeError, ValueError):
                    signature = None
                parsed.append((block, element, signature))

            if len({s for _, _, s in parsed if s}) < 2:
                keep = parsed[0]            # 重複但不矛盾，留第一個就好
            else:
                # 這一段的範圍：從這一小節到下一個宣告拍號的小節為止
                end = len(measures)
                for later in range(index + 1, len(measures)):
                    if measures[later].find("attributes/time") is not None:
                        end = later
                        break
                run = lengths[index:end]

                keep, best = parsed[0], -1
                for candidate in parsed:
                    if candidate[2] is None:
                        continue
                    expected = _expected(candidate[2])
                    agree = sum(1 for d in run if abs(d - expected) < TOLERANCE)
                    if agree > best:
                        keep, best = candidate, agree

            for block, element, _ in parsed:
                if element is not keep[1]:
                    block.remove(element)
                    removed += 1
    return removed


def _write_time(measure, signature):
    """把拍號寫進這一小節，並清掉同一小節裡其他的 <time>。

    **一個小節可以有好幾個 `<attributes>`。** homr 產出的第 1 小節就是兩個：
    第一個裝 divisions / staves / part-symbol，第二個裝 key / time / clef。
    舊版寫進 `measure.find("attributes")`（第一個），結果同一小節出現兩個
    互相矛盾的拍號 —— 而且**誰算數要看讀取端怎麼掃**：

        repair._walk        用 `attributes/time`，掃到第一個 -> 讀到改過的
        validate            逐一走過每個 attributes -> 最後一個蓋掉 -> 讀到舊的

    所以修好的拍號在檢查那邊完全沒生效，`time` 改了 1 處而拍數不對的小節
    一個都沒少。改法是**寫進已經有 time 的那一塊**，並刪掉其餘的。
    """
    beats, beat_type = signature
    blocks = measure.findall("attributes")
    holder = next((a for a in blocks if a.find("time") is not None), None)
    if holder is None:
        holder = blocks[0] if blocks else None
        if holder is None:
            holder = etree.Element("attributes")
            measure.insert(0, holder)

    # 同一小節裡別的 time 一律刪掉，免得留下互相矛盾的宣告
    for block in blocks:
        if block is holder:
            continue
        for extra in block.findall("time"):
            block.remove(extra)

    time = holder.find("time")
    if time is None:
        time = etree.SubElement(holder, "time")
    for child in list(time):
        time.remove(child)
    etree.SubElement(time, "beats").text = str(beats)
    etree.SubElement(time, "beat-type").text = str(beat_type)
    return True
