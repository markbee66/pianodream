"""影像量測的基礎工具 —— 讀圖、二值化、估行距、量歪斜與亮度。

從 `quality.py` 拆出來的。這一層**只回答「量到多少」，不判斷「能不能用」**：
判斷是 Gate A 的事（`quality.check_image()` / `_judge()`），會隨著校準結果一直
變動；量測本身則是穩定的，而且被很多地方共用 —— `layout`、`preprocess`、
`grandstaff`、`ocr`、`title`、`tempo` 都吃這裡的函式，其中大部分根本不在乎
Gate A 的門檻長什麼樣。

所有指標都以「五線譜行距」(interline) 為基準，因為它才是決定 OMR 成敗的關鍵尺度：
同一張譜拍遠拍近，畫素數差很多但行距說明的是「一個音符有幾個像素可以描述」。
模糊度也先把圖縮放到固定行距再量，否則同一個門檻在不同解析度的照片上沒有可比性。
"""

from pathlib import Path

import cv2
import numpy as np

# 轉 90 度之後的行距要比原本大這麼多倍，才判定「原圖是躺著的」。
# 實測三張躺著的圖差距是 29/8、35/11（3.2 與 3.6 倍），而正常照片兩個方向
# 的差距通常在 1.2 倍以內，所以 1.5 有很寬的安全邊界。
QUARTER_TURN_RATIO = 1.5
# 轉正之後的行距至少要有這麼大，才值得相信「轉了會比較好」
INTERLINE_TURN_MIN = 8.0
# 轉 90 度之後「水平長線」要變成原本的幾倍，才確定原圖是躺著的。
# 實測躺著的是 1.30 與 1.64，正立的是 0.00–0.37 —— 中間空得很開，取 1.0。
HORIZONTAL_TURN_RATIO = 1.0

NORM_INTERLINE = 10.0   # 量模糊度時統一縮放到這個行距


# ---------------------------------------------------------------------------
# 讀寫與二值化
# ---------------------------------------------------------------------------

def _imread(path):
    """讀圖，回傳 3 通道 BGR。

    兩個都會靜靜出錯的坑：

    **一、非 ASCII 路徑。** cv2.imread 在 Windows 上讀不了含中文的路徑，
    而且回報的是「file format is not supported」，完全看不出真正原因。
    所以走 numpy 讀進 bytes 再解碼。

    **二、透明背景的 PNG。** 用 IMREAD_COLOR 讀會直接丟掉 alpha，露出底下
    存的 RGB —— 那個值通常不是白色。實測使用者上傳的譜，透明處底下是深綠
    (76,112,71)，讀出來就是「黑色譜線畫在深綠底上」，譜線幾乎看不見，
    整頁認不出任何東西。這裡改成**合成到白底**，也就是這種譜本來該有的樣子。
    """
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"這個檔案不是能讀的圖片：{Path(path).name}")

    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        blended = img[:, :, :3].astype(np.float32) * alpha + 255.0 * (1.0 - alpha)
        return blended.round().astype(np.uint8)
    return img[:, :, :3]


def _imwrite(path, img):
    ext = (Path(path).suffix or ".jpg").lower()
    # 品質參數只對 JPEG 有意義。傳給 PNG 的話 OpenCV 會印一行看不懂的警告，
    # 而且會讓人以為 PNG 也被壓過。
    params = [int(cv2.IMWRITE_JPEG_QUALITY), 88] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if ok:
        buf.tofile(str(path))
    return ok


def _binarize(gray):
    """自適應二值化，回傳「墨水 = True」的布林圖。

    用 adaptive 而不是全域 Otsu，因為手機拍的譜常有一半亮一半暗，
    全域門檻會把暗的那半整片當成墨水。
    """
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 10
    )
    return binary > 0


def _runs(flat):
    """一維 run-length 編碼，回傳 (每段的值, 每段的長度)。"""
    if flat.size == 0:
        return np.empty(0, bool), np.empty(0, int)
    change = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [flat.size]))
    return flat[starts], ends - starts


def estimate_interline(ink, sample_step=3):
    """估計五線譜的行距與譜線粗細。

    做法是 OMR 的標準手法：沿著垂直方向數黑白交替的長度，
    黑段長度的眾數就是譜線粗細、白段長度的眾數就是譜線之間的空隙。
    整頁大部分的垂直掃描線都會穿過五線譜，所以眾數很穩，
    不受標題文字、歌詞、汙漬影響。

    回傳 (行距, 譜線粗細)，估不出來時回 (0, 0)。
    """
    h, w = ink.shape
    if h < 20 or w < 20:
        return 0.0, 0.0

    cols = ink[:, ::sample_step]
    # 每一欄尾端補一格白，避免相鄰欄的黑段在攤平後被接成同一段
    padded = np.vstack([cols, np.zeros((1, cols.shape[1]), bool)])
    flat = padded.T.reshape(-1)
    values, lengths = _runs(flat)

    black = lengths[values]
    white = lengths[~values]
    # 太長的白段是空白區域、太長的黑段是實心色塊，都不是譜線
    black = black[(black >= 1) & (black <= max(3, h // 50))]
    white = white[(white >= 2) & (white <= max(6, h // 25))]
    if black.size < 50 or white.size < 50:
        return 0.0, 0.0

    line_thickness = float(np.bincount(black).argmax())
    space = float(np.bincount(white).argmax())
    if space <= 0:
        return 0.0, 0.0
    # 行距 = 一條線 + 一個空隙，也就是相鄰兩條譜線中心的距離
    return space + line_thickness, line_thickness


# ---------------------------------------------------------------------------
# 各項指標
# ---------------------------------------------------------------------------

def measure_blur(gray, interline, ink=None):
    """清晰度：Laplacian 變異數，但做了兩件必要的修正。

    **一、先把圖縮到固定行距再量。** 直接量原圖的話，同一張譜拍得越大數值越高，
    門檻就沒有可比性 —— 這是網路上大部分模糊偵測範例的通病。縮放到
    interline=10px 之後，量到的才是「相對於音符大小」的銳利度。

    **二、扣掉雜訊底線。** Laplacian 變異數對高斯雜訊極度敏感：ISO 拉高的顆粒
    本身就是滿滿的高頻，量出來的數字會跟「對焦很準」一模一樣。這不是理論疑慮，
    是實測到的 —— 用模擬照片跑出來，一張辨識率只有 10% 的糊照片量到 250，
    比一張辨識率 100% 的清楚照片（207）還高，門檻等於在擲骰子。

    修法：在**空白紙面**（沒有墨水的地方）另外量一次。那裡沒有任何該有的細節，
    量到的全部是雜訊，扣掉之後剩下的才是真正來自筆畫邊緣的高頻。
    """
    if interline <= 0:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    scale = NORM_INTERLINE / interline
    if scale < 1.0:
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        small_ink = (None if ink is None else
                     cv2.resize(ink.astype(np.uint8), (small.shape[1], small.shape[0]),
                                interpolation=cv2.INTER_NEAREST) > 0)
    else:
        # 放大不會生出細節，量出來會虛高，所以不放大，直接如實回報偏低
        small, small_ink = gray, ink

    lap = cv2.Laplacian(small, cv2.CV_64F)
    total = float(lap.var())
    floor = _noise_floor(lap, small_ink)
    return max(0.0, total - floor)


def _noise_floor(lap, ink):
    """空白紙面的 Laplacian 變異數 —— 也就是這張照片的雜訊有多大。

    「空白」的定義是：以 1 個譜線間距為半徑膨脹墨水之後仍然碰不到的地方。
    留這圈餘裕是因為筆畫旁邊的過渡帶還帶著真實細節，算進去會高估雜訊。
    找不到夠大的空白區（整頁塞滿）就回 0，寧可不修也不要亂修。
    """
    if ink is None:
        return 0.0
    grown = cv2.dilate(ink.astype(np.uint8), np.ones((7, 7), np.uint8), iterations=2) > 0
    blank = ~grown
    if blank.sum() < max(2000, 0.02 * blank.size):
        return 0.0
    return float(lap[blank].var())


def _horizontal_lines(ink, interline):
    """只留下夠長的水平筆畫 —— 也就是譜線。"""
    width = max(15, int(interline * 8))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width, 1))
    opened = cv2.morphologyEx(ink.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
    return opened > 0


def detect_quarter_turn(ink):
    """樂譜是不是橫躺的。回傳要順時針轉幾度才擺正（0 或 90）。

    **`measure_skew()` 看不出這件事** —— 它的簽章就寫著 `max_deg=20.0`，
    只在 ±20 度內搜尋，90 度完全在範圍外，只會回傳一個貼著上限的無意義值。
    實測〈失敗圖片/切割2.jpg〉是整頁躺著的，`measure_skew()` 回報 -21.2 度，
    然後 Gate A 拿這個數字判斷、preprocess 拿它去「轉正」—— 全都建立在錯的前提上。

    手機直握拍橫向樂譜是**極常見**的情況，所以這不是邊緣案例。

    判準用行距，不用譜線密度：`estimate_interline()` 是沿垂直方向數黑白交替，
    譜線擺正時它量到的是「譜線之間的空隙」（穩定的十幾到三十幾像素）；
    躺著的時候垂直掃描是**沿著譜線走**，量到的是雜訊，值會小很多。

        切割1  原圖 31 / 轉 90 度 8    -> 原圖是對的
        切割2  原圖  8 / 轉 90 度 29   -> 躺著
        切割3  原圖 11 / 轉 90 度 35   -> 躺著

    差距不明顯時一律回 0 —— 轉錯比不轉更糟。
    """
    # 用 np.rot90 而不是 cv2.rotate：`_binarize()` 回傳的是 **bool** 陣列，
    # 而 cv2 對 bool 與 uint8 會給出不同的結果（實測切割2：bool 量到 29、
    # uint8 量到 7），OpenCV 本來就不保證支援 bool。numpy 沒有這個問題。
    rotated = np.ascontiguousarray(np.rot90(ink, k=-1))
    upright, _ = estimate_interline(ink)
    turned, _ = estimate_interline(rotated)
    if not turned or turned < INTERLINE_TURN_MIN:
        return 0

    # **兩個條件都要成立。** 只看行距會誤判：〈うまぴょい伝説〉是正立的，
    # 轉 90 度之後行距反而從 21 變 36（比值 1.71），因為垂直方向的符桿間距
    # 剛好也很規律。
    #
    # 第二個訊號沒有這個問題：譜線在正立時是**水平**的，所以轉 90 度應該
    # 讓水平長線大幅減少。實測分得很開：
    #
    #     躺著的  切割2 1.64、切割3 1.30      （轉了才出現水平線）
    #     正立的  うまぴょい 0.11、示範 0.00、蕭邦 0.04、Rush E 0.13
    if upright and turned < upright * QUARTER_TURN_RATIO:
        return 0

    before = float(_horizontal_lines(ink, upright or 10).sum())
    after = float(_horizontal_lines(rotated, turned).sum())
    if before <= 0:
        return 90 if after > 0 else 0
    return 90 if after / before >= HORIZONTAL_TURN_RATIO else 0


def measure_skew(ink, max_deg=20.0):
    """傾斜角：轉一轉看哪個角度讓水平投影最尖銳。

    譜線擺正時，同一條線的像素會全部落在同一列，投影剖面出現又高又窄的尖峰，
    變異數最大。用這個比 Hough 穩，因為譜線本來就是整頁最強的水平結構。

    刻意吃原始的墨水圖、不吃「水平開運算」後的圖 —— 譜歪掉的時候水平核心
    根本留不住斜的譜線，用那張圖量會永遠得到 0 度。
    """
    h, w = ink.shape
    # 解析度直接決定角度精度：縮太小的話，1 度以內的差異在投影上就分辨不出來
    scale = min(1.0, 1600 / max(w, h))
    small = cv2.resize(ink.astype(np.uint8) * 255, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    center = (small.shape[1] / 2, small.shape[0] / 2)

    def score_at(angle):
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(small, matrix, (small.shape[1], small.shape[0]),
                                 flags=cv2.INTER_NEAREST, borderValue=0)
        profile = rotated.sum(axis=1).astype(np.float64)
        return float(profile.var())

    best_angle, best_score = 0.0, -1.0
    # 由粗到細三輪：先 1 度掃全域，再逐步縮小範圍換取精度
    for step, span in ((1.0, max_deg), (0.2, 1.0), (0.05, 0.2)):
        low, high = best_angle - span, best_angle + span
        if best_score < 0:
            low, high = -max_deg, max_deg
        for angle in np.arange(low, high + step / 2, step):
            s = score_at(float(angle))
            if s > best_score:
                best_score, best_angle = s, float(angle)
    return round(best_angle, 2)


def detect_staves(lines_img, interline):
    """找出五線譜。回傳 (譜表數, 橫向覆蓋率, 譜線 y 座標清單)。

    先把水平筆畫壓成每一列的墨水量，取出峰值當作候選譜線，
    再依「間距接近 interline」把連續五條併成一個譜表。
    """
    h, w = lines_img.shape
    profile = lines_img.sum(axis=1).astype(np.float64)
    if profile.max() <= 0:
        return 0, 0.0, []

    threshold = max(w * 0.25, profile.max() * 0.35)
    rows = profile >= threshold
    values, lengths = _runs(rows)

    centers, coverage = [], []
    pos = 0
    for value, length in zip(values, lengths):
        if value and length <= max(3, interline * 0.8):
            band = slice(pos, pos + length)
            centers.append(pos + length / 2)
            coverage.append(float(lines_img[band].sum()) / (length * w))
        pos += length

    if not centers:
        return 0, 0.0, []

    # 五條一組：間距落在 interline 附近就算同一個譜表
    staves, group = [], [centers[0]]
    tolerance = max(2.0, interline * 0.5)
    for prev, cur in zip(centers, centers[1:]):
        if abs((cur - prev) - interline) <= tolerance:
            group.append(cur)
        else:
            staves.append(group)
            group = [cur]
    staves.append(group)

    full = [g for g in staves if len(g) >= 5]
    return len(full), float(np.mean(coverage)) if coverage else 0.0, centers


def measure_perspective(ink, interline):
    """透視變形：比較左右三分之一的行距。

    從斜上方拍的話，近端的譜比遠端大，行距就會左右不一致。
    """
    if interline <= 0:
        return 1.0
    w = ink.shape[1]
    left, _ = estimate_interline(ink[:, : w // 3])
    right, _ = estimate_interline(ink[:, -(w // 3):])
    if left <= 0 or right <= 0:
        return 1.0
    return max(left, right) / min(left, right)


def deskew(ink, angle):
    """把墨水圖轉正。譜線擺正之後才找得到譜表。"""
    if abs(angle) < 0.2:
        return ink
    h, w = ink.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(ink.astype(np.uint8), matrix, (w, h),
                             flags=cv2.INTER_NEAREST, borderValue=0)
    return rotated > 0


def _rotate_gray(gray, angle):
    """把灰階圖轉正。邊界補白，才不會讓黑邊被當成暗部截斷。"""
    if abs(angle) < 0.2:
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)


def measure_lighting(gray, ink, staff_lines, interline, blocks=80):
    """回傳 (死黑比例, 亮度中位數, 反光比例)。

    這裡刻意**不**把「純白像素」當成過曝 —— 樂譜本來就是白紙，一張曝光完美的
    譜有大半畫面是 255，那不是問題。真正會害到辨識的是兩件事：

      死黑：暗部細節整片壓成 0，那裡的譜線再也救不回來
      反光：燈光把譜線沖斷了

    反光的量法是直接沿著每一條偵測到的譜線走，看有沒有「這裡應該有線、卻又白又
    空」的區段。只看譜線的實際起訖範圍，所以頁面左右的空白邊界不會被誤判 ——
    那裡本來就沒有線，白也是應該的。
    """
    dark_clip = float((gray <= 2).sum()) / gray.size
    median = float(np.median(gray))

    if not staff_lines or interline <= 0:
        return dark_clip, median, 0.0

    h, w = gray.shape
    half = max(1, int(round(interline * 0.5)))
    step = max(4, w // blocks)
    washed = total = 0

    for y in staff_lines:
        y0, y1 = max(0, int(y) - half), min(h, int(y) + half + 1)
        if y1 - y0 < 1:
            continue
        band_ink = ink[y0:y1]
        band_gray = gray[y0:y1]
        present = band_ink.any(axis=0)
        xs = np.flatnonzero(present)
        if xs.size < 2:
            continue
        # 只檢查這條線實際存在的範圍，中間才有「應該有線卻沒有」可言
        for x in range(xs[0], xs[-1] - step, step):
            total += 1
            seg_ink = band_ink[:, x: x + step]
            if seg_ink.mean() < 0.02 and band_gray[:, x: x + step].mean() > 235:
                washed += 1

    return dark_clip, median, (washed / total) if total else 0.0


def content_bbox(ink):
    """內容的外框。用來判斷譜有沒有被切到。"""
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])
