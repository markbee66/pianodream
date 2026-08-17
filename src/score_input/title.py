"""從樂譜照片上讀出曲名與作曲者。

## 為什麼要自己寫

homr 有 `title_detection.py`，但它的結果不能用：

    士兵進行曲 → "Grade II"      （抓到左上角的難度標籤）
    Alkan     → "ement"          （"Lentement" 這個速度術語的殘片）
    うまぴょい  → ""              （這個是對的，那幾頁本來就沒印標題）

兩個原因。一是它用 `bbox 高度 ÷ 字數` 當分數，等於獎勵**短**字串，
所以兩個字的殘片會贏過真正的標題。二是它的 `cleanup_text` 會把
`[a-zA-Z0-9]` 以外的字元**全部刪掉** —— 中文與日文標題會整個消失，
而這個專案的使用者拍的就是中文/日文譜。

## 真正分得開的是「置中」

實際量四份譜，標題、作曲者、速度術語在**水平位置**上分得非常乾淨：

    「士兵進行曲」       中心 x = 0.51   高 6.7 個行距   ← 標題
    「Robert Schumann」 中心 x = 0.91   高 7.8 個行距   ← 作曲者（比標題還高！）
    「Grade II」        中心 x = 0.07                  ← 左上角標籤
    「Allegro deciso」  中心 x = 0.25                  ← 速度術語
    「38」             中心 x = 0.05                  ← 頁碼

**只看字高會選到作曲者** —— 它的字框比標題還高。但排版慣例是標題置中、
作曲者靠右、速度術語靠左，這個規則在四份來源完全不同的譜上都成立。

所以：先用「置中」篩掉八成，再從剩下的裡面挑最大的字。

## 找不到就說找不到

うまぴょい伝説那四頁是曲子的中間頁，根本沒有標題。這種時候回傳空字串，
讓使用者自己命名 —— 硬猜一個出來比留白更糟。
"""

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import quality

# 置中的容許範圍（佔頁寬的比例）。0.15 是量出來的：標題落在 0.50–0.51，
# 最靠近的干擾項（作曲者）在 0.85 以上，中間空得很開。
CENTER_TOLERANCE = 0.15
COMPOSER_MIN_X = 0.62       # 靠右到這個程度才可能是作曲者

MIN_OCR_CONFIDENCE = 0.70   # 低於這個多半是把音符認成文字
MIN_HEIGHT_RATIO = 1.8      # 字高至少要有這麼多個行距，標題不會比音符小
SEARCH_ABOVE_STAFF = 1.0    # 往上找到第一條譜線上方幾個行距為止

# 這些一看就不是曲名
# 速度與表情記號。**不能只擋開頭**：實測裁切照片把「poco rit.」當成曲名放進歌單，
# 因為它剛好置中、字又比周圍大。這類詞出現在字串**任何位置**都表示它是記號不是曲名。
_TEMPO_WORDS = re.compile(
    r"(a\s*tempo|adagio|allegr\w*|andant\w*|largo|lento|lentement|moderato|presto|"
    r"vivace|grave|maestoso|rubato|dolce|cantabile|espressiv\w*|deciso|marcato|"
    r"con\s+moto|ma\s+non\s+troppo|poco|molto|assai|sempre|subito|"
    r"rit\.?|ritard\w*|rallent\w*|accel\w*|string\w*|cresc\w*|dim\.?|decresc\w*|"
    r"ten\.?|stacc\w*|legato|pesante|leggier\w*|calando|smorz\w*|"
    r"fine|d\.?\s*c\.?|d\.?\s*s\.?|coda|segno|simile|loco|"
    r"\bva\b|8va|8vb|ped\.?)", re.IGNORECASE)
_LABEL = re.compile(r"^(op(us)?\.?|nr\.?|no\.?|grade|bpm|d\.?c\.?|fine|"
                    r"page|第\s*\d+\s*[頁页]|練習|练习)\b", re.IGNORECASE)
_ONLY_SYMBOLS = re.compile(r"^[\W\d_]+$", re.UNICODE)
_DATES = re.compile(r"[（(]\s*\d{3,4}\s*[-–—~]\s*\d{3,4}\s*[)）]")
_METRONOME = re.compile(r"[=＝]\s*\d{2,3}")


# 集合名的特徵：Suite / Sonata No.2 / Book 1 / Op.46 這種「一組作品」的講法。
# 只有最大的那一筆長成這樣時才會去找樂章名。
_COLLECTION = re.compile(
    r"\b(suite|sonata|sonatine|book|album|collection|cycle|"
    r"op\.?\s*\d+|no\.?\s*\d+|bwv\s*\d+|k\.?\s*\d+)\b", re.IGNORECASE)

# 樂章名的特徵：被引號括起來（各種彎引號都算），或用 from / aus 引出。
# 〈山魔王的宮殿〉在譜上就是印成 "In The Hall Of The Mountain King"。
_QUOTED = re.compile(r"""^\s*["'‘’“”「『]""")


def _movement_title(centred, best):
    """最大的那一筆是集合名時，回傳底下真正的樂章名；否則回 None。

    只在**證據明確**時才改：最大的那一筆要長得像集合名，而候選要被引號括起來。
    寧可維持原本的行為，也不要把正常的曲名換成別的東西。
    """
    if not _COLLECTION.search(best["text"]):
        return None

    smaller = [i for i in centred
               if i is not best and i["height"] < best["height"]
               and _QUOTED.search(i["text"])]
    if not smaller:
        return None
    # 引號候選裡挑最大的 —— 樂章名通常是第二大的那一行
    return max(smaller, key=lambda i: i["height"])


@dataclass
class TitleResult:
    title: str = ""
    composer: str = ""
    confidence: float = 0.0
    candidates: list = field(default_factory=list)
    reason: str = ""
    #: 組曲／曲集名。曲名取樂章名時，這裡留著上一層的名字（例如
    #: title="In The Hall Of The Mountain King"、collection="Peer Gynt, Suite No.1"）
    collection: str = ""

    @property
    def ok(self):
        return bool(self.title)

    def as_dict(self):
        return {"title": self.title, "composer": self.composer,
                "collection": self.collection,
                "confidence": round(self.confidence, 3), "reason": self.reason,
                "candidates": self.candidates[:6]}

    def describe(self):
        if not self.title:
            return f"這一頁上找不到曲名（{self.reason}）"
        who = f"　作曲：{self.composer}" if self.composer else ""
        return f"曲名「{self.title}」（信心 {self.confidence:.2f}）{who}"


_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def _looks_like_junk(text):
    text = text.strip()
    if len(text) < 2:
        return True                      # 單一個字母多半是 f / p 之類的力度記號
    if _ONLY_SYMBOLS.match(text):
        return True                      # 純數字 = 頁碼或小節號
    if _METRONOME.search(text) or _TEMPO_WORDS.search(text) or _LABEL.match(text):
        return True
    # 認出來的字裡至少要有一半是「字」，不然多半是把音符讀成符號
    letters = sum(1 for c in text if c.isalpha())
    return letters < max(2, len(text.strip()) * 0.4)


def _search_region(gray):
    """第一條譜線以上的範圍。回傳 (裁好的圖, 行距)。"""
    ink = quality._binarize(gray)
    interline, _ = quality.estimate_interline(ink)
    if interline <= 0:
        interline = 12.0

    lines = quality._horizontal_lines(ink, interline)
    _, _, staff_lines = quality.detect_staves(lines, interline)
    if staff_lines:
        top = int(min(staff_lines) - interline * SEARCH_ABOVE_STAFF)
    else:
        top = int(gray.shape[0] * 0.22)
    top = max(int(interline * 2), min(top, gray.shape[0] - 1))
    return gray[:top], interline


def detect(image):
    """讀一張樂譜圖上的曲名。只該對**第一頁**用 —— 後面幾頁沒有標題。"""
    result = TitleResult()
    try:
        img = quality._imread(image)
    except ValueError as exc:
        result.reason = str(exc)
        return result

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    region, interline = _search_region(gray)
    if region.shape[0] < interline * 2:
        result.reason = "譜表太靠近頁面上緣，上面沒有空間放標題"
        return result

    try:
        raw = _engine()(cv2.cvtColor(region, cv2.COLOR_GRAY2BGR))
    except Exception as exc:            # noqa: BLE001 - OCR 掛掉不該擋住建構
        result.reason = f"文字辨識失敗：{exc}"
        return result

    if not getattr(raw, "txts", None):
        result.reason = "上方區域沒有讀到任何文字"
        return result

    width = float(img.shape[1])
    items = []
    for box, text, score in zip(raw.boxes, raw.txts, raw.scores):
        text = (text or "").strip()
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "text": text,
            "score": float(score),
            "height": float(max(ys) - min(ys)) / interline,
            "center": float((min(xs) + max(xs)) / 2) / width,
            "top": float(min(ys)),
        })

    # 一律轉成內建 float —— OCR 回來的是 numpy float32，
    # 直接塞進 manifest 會在 json.dump 掛掉（float32 不能序列化）
    result.candidates = [
        {"text": i["text"], "center": round(float(i["center"]), 2),
         "height": round(float(i["height"]), 1), "ocr": round(float(i["score"]), 2)}
        for i in sorted(items, key=lambda i: -i["height"])[:8]
    ]

    usable = [i for i in items
              if i["score"] >= MIN_OCR_CONFIDENCE
              and i["height"] >= MIN_HEIGHT_RATIO
              and not _looks_like_junk(i["text"])]
    if not usable:
        result.reason = "上方只有頁碼、速度術語之類的文字，沒有像曲名的東西"
        return result

    centred = [i for i in usable if abs(i["center"] - 0.5) <= CENTER_TOLERANCE]
    if not centred:
        result.reason = "沒有置中的文字 —— 這一頁可能不是曲子的第一頁"
        return result

    # 置中的裡面挑最大的；一樣大就挑比較上面的
    best = max(centred, key=lambda i: (round(i["height"], 1), -i["top"]))

    # 組曲／曲集的封面上，**集合名往往印得比樂章名大**，挑最大的就會挑錯：
    #
    #   "Peer Gynt, Suite No.1, Op.46"       height 12.0  <- 組曲名
    #   "In The Hall Of The Mountain King"   height  7.9  <- 真正在彈的樂章
    #
    # 使用者要的是樂章名（他叫這首「山魔王的宮殿」）。判準是最大的那一筆
    # 長得像集合名（Suite / Book / Op. 加編號），而底下又有一筆用引號括起來
    # 或明顯是標題的 —— 那一筆才是樂章。
    movement = _movement_title(centred, best)
    if movement is not None:
        result.collection = best["text"]
        best = movement

    result.title = best["text"]

    # 信心：比第二名大多少，加上 OCR 自己的把握
    others = [i["height"] for i in centred if i is not best]
    margin = best["height"] / max(others) if others else 1.6
    result.confidence = float(np.clip(best["score"] * np.clip(margin / 1.3, 0.4, 1.0), 0, 1))

    right = [i for i in usable if i["center"] >= COMPOSER_MIN_X]
    if right:
        composer = max(right, key=lambda i: i["height"])["text"]
        # 「Johann André (1741-1799)」→「Johann André」
        result.composer = _DATES.sub("", composer).strip(" ,;·")

    return result
