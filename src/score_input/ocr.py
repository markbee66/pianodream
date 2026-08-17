"""整頁文字辨識，一頁只跑一次，結果分給所有需要的規則共用。

## 為什麼要抽出來

樂譜上有一堆**文字**，而且每一種都由不同的模組在用：

    曲名、作曲者      title.py
    節拍器記號 ♩=120  tempo.py
    小節號            layout.py
    連音數字 3 5 6 7  rules.py
    8va / Ped. / f p  rules.py

以前是每個模組各自 `RapidOCR()(img)` 一次，同一頁被辨識三遍以上，
慢而且結果還可能不一致（各自用了不同的縮放）。這裡掃一次、存成一份
帶座標的清單，誰要用就自己篩。

## 一定要放大再辨識

節拍器記號、連音數字、指法都是很小的字，而且夾在密集的音符之間。
實測〈うまぴょい伝説〉鋼琴版原尺寸只讀到開頭的 ♩=114，第 13 小節的
♩=170 完全偵測不到。放大到 OCR_TARGET_WIDTH 之後才找得回來。

座標一律換算回**原圖尺度**，呼叫端不必知道放大過。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .quality import _imread

#: OCR 之前把圖放大到這個寬度。小字（連音數字、指法、節拍器記號）非常吃解析度。
OCR_TARGET_WIDTH = 2400

_PURE_NUMBER = re.compile(r"^\d{1,3}$")


@dataclass
class TextItem:
    """一段辨識到的文字，座標已經換算回原圖。"""

    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center_x(self):
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self):
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self):
        return self.y1 - self.y0

    def as_dict(self):
        return {"text": self.text, "score": round(self.score, 3),
                "box": [round(self.x0, 1), round(self.y0, 1),
                        round(self.x1, 1), round(self.y1, 1)]}


@dataclass
class PageText:
    """一頁的所有文字。"""

    width: int = 0
    height: int = 0
    items: list = field(default_factory=list)

    def confident(self, minimum=0.5):
        return [i for i in self.items if i.score >= minimum]

    def numbers(self, minimum=0.5):
        """純數字的那些。小節號、指法、連音數字都長這樣，靠位置再分。"""
        return [i for i in self.confident(minimum) if _PURE_NUMBER.match(i.text)]

    def matching(self, pattern, minimum=0.5):
        """文字符合某個 regex 的那些。"""
        compiled = re.compile(pattern, re.IGNORECASE)
        return [i for i in self.confident(minimum) if compiled.search(i.text)]

    def within(self, x0, y0, x1, y1, minimum=0.5):
        """框在某個矩形裡的那些（用文字的中心點判斷）。"""
        return [i for i in self.confident(minimum)
                if x0 <= i.center_x <= x1 and y0 <= i.center_y <= y1]


_ENGINE = None
_CACHE = {}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def read_page(image, use_cache=True):
    """辨識一整頁的文字。同一個檔案重複呼叫會走快取。"""
    key = str(Path(image).resolve())
    if use_cache and key in _CACHE:
        return _CACHE[key]

    page = PageText()
    try:
        img = _imread(image)
    except (ValueError, OSError):
        return page

    page.height, page.width = img.shape[0], img.shape[1]

    scale = 1.0
    if img.shape[1] < OCR_TARGET_WIDTH:
        scale = OCR_TARGET_WIDTH / img.shape[1]
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    try:
        result = _engine()(img)
    except Exception:      # noqa: BLE001 - OCR 掛掉不該擋住整個建構
        return page
    if not getattr(result, "txts", None):
        if use_cache:
            _CACHE[key] = page
        return page

    for box, text, score in zip(result.boxes, result.txts, result.scores):
        text = (text or "").strip()
        if not text:
            continue
        xs = [float(p[0]) / scale for p in box]
        ys = [float(p[1]) / scale for p in box]
        page.items.append(TextItem(text=text, score=float(score),
                                   x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)))

    page.items.sort(key=lambda i: (i.y0, i.x0))
    if use_cache:
        _CACHE[key] = page
    return page


def clear_cache():
    _CACHE.clear()
