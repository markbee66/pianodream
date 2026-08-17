"""把手機拍的照片整理成「像掃描檔」再送去辨識。

## 為什麼需要這一層

homr 內部第一件事就是 `resize_image()`，**不管原圖多大，一律縮到寬 1920**
（`.venv/Lib/site-packages/homr/resize.py`）。所以「譜線在模型眼裡有多大」
不是由相機解析度決定的，而是由**譜佔畫面多少**決定的：

    模型看到的行距 = 原圖行距 × 1920 / 原圖寬度

拍照時紙不會填滿畫面，紙上還有一圈白邊，兩者相乘之後常常只剩六成。
一張 4000px 寬、行距 26px 的漂亮照片，送進去可能只剩 7px —— 那是 Gate A
判定「解析度不足、請靠近一點重拍」的區間，而使用者其實已經拍得很好了。

homr 自己有 `autocrop()`，但它的放行條件是
`x < width*0.25 or y < height*0.25` —— 只要紙的左邊或上邊落在畫面前四分之一
就整個放棄，實際照片幾乎都符合，等於沒作用。而且它裁的是**紙**，不是**譜**，
紙上的白邊照樣佔著寬度。

## 這一層做什麼

    1 找紙        抓最大的四邊形（紙是畫面裡最大的亮色區塊）
    2 透視矯正    四點透視變換，把斜拍的紙拉回矩形
    3 攤平打光    除以大尺度背景估計，把一邊亮一邊暗拉平
    4 轉正        沿用 quality.measure_skew（投影變異數最大化）
    5 裁到譜      只留內容 + 兩個行距的邊

**順序不能換**：攤平打光要排在找紙**後面**。第一版反過來，結果桌面被一起拉亮到
跟紙一樣白，四邊形當然就找不到了 —— 每張照片都退回「整張圖」，透視矯正與裁切
雙雙失效，而且完全不會報錯，只是靜靜地什麼都沒做。

## 每一步值多少

`tools/omr_bench.py --pieces andre --ablate normal`，跟 Mutopia 的標準答案逐音比對：

    完整前處理        98.8%
    少了「warp」      88.6%   -10.2   ← 透視矯正是主力
    少了「light」     98.4%    -0.4
    少了「crop」      98.4%    -0.4
    少了「deskew」    98.8%    +0.0   ← 透視矯正已經把紙拉正，這步只是備援
    少了「contrast」 100.0%    +1.2   ← 所以把它刪了

**曾經有第 6 步「拉開對比」，量完之後刪掉。** 百分位數拉伸（2% 拉到黑、98% 拉到
白）等於一次柔性二值化，把筆畫邊緣的灰階過渡壓平了。homr 的分割網路吃的是真實
照片與掃描檔，這種分佈它沒看過，辨識率反而掉 1.2 個百分點。
**看起來「更清楚」的圖不一定更好認** —— 這件事只能量，不能用眼睛判斷。

輸出一律寫成 PNG。曾經寫 JPEG，同一份輸入辨識率 89.5%，改成 PNG 之後 98.8% ——
來源已經被相機壓過一次，這裡再壓一次，壓縮痕跡正好疊在譜線上。

每一步都可能失敗（找不到紙、算不出行距），失敗就跳過那一步繼續往下 ——
**寧可少做一步，也不要把一張本來還能辨識的圖弄壞**。
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import quality

# 裁切後在譜的四周留幾個行距的白邊。
# 留 0 的話 homr 的譜表偵測會在邊界上失準，留太多又浪費寬度。
MARGIN_INTERLINE = 2.0

# 目標：讓 homr 縮到 1920 之後，行距落在這個值附近。
# homr 的 transformer 會把每一行譜再正規化，所以真正怕的是**太小**（分割網路
# 找不到譜線）。訂在 14 是「A4 整頁塞進 1920 寬」的典型值，也就是掃描檔的樣子。
TARGET_INTERLINE_AT_1920 = 14.0
HOMR_WIDTH = 1920

# 四邊形要夠大才算是紙，否則可能只是桌上的一本書
MIN_PAGE_AREA_FRAC = 0.25
# 角度偏離直角超過這麼多就不套用透視矯正 —— 大概是找錯東西了
MAX_CORNER_SKEW = 0.28



@dataclass
class PrepResult:
    """做了哪些事、圖變成什麼樣。整份會被寫進 manifest，出問題時查得到。"""
    path: Path = None
    steps: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    interline_before: float = 0.0
    interline_after: float = 0.0
    effective_before: float = 0.0     # homr 縮到 1920 之後的行距（處理前）
    effective_after: float = 0.0      # 同上（處理後）
    size_before: tuple = (0, 0)
    size_after: tuple = (0, 0)
    skew_deg: float = 0.0

    def as_dict(self):
        return {
            "steps": list(self.steps),
            "skipped": list(self.skipped),
            "interline": [round(self.interline_before, 2), round(self.interline_after, 2)],
            "effective_interline": [round(self.effective_before, 2),
                                    round(self.effective_after, 2)],
            "size": [list(self.size_before), list(self.size_after)],
            "skew_deg": round(self.skew_deg, 2),
        }

    def summary_line(self):
        gain = (self.effective_after / self.effective_before
                if self.effective_before > 0 else 1.0)
        return (f"{'、'.join(self.steps) or '沒有可做的處理'}　"
                f"模型看到的行距 {self.effective_before:.1f} → "
                f"{self.effective_after:.1f}px（{gain:.2f}×）")


# ---------------------------------------------------------------------------
# 各步驟
# ---------------------------------------------------------------------------

def flatten_lighting(gray, interline):
    """除以背景估計，把打光不均攤平。

    背景估計用一個**遠大於音符**的中值濾波：音符與譜線在這個尺度下會被抹掉，
    留下的就是紙面本身的亮度分佈。相除之後一邊亮一邊暗就消失了。

    用中值不用高斯：高斯會被大片黑色（例如密集的和弦）拉低，那一塊除完會過亮，
    音符被洗掉。中值對這種局部塊狀的干擾穩定得多。
    """
    k = int(max(15, round(interline * 6)))
    if k % 2 == 0:
        k += 1
    # 中值濾波在大核心時很慢，先縮小算再放大 —— 背景本來就是低頻，不損失資訊
    scale = min(1.0, 400.0 / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ks = int(max(3, round(k * scale)))
    if ks % 2 == 0:
        ks += 1
    bg_small = cv2.medianBlur(small, min(ks, 255))
    bg = cv2.resize(bg_small, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_CUBIC)

    bg = np.maximum(bg.astype(np.float32), 1.0)
    flat = gray.astype(np.float32) / bg * 200.0
    return np.clip(flat, 0, 255).astype(np.uint8)


def find_page_quad(gray):
    """找出紙的四個角。找不到就回 None。

    照片裡紙是最大的亮色區塊。用 Otsu 取亮的部分、取最大的外輪廓、
    再用 approxPolyDP 逼近成四邊形。逼不出四個角（例如紙被切到、或整張圖
    都是紙）就放棄 —— 這種情況本來就不需要透視矯正。
    """
    scale = min(1.0, 900.0 / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (5, 5), 0)

    _, mask = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area < MIN_PAGE_AREA_FRAC * small.shape[0] * small.shape[1]:
        return None

    peri = cv2.arcLength(biggest, True)
    quad = None
    for eps in (0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(biggest, eps * peri, True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        return None
    return _order_corners(quad / scale)


def _order_corners(pts):
    """排成 左上、右上、右下、左下。"""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.float32([pts[np.argmin(s)], pts[np.argmin(d)],
                       pts[np.argmax(s)], pts[np.argmax(d)]])


def _quad_is_sane(quad, shape):
    """四邊形像不像一張被斜拍的紙。

    兩個條件：對邊長度不能差太多（差太多多半是抓到桌面或陰影），
    以及四個角要接近直角 —— 紙本來就是矩形，透視不會把直角扭到 70 度以下。
    """
    tl, tr, br, bl = quad
    top, bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    left, right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    if min(top, bottom, left, right) < 0.15 * min(shape):
        return False
    if max(top, bottom) / max(1e-6, min(top, bottom)) > 1.6:
        return False
    if max(left, right) / max(1e-6, min(left, right)) > 1.6:
        return False

    for i in range(4):
        a = quad[(i - 1) % 4] - quad[i]
        b = quad[(i + 1) % 4] - quad[i]
        cos = float(np.dot(a, b) / max(1e-6, np.linalg.norm(a) * np.linalg.norm(b)))
        if abs(cos) > MAX_CORNER_SKEW:      # cos 0.28 ≈ 74 度
            return False
    return True


def warp_page(gray, quad):
    """把四邊形拉回矩形。輸出尺寸取對邊的較長者，避免壓縮掉細節。"""
    tl, tr, br, bl = quad
    w = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    h = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    if w < 50 or h < 50:
        return None
    dst = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    matrix = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(gray, matrix, (w, h), flags=cv2.INTER_CUBIC,
                               borderValue=255)


def crop_to_music(gray, interline):
    """裁到墨水的外框，四周留 MARGIN_INTERLINE 個行距。

    這一步的收益最大：紙上的白邊在 homr 眼裡跟譜一樣佔寬度。
    """
    ink = quality._binarize(gray)
    # 先清掉孤立雜點，否則紙邊的一顆黑點就會把外框撐回整頁
    ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN,
                           np.ones((3, 3), np.uint8)) > 0
    box = quality.content_bbox(ink)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    m = int(round(max(4.0, interline * MARGIN_INTERLINE)))
    h, w = gray.shape
    x0, y0 = max(0, x0 - m), max(0, y0 - m)
    x1, y1 = min(w, x1 + m + 1), min(h, y1 + m + 1)
    if x1 - x0 < w * 0.25 or y1 - y0 < h * 0.2:
        return None      # 裁掉太多，多半是墨水偵測出了問題
    return gray[y0:y1, x0:x1]


def _effective_interline(interline, width):
    """homr 把圖縮到寬 1920 之後，行距會變成多少。"""
    if interline <= 0 or width <= 0:
        return 0.0
    return interline * HOMR_WIDTH / width


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

#: prepare() 的步驟名稱，給 skip= 用。順序就是實際執行的順序。
STEPS = ("warp", "light", "deskew", "crop")


def prepare(path, out_path, skip=()):
    """整理一張照片，回傳 PrepResult。

    skip 可以關掉個別步驟（名稱見 STEPS），用來做消融實驗 ——
    「哪一步真的有幫助」只能量，不能猜。正式流程不傳這個參數。

    out_path 一定會被寫出來（就算一步都沒做），這樣呼叫端只要無條件用它就好，
    不必分「有處理」和「沒處理」兩條路。**副檔名會被強制改成 .png**：
    來源已經是相機壓過一次的 JPEG，這裡再壓一次，壓縮痕跡會疊在譜線上，
    而這一步的產物是要餵給模型的，不是給人看的，沒有理由省那點空間。
    """
    result = PrepResult()
    img = quality._imread(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    result.size_before = (gray.shape[1], gray.shape[0])

    # 0 整頁躺著就先轉正。**一定要排在最前面**：
    #
    #   * 透視矯正假設頁面大致正立，躺著的話四邊形找出來也是錯的
    #   * 攤平打光會破壞行距估計（實測切割2：攤平前兩個方向的行距比是 3.62，
    #     攤平後只剩 1.12），排在它後面就偵測不到了
    #
    # `measure_skew()` 看不出這件事 —— 它只在 ±20 度內搜尋（見它的簽章），
    # 90 度完全在範圍外。實測〈失敗圖片/切割2.jpg〉整頁躺著，它回報 -21.2 度，
    # 於是 Gate A 與這裡的轉正全都建立在錯的前提上。
    turn = quality.detect_quarter_turn(quality._binarize(gray))
    if turn:
        gray = np.ascontiguousarray(np.rot90(gray, k=-1))
        result.size_before = (gray.shape[1], gray.shape[0])
        result.steps.append(f"整頁轉正 {turn}°")

    interline, _ = quality.estimate_interline(quality._binarize(gray))
    result.interline_before = interline
    result.effective_before = _effective_interline(interline, gray.shape[1])
    work_interline = interline if interline > 0 else 16.0

    skip = set(skip)

    # 1-2 找紙 + 透視矯正。一定要在攤平打光之前 —— 見模組開頭。
    if "warp" in skip:
        result.skipped.append("（消融）沒有矯正透視")
    else:
        quad = find_page_quad(gray)
        if quad is not None and _quad_is_sane(quad, gray.shape):
            warped = warp_page(gray, quad)
            if warped is not None:
                gray = warped
                result.steps.append("透視矯正")
            else:
                result.skipped.append("四邊形太小，沒有矯正透視")
        else:
            result.skipped.append("找不到紙的四個角，沒有矯正透視")

    # 3 攤平打光
    if "light" in skip:
        result.skipped.append("（消融）沒有攤平打光")
    else:
        flat = flatten_lighting(gray, work_interline)
        if float(np.std(gray.astype(np.float32) - flat.astype(np.float32))) > 3.0:
            gray = flat
            result.steps.append("攤平打光")
        else:
            result.skipped.append("打光本來就均勻")

    # 4 轉正
    if "deskew" in skip:
        result.skipped.append("（消融）沒有轉正")
    else:
        ink = quality._binarize(gray)
        skew = quality.measure_skew(ink)
        result.skew_deg = skew
        if abs(skew) >= 0.3:
            gray = quality._rotate_gray(gray, skew)
            result.steps.append(f"轉正 {skew:+.1f}°")
        else:
            result.skipped.append("本來就是正的")

    # 5 裁到譜
    interline, _ = quality.estimate_interline(quality._binarize(gray))
    cropped = (None if "crop" in skip
               else crop_to_music(gray, interline if interline > 0 else work_interline))
    if cropped is not None and cropped.shape[1] < gray.shape[1] * 0.97:
        gray = cropped
        result.steps.append("裁到樂譜")
    elif "crop" in skip:
        result.skipped.append("（消融）沒有裁切")
    elif cropped is None:
        result.skipped.append("找不到內容範圍，沒有裁切")
    else:
        result.skipped.append("本來就沒有多餘的邊")

    interline, _ = quality.estimate_interline(quality._binarize(gray))
    result.interline_after = interline
    result.effective_after = _effective_interline(interline, gray.shape[1])
    result.size_after = (gray.shape[1], gray.shape[0])

    out_path = Path(out_path).with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    quality._imwrite(out_path, cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
    result.path = out_path
    return result

