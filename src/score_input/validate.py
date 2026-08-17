"""Gate B：辨識完之後的樂理合法性檢查。

⚠ **這裡的 confidence 是「找不到內部矛盾」，不是「辨識正確」。** 兩者差很多：

  - 它只看得到「這一小節的拍數跟拍號對不上」這類自我矛盾。整小節一起認錯、
    音高整段偏一個八度、節奏型態認成別的 —— 這些內部完全自洽，它一律看不到。
  - `repair.py` 修過的地方會讓矛盾消失，分數因此變高。**修出來的高分是猜的**，
    不是原本就對。所以 `pipeline` 會把修過的數量一起報出去，
    不要只看那個數字。

真正的正確率只能拿標準答案量（`tools/omr_bench.py` 對 Mutopia 的公有領域樂譜）。


Gate A 管的是「這張照片能不能看」，這一關管的是「看得清楚但認錯了」——
那是 Gate A 完全抓不到的另一半問題。

判斷依據是樂譜本身必須成立的規則。最強的訊號是**每小節的音符時值總和要等於拍號**：
漏認一個音、把八分認成四分、多生一個音符出來，都會讓某一小節的拍數對不上。
這個檢查不需要知道原譜長什麼樣，純粹從產物內部就能發現矛盾。

**同一件事要逐聲部再看一次**，理由有兩個（尺本身在 `timing.py`）：

  1. 小節總長是一個數字，看到它只知道「這裡不對」；逐聲部一列出來就直接指到
     哪一行譜出問題，訊息因此變成「voice 1 是 4.5 拍、voice 5 是 5 拍」。
  2. 各聲部**都各自剛好填滿**的小節不算錯，就算游標對不上也一樣。homr 的大譜表
     是一個音一個音交錯排的，backup 常常擺錯位置 —— 實測 12 首裡有 44 個小節
     （占 8%）兩個聲部都剛好是 4 拍，卻因為游標說 4.5 而被判成拍數不對。

文字記譜那條路徑也會跑這一關 —— parser 只保證語法對，打錯音一樣要抓。
"""

from collections import defaultdict
from pathlib import Path

from lxml import etree

from .timing import measure_duration, voice_durations, voices_agree

PIANO_LOW, PIANO_HIGH = 21, 108      # A0 – C8
_STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

THRESHOLDS = {
    "duration_tolerance": 0.02,   # 小節拍數的容許誤差（以四分音符為單位）
    "bad_measure_warn": 0.10,     # 有問題的小節超過這個比例就整體算可疑
    "empty_measure_warn": 0.25,
    "min_notes": 8,
    "short_page_ratio": 0.5,      # 某一段的小節數少於中位數的這個比例就算離群
}


class Problem:
    def __init__(self, code, level, message, measure=None, hint=""):
        self.code = code
        self.level = level          # "error" | "warn"
        self.message = message
        self.measure = measure
        self.hint = hint

    def as_dict(self):
        return {"code": self.code, "level": self.level, "message": self.message,
                "measure": self.measure, "hint": self.hint}

    def __repr__(self):
        where = f"第 {self.measure} 小節：" if self.measure else ""
        return f"{where}{self.message}"


# ---------------------------------------------------------------------------
# 從 MusicXML 直接讀，不經過 partitura
# ---------------------------------------------------------------------------
# partitura 讀檔時會自己修正一些矛盾（補齊小節、調整時值），那正是我們要抓的東西。
# 要驗「認得對不對」就必須看原始的 XML。

def _iter_measures(root):
    for part in root.findall("part"):
        pid = part.get("id", "P1")
        for measure in part.findall("measure"):
            yield pid, measure


def _midi_of(note):
    pitch = note.find("pitch")
    if pitch is None:
        return None
    step = (pitch.findtext("step") or "C").strip().upper()
    octave = int(float(pitch.findtext("octave") or 4))
    alter = float(pitch.findtext("alter") or 0)
    return 12 * (octave + 1) + _STEP_SEMITONE.get(step, 0) + int(alter)


#: 算這一小節實際佔了多少四分音符（游標模型）。跟 `repair.py` 用**同一份**
#: 實作，見 `timing.py`。
_measure_duration = measure_duration


def _voice_detail(measure, divisions, expected):
    """「voice 1 是 4.5 拍、voice 5 是 5 拍，兩者不一致」這句話。

    只有一個聲部、或各聲部彼此一致時回空字串 —— 那種情況多講也沒有資訊。

    為什麼值得多印這一句：小節總長是一個數字，看到它只知道「這裡不對」，
    要往下追還得自己去翻 XML。逐聲部一列出來就直接指到哪一行譜出問題，
    而網頁的逐小節校對面板讀的就是 `problem["message"]`，不必改任何前端。
    """
    lengths = voice_durations(measure, divisions)
    if len(lengths) < 2 or len({round(v, 4) for v in lengths.values()}) == 1:
        return ""
    shown = "、".join(f"voice {v} 是 {lengths[v]:g} 拍"
                     for v in sorted(lengths, key=lambda k: (len(k), k)))
    off = [v for v in lengths if abs(lengths[v] - expected) > THRESHOLDS["duration_tolerance"]]
    tail = f"，對不上的是 voice {'、'.join(sorted(off, key=lambda k: (len(k), k)))}" if off else ""
    return f"（{shown}，各聲部不一致{tail}）"


def check_musicxml(path, label=None, thresholds=None, initial=(4, 4)):
    """檢查一份 MusicXML，回傳 {confidence, problems, stats}。

    initial 是「這一頁開始時的拍號」。**多頁的譜只在第一頁印拍號**，後面幾頁沿用，
    所以逐頁檢查時一定要把上一頁的結尾拍號傳進來 —— 不傳的話後面每一頁都從 4/4
    起算，整頁的小節全部被判定拍數不對，而檔案其實沒有任何問題。

    `repair._walk()` 早就收這個參數（見它的註解），validate 這一側沒補上，於是
    〈Andre 小奏鳴曲〉第 2 頁（2/4、沒印拍號）被拿 4/4 去量，12 個小節全部報成
    「剛好一半」。實測 12 首裡有 **23 頁**沒有拍號宣告，8 首受影響。

    回傳的 `stats["next_time"]` 是這一頁結束時的拍號，直接餵給下一頁。
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    path = Path(path)
    label = label or path.name
    try:
        parser = etree.XMLParser(recover=True, resolve_entities=False)
        root = etree.parse(str(path), parser).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        return {
            "confidence": 0.0,
            "problems": [Problem("UNREADABLE", "error",
                                 f"樂譜檔讀不了：{exc}", hint="這一段要重新辨識或重拍。").as_dict()],
            "stats": {},
        }

    problems = []
    divisions = 1.0
    try:
        beats, beat_type = float(initial[0]), float(initial[1])
    except (TypeError, ValueError, IndexError):
        beats, beat_type = 4.0, 4.0
    measure_count = notes = empty = 0
    bad_measures = []
    pitches = []

    for _pid, measure in _iter_measures(root):
        measure_count += 1
        number = measure.get("number", str(measure_count))

        for attributes in measure.findall("attributes"):
            if attributes.findtext("divisions"):
                divisions = float(attributes.findtext("divisions")) or 1.0
            time_element = attributes.find("time")
            if time_element is not None:
                try:
                    beats = float(time_element.findtext("beats") or beats)
                    beat_type = float(time_element.findtext("beat-type") or beat_type)
                except ValueError:
                    pass

        measure_notes = [n for n in measure.findall("note") if n.find("rest") is None]
        notes += len(measure_notes)
        if not measure.findall("note"):
            empty += 1

        for note in measure_notes:
            midi = _midi_of(note)
            if midi is None:
                continue
            pitches.append(midi)
            if not PIANO_LOW <= midi <= PIANO_HIGH:
                problems.append(Problem(
                    "PITCH_RANGE", "error",
                    f"出現 MIDI {midi} 的音，超出鋼琴音域（{PIANO_LOW}–{PIANO_HIGH}）",
                    measure=number,
                    hint="通常是譜線的位置認錯，或是把加線數錯了。",
                ))

        expected = beats * (4.0 / beat_type)
        actual = _measure_duration(measure, divisions)
        # 最後一小節允許不滿（收尾），弱起小節在第一小節也允許
        is_edge = measure_count == 1 or measure.getparent().index(measure) == len(
            measure.getparent().findall("measure")
        ) - 1
        # 游標對不上、但**每一個聲部各自都剛好填滿**的小節不算錯。
        # homr 的大譜表是一個音一個音交錯排的，backup 常常擺錯位置，游標因此
        # 走出一個沒有意義的數字 —— 實測 12 首裡有 44 個這種小節（占 8%），
        # 〈山魔王的宮殿〉第 48 小節就是：兩個聲部都是 4.0 拍，游標卻說 4.5。
        # 理由見 `timing.py`。
        if (abs(actual - expected) > th["duration_tolerance"]
                and not voices_agree(measure, divisions, expected,
                                     th["duration_tolerance"])):
            if is_edge and actual < expected:
                pass
            else:
                bad_measures.append(number)
                short = actual < expected
                problems.append(Problem(
                    "BAD_DURATION", "error",
                    f"拍數是 {actual:g} 拍，但拍號 {beats:g}/{beat_type:g} 要求 {expected:g} 拍"
                    + _voice_detail(measure, divisions, expected),
                    measure=number,
                    hint=("可能漏認了一個音，或把音符時值認短了。"
                          if short else
                          "可能多認了一個音，或把音符時值認長了。"),
                ))

    stats = {
        "measures": measure_count,
        "notes": notes,
        "empty_measures": empty,
        "bad_measures": bad_measures,
        "pitch_range": [min(pitches), max(pitches)] if pitches else None,
        "time": f"{beats:g}/{beat_type:g}",
        # 這一頁結束時的拍號，給下一頁當 initial 用
        "next_time": (int(beats) if float(beats).is_integer() else beats,
                      int(beat_type) if float(beat_type).is_integer() else beat_type),
    }

    if measure_count == 0:
        problems.append(Problem("NO_MEASURES", "error", "整份樂譜沒有任何小節",
                                hint="這一段的辨識完全失敗，請重拍或改用文字記譜輸入。"))
    elif notes < th["min_notes"]:
        problems.append(Problem(
            "TOO_FEW_NOTES", "error",
            f"整份只認出 {notes} 個音符，幾乎等於沒認到",
            hint="檢查照片是不是拍到空白頁、或譜被嚴重遮擋。",
        ))

    if measure_count and empty / measure_count > th["empty_measure_warn"]:
        problems.append(Problem(
            "MANY_EMPTY", "warn",
            f"有 {empty}/{measure_count} 個小節是空的",
            hint="譜線可能有一部分沒被認出來，建議重拍這一段。",
        ))

    if measure_count and len(bad_measures) / measure_count > th["bad_measure_warn"]:
        problems.append(Problem(
            "MANY_BAD_MEASURES", "warn",
            f"有 {len(bad_measures)}/{measure_count} 個小節的拍數不對，整段可信度低",
            hint="與其一個個修，不如把這一段重拍一次。",
        ))

    return {
        "label": label,
        "confidence": _confidence(stats, problems, th),
        "problems": [p.as_dict() for p in problems],
        "stats": stats,
    }


def _confidence(stats, problems, th):
    """0–1 的信心值。不是機率，是給使用者排序「先處理哪一頁」用的指標。"""
    measures = stats.get("measures", 0)
    if not measures or stats.get("notes", 0) < th["min_notes"]:
        return 0.0

    score = 1.0
    score -= 0.7 * (len(stats.get("bad_measures", [])) / measures)
    score -= 0.4 * (stats.get("empty_measures", 0) / measures)
    score -= 0.15 * sum(1 for p in problems if p.code == "PITCH_RANGE")
    return round(max(0.0, min(1.0, score)), 2)


# ---------------------------------------------------------------------------
# 跨段檢查
# ---------------------------------------------------------------------------

def check_sequence(reports):
    """看整份譜的各段之間有沒有兜不起來的地方。

    reports 依頁序排好。回傳整體層級的問題清單。
    """
    problems = []
    usable = [r for r in reports if r.get("stats", {}).get("measures")]
    if len(usable) < 2:
        return [p.as_dict() for p in problems]

    times = defaultdict(list)
    for r in usable:
        times[r["stats"].get("time")].append(r.get("label"))
    if len(times) > 1:
        detail = "、".join(f"{t}（{'/'.join(v)}）" for t, v in times.items())
        problems.append(Problem(
            "TIME_MISMATCH", "warn",
            f"各段的拍號不一致：{detail}",
            hint="同一首曲子中途變拍是可能的，但更常見的原因是某一段的拍號認錯了。",
        ))

    counts = sorted(r["stats"]["measures"] for r in usable)
    median = counts[len(counts) // 2]
    # 最後一段不檢查：曲子的最後一頁本來就常常只有一兩行，
    # 那是正常的收尾，不是漏認。（跟小節拍數檢查豁免最後一小節同樣的道理）
    for r in usable[:-1]:
        n = r["stats"]["measures"]
        if median and n < median * THRESHOLDS["short_page_ratio"]:
            problems.append(Problem(
                "SHORT_PAGE", "warn",
                f"{r.get('label')} 只有 {n} 個小節，其他段平均 {median} 個",
                hint="這一段可能有一部分沒被認出來，翻回原譜對一下。",
            ))

    return [p.as_dict() for p in problems]


def format_report(reports, sequence_problems=(), show_ok=True):
    """把檢查結果排成終端機看得懂的樣子。"""
    lines = []
    for r in reports:
        confidence = r.get("confidence", 0.0)
        problems = r.get("problems", [])
        mark = "✓" if confidence >= 0.85 and not problems else ("!" if confidence >= 0.5 else "X")
        if not problems and not show_ok:
            continue
        stats = r.get("stats", {})
        lines.append(
            f"  [{mark}] {r.get('label')}　信心 {confidence:.2f}"
            f"　{stats.get('measures', 0)} 小節 / {stats.get('notes', 0)} 音符"
        )
        for p in problems[:6]:
            where = f"第 {p['measure']} 小節：" if p.get("measure") else ""
            lines.append(f"        - {where}{p['message']}")
            if p.get("hint"):
                lines.append(f"          → {p['hint']}")
        if len(problems) > 6:
            lines.append(f"        - ...另外還有 {len(problems) - 6} 個問題")

    for p in sequence_problems:
        lines.append(f"  [!] {p['message']}")
        if p.get("hint"):
            lines.append(f"      → {p['hint']}")

    return "\n".join(lines)
