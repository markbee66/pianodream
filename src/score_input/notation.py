"""數字記譜（簡譜）與字母記譜的文字檔解析。

兩種記譜法只有「音高怎麼寫」不同，時值 / 小節線 / 和弦 / 左右手的文法完全共用，
所以這裡是一套 tokenizer 配兩個 pitch resolver，不是兩份程式。

    數字記譜               字母記譜
    1=C  4/4              KEY=C  4/4
    R: 1 1 5 5 | 6 6 5 -  R: C C G G | A A G -
    L: [1 5] -  [4 1] -   L: [C3 G3] - [F3 C4] -

音高以外的記號（兩種通用）：

    0        休止符
    -        延長一拍
    1.       附點（1.5 拍）；1.. 雙附點
    1_       八分音符（一條底線）；1__ 十六分
    [1 3 5]  和弦，時值寫在 ] 後面：[1 3 5]_
    ~        圜滑線／連音線：接到下一個音（1~ 1）
    |        小節線
    #        這一行剩下的是註解

數字記譜專用：
    1-7      音階級數（1 = 調號指定的主音）
    1'       高八度（可疊：1''）；1, 低八度
    #4 b7 n4 升 / 降 / 還原

字母記譜專用：
    C-B      音名，中央 C 記為 C4
    C#4 Bb3  升降記號（#C4 這種寫法也接受）
    C        沒寫八度就沿用上一個音的八度

解析時**所有錯誤一次收集完再回報**，不是遇到第一個就中斷 —— 使用者才能一次改完。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

LETTERS = ["C", "D", "E", "F", "G", "A", "B"]
# 大調音階從主音起算的半音數，用來把簡譜的級數換成實際音高
MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]
# 調號的升降記號出現順序
SHARP_ORDER = ["F", "C", "G", "D", "A", "E", "B"]
FLAT_ORDER = ["B", "E", "A", "D", "G", "C", "F"]

KEY_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "BB": -2, "EB": -3, "AB": -4, "DB": -5, "GB": -6, "CB": -7,
}

# 中央 C = C4。簡譜沒加八度記號的 1 就落在第 4 八度區。
DEFAULT_OCTAVE = 4
# 每個四分音符切幾份。48 能整除 32 分音符與三連音，之後要擴充不用改。
QUARTER_DIV = 48

JIANPU = "jianpu"
LETTER = "letter"


class NotationSyntaxError(RuntimeError):
    """語法有錯，附上所有錯誤的位置。"""

    def __init__(self, errors):
        self.errors = errors
        super().__init__(format_errors(errors))


@dataclass
class NotationError:
    line: int
    col: int
    message: str
    hint: str = ""
    source: str = ""

    def render(self):
        where = f"第 {self.line} 行" + (f"第 {self.col} 字" if self.col else "")
        out = [f"{where}：{self.message}"]
        if self.source:
            out.append(f"  {self.source}")
            if self.col:
                out.append("  " + " " * (self.col - 1) + "^")
        if self.hint:
            out.append(f"  → {self.hint}")
        return "\n".join(out)


def format_errors(errors):
    return "\n\n".join(e.render() for e in errors)


@dataclass
class Token:
    text: str
    line: int
    col: int


@dataclass
class Event:
    """一個發聲事件：單音、和弦或休止符。時值單位是四分音符。"""

    pitches: list          # [(step, octave, alter)]，空 list = 休止符
    duration: float        # 以四分音符為單位
    onset: float
    measure: int
    hand: str
    tie_next: bool = False
    token: Token = None


@dataclass
class ParsedScore:
    events: list = field(default_factory=list)
    title: str = ""
    key_name: str = "C"
    fifths: int = 0
    time_num: int = 4
    time_den: int = 4
    bpm: float = 100.0
    notation: str = JIANPU
    hands: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def has_left_hand(self):
        return "L" in self.hands


# ---------------------------------------------------------------------------
# 調號
# ---------------------------------------------------------------------------

def key_to_fifths(name):
    """把 'C' / 'bB' / 'Bb' / 'F#' 換成 MusicXML 的 fifths 值。"""
    raw = (name or "C").strip()
    # 簡譜習慣把降記號寫在前面（bB = 降 B），字母譜寫在後面（Bb），兩種都收
    m = re.fullmatch(r"([#b]?)\s*([A-Ga-g])\s*([#b]?)", raw)
    if not m:
        return None, None
    pre, letter, post = m.group(1), m.group(2).upper(), m.group(3)
    accidental = (pre or post or "").lower().replace("#", "#")
    canonical = letter + ("#" if (pre or post) == "#" else "B" if accidental == "b" else "")
    fifths = KEY_FIFTHS.get(canonical.upper())
    if fifths is None:
        return None, None
    display = letter + ((pre or post) if (pre or post) else "")
    return fifths, display


def key_signature_alter(letter, fifths):
    """這個音名在該調號下本來就帶的升降。"""
    if fifths > 0:
        return 1 if letter in SHARP_ORDER[:fifths] else 0
    if fifths < 0:
        return -1 if letter in FLAT_ORDER[: -fifths] else 0
    return 0


def midi_of(step, octave, alter):
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[step]
    return 12 * (octave + 1) + base + (alter or 0)


# ---------------------------------------------------------------------------
# 記譜法自動判斷
# ---------------------------------------------------------------------------

def detect_notation(text):
    """看內容決定是數字記譜還是字母記譜。

    檔頭寫 NOTATION=jianpu|letter 就直接聽它的；否則數人頭 ——
    音高位置出現的是數字還是 A-G 字母。
    """
    m = re.search(r"^\s*NOTATION\s*=\s*(\w+)", text, re.MULTILINE | re.IGNORECASE)
    if m:
        value = m.group(1).lower()
        if value in {JIANPU, "number", "數字", "簡譜"}:
            return JIANPU
        if value in {LETTER, "abc", "字母"}:
            return LETTER

    body = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0] if not re.match(r"\s*#[A-Ga-g0-7]", raw) else raw
        m = re.match(r"\s*([RLrl])\s*[:：]\s*(.*)", line)
        if m:
            body.append(m.group(2))
    joined = " ".join(body)
    # 字母譜的 C4 裡那個 4 是八度、不是音高，所以只數「前面沒有音名」的數字。
    # 不這樣分的話 `C4 C4 G4 G4` 會被數成 4 個簡譜音高而判錯。
    digits = len(re.findall(r"(?<![A-Ga-g#bn])[1-7]", joined))
    letters = len(re.findall(r"(?<![A-Za-z])[A-Ga-g](?![A-Za-z])", joined))
    return LETTER if letters > digits else JIANPU


# ---------------------------------------------------------------------------
# 時值
# ---------------------------------------------------------------------------

_SUFFIX_RE = re.compile(r"^(_*)(\.*)(~?)$")


def parse_duration_suffix(suffix, token, errors):
    """把 `_` 與 `.` 換成拍數倍率。回傳 (倍率, 是否接下一個音)。

    `_` 一條底線減半（八分）、兩條再減半（十六分）；`.` 附點加一半。
    順序必須是先底線後附點 —— 反過來寫在手寫簡譜裡也不存在。
    """
    m = _SUFFIX_RE.match(suffix)
    if not m:
        errors.append(
            NotationError(
                token.line, token.col, f"看不懂的時值記號 `{suffix}`",
                "時值記號的順序是：先底線 `_`（縮短）再附點 `.`（加長），最後才是連音線 `~`。"
                "例如 `1_.` 是附點八分音符。",
            )
        )
        return 1.0, False
    unders, dots, tie = m.group(1), m.group(2), m.group(3)
    ratio = 0.5 ** len(unders)
    if dots:
        # 附點加一半、雙附點再加四分之一，等比級數
        ratio *= 2 - 0.5 ** len(dots)
    return ratio, bool(tie)


# ---------------------------------------------------------------------------
# 音高
# ---------------------------------------------------------------------------

_JIANPU_PITCH = re.compile(r"^([#b n]*)([0-7])([',]*)")
_LETTER_PITCH = re.compile(r"^([#bn]*)([A-Ga-g])([#bn]*)(-?\d)?")


class PitchReader:
    """從 token 字串的開頭吃掉一個音高，回傳 (音高, 剩下的字)。

    做成「吃掉開頭」而不是「整串比對」，是為了讓 `[135]` 這種不加空白的和弦
    也能正確拆成 1 / 3 / 5 —— 每個音高 token 本身就是自我界定的。
    """

    def __init__(self, notation, fifths, errors):
        self.notation = notation
        self.fifths = fifths
        self.errors = errors
        self.last_octave = DEFAULT_OCTAVE

    def read(self, text, token, offset=0):
        if self.notation == JIANPU:
            return self._read_jianpu(text, token, offset)
        return self._read_letter(text, token, offset)

    def _accidental_shift(self, marks, token, offset):
        shift, natural = 0, False
        for ch in marks:
            if ch == "#":
                shift += 1
            elif ch == "b":
                shift -= 1
            elif ch == "n":
                natural = True
        return shift, natural

    def _read_jianpu(self, text, token, offset):
        m = _JIANPU_PITCH.match(text)
        if not m:
            return None, text, False
        marks, degree_ch, octave_marks = m.group(1).replace(" ", ""), m.group(2), m.group(3)
        rest_text = text[m.end():]

        if degree_ch == "0":
            return [], rest_text, True

        degree = int(degree_ch)
        letter_index = LETTERS.index(_tonic_letter(self.fifths)) + (degree - 1)
        step = LETTERS[letter_index % 7]
        octave = DEFAULT_OCTAVE + letter_index // 7
        octave += octave_marks.count("'") - octave_marks.count(",")

        shift, natural = self._accidental_shift(marks, token, offset)
        alter = shift if natural else key_signature_alter(step, self.fifths) + shift
        return [(step, octave, alter)], rest_text, True

    def _read_letter(self, text, token, offset):
        if text[:1] == "0" or text[:1] in {"R", "r"}:
            return [], text[1:], True

        m = _LETTER_PITCH.match(text)
        if not m:
            return None, text, False
        pre, letter, post, octave_digit = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        rest_text = text[m.end():]

        octave = int(octave_digit) if octave_digit is not None else self.last_octave
        self.last_octave = octave

        shift, natural = self._accidental_shift(pre + post, token, offset)
        alter = shift if natural else key_signature_alter(letter, self.fifths) + shift
        return [(letter, octave, alter)], rest_text, True


def _tonic_letter(fifths):
    """調號的主音音名。簡譜的 1 就是它。"""
    for name, value in KEY_FIFTHS.items():
        if value == fifths:
            return name[0]
    return "C"


# ---------------------------------------------------------------------------
# 主解析
# ---------------------------------------------------------------------------

def parse_text(text, source_name="記譜檔", notation=None):
    """把記譜文字解析成 ParsedScore。語法有錯就丟 NotationSyntaxError。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    errors = []
    notation = notation or detect_notation(text)

    score = ParsedScore(notation=notation)
    streams = {"R": [], "L": []}  # hand -> [Token]
    seen_time, seen_key = False, False

    for lineno, raw in enumerate(lines, start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue

        m = re.match(r"^\s*([RLrl])\s*[:：]\s*(.*)$", line)
        if m:
            hand = m.group(1).upper()
            body_col = m.start(2) + 1
            tokens = _tokenize(m.group(2), lineno, body_col)
            if tokens:
                # 換行就是小節線 —— 大家寫記譜檔一行就是一組完整小節，
                # 不會有人特地在行末補 `|`。行末本來就有 `|` 時不重複計。
                if tokens[-1].text != "|":
                    tokens.append(Token("|", lineno, body_col + len(m.group(2))))
                streams[hand].extend(tokens)
            continue

        handled, seen_time, seen_key = _read_header(
            line, lineno, raw, score, errors, seen_time, seen_key
        )
        if not handled:
            errors.append(
                NotationError(
                    lineno, 1, f"看不懂這一行：{line.strip()[:40]}",
                    "音符要寫在 `R:`（右手）或 `L:`（左手）後面。"
                    "檔頭可以寫調號（`1=C` 或 `KEY=C`）、拍號（`4/4`）、速度（`BPM=100`）。",
                    raw,
                )
            )

    score.hands = [h for h in ("R", "L") if streams[h]]
    if not score.hands:
        errors.append(
            NotationError(
                1, 0, "整份檔案沒有任何音符",
                "至少要有一行 `R:` 開頭的右手旋律，例如：\n     R: 1 1 5 5 | 6 6 5 -",
            )
        )
        raise NotationSyntaxError(errors)

    reader = PitchReader(notation, score.fifths, errors)
    beat_q = 4.0 / score.time_den          # 一拍等於幾個四分音符
    measure_q = score.time_num * beat_q    # 一小節等於幾個四分音符

    for hand in score.hands:
        reader.last_octave = DEFAULT_OCTAVE
        events = _parse_stream(streams[hand], hand, reader, beat_q, errors, lines)
        # 先驗小節拍數再接連音線 —— 順序反過來的話，跨小節的連音會把後一小節的
        # 長度算到前一小節頭上，變成假的「拍數不對」。
        _check_measures(events, measure_q, score, hand, errors, lines)
        _apply_ties(events)
        score.events.extend(events)

    if errors:
        raise NotationSyntaxError(errors)

    _warn_hand_length(score)
    score.events.sort(key=lambda e: (e.onset, e.hand != "R"))
    return score


def _strip_comment(line):
    """砍掉註解。`#` 也是升記號，所以只有後面不接音高時才算註解起點。"""
    out = []
    for i, ch in enumerate(line):
        if ch == "#" and not re.match(r"#[A-Ga-g0-7]", line[i:]):
            break
        out.append(ch)
    return "".join(out)


def _read_header(line, lineno, raw, score, errors, seen_time, seen_key):
    body = line.strip()

    m = re.fullmatch(r"(?:TITLE|標題)\s*[=:：]\s*(.+)", body, re.IGNORECASE)
    if m:
        score.title = m.group(1).strip()
        return True, seen_time, seen_key

    m = re.fullmatch(r"NOTATION\s*=\s*\w+", body, re.IGNORECASE)
    if m:
        return True, seen_time, seen_key

    m = re.fullmatch(r"(?:BPM|速度)\s*[=:：]\s*(\d+(?:\.\d+)?)", body, re.IGNORECASE)
    if m:
        bpm = float(m.group(1))
        if not 20 <= bpm <= 300:
            errors.append(
                NotationError(lineno, 1, f"速度 {bpm:g} BPM 超出合理範圍",
                              "請填 20–300 之間的數字。", raw)
            )
        else:
            score.bpm = bpm
        return True, seen_time, seen_key

    # 調號：簡譜寫 1=C，字母譜寫 KEY=C
    m = re.fullmatch(r"(?:1\s*=|KEY\s*[=:：]|調\s*[=:：])\s*([#b]?[A-Ga-g][#b]?)", body, re.IGNORECASE)
    if m:
        fifths, display = key_to_fifths(m.group(1))
        if fifths is None:
            errors.append(
                NotationError(lineno, 1, f"不認得的調號 `{m.group(1)}`",
                              "可用 C G D A E B F# C# F bB bE bA bD bG bC（降記號寫前後都可以）。", raw)
            )
        elif seen_key:
            errors.append(
                NotationError(lineno, 1, "調號重複指定了",
                              "一份檔案只能有一個調號。中途轉調目前不支援，請拆成兩個檔案。", raw)
            )
        else:
            score.fifths, score.key_name = fifths, display
            seen_key = True
        return True, seen_time, seen_key

    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", body)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den not in {1, 2, 4, 8, 16} or not 1 <= num <= 32:
            errors.append(
                NotationError(lineno, 1, f"拍號 {num}/{den} 不合理",
                              "分母要是 1 2 4 8 16 其中之一，分子 1–32。常見的是 4/4、3/4、6/8。", raw)
            )
        elif seen_time:
            errors.append(
                NotationError(lineno, 1, "拍號重複指定了",
                              "一份檔案只能有一個拍號。中途變拍請拆成兩個檔案。", raw)
            )
        else:
            score.time_num, score.time_den = num, den
            seen_time = True
        return True, seen_time, seen_key

    return False, seen_time, seen_key


def _tokenize(body, lineno, base_col):
    """切出 token 並記住每個 token 在原始檔的行列位置，錯誤訊息才指得準。"""
    tokens = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "|":
            tokens.append(Token("|", lineno, base_col + i))
            i += 1
            continue
        if ch == "[":
            end = body.find("]", i)
            if end == -1:
                tokens.append(Token(body[i:], lineno, base_col + i))
                break
            j = end + 1
            while j < n and body[j] in "_.~":
                j += 1
            tokens.append(Token(body[i:j], lineno, base_col + i))
            i = j
            continue
        j = i
        while j < n and not body[j].isspace() and body[j] not in "|[":
            j += 1
        tokens.append(Token(body[i:j], lineno, base_col + i))
        i = j
    return tokens


def _parse_stream(tokens, hand, reader, beat_q, errors, lines):
    events = []
    onset = 0.0
    measure = 1

    for token in tokens:
        text = token.text
        src = lines[token.line - 1] if token.line - 1 < len(lines) else ""

        if text == "|":
            measure += 1
            continue

        if set(text) == {"-"}:
            # 延長：把長度加到前一個發聲事件上
            if not events:
                errors.append(
                    NotationError(token.line, token.col, "`-` 前面沒有音符可以延長",
                                  "`-` 是把前一個音延長一拍，所以不能放在小節或整行的最前面。", src)
                )
                continue
            events[-1].duration += len(text) * beat_q
            onset += len(text) * beat_q
            continue

        if text.startswith("["):
            event = _parse_chord(text, token, reader, beat_q, errors, src)
        else:
            event = _parse_single(text, token, reader, beat_q, errors, src)

        if event is None:
            continue
        event.onset = onset
        event.measure = measure
        event.hand = hand
        onset += event.duration
        events.append(event)

    return events


def _parse_single(text, token, reader, beat_q, errors, src):
    pitches, rest, ok = reader.read(text, token, 0)
    if not ok:
        errors.append(_unknown_pitch_error(text, token, reader.notation, src))
        return None
    ratio, tie = parse_duration_suffix(rest, token, errors)
    return Event(pitches=pitches, duration=beat_q * ratio, onset=0.0,
                 measure=0, hand="", tie_next=tie, token=token)


def _parse_chord(text, token, reader, beat_q, errors, src):
    end = text.find("]")
    if end == -1:
        errors.append(
            NotationError(token.line, token.col, "和弦的方括號沒有關起來",
                          "每個 `[` 都要有對應的 `]`，例如 `[1 3 5]`。", src)
        )
        return None
    inner, suffix = text[1:end], text[end + 1:]
    pitches = []
    body = inner.strip()
    while body:
        if body[0].isspace() or body[0] == ",":
            body = body[1:]
            continue
        got, body, ok = reader.read(body, token, 0)
        if not ok:
            errors.append(_unknown_pitch_error(body, token, reader.notation, src))
            return None
        pitches.extend(got)
    if not pitches:
        errors.append(
            NotationError(token.line, token.col, "和弦括號裡面是空的",
                          "和弦要寫成 `[1 3 5]`（簡譜）或 `[C3 G3]`（字母譜）。", src)
        )
        return None
    ratio, tie = parse_duration_suffix(suffix, token, errors)
    return Event(pitches=pitches, duration=beat_q * ratio, onset=0.0,
                 measure=0, hand="", tie_next=tie, token=token)


def _unknown_pitch_error(text, token, notation, src):
    bad = text[:1] or "?"
    if notation == JIANPU:
        hint = ("數字記譜的音高只有 1-7，休止符用 0。"
                "高八度寫 `1'`、低八度寫 `1,`、升降寫 `#4` `b7`。")
    else:
        hint = ("字母記譜的音名只有 A-G，休止符用 0。"
                "八度寫在後面（`C4`），升降寫成 `C#4` 或 `Bb3`。")
    return NotationError(token.line, token.col, f"`{bad}` 不是合法的音高", hint, src)


def _apply_ties(events):
    """把連音線接起來 —— 併成一個長音。

    下游（對齊、評分、音遊譜面）看的是實際發聲，兩個綁在一起的音跟一個長音
    在聽感與判定上完全相同，所以直接併長度最單純，也不必處理 MusicXML 的
    tie 元素配對。
    """
    i = 0
    while i < len(events) - 1:
        cur, nxt = events[i], events[i + 1]
        if cur.tie_next and _same_pitches(cur, nxt):
            cur.duration += nxt.duration
            cur.tie_next = nxt.tie_next
            del events[i + 1]
            continue
        cur.tie_next = False
        i += 1
    if events:
        events[-1].tie_next = False


def _same_pitches(a, b):
    return sorted(a.pitches) == sorted(b.pitches)


def _check_measures(events, measure_q, score, hand, errors, lines):
    """檢查每個小節的拍數對不對。這是文字記譜最容易出錯的地方。"""
    by_measure = {}
    for ev in events:
        by_measure.setdefault(ev.measure, []).append(ev)

    beat_q = 4.0 / score.time_den
    last = max(by_measure) if by_measure else 0
    label = "右手" if hand == "R" else "左手"

    for number in sorted(by_measure):
        total = sum(e.duration for e in by_measure[number])
        if abs(total - measure_q) < 1e-6:
            continue
        # 最後一小節允許不滿（弱起或收尾），只有超過才算錯
        if number == last and total < measure_q:
            score.warnings.append(
                f"{label}最後一小節只有 {total / beat_q:g} 拍（拍號 {score.time_num}/{score.time_den}），"
                f"當成收尾處理"
            )
            continue
        token = by_measure[number][0].token
        src = lines[token.line - 1] if token and token.line - 1 < len(lines) else ""
        errors.append(
            NotationError(
                token.line if token else 0, 0,
                f"{label}第 {number} 小節有 {total / beat_q:g} 拍，但拍號是 "
                f"{score.time_num}/{score.time_den}（應該是 {score.time_num} 拍）",
                "檢查是不是多打或少打了 `-`，或是底線 `_` 的數量不對。",
                src,
            )
        )


def _warn_hand_length(score):
    if not score.has_left_hand:
        return
    lengths = {}
    for hand in ("R", "L"):
        evs = [e for e in score.events if e.hand == hand]
        lengths[hand] = max((e.onset + e.duration for e in evs), default=0.0)
    if abs(lengths["R"] - lengths["L"]) > 1e-6:
        score.warnings.append(
            f"左右手長度不一樣（右手 {lengths['R']:g} 拍、左手 {lengths['L']:g} 拍），"
            f"短的那手後面會是空白"
        )


# ---------------------------------------------------------------------------
# 輸出 MusicXML
# ---------------------------------------------------------------------------

def to_musicxml(score, out_path, part_name="Piano"):
    """把 ParsedScore 寫成 MusicXML。

    用 partitura 建 Part 再存檔，而不是自己拼 XML —— 這樣產出的檔案保證能被
    同一套 partitura 讀回來，也就保證能餵進現有的評分 pipeline。
    """
    import partitura as pt
    from partitura.score import KeySignature, Note, Part, Rest, TimeSignature, add_measures

    part = Part("P1", part_name, quarter_duration=QUARTER_DIV)
    part.add(TimeSignature(score.time_num, score.time_den), 0)
    part.add(KeySignature(score.fifths, "major"), 0)

    for ev in score.events:
        staff = 1 if ev.hand == "R" else 2
        voice = 1 if ev.hand == "R" else 5
        start = int(round(ev.onset * QUARTER_DIV))
        end = start + max(1, int(round(ev.duration * QUARTER_DIV)))
        if not ev.pitches:
            part.add(Rest(voice=voice, staff=staff), start, end)
            continue
        for step, octave, alter in ev.pitches:
            part.add(
                Note(step=step, octave=octave, alter=alter or None, voice=voice, staff=staff),
                start, end,
            )

    add_measures(part)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pt.save_musicxml(part, str(out_path))
    return out_path


def parse_file(path, notation=None):
    """讀檔並解析。檔案編碼一律當 UTF-8，讀不動就退回系統預設。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp950", errors="replace")
    return parse_text(text, source_name=path.name, notation=notation)


def summarize(score):
    """給 CLI / 網頁顯示的統計。"""
    import math

    notes = [e for e in score.events if e.pitches]
    pitches = [midi_of(*p) for e in notes for p in e.pitches]
    # 用總長度換算小節數，不用 event.measure —— 跨小節的連音線併音之後，
    # 被吃掉那一小節的編號就不會出現在任何 event 上。
    total_q = max((e.onset + e.duration for e in score.events), default=0.0)
    measure_q = score.time_num * (4.0 / score.time_den)
    return {
        "notation": score.notation,
        "title": score.title,
        "key": score.key_name,
        "time": f"{score.time_num}/{score.time_den}",
        "bpm": score.bpm,
        "hands": score.hands,
        "measures": math.ceil(total_q / measure_q - 1e-9) if measure_q else 0,
        "notes": len(pitches),
        "pitch_range": (min(pitches), max(pitches)) if pitches else None,
        "warnings": score.warnings,
    }
