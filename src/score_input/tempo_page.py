"""從**照片**讀速度標記：節拍器記號、漸快／漸慢術語。

從 `tempo.py` 拆出來的。**homr 完全不輸出速度資訊** —— 產出的 MusicXML 裡沒有
`<sound tempo>`、沒有 `<metronome>`、連 `<words>` 都沒有（實測 12 首都是 0）。
速度其實印在譜上，只是以「圖上的文字」存在，所以只能用 OCR 去讀。

這一層回傳的小節是**頁內第幾格**，不是譜上印的號碼 —— 理由見 `_measure_below()`。
換算成全曲小節是 `pagemap.detect_tempo_map()` 的事。
"""

import re
from pathlib import Path

import numpy as np

from .tempo_text import TempoResult, from_text

# 漸變速度術語 -> 往哪個方向變。homr 不輸出 <words>，實測 12 首的 MusicXML 裡
# words / sound / metronome 全部是 0，所以只能從圖上的文字讀。
#
# 這些字**不是**「這裡的速度是多少」，而是「從這裡開始往某個方向變」，
# 所以跟節拍器記號分開處理：記號是階梯，這些是斜坡。
# 一律用詞界錨定：這些正則要掃過**整頁**的 OCR 文字，不錨的話
# 「Spirito」裡的 rit、「dolce」旁邊的字都會被當成變速記號。
_GRADUAL_WORDS = [
    ("faster", re.compile(r"\b(accel\w*|string\w*|stretto|pi[uù]\s*mosso)\b", re.I)),
    # `rit` 不寫成 `rit\.`：句點後面沒有詞界，尾端的 \b 會讓整條失效。
    # 靠詞界本身區分 —— 「rit.」的 t 後面是邊界，「ritmo」的不是。
    ("slower", re.compile(r"\b(rit|ritard\w*|rall\w*|allarg\w*|calando|"
                          r"smorz\w*|morendo|meno\s*mosso|slentando)\b", re.I)),
    ("reset",  re.compile(r"\b(a\s*tempo|tempo\s*(primo|i)\b|l'?istesso)\b", re.I)),
]

# 符頭填充率低於這個就是**空心**（二分音符）。實測七個記號：
#     空心 0.57　｜　實心 0.69 / 0.83 / 0.85 / 0.85 / 0.86 / 0.92
# 訂在 0.62 兩邊都有餘裕。量不準時一律當四分音符 —— 猜錯速度比不猜更糟。
HOLLOW_HEAD_FILL = 0.62

#: 節拍器記號的音符值 -> 換算成四分音符要乘多少
HALF_NOTE = 2.0
QUARTER_NOTE = 1.0


def from_image(image, top_fraction=0.40):
    """OCR 樂譜上方那一區，找速度標記。

    只看上面 40%：速度標記一定印在曲子開頭，往下都是音符與歌詞，
    掃全頁只會把指法數字、小節號一起讀進來當成雜訊。
    """
    try:
        import cv2      # noqa: F401 - 只是確認裝了
        from rapidocr import RapidOCR      # noqa: F401
    except ImportError:
        return TempoResult()

    path = Path(image)
    if not path.exists():
        return TempoResult()

    try:
        import cv2
        from . import ocr as ocr_mod
        from .imaging import _imread
        page_text = ocr_mod.read_page(path)
        gray = cv2.cvtColor(_imread(path), cv2.COLOR_BGR2GRAY)
    except Exception:
        # OCR 失敗不該讓整個建構流程掛掉，回報偵測不到就好
        return TempoResult()

    limit = page_text.height * top_fraction
    best = TempoResult()
    for item in page_text.confident(0.3):
        if item.center_y > limit:
            continue
        found = from_text(item.text)
        if not found.ok or found.confidence <= best.confidence:
            continue
        # 節拍器記號要看符頭是實心還是空心 —— 𝅗𝅥=61 等於四分音符 122
        if found.source == "metronome":
            unit = beat_unit(gray, item)
            if unit != QUARTER_NOTE:
                found = TempoResult(round(found.bpm * unit), found.source,
                                    f"{found.evidence}（符頭是空心的＝二分音符，"
                                    f"換算成四分音符 {round(found.bpm * unit)}）",
                                    found.confidence)
        best = found
    return best


def beat_unit(gray, box):
    """節拍器記號用的是幾分音符 —— 回傳「換算成四分音符要乘多少」。

    ♩=120 跟 𝅗𝅥=120 差**一倍**，而樂譜很常用二分音符標快板。
    以前只抓 `=` 後面的數字、一律當四分音符，結果〈蕭邦 冬風練習曲〉
    印的是 𝅗𝅥=61（四分音符 122），被當成 61，整首慢一半：7.3 分鐘，
    實際只有約 3 分半。

    **OCR 的字元完全不能用來判斷** —— 空心的二分音符與實心的四分音符
    都被讀成同一個字元 "d"。唯一可靠的是**符頭實心還是空心**，那是影像問題：

        1 音符符號就緊貼在 `=` 的**左邊**（OCR 把它當成一個字讀進去了）
        2 在那一小塊裡做連通元件，取**最高**的那一塊 —— 音符有符桿所以最高，
          等號則是又寬又扁的兩條
        3 那一塊裡最寬的幾列就是符頭，量它的填充率

    切法一定要跟著 `=` 走，不能取整個框的前 40%：〈士兵進行曲〉印的是
    `Allegro deciso( ♩=120)`，前 40% 落在 "Allegro de" 上，最高的那一塊變成
    字母 "A"，字母中間是空的 -> 判成空心 -> 120 變 240。
    文字是等寬排的，用 `=` 在字串裡的位置換算它的 x，誤差在一兩個字元內，
    再往左取 1.6 倍字高就穩穩罩住音符符號。

    box 是 `ocr.TextItem`，座標已經換算回原圖尺度。
    """
    import cv2

    width = box.x1 - box.x0
    height = box.y1 - box.y0
    if width < 8 or height < 6:
        return QUARTER_NOTE

    text = box.text or ""
    cut = max(text.find("="), text.find("＝"))
    if cut <= 0 or not text:
        return QUARTER_NOTE
    equals_x = box.x0 + width * (cut / len(text))
    left = max(box.x0, equals_x - height * 1.6)
    crop = gray[int(box.y0):int(box.y1), int(left):int(equals_x)]
    if crop.size == 0 or min(crop.shape) < 6:
        return QUARTER_NOTE

    from .imaging import _binarize
    ink = _binarize(crop).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    if count < 2:
        return QUARTER_NOTE

    tallest = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_HEIGHT])
    mask = labels == tallest
    ys, xs = np.nonzero(mask)
    if xs.size < 20:
        return QUARTER_NOTE

    note = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    widths = note.sum(axis=1)
    if widths.max() < 3:
        return QUARTER_NOTE
    rows = np.flatnonzero(widths >= widths.max() * 0.6)
    band = note[rows.min():rows.max() + 1, :]
    cols = np.flatnonzero(band.any(axis=0))
    if cols.size < 2:
        return QUARTER_NOTE
    head = band[:, cols.min():cols.max() + 1]

    return HALF_NOTE if float(head.mean()) < HOLLOW_HEAD_FILL else QUARTER_NOTE


def marks_on_page(image, layout_page=None):
    """找出**整頁**所有的節拍器記號，回傳 [{bpm, measure, y, text}]。

    `from_image()` 只看上面 40%、只回傳一個值 —— 那對「整首一個速度」的譜夠用，
    但很多曲子中途會換速度。〈うまぴょい伝説〉四頁上印了 **8 個**節拍器記號
    （105、100、115、121、170、167、110、170），只取第一個就等於整首都用錯速度。

    這裡掃全頁，並用 layout 算好的小節框把每個記號對到**第幾小節** ——
    記號印在哪一行的上方，就從那一行的第一個小節開始生效。

    只認 `♩=120` 這種明確的節拍器記號，不認速度術語：術語只是大概的範圍，
    拿它當「這裡開始變速」的依據太危險。
    """
    try:
        import numpy as np      # noqa: F401 - 只是確認裝了
        from rapidocr import RapidOCR      # noqa: F401
    except ImportError:
        return []

    path = Path(image)
    if not path.exists():
        return []

    try:
        import cv2
        from . import ocr as ocr_mod
        from .imaging import _imread
        # 走共用的 OCR：它已經處理過放大（小字才讀得到）與座標換算
        page_text = ocr_mod.read_page(path)
        gray = cv2.cvtColor(_imread(path), cv2.COLOR_BGR2GRAY)
    except Exception:      # noqa: BLE001 - OCR 掛掉不該擋住建構
        return []
    if not page_text.items:
        return []

    boxes = _measure_boxes(layout_page)

    marks = []
    for item in page_text.matching(r"[=＝]\s*\d{2,3}"):
        found = from_text(item.text)
        if not found.ok or found.source != "metronome":
            continue
        y, x = item.y0, item.x0
        index = _measure_below(boxes, x, y)

        # ♩=120 與 𝅗𝅥=120 差一倍，一律當四分音符會讓曲子慢一半
        unit = beat_unit(gray, item)
        marks.append({"bpm": float(found.bpm) * unit, "index": index,
                      "unit": "half" if unit == HALF_NOTE else "quarter",
                      "y": round(y, 1), "x": round(x, 1),
                      "text": item.text.strip()[:40]})

    marks.sort(key=lambda m: m["y"])
    return marks


def gradual_on_page(image, layout_page=None):
    """找出**整頁**的漸快／漸慢術語，回傳 [{kind, measure, y, x, text}]。

    kind 是 faster / slower / reset。跟 `marks_on_page()` 走同一份 OCR 結果
    （`ocr.read_page()` 一頁只跑一次），所以多這一趟幾乎不花時間。

    〈山魔王的宮殿〉整首印了 22 個 accel、〈李斯特 鐘〉9 個（rit./accel/smorz/
    più mosso/Tempo I），其餘 10 首是 0 —— 沒有漸變記號的曲子完全不受影響。
    """
    try:
        from . import ocr as ocr_mod
    except ImportError:
        return []

    path = Path(image)
    if not path.exists():
        return []
    try:
        page_text = ocr_mod.read_page(path)
    except Exception:      # noqa: BLE001 - OCR 掛掉不該擋住建構
        return []
    if not page_text.items:
        return []

    boxes = _measure_boxes(layout_page)

    found = []
    for item in page_text.confident(0.5):
        text = item.text.strip()
        if not text:
            continue
        for kind, pattern in _GRADUAL_WORDS:
            if not pattern.search(text):
                continue
            found.append({"kind": kind,
                          "index": _measure_below(boxes, item.x0, item.y0),
                          "y": round(item.y0, 1), "x": round(item.x0, 1),
                          "text": text[:40]})
            break

    found.sort(key=lambda g: g["y"])
    return found


def _measure_boxes(layout_page):
    """把版面偵測算出的小節框整理成 [{top, x0, x1, index, number}]。

    記號印在它生效的那個小節**正上方**，所以要同時看 x 與 y —— 一行裡可能有
    好幾個變速記號（〈うまぴょい伝説〉第一行就有三個），只看 y 的話它們會
    全部塌成同一個小節。

    `index` 是**頁內第幾格**（1 起算），`number` 是譜上印的號碼。
    對到合併檔要用 index，不能用 number，理由見 `_measure_below()`。
    """
    boxes = []
    for position, box in enumerate((layout_page or {}).get("measures", []), start=1):
        corners = box.get("corners") or []
        if not corners:
            continue
        xs = [float(c[0]) for c in corners]
        ys = [float(c[1]) for c in corners]
        boxes.append({"top": min(ys), "x0": min(xs), "x1": max(xs),
                      "index": position,
                      "number": box.get("number") or box.get("index")})
    return boxes


def _measure_below(boxes, x, y):
    """印在 (x, y) 的記號落在**這一頁的第幾格**（1 起算）。找不到就回 None。

    回傳頁內序號而不是譜上印的號碼，因為 `layout.py` 讀得到印出來的小節號時
    給的是全曲編號、讀不到時退回每頁 1..N —— 兩種混在一起。〈Rush E〉十頁裡
    p3–p5、p7–p9 是全曲編號，p2/p6/p10 是每頁編號，把後者當成全曲編號的話，
    第 6 頁（全曲第 84 小節起）的速度記號會被套到第 1 小節去。

    頁內序號一律一致，再由呼叫端加上該頁的起始偏移換成全曲小節 ——
    這跟 `enrich.apply_ottavas()` 用的是同一套做法。
    """
    below = [b for b in boxes if b["top"] >= y - 1]
    if not below:
        return None
    top = min(b["top"] for b in below)
    row = [b for b in below if b["top"] <= top + 1]          # 記號正下方那一行
    covering = [b for b in row if b["x0"] - 1 <= x <= b["x1"]]
    # 落在小節框內就用那一格，落在框與框之間就用右邊最近的那一格
    target = (min(covering, key=lambda b: b["x0"]) if covering
              else min(row, key=lambda b: (b["x1"] < x, abs(b["x0"] - x))))
    return target["index"]
