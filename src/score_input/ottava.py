"""從影像找出 8va / 8vb 八度記號。

從 `layout.py` 拆出來的。它跟「切小節」共用譜線分群（`staves.py`），但目的
完全不同：切小節是為了在照片上圈位置，這裡是為了**把音高改對** ——
homr 完全不產生 `<octave-shift>`，整段 8va 的音就這樣低了一個八度。
〈李斯特 鐘〉13 頁上印了 48 條，幾乎每一行右手都有，那正是使用者說
「狀況奇差」聽起來的樣子。

**不能靠 OCR。** 8va 是斜體加上標的花體字，rapidocr 在李斯特那 13 頁上
一個都沒讀到（整頁只認出 22 個文字塊，沒有一個是 8va）。但它後面拖的那條
**虛線**在影像上非常好認：一長串等高、等寬、等距的小橫槓，而且畫在譜表外面。
"""

import cv2
import numpy as np

from .imaging import _binarize, _imread, deskew, estimate_interline, measure_skew
from .staves import _group_staves, _group_systems, _staff_line_rows

# 虛線的形狀。單位一律是行距。
OTTAVA_DASH_MAX_HEIGHT = 0.35   # 橫槓多高才還算是「線」而不是符尾或連桿
OTTAVA_DASH_MIN_WIDTH = 0.40    # 太短的是點、附點、休止符的殘塊
OTTAVA_DASH_MAX_WIDTH = 1.60    # 超過就是譜線、連桿或字，不是虛線的一節
# 一節要夠扁。斷奏點跟附點也是又小又排成一列（李斯特那頁音符上方就是一排斷奏點），
# 光看大小分不開；但點大約 1:1，虛線的一節實測 17x5 ≈ 3.4:1。
OTTAVA_DASH_MIN_ASPECT = 2.2
OTTAVA_DASH_MAX_GAP = 2.20      # 兩節之間空多少還算同一條線
OTTAVA_ROW_TOLERANCE = 0.30     # 同一條線的高低差
OTTAVA_MIN_DASHES = 4           # 至少要這麼多節，才不會把兩三個雜點串成線
OTTAVA_MIN_SPAN = 4.0           # 整條線至少要這麼長
OTTAVA_MAX_DISTANCE = 14.0      # 離目標譜表最多這麼遠

# 線的左端要有「8va」那個字，否則不算。這是擋掉 (accel) / (rit.) 那類
# **一模一樣的虛線**唯一有效的辦法 —— 它們的線跟 8va 的線在影像上分不出來。
#
# 量出來的字形非常一致（李斯特 13 頁，行距 21）：8va 是**一個**連通元件，
# 1.52 x 1.90 個行距，頂端固定在線上方 0.43–0.48 個行距 —— 斜體粗 8 跟上標
# 的 va 是連在一起的。(accel) 那類的最高元件只有 0.71x1.67（小寫字母的上伸部），
# 12 個樣本兩群完全不重疊。
OTTAVA_GLYPH_MIN_WIDTH = 1.15
OTTAVA_GLYPH_MAX_WIDTH = 2.10
OTTAVA_GLYPH_MIN_HEIGHT = 1.45
OTTAVA_GLYPH_MAX_HEIGHT = 2.50
OTTAVA_GLYPH_MAX_TOP = 0.85      # 字的頂端跟線的高度差
OTTAVA_GLYPH_SEARCH = 3.6        # 往左找幾個行距


def find_ottava_lines(ink, interline):
    """找出 8va / 8vb 的虛線，回傳 [(y, x0, x1, 節數)]（轉正後的座標）。

    判準是「一長串等高的小橫槓」：

        高 < 0.35 行距     比符尾、連桿、符桿都細（實測一節是 17x5 px，行距 21）
        寬 0.4–1.6 行距    比點大、比譜線與連桿短
        寬/高 >= 2.2       擋掉斷奏點：它也排成一列，但大約 1:1
        同一列 ±0.3 行距   虛線是水平的
        間隔 < 2.2 行距    再遠就是兩條不同的線
        至少 4 節、跨 4 行距

    **譜線為什麼不會被誤認**：一條譜線是**一個**連通元件，橫跨整個系統，
    寬度遠超過 1.6 個行距，第一關就被擋掉。虛線則是一節一節分開的。
    """
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    dashes = []
    for i in range(1, count):
        x, y, w, h = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                      stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
        if h > interline * OTTAVA_DASH_MAX_HEIGHT:
            continue
        if not (interline * OTTAVA_DASH_MIN_WIDTH <= w <= interline * OTTAVA_DASH_MAX_WIDTH):
            continue
        if w < h * OTTAVA_DASH_MIN_ASPECT:
            continue                 # 太方 —— 是斷奏點或附點，不是虛線的一節
        dashes.append((y + h / 2.0, float(x), float(x + w)))

    if len(dashes) < OTTAVA_MIN_DASHES:
        return []

    # 依高度分列，再在每一列裡依 x 串起來
    dashes.sort(key=lambda d: (d[0], d[1]))
    tolerance = interline * OTTAVA_ROW_TOLERANCE
    rows, current = [], [dashes[0]]
    for previous, dash in zip(dashes, dashes[1:]):
        if dash[0] - previous[0] <= tolerance:
            current.append(dash)
        else:
            rows.append(current)
            current = [dash]
    rows.append(current)

    max_gap = interline * OTTAVA_DASH_MAX_GAP
    lines = []
    for row in rows:
        row.sort(key=lambda d: d[1])
        run = [row[0]]
        for dash in row[1:]:
            if dash[1] - run[-1][2] <= max_gap:
                run.append(dash)
            else:
                lines.extend(_accept_ottava(run, interline))
                run = [dash]
        lines.extend(_accept_ottava(run, interline))
    return lines


def _accept_ottava(run, interline):
    if len(run) < OTTAVA_MIN_DASHES:
        return []
    x0, x1 = run[0][1], run[-1][2]
    if x1 - x0 < interline * OTTAVA_MIN_SPAN:
        return []
    y = sum(d[0] for d in run) / len(run)
    return [(y, x0, x1, len(run))]


def _hook_direction(ink, interline, y, x0, x1):
    """虛線末端的鉤是朝上還朝下。回傳 +1（朝下）、-1（朝上）、0（沒有鉤）。

    **鉤永遠指向它要修飾的那一行譜表** —— 這是製譜慣例，也是唯一不用讀字
    就分得出 8va（往上八度）與 8vb（往下八度）的訊號。而字是讀不到的：
    rapidocr 在李斯特那 13 頁上一個 8va 都沒認出來。

    找法是找**連通元件**，不是數某個窗口裡的墨水量。數墨水會被旁邊的東西
    汙染：實測第 2 條線末端上方 128、下方 131，幾乎一樣 —— 上面那 128
    是踏板記號的方括號，跟這條線一點關係都沒有。
    """
    reach = int(interline * 2.2)
    pad = int(interline * 0.8)
    top = max(0, int(y) - reach)
    bottom = min(ink.shape[0], int(y) + reach)

    # **只看右端。** 左端是「8va」那個字，不是鉤 —— 去那裡找一豎會挖到字的
    # 筆畫，實測李斯特第 8 頁那條就被判成「鉤朝上」，整段變成降八度。
    del x0
    left = max(0, int(x1) - pad)
    right = min(ink.shape[1], int(x1) + pad)
    window = ink[top:bottom, left:right].astype(np.uint8)
    if window.size == 0:
        return 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(window, 8)
    touch = interline * 0.35
    for i in range(1, count):
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        y0 = stats[i, cv2.CC_STAT_TOP] + top
        cx = stats[i, cv2.CC_STAT_LEFT] + w / 2.0 + left
        if h < interline * 0.7 or w > interline * 0.35:
            continue                          # 不是又細又高的一豎
        if abs(cx - x1) > interline * 0.6:
            continue                          # 不在線的末端
        # 鉤一定是**從線本身長出來的**，所以它必須碰到線的高度。
        # 少了這一條，末端窗口裡任何一根細長的筆畫（右邊界的小節線、旁邊的符桿）
        # 都會被當成鉤：實測李斯特第 8 頁那條就被判成「鉤朝上」而整段降八度。
        if not (y0 - touch <= y <= y0 + h + touch):
            continue
        return 1 if y0 + h / 2.0 > y else -1
    return 0


def detect_ottavas(image):
    """找出一頁上的八度記號，回傳 [{staff, shift, x0, x1, y}]（**原圖**座標）。

    staff 是這一頁**由上往下數第幾行譜表**（0 起算），呼叫端要自己換算成
    「第幾個系統的上/下行」。shift 是 +12 或 -12 個半音。

    判斷修飾哪一行的順序：

        1 線的左端要有「8va」那個字，沒有就不算（擋掉 (accel) 那類虛線）
        2 末端的鉤朝哪一邊，那一邊的那行譜表就是目標（鉤指向它）
        3 沒有鉤（線延續到下一個系統、或末端被裁掉）就取**下面**那一行、往上八度

    **不能用「最近的一行」**。系統之間的空白很寬，寫在下一個系統上方的 8va
    常常離上一個系統的低音譜表更近：實測李斯特第 1 頁那條 `8va scherzando`
    距上一行 5.4 個行距、距它真正要修飾的下一行 11.0 個 —— 只看距離會整段
    降八度，剛好反過來。
    """
    img = _imread(image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    interline, _ = estimate_interline(_binarize(gray))
    if interline <= 0:
        return []

    ink = _binarize(gray)
    skew = measure_skew(ink)
    straight = deskew(ink, skew)
    centers = _staff_line_rows(straight, interline)
    staves = _group_staves(centers, interline)
    if not staves:
        return []
    places = _staff_positions(centers, interline, len(staves))

    glyphs = _glyph_boxes(straight)
    back = cv2.getRotationMatrix2D((gray.shape[1] / 2, gray.shape[0] / 2), -skew, 1.0)
    found = []
    for y, x0, x1, _ in find_ottava_lines(straight, interline):
        glyph_x = _ottava_glyph_x(glyphs, interline, y, x0)
        if glyph_x is None:
            continue                # 沒有「8va」那個字 —— 多半是 (accel) 的虛線
        hook = _hook_direction(straight, interline, y, x0, x1)
        target = _ottava_target(staves, y, hook)
        if target is None:
            continue
        index, shift = target
        if abs(_staff_distance(staves[index], y)) > interline * OTTAVA_MAX_DISTANCE:
            continue
        # 目標譜表的中心 y 也一起換算回原圖 —— 對小節框的時候要用它，
        # 不能用「第幾個系統」：系統的編號是版面偵測那條路算的，跟這裡的
        # 譜表分群是兩套，對不起來的時候會把整頁的小節都圈進去。
        staff = staves[index]
        mid = (staff[0] + staff[-1]) / 2.0
        points = np.array([[[glyph_x, y], [x1, y], [x0, mid]]], dtype=np.float32)
        (ax, ay), (bx, by), (_, staff_y) = cv2.transform(points, back)[0]
        found.append({"staff": index, "part_staff": places[index] + 1,
                      "shift": shift, "hook": hook,
                      "x0": float(min(ax, bx)), "x1": float(max(ax, bx)),
                      "y": float((ay + by) / 2.0), "staff_y": float(staff_y)})
    return found


def _staff_positions(centers, interline, total):
    """每一行譜表在它那個系統裡排第幾（0 = 上行 = 右手 = MusicXML staff 1）。

    鋼琴譜是大譜表：**一頁的譜表數是偶數，而且兩行一組**，所以偶數的時候
    直接用 `index % 2` —— 那是最不會出錯的算法，也是最容易驗的。

    試過兩種更「聰明」的作法，兩種都會把右手判成左手：

        照小節框的上下半分   小節框的系統歸屬來自版面偵測那條路，
                             它把兩個系統併成一個的時候，下一個系統的高音譜表
                             就落到「下半」，整段左手被拉高一個八度
        照 `_group_systems`  同樣會把系統併掉（李斯特第 3、9、10、12 頁）

    譜表數是奇數（單行譜、或漏掉一行）才退回系統分群。
    """
    if total % 2 == 0:
        return [i % 2 for i in range(total)]

    places = []
    for system in _group_systems(centers, interline):
        for position, _ in enumerate(_group_staves(system, interline)):
            places.append(position)
    if len(places) != total:
        places = [0] * total          # 分不出來就一律當上行，不要亂猜左手
    return places


def ottava_spans(image, page):
    """八度記號 -> [{staff, shift, from, to}]，小節號用 `page` 裡算好的。

    `page` 是 `PageLayout.as_dict()`。

    每一條線只跟**罩住它那行譜表的那一個系統**比對，而且是用 y 座標去找那個
    系統，不是用系統編號 —— 編號是版面偵測那條路算的，跟這裡的譜表分群是兩套，
    對不起來就會把整頁的小節都圈進去。

    右手/左手則由 `detect_ottavas()` 自己的譜表分群決定，不看小節框 ——
    小節框的系統歸屬在某些頁面上會把兩個系統併成一個。
    """
    boxes = (page or {}).get("measures") or []
    if not boxes:
        return []

    by_system = {}
    for box in boxes:
        corners = box.get("corners") or []
        if not corners:
            continue
        xs = [float(c[0]) for c in corners]
        ys = [float(c[1]) for c in corners]
        by_system.setdefault(int(box["system"]), []).append(
            {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
             "n": int(box.get("number") or 0), "i": int(box["index"])})

    spans = []
    for mark in detect_ottavas(image):
        row = _system_covering(by_system, mark["staff_y"])
        if not row:
            continue
        # 中心落在線的範圍內就算被涵蓋。用中心不用重疊，是因為線的兩端常常
        # 只壓到隔壁小節的一點點邊 —— 那一小節其實不在八度記號裡面。
        covered = [b for b in row
                   if mark["x0"] <= (b["x0"] + b["x1"]) / 2 <= mark["x1"]]
        if not covered:
            continue
        numbers = [b["n"] for b in covered if b["n"]]
        spans.append({
            "staff": min(2, mark["part_staff"]), "shift": mark["shift"],
            # 頁內第幾格。套用的時候用這個 —— 印出來的號碼是給人看的，
            # 而合併檔的小節是照順序編的，兩者在辨識漏掉小節的頁面上會差幾格。
            "index_from": min(b["i"] for b in covered),
            "index_to": max(b["i"] for b in covered),
            "from": min(numbers) if numbers else 0,
            "to": max(numbers) if numbers else 0,
        })
    return spans


def _system_covering(by_system, y):
    """哪一個系統的框罩住這個 y。都沒罩到就取最近的那個。"""
    best, distance = None, None
    for row in by_system.values():
        top = min(b["y0"] for b in row)
        bottom = max(b["y1"] for b in row)
        if top <= y <= bottom:
            return row
        gap = top - y if y < top else y - bottom
        if distance is None or gap < distance:
            best, distance = row, gap
    return best


def _glyph_boxes(ink):
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    return [(stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
             stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
            for i in range(1, count)]


def _ottava_glyph_x(boxes, interline, y, x0):
    """線的左端那個「8va」的左緣 x。找不到就回 None（那就不是八度記號）。

    回傳的是**字的左緣**，不是虛線的起點：八度記號從字開始生效，而字寬將近
    兩個行距。用虛線的起點去對小節，一整個小節會被漏掉 —— 實測李斯特第 1 頁
    第 5 小節的 8va 就這樣少算了一格。
    """
    for bx, by, bw, bh in boxes:
        if not (x0 - interline * OTTAVA_GLYPH_SEARCH <= bx + bw / 2.0
                < x0 + interline * 0.3):
            continue
        if abs(by - y) > interline * OTTAVA_GLYPH_MAX_TOP:
            continue
        if not (interline * OTTAVA_GLYPH_MIN_WIDTH <= bw
                <= interline * OTTAVA_GLYPH_MAX_WIDTH):
            continue
        if interline * OTTAVA_GLYPH_MIN_HEIGHT <= bh <= interline * OTTAVA_GLYPH_MAX_HEIGHT:
            return float(bx)
    return None


def _staff_distance(staff, y):
    """y 在這行譜表上方是負的、下方是正的、在裡面是 0。"""
    if y < staff[0]:
        return y - staff[0]
    if y > staff[-1]:
        return y - staff[-1]
    return 0.0


def _ottava_target(staves, y, hook):
    """這條線修飾哪一行譜表、往哪個方向移。回傳 (譜表索引, ±12)。"""
    if hook > 0:                        # 鉤朝下 -> 目標在下面 -> 那一行要升八度
        below = [i for i, s in enumerate(staves) if s[0] > y]
        return (min(below), 12) if below else None
    if hook < 0:                        # 鉤朝上 -> 目標在上面 -> 那一行要降八度
        above = [i for i, s in enumerate(staves) if s[-1] < y]
        return (max(above), -12) if above else None

    # 沒有鉤（線延續到下一個系統、或末端被裁掉）就取**下面**那一行、往上八度。
    #
    # 不能取「最近的一行」：系統之間的空白很寬，而 8va 要畫在加線一大堆的音符
    # 上方，所以它常常離上一個系統的低音譜表比較近。實測李斯特第 8 頁那三條
    # 8va 離上一行 5.1 個行距、離它真正修飾的下一行 10.8 個 —— 取最近的會**全部
    # 反過來**變成降八度。
    #
    # 「下面那一行、往上」是慣例的常態：8va 寫在譜表上方，8vb 寫在下方而且
    # 幾乎都會畫鉤。猜錯的代價是一段高八度，跟現在完全不處理的代價一樣大，
    # 但猜對的機率高得多。
    below = [i for i, s in enumerate(staves) if s[0] > y]
    return (min(below), 12) if below else None
