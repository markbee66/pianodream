"""找出每個小節在樂譜照片上的位置。

檢討功能要「把彈不好的那幾小節在譜上圈紅」，就需要知道第 N 小節畫在照片的哪裡。
辨識出來的 MusicXML 完全沒有座標資訊，所以只能回頭從影像本身找。

作法：

    二值化 -> 估行距 -> 轉正 -> 找五線譜 -> 把譜表併成「系統」（鋼琴譜一個系統兩行）
    -> 讀出系統左端印的小節號 -> 在系統內找小節線 -> 兩條線之間就是一個小節

## 三層，一層比一層可靠

    A  譜上印的小節號   OCR 每個系統左端的數字，直接知道這一行從第幾小節開始
    B  小節線           在系統內切分，大譜表很準
    C  等分             A 說有 n 個小節但 B 只找到別的數量時，把系統寬度均分成 n 份

**A 是骨架，B 只負責細切。** 幾何為什麼分不開符桿與單行譜的小節線，
見 `staves.py` 的模組說明。

座標一律換算回**原圖**，因為要畫在使用者拍的那張照片上，不是轉正後的版本。

這個檔案只管「切小節、編號碼」。相關但獨立的三塊各自成檔，名字在這裡原樣再匯出
讓既有的呼叫端不用改：

    staves.py    譜線 / 譜表 / 系統 / 小節線的幾何
    ottava.py    8va / 8vb 虛線偵測（跟切小節共用譜線分群，但目的是改音高）
    annotate.py  把小節框畫到照片上
"""

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .annotate import _draw_labels, highlight  # noqa: F401
from .imaging import (_binarize, _imread, _rotate_gray, deskew,
                      estimate_interline, measure_skew)
from .ottava import (OTTAVA_MAX_DISTANCE, detect_ottavas,  # noqa: F401
                     find_ottava_lines, ottava_spans)
from .staves import (BARLINE_HEIGHT_RATIO, BARLINE_MERGE_INTERLINE,  # noqa: F401
                     BARLINE_MIN_INTERLINE, SYSTEM_MIN_RATIO, _group_staves,
                     _group_systems, _merge_close, _staff_line_rows,
                     _systems_from_barlines, find_barlines, system_frames)

# 一個小節至少要這麼寬，否則多半是雙線被拆開
MIN_MEASURE_INTERLINE = 2.5

# 行距小於這個就先放大再處理。〈うまぴょい伝説〉第 4 頁只有 771x494、行距 6，
# 譜線偵測會漏掉線，四行譜表被併成一個。放大之後偵測、OCR 都跟著變準。
MIN_WORK_INTERLINE = 9.0
WORK_INTERLINE = 12.0

# 小節號印在系統左端上方。這兩個值圈出「哪裡算左端上方」
NUMBER_MAX_X = 0.28         # 佔頁寬的比例
NUMBER_MAX_ABOVE = 3.5      # 在譜表頂線上方幾個行距以內
MAX_PER_SYSTEM = 24         # 一行最多塞幾個小節，超過就是號碼讀錯了
_PURE_NUMBER = re.compile(r"^\d{1,3}$")


@dataclass
class MeasureBox:
    """一個小節在原圖上的位置。四個角是因為可能有傾斜，不是正矩形。"""

    index: int          # 這一頁裡的序號，從 1 開始
    system: int         # 第幾個系統（一行）
    corners: list       # [(x, y) x4]，原圖座標，順時針
    x0: float = 0.0     # 轉正座標系裡的範圍，排序與偵錯用
    x1: float = 0.0
    y0: float = 0.0
    y1: float = 0.0
    number: int = 0     # 譜上印的小節號推出來的絕對編號；0 = 不知道
    exact: bool = True  # False 表示這一格是等分內插出來的，不是真的找到小節線

    def as_dict(self):
        return {
            "index": self.index,
            "system": self.system,
            "number": self.number,
            "exact": self.exact,
            "corners": [[round(x, 1), round(y, 1)] for x, y in self.corners],
        }


@dataclass
class PageLayout:
    width: int = 0
    height: int = 0
    skew_deg: float = 0.0
    interline: float = 0.0
    systems: int = 0
    measures: list = field(default_factory=list)
    #: 每個系統左端印的小節號（讀不到就是 None），index 對應 system - 1
    system_numbers: list = field(default_factory=list)

    @property
    def first_measure(self):
        """這一頁從第幾小節開始（照譜上印的）。讀不到回 0。"""
        for n in self.system_numbers:
            if n:
                return n
        return 0

    def as_dict(self):
        return {
            "size": [self.width, self.height],
            "skew_deg": round(self.skew_deg, 2),
            "interline": round(self.interline, 2),
            "systems": self.systems,
            "system_numbers": list(self.system_numbers),
            "first_measure": self.first_measure,
            "measures": [m.as_dict() for m in self.measures],
        }


# ---------------------------------------------------------------------------
# 譜上印的小節號
# ---------------------------------------------------------------------------

def read_system_numbers(gray, frames, interline):
    """讀每個系統左端印的小節號，回傳跟 frames 等長的 list（讀不到就是 None）。

    排版慣例是把小節號印在每一行的左上角。這是**譜自己標的答案**，
    比我們從像素推的任何東西都可靠，而且跟辨識流程完全獨立。

    兩道過濾：位置（要在系統左端上方）與**單調遞增**。
    指法數字也是小數字、也印在附近，靠遞增這條就能濾掉
    —— 實測〈うまぴょい伝説〉第 2 頁混進了 "1" 和 "2"。
    """
    if not frames:
        return []
    try:
        from rapidocr import RapidOCR
    except ImportError:
        return [None] * len(frames)

    try:
        result = _ocr_engine(RapidOCR)(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
    except Exception:       # noqa: BLE001 - 讀不到就退到別層，不該擋住整頁
        return [None] * len(frames)
    if not getattr(result, "txts", None):
        return [None] * len(frames)

    width = gray.shape[1]
    found = [[] for _ in frames]
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        text = (text or "").strip()
        if not _PURE_NUMBER.match(text) or float(score) < 0.5:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        if (min(xs) + max(xs)) / 2.0 / width > NUMBER_MAX_X:
            continue
        bottom = max(ys)
        for i, frame in enumerate(frames):
            above = frame["top"] - bottom
            if -interline * 0.8 <= above <= interline * NUMBER_MAX_ABOVE:
                found[i].append((int(text), float(score)))
                break

    # 一個系統可能讀到好幾個候選，取信心最高的
    numbers = [max(c, key=lambda t: t[1])[0] if c else None for c in found]
    return _plausible(_keep_increasing(numbers))


def _plausible(numbers):
    """再擋一層明顯不合理的讀數。

    〈士兵進行曲〉那一頁沒有印小節號，OCR 卻從指法與力度記號拼出 "531"。
    只讀到孤零零一個數字時完全沒辦法交叉檢查，所以**整層直接放棄**，
    退回用小節線 —— 錯的錨點比沒有錨點糟得多。
    """
    known = [n for n in numbers if n]
    # 一兩行的頁面（多半是最後一頁）沒有第二個號碼可以交叉檢查，
    # 但它的錯誤空間也小 —— 前後兩頁的邊界會把它夾住。三行以上就嚴格要求。
    if len(known) < (1 if len(numbers) <= 2 else 2):
        return [None] * len(numbers)

    # 一行塞超過 MAX_PER_SYSTEM 個小節不合常理，多半是讀錯
    kept = list(numbers)
    positions = [i for i, n in enumerate(numbers) if n]
    for a, b in zip(positions, positions[1:]):
        span = numbers[b] - numbers[a]
        if span > MAX_PER_SYSTEM * (b - a):
            kept[b] = None
    return kept if sum(1 for n in kept if n) >= 2 else [None] * len(numbers)


def _keep_increasing(numbers):
    """只留下由上往下**嚴格遞增**的那些，其餘視為誤讀。

    用最長遞增子序列，不是「碰到不遞增就砍」—— 後者只要第一個讀錯，
    後面全部跟著陪葬。
    """
    known = [(i, n) for i, n in enumerate(numbers) if n]
    if len(known) <= 1:
        return list(numbers)

    best = [1] * len(known)
    prev = [-1] * len(known)
    for i in range(len(known)):
        for j in range(i):
            if known[j][1] < known[i][1] and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    end = int(np.argmax(best))
    keep = set()
    while end >= 0:
        keep.add(known[end][0])
        end = prev[end]
    return [n if i in keep else None for i, n in enumerate(numbers)]


_OCR_CACHE = {}


def _ocr_engine(factory):
    if "engine" not in _OCR_CACHE:
        _OCR_CACHE["engine"] = factory()
    return _OCR_CACHE["engine"]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _content_span(ink, top, bottom):
    """這個系統左右兩端的實際內容範圍，用來當第一小節的左界與最後的右界。"""
    band = ink[int(top): int(bottom) + 1]
    cols = np.flatnonzero(band.any(axis=0))
    if cols.size == 0:
        return None
    return float(cols[0]), float(cols[-1])


def detect(image, min_measure_width=None, read_numbers=True,
           next_first_measure=None, first_measure=None):
    """分析一張樂譜照片，回傳 PageLayout。

    兩個「邊界」參數，都是因為印刷慣例會在頭尾各少一個號碼：

    first_measure     這一頁從第幾小節開始。**第一個系統通常不印號碼**
                      （沒有人會在第一行左邊寫 "1"），所以要從外面告訴它。
    next_first_measure 下一頁從第幾小節開始。每頁最後一個系統沒有「下一個號碼」
                      可以相減，算不出該有幾個小節。最後一頁就傳「總小節數 + 1」。

    呼叫端（`pagemap.detect_layout`）會先掃一遍拿到各頁的起始號碼，再回頭補。
    """
    img = _imread(image)
    original = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = original.shape

    interline, _ = estimate_interline(_binarize(original))
    layout = PageLayout(width=w, height=h, interline=interline)
    if interline <= 0:
        return layout

    # 太小的圖先放大再處理：譜線偵測與 OCR 都吃解析度，
    # 〈うまぴょい伝説〉第 4 頁（771x494、行距 6）不放大的話四行譜表只認到一行
    scale = 1.0
    gray = original
    if interline < MIN_WORK_INTERLINE:
        scale = WORK_INTERLINE / interline
        gray = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        interline, _ = estimate_interline(_binarize(gray))
        if interline <= 0:
            return layout

    ink = _binarize(gray)
    layout.skew_deg = measure_skew(ink)
    straight = deskew(ink, layout.skew_deg)
    straight_gray = _rotate_gray(gray, layout.skew_deg)

    # 兩條路各算一次，再決定用哪一條。
    #
    # 譜線骨架 + 印出來的號碼在單行譜上遠勝舊路徑（〈うまぴょい伝説〉27 → 180），
    # 但它依賴譜線偵測；有些頁面（〈士兵進行曲〉）譜線只認到一半，那時舊的
    # 「純靠小節線」反而準。**沒有一條路在所有頁面上都贏**，所以看號碼夠不夠
    # 可信來決定：號碼可信就走新路（有錨點可以校正），否則原封不動走舊路。
    frames = system_frames(straight, interline)
    numbers = (read_system_numbers(straight_gray, frames, interline)
               if (read_numbers and frames) else [None] * len(frames))
    # 第一個系統照慣例不印號碼，但呼叫端知道這一頁從哪開始
    if numbers and not numbers[0] and first_measure:
        if all(n is None or n > first_measure for n in numbers):
            numbers[0] = first_measure

    if not _numbers_trustworthy(numbers):
        frames = [{"top": s["top"], "bottom": s["bottom"], "bars": list(s["bars"])}
                  for s in _systems_from_barlines(find_barlines(straight, interline),
                                                  interline)]
        numbers = [None] * len(frames)

    layout.systems = len(frames)
    if not frames:
        return layout
    layout.system_numbers = list(numbers)

    # 轉正 -> 原圖的反向旋轉矩陣。座標先除回原尺度，再轉回原圖
    back = cv2.getRotationMatrix2D((w / 2, h / 2), -layout.skew_deg, 1.0)
    min_width = min_measure_width or interline * MIN_MEASURE_INTERLINE
    tolerance = max(2.0, interline * BARLINE_MERGE_INTERLINE)

    # 印出來的號碼是**錨點**，不是每一行都有。中間沒印的用累加補 ——
    # 這樣「上一行有幾個小節」的資訊就能一路傳下去，最後一行也才算得出來。
    running = numbers[0] if numbers and numbers[0] else None

    index = 0
    for system_no, frame in enumerate(frames, start=1):
        pad = interline * 1.2          # 上下各留一點，圈起來才不會壓到音符
        y0 = max(0.0, frame["top"] - pad)
        y1 = min(float(straight.shape[0] - 1), frame["bottom"] + pad)

        span = _content_span(straight, y0, y1)
        if span is None:
            continue
        left, right = span

        # 第一小節的左界不是小節線而是譜表開頭（那裡是譜號與調號），
        # 所以左右兩端要自己補上去
        bars = [b for b in frame["bars"] if left < b < right]
        edges = _merge_close(sorted([left] + bars + [right]), tolerance)
        cells = [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= min_width]

        position = system_no - 1
        start_number = numbers[position] if numbers[position] else running
        expected = _expected_measures(numbers, position, start_number,
                                      next_first_measure)
        exact = True
        if expected and len(cells) != expected:
            # 小節線沒切出應有的數量 —— 相信譜上印的號碼，把這一行等分
            cells = [(left + (right - left) * i / expected,
                      left + (right - left) * (i + 1) / expected)
                     for i in range(expected)]
            exact = False

        if start_number:
            running = start_number + len(cells)

        for offset, (a, b) in enumerate(cells):
            index += 1
            layout.measures.append(MeasureBox(
                index=index,
                system=system_no,
                number=(start_number + offset) if start_number else 0,
                exact=exact,
                corners=_to_original(back, a / scale, y0 / scale, b / scale, y1 / scale),
                x0=a, x1=b, y0=y0, y1=y1,
            ))

    _backfill_leading(layout)
    return layout


def _backfill_leading(layout):
    """把**開頭那幾行**沒有號碼的小節倒推回去。

    一頁的第一行常常不印號碼（號碼通常從第二行開始印），所以 `numbers[0]` 是
    None、`running` 還沒起頭，那一行的每一格就只能填 0。原本的做法是讓上層
    「照順序往下數」補號 —— 而那會**撞號**：

        蕭邦 冬風練習曲 第 3 頁  system_numbers = [None, 65, None, 73, ...]
        第 1 行沒號碼 -> 上層從前一頁的 64 往下數，補成 65 66 67 68
        第 2 行印著 65 -> 也是 65 66 67 68        ← 同一個小節號指到兩個地方

    號碼是譜上印的，是**錨點**；沒號碼的那幾行要往回數到錨點為止，不是往前蓋過去。
    倒推之後第 1 行變成 61–64，`first_measure` 也跟著變成 61，
    上一頁最後一行的「下一頁從第幾小節開始」才會對，不會被拉長 4 個小節。
    """
    if not layout.measures:
        return
    first_known = next((i for i, m in enumerate(layout.measures) if m.number), None)
    if first_known is None or first_known == 0:
        return

    start = layout.measures[first_known].number - first_known
    if start < 1:
        return                      # 倒推會變成 0 或負數 —— 寧可留白也不要編錯

    for offset in range(first_known):
        layout.measures[offset].number = start + offset

    # system_numbers 是 first_measure 的來源，也要一起補，不然這一頁的起點
    # 還是回報成第二行的號碼。
    seen = 0
    for position in range(len(layout.system_numbers)):
        if seen >= first_known:
            break
        boxes = [m for m in layout.measures if m.system == position + 1]
        if boxes and not layout.system_numbers[position]:
            layout.system_numbers[position] = boxes[0].number
        seen += len(boxes)


def _numbers_trustworthy(numbers):
    """讀到的小節號夠不夠格當錨點。

    要求**至少 3 個**、而且要蓋過**一半以上**的系統。門檻訂得嚴是有原因的：
    只讀到零星幾個號碼時，用它去反推「這一行該有幾個小節」等於拿雜訊當真理。
    〈Alkan 前奏曲〉整頁只讀到 2 個（其中還有誤讀），照著切會從 11 個小節
    變成 13 個 —— 比原本的做法更差。寧可退回舊路徑。
    """
    if not numbers:
        return False
    known = [n for n in numbers if n]
    if not known:
        return False
    # 一兩行的頁面例外：只要讀到一個號碼就夠，前後頁的邊界會把它夾住
    if len(numbers) <= 2:
        return True
    return len(known) >= 3 and len(known) * 2 >= len(numbers)


def _expected_measures(numbers, position, start_number, next_first_measure=None):
    """從印出來的小節號推「這一行應該有幾個小節」。推不出來回 None。

    用下一個有讀到號碼的系統減掉這一行的起點，中間隔了幾個系統就平均分配 ——
    中間那幾行沒讀到號碼時，至少總數還是對的。
    **最後一個系統**沒有「下一個」可以減，改用下一頁的起始號碼
    （最後一頁就是總小節數 + 1）。
    """
    if not start_number:
        return None
    for ahead in range(position + 1, len(numbers)):
        if numbers[ahead]:
            span = numbers[ahead] - start_number
            gap = ahead - position
            if span <= 0 or span < gap:
                return None
            return max(1, round(span / gap))

    # 只有最後一行能用下一頁的號碼 —— 中間那幾行用了會把整頁的小節都塞進同一行
    if (position == len(numbers) - 1 and next_first_measure
            and next_first_measure > start_number):
        span = next_first_measure - start_number
        if span <= MAX_PER_SYSTEM:
            return span
    return None


def _to_original(back, x0, y0, x1, y1):
    """把轉正座標系的矩形四角換回原圖座標（因此可能是斜的四邊形）。"""
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], dtype=np.float64).T
    xs, ys = back @ pts
    return [(float(x), float(y)) for x, y in zip(xs, ys)]
