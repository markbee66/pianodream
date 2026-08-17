"""鋼琴記譜規則層 —— 把「譜上的符號代表什麼」集中寫在一個地方。

## 為什麼要有這一層

之前是**一個症狀修一次**：三連音修一次、拍號修一次、左右手修一次…
每一個 bug 其實都是同一件事 —— **一條沒有寫進程式的記譜規則**。
辨識引擎漏掉的符號不會自己回來，下游只好從拍數硬猜，猜錯了就變成
「小節加總對了、音樂是錯的」。

所以規則集中在這裡，而且分成兩種來源：

    read      從譜面上真的讀到（連音數字、8va、Ped.、強弱）
    inferred  是推算的（拍數對不上時反推）

**inferred 不得提高信心分數。** 這是這一輪學到最貴的一課：把五連音湊成
三連音之後，小節加總對了、Gate B 不再抱怨、信心變成 1.00 —— 驗證通過，
問題卻被蓋掉而且再也看不見。

## 執行順序（順序本身就是規則）

    1 結構合法性   musicxml_fix：會讓 partitura 掛掉的寫法
    2 符號還原     這裡。把 OMR 漏掉的符號從譜面讀回來
    3 時值推算     repair：只在第 2 層讀不到時才動手
    4 結構展開     反覆記號
    5 可彈性檢查   只回報不修改

**第 2 層一定要排在第 3 層前面：能讀到的就不要猜。**
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import musicxml_fix, ocr, repair

_STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# 連結線的判準：同音高、同聲部，而且**第二個音的起點正好等於第一個音的終點**。
#
# 「中間隔幾個音」不能當判準 —— 實測不同音高的圜滑線也有 11 對是緊鄰的，
# 跟同音高的分佈完全重疊。時間上是否接續才分得開：
#
#     同音高 + 時間緊接    39 對  ← 連結線
#     同音高但不緊接        6 對  ← 樂句線剛好回到同一個音
#     不同音高             62 對  ← 全部是圜滑線
TIE_TOLERANCE = 1e-6

# 連音數字印在符桿外側。往上下找多遠（以行距為單位）
TUPLET_SEARCH_INTERLINE = 3.5
_TUPLET_DIGITS = {3, 5, 6, 7, 9}

_OCTAVE_UP = re.compile(r"8\s*v?[ab]?\.?$|8\s*va|^8$", re.IGNORECASE)
_OCTAVE_DOWN = re.compile(r"8\s*vb|8\s*ba|15\s*mb", re.IGNORECASE)
_PEDAL_ON = re.compile(r"ped\.?", re.IGNORECASE)
_DYNAMIC = re.compile(r"^(pp+|p|mp|mf|f{1,3}|sf+z?|fp)$")
_DYNAMIC_WORD = re.compile(r"(cresc|decresc|dim|rall|rit|accel)", re.IGNORECASE)


@dataclass
class RuleReport:
    """每一條規則做了什麼，以及是讀到的還是猜的。"""

    read: dict = field(default_factory=dict)        # 從譜面讀到的
    inferred: dict = field(default_factory=dict)    # 推算出來的
    checks: dict = field(default_factory=dict)      # 只回報不修改的體檢
    next_signature: tuple = (4, 4)                  # 這一頁結束時的拍號，接給下一頁

    def add_read(self, name, count):
        if count:
            self.read[name] = self.read.get(name, 0) + count

    def add_inferred(self, name, count):
        if count:
            self.inferred[name] = self.inferred.get(name, 0) + count

    @property
    def trustworthy(self):
        """有沒有任何一項是猜的。有的話信心分數就不能照單全收。"""
        return not self.inferred

    def as_dict(self):
        return {"read": dict(self.read), "inferred": dict(self.inferred),
                "checks": dict(self.checks)}

    def describe(self):
        parts = []
        if self.read:
            parts.append("讀到 " + "、".join(f"{k} {v}" for k, v in self.read.items()))
        if self.inferred:
            parts.append("推算 " + "、".join(f"{k} {v}" for k, v in self.inferred.items()))
        return "；".join(parts) or "沒有需要補的符號"


# ---------------------------------------------------------------------------
# 2-a 連結線：從圜滑線還原
# ---------------------------------------------------------------------------

def _midi_of(note):
    pitch = note.find("pitch")
    if pitch is None:
        return None
    step = (pitch.findtext("step") or "C").strip().upper()
    octave = int(float(pitch.findtext("octave") or 4))
    alter = int(float(pitch.findtext("alter") or 0))
    return 12 * (octave + 1) + _STEP.get(step, 0) + alter


def restore_ties(root):
    """把「其實是連結線」的圜滑線轉成 <tie>。回傳補了幾個。

    辨識引擎一個 tie 都認不出來（實測 6 份譜全部是 0），因為連結線與圜滑線
    在紙上長得**一模一樣**，模型只學會了後者。但記譜規則本身分得開：

        連結線  連同一個音高、同一個聲部、時間上緊鄰的兩個音 —— 意思是「按住別放」
        圜滑線  連不同音高，常常跨一整個樂句

    沒有連結線的後果很具體：該按住的長音變成**重彈兩次**，音遊叫你多敲一次，
    評分把「按住」判成錯。

    只認最嚴格的情況，寧可漏掉也不要把該重彈的音黏成長音。
    """
    added = 0
    for part in root.findall("part"):
        rows = _scan_onsets(part)
        # ⚠ lxml 每次存取都會給一個新的 Python proxy，`id()` 不穩定。
        # 一定要把元素**留在 list 裡**再拿它當 key，否則查表全部落空。
        timing = {}
        voices = {}
        for note, voice, onset, duration in rows:
            timing[note] = (onset, duration)
            voices.setdefault(voice, []).append(note)

        for sequence in voices.values():
            opens = {}
            for note in sequence:
                for slur in note.findall("notations/slur"):
                    number = slur.get("number") or "1"
                    kind = slur.get("type")
                    if kind == "start":
                        opens[number] = (note, slur)
                    elif kind == "stop" and number in opens:
                        start_note, start_slur = opens.pop(number)
                        if start_note not in timing or note not in timing:
                            continue
                        onset, duration = timing[start_note]
                        following, _ = timing[note]
                        same_pitch = (_midi_of(start_note) is not None
                                      and _midi_of(start_note) == _midi_of(note))
                        adjacent = abs(onset + duration - following) < TIE_TOLERANCE
                        if same_pitch and adjacent:
                            _make_tie(start_note, note, start_slur, slur)
                            added += 1
    return added


def _scan_onsets(part):
    """走一遍，回傳 [(note, voice, 起點, 長度)]，單位都是四分音符。

    回傳的 list 會**持有元素的引用** —— 見 restore_ties 裡的 lxml 註解。
    """
    rows = []
    base = 0.0
    divisions = 1.0
    for measure in part.findall("measure"):
        value = measure.findtext("attributes/divisions")
        if value:
            try:
                divisions = float(value)
            except ValueError:
                pass
        cursor = longest = anchor = 0.0
        for element in measure:
            if element.tag == "note":
                duration = float(element.findtext("duration") or 0)
                chord = element.find("chord") is not None
                grace = element.find("grace") is not None
                start = anchor if (chord or grace) else cursor
                rows.append((element, element.findtext("voice") or "1",
                             base + start / divisions, duration / divisions))
                if chord or grace:
                    continue
                anchor = cursor
                cursor += duration
                longest = max(longest, cursor)
            elif element.tag == "backup":
                cursor -= float(element.findtext("duration") or 0)
            elif element.tag == "forward":
                cursor += float(element.findtext("duration") or 0)
                longest = max(longest, cursor)
        base += longest / divisions if divisions else 0.0
    return rows


# 前後兩小節的音高重疊到這個比例，才認為是「同一個和弦被按著」。
# 〈Rush E〉結尾四小節實測重疊 70%/62%/67%/75%，所以門檻訂在 0.6；
# 訂 0.75 只救得到最後一對，訂 0.9 一對都救不到。
CHORD_TIE_OVERLAP = 0.60
# 太小的「和弦」不套用 —— 兩個音的重複在密集音型裡太常見，證據不夠強
CHORD_TIE_MIN_NOTES = 3


def extend_chord_ties(root):
    """整個和弦被按著、但引擎只認出其中一條連結線時，把其餘的補上。回傳補了幾個。

    〈Rush E〉結尾是四個小節的巨大音簇連在一起（譜上每個符頭都畫著連結線），
    但 homr **每小節只認出 1 條**，其餘十幾個音全部變成重新彈奏 ——
    玩起來就是「該壓著的地方叫你連按四次」。

    `restore_ties()` 補不了這個：它是把**圜滑線**轉成連結線，而這裡連圜滑線
    都沒被認出來，沒有東西可以轉。

    所以改用另一組證據：

        1. 前一小節已經有**至少一條被認出的連結線** —— 譜上確實畫了連結線
        2. 前後兩小節的音高集合重疊 >= CHORD_TIE_OVERLAP
        3. 和弦至少 CHORD_TIE_MIN_NOTES 個音

    三個條件同時成立時，把兩邊都有的音高補上連結線。只補**交集**，
    單邊才有的音仍然當成重新彈奏 —— 那多半是引擎在兩個小節讀出了不同的音。

    實測波及 12 首裡 5 首、23 對小節、129 個音（約全部音符的 0.26%）。
    萬一判錯的後果是「該重彈的變成按住」，比反過來輕得多。
    """
    added = 0
    for part in root.findall("part"):
        measures = part.findall("measure")
        for first, second in zip(measures, measures[1:]):
            head = _tieable_notes(first)
            tail = _tieable_notes(second)
            if len(head) < CHORD_TIE_MIN_NOTES or not tail:
                continue
            if not any(_already_tied(n) for notes in head.values() for n in notes):
                continue          # 沒有任何一條被認出的連結線，證據不足

            shared = set(head) & set(tail)
            if len(shared) / len(head) < CHORD_TIE_OVERLAP:
                continue

            for pitch in shared:
                start, stop = head[pitch][-1], tail[pitch][0]
                # 只擋「同方向已經有了」。連結線鏈中間的音符**同時**帶著
                # stop 與 start，一律跳過已有任何 tie 的音會把鏈條切斷：
                # m165 當了 m164->165 的終點之後，就再也接不到 m166。
                if _tied_as(start, "start") or _tied_as(stop, "stop"):
                    continue
                _make_tie(start, stop, None, None)
                added += 1
    return added


def _tieable_notes(measure):
    """這一小節裡「有音高、不是裝飾音」的音符，依音高分組。"""
    found = {}
    for note in measure.findall("note"):
        if note.find("rest") is not None or note.find("grace") is not None:
            continue
        midi = _midi_of(note)
        if midi is None:
            continue
        found.setdefault(midi, []).append(note)
    return found


def _already_tied(note):
    return bool(note.findall("tie")) or bool(note.findall("notations/tied"))


def _tied_as(note, kind):
    """這個音符有沒有 kind（start / stop）方向的連結線。

    一個音符可以同時有兩種 —— 那表示它在連結線鏈的中間（前一個音連過來、
    再連到下一個音）。判斷「能不能再接」時必須分方向看。
    """
    if any(t.get("type") == kind for t in note.findall("tie")):
        return True
    return any(t.get("type") == kind for t in note.findall("notations/tied"))


def _make_tie(first, second, first_slur, second_slur):
    """把一對音符綁成連結線，順便把原本的圜滑線標記拿掉。

    first_slur / second_slur 給 None 表示「本來就沒有圜滑線可以拿掉」——
    `extend_chord_ties()` 是憑重疊率補的，不是從圜滑線轉來的。
    """
    for note, kind in ((first, "start"), (second, "stop")):
        tie = etree.Element("tie")
        tie.set("type", kind)
        # <tie> 在 <note> 裡的位置固定在 <duration> 之後、<voice> 之前
        anchor = note.find("duration")
        position = list(note).index(anchor) + 1 if anchor is not None else len(note)
        note.insert(position, tie)

        notations = note.find("notations")
        if notations is None:
            notations = etree.SubElement(note, "notations")
        tied = etree.SubElement(notations, "tied")
        tied.set("type", kind)

    for slur in (first_slur, second_slur):
        if slur is None:
            continue
        parent = slur.getparent()
        if parent is not None:
            parent.remove(slur)


# ---------------------------------------------------------------------------
# 2-b 從版面讀回來的符號
# ---------------------------------------------------------------------------

def read_page_symbols(page_text, layout_page, interline):
    """從一頁的文字辨識結果整理出各種符號，回傳 dict。

    只負責「讀」，不負責寫進 MusicXML —— 因為同一份 MusicXML 是多頁合併的，
    要等合併之後才知道每一頁的小節對應到哪裡。

    回傳的 `measure` 是**頁內第幾格**（1 起算），不是譜上印的號碼。
    `layout.py` 讀得到印出來的小節號時給的是全曲編號、讀不到就退回每頁 1..N，
    兩種混在一起 —— 直接當成全曲編號用的話，沒有印小節號的那幾頁會把符號
    套到開頭去（速度記號就踩過這個坑，見 `pagemap.detect_tempo_map()`）。
    呼叫端要用 `pagemap.scaled(index, boxes, measures) + offset` 換算，跟
    `enrich.apply_ottavas()` 同一套。
    """
    found = {"tuplets": [], "octave": [], "pedal": [], "dynamics": [], "fingering": []}
    if not page_text or not page_text.items:
        return found

    boxes = []
    for position, box in enumerate((layout_page or {}).get("measures", []), start=1):
        corners = box.get("corners") or []
        if not corners:
            continue
        xs = [float(c[0]) for c in corners]
        ys = [float(c[1]) for c in corners]
        boxes.append({"n": position,
                      "x0": min(xs), "x1": max(xs),
                      "y0": min(ys), "y1": max(ys)})

    def measure_at(x, y):
        """這個位置落在第幾小節。找不到就回 None。"""
        inside = [b for b in boxes if b["x0"] - 2 <= x <= b["x1"] + 2
                  and b["y0"] - interline * TUPLET_SEARCH_INTERLINE <= y
                  <= b["y1"] + interline * TUPLET_SEARCH_INTERLINE]
        if not inside:
            return None
        return min(inside, key=lambda b: abs((b["y0"] + b["y1"]) / 2 - y))["n"]

    for item in page_text.confident(0.5):
        text = item.text.strip()
        measure = measure_at(item.center_x, item.center_y)

        if text.isdigit() and int(text) in _TUPLET_DIGITS and item.height <= interline * 2.2:
            found["tuplets"].append({"digit": int(text), "measure": measure,
                                     "x": item.center_x, "y": item.center_y})
        elif _OCTAVE_DOWN.search(text):
            found["octave"].append({"shift": -12, "measure": measure, "text": text})
        elif _OCTAVE_UP.search(text):
            found["octave"].append({"shift": 12, "measure": measure, "text": text})
        elif _PEDAL_ON.search(text):
            found["pedal"].append({"measure": measure, "x": item.center_x})
        elif _DYNAMIC.match(text) or _DYNAMIC_WORD.search(text):
            found["dynamics"].append({"mark": text, "measure": measure})
        elif text.isdigit() and 1 <= int(text) <= 5 and item.height <= interline * 1.6:
            found["fingering"].append({"finger": int(text), "measure": measure})

    return found


def apply_tuplet_digits(root, tuplets):
    """譜上印著連音數字時，直接照它改時值 —— 不要靠拍數反推。

    〈うまぴょい伝説〉第 1 小節印著「5」，`repair.py` 卻只認得三連音，
    從那五個音裡挑三個湊成三連音，加總對了、音樂錯了。
    讀到數字就沒有這個問題。
    """
    if not tuplets:
        return 0

    wanted = {}
    for entry in tuplets:
        if entry.get("measure"):
            wanted.setdefault(int(entry["measure"]), set()).add(int(entry["digit"]))

    changed = 0
    for part in root.findall("part"):
        for number, measure in enumerate(part.findall("measure"), start=1):
            digits = wanted.get(number)
            if not digits:
                continue
            for digit in sorted(digits):
                changed += _mark_tuplet_run(measure, digit)
    return changed


def _mark_tuplet_run(measure, digit):
    """把小節裡最長的一串等長音符標成 digit 連音（還沒被標過的才動）。"""
    runs, current = [], []
    for note in measure.findall("note"):
        if note.find("chord") is not None or note.find("grace") is not None:
            continue
        if note.find("time-modification") is not None:
            current = []
            continue
        duration = note.findtext("duration")
        if current and current[-1][1] == duration:
            current.append((note, duration))
        else:
            if len(current) >= digit:
                runs.append(current)
            current = [(note, duration)]
    if len(current) >= digit:
        runs.append(current)

    for run in runs:
        if len(run) != digit:
            continue
        normal = 2 if digit == 3 else (4 if digit in (5, 6, 7) else 8)
        for note, _ in run:
            modification = etree.Element("time-modification")
            etree.SubElement(modification, "actual-notes").text = str(digit)
            etree.SubElement(modification, "normal-notes").text = str(normal)
            anchor = note.find("dot") or note.find("type")
            position = list(note).index(anchor) + 1 if anchor is not None else len(note)
            note.insert(position, modification)
        return 1
    return 0


# ---------------------------------------------------------------------------
# 5 可彈性檢查（只回報，不修改）
# ---------------------------------------------------------------------------

HAND_SPAN = 12          # 一隻手最多張得開幾個半音
PIANO_LOW, PIANO_HIGH = 21, 108


def playability(root):
    """這份譜人彈得出來嗎。數字異常代表前面某一層認錯了。"""
    out_of_range = 0
    total = 0
    for note in root.iter("note"):
        midi = _midi_of(note)
        if midi is None:
            continue
        total += 1
        if not (PIANO_LOW <= midi <= PIANO_HIGH):
            out_of_range += 1
    return {"notes": total, "out_of_range": out_of_range}


# ---------------------------------------------------------------------------
# 2-c 八度記號
# ---------------------------------------------------------------------------

MIN_MIDI = 21       # A0
MAX_MIDI = 108      # C8


def apply_ottavas(root, spans):
    """把 8va / 8vb 套到音符的音高上。回傳移了幾個音。

    **改音高，不寫 `<octave-shift>`。** 下游（partitura -> note_array -> 音遊譜面、
    以及評分的對齊）讀的是音高；寫成 direction 的話，會不會被套用要看讀取端，
    而「靜靜地不套用」正是這個 bug 原本的樣子 —— homr 連 direction 都不產，
    整首 8va 段落就這樣低了一個八度。

    同一個 (staff, 小節) 只移一次：兩條記號在版面上重疊時（跨頁、或同一行
    印了兩段），重複套用會變成兩個八度。
    """
    if not spans:
        return 0

    wanted = {}
    for span in spans:
        staff = str(span.get("staff") or 1)
        shift = int(span.get("shift") or 0)
        if not shift:
            continue
        for measure in range(int(span["from"]), int(span["to"]) + 1):
            wanted.setdefault((staff, measure), shift)

    moved = 0
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            try:
                number = int(measure.get("number"))
            except (TypeError, ValueError):
                continue
            for note in measure.findall("note"):
                pitch = note.find("pitch")
                if pitch is None:
                    continue
                staff = (note.findtext("staff") or "1").strip()
                shift = wanted.get((staff, number))
                if not shift:
                    continue
                midi = _midi_of(note)
                if midi is None or not MIN_MIDI <= midi + shift <= MAX_MIDI:
                    continue        # 移出 88 鍵就是判斷錯了，寧可不動
                octave = pitch.find("octave")
                octave.text = str(int(float(octave.text)) + shift // 12)
                moved += 1
    return moved


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def apply(path, page_symbols=None, initial=(4, 4), ottavas=None):
    """對一份 MusicXML 套用所有規則。回傳 RuleReport。

    page_symbols：各頁從版面讀到的符號（`read_page_symbols()` 的結果），
    合併後的檔案才有辦法對到小節，所以由呼叫端收集好再傳進來。
    ottavas：各頁的八度記號（`layout.ottava_spans()` 的結果），同理。
    """
    path = Path(path)
    report = RuleReport()

    # 1 結構合法性
    report.add_read("結構修正", musicxml_fix.sanitize_file(path))

    parser = etree.XMLParser(remove_blank_text=False, recover=True, resolve_entities=False)
    try:
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError):
        return report
    root = tree.getroot()

    # 2 符號還原 —— 這一層是「讀」，一定要排在推算前面
    report.add_inferred("連結線", restore_ties(root))
    # 整個和弦被按著、引擎卻只認出一條連結線的情況（〈Rush E〉結尾），
    # 要排在 restore_ties() 後面：它可能剛補上那條當證據用的連結線。
    report.add_inferred("和弦連結線", extend_chord_ties(root))
    report.add_read("八度記號音符", apply_ottavas(root, ottavas))
    if page_symbols:
        merged = {"tuplets": []}
        for page in page_symbols:
            merged["tuplets"].extend(page.get("tuplets") or [])
        report.add_read("連音數字", apply_tuplet_digits(root, merged["tuplets"]))
        for name in ("octave", "pedal", "dynamics", "fingering"):
            count = sum(len(p.get(name) or []) for p in page_symbols)
            report.add_read({"octave": "八度記號", "pedal": "踏板",
                             "dynamics": "強弱", "fingering": "指法"}[name], count)

    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=False)

    # 3 時值推算 —— 只處理第 2 層讀不到的部分
    fixed = repair.repair_file(path, initial=initial)
    report.next_signature = fixed.get("next", initial)
    report.add_inferred("補完連音", fixed.get("partial_tuplet", 0))
    report.add_inferred("三連音", fixed.get("tuplet", 0))
    report.add_inferred("拍號", fixed.get("time", 0))
    report.add_read("拍號正規化", fixed.get("normalized", 0))

    # 5 可彈性檢查
    tree = etree.parse(str(path), parser)
    report.checks = playability(tree.getroot())
    report.checks["measure_health"] = round(fixed.get("after", 0.0), 3)
    return report
