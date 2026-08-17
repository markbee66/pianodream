"""譜線、譜表、系統、小節線 —— 版面偵測的幾何底層。

從 `layout.py` 拆出來的。這一層只回答「紙上有哪些線、它們怎麼分群」，
不碰小節編號、不碰座標換算，也不知道 `PageLayout` 長什麼樣。
`layout`（切小節）與 `ottava`（找八度記號的虛線）都建立在它上面。

## 為什麼小節線只能當輔助

`find_barlines()` 靠「夠長」跟符桿區分 —— 鋼琴大譜表的小節線高 15–16 個行距、
符桿只有 3.8，用 6 倍行距的垂直核心一開運算就乾淨了。但**單行譜的小節線只有
4 個行距高，會被同一個核心整條抹掉**：〈うまぴょい伝説〉180 個小節只找到 27 個。

試過把判準改成「垂直筆畫兩端貼齊譜表」，失敗 —— 符桿長 3.5 個行距，從底線往上
剛好逼近頂線，一樣符合，André 第 1 頁反而從 47 個小節暴增到 93 個。
**純靠幾何分不開符桿與單行譜的小節線。**

所以 `system_frames()` 以**譜線**為骨架，小節線只塞進去負責細切。
"""

import cv2
import numpy as np

# 小節線至少要這麼高（單位：行距）。實測鋼琴譜小節線約 15–16 個行距、
# 符桿只有 3.8 個，取 6 就能乾淨分開而且容得下單行譜表的情況
BARLINE_MIN_INTERLINE = 6.0
# 小節線至少要有「全頁最高那一群」的這個比例。用來擋掉整群都是符桿的假系統
SYSTEM_MIN_RATIO = 0.70
# 系統內的高度門檻（相對於該系統最高的那條線）
BARLINE_HEIGHT_RATIO = 0.75
# 兩條小節線靠得比這個近就當成同一條（重複記號是雙線、有時還加粗）
BARLINE_MERGE_INTERLINE = 1.6


def _staff_line_rows(ink, interline):
    """回傳偵測到的譜線 y 座標（轉正後的座標系）。"""
    width = max(15, int(interline * 8))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width, 1))
    opened = cv2.morphologyEx(ink.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel) > 0

    h, w = opened.shape
    profile = opened.sum(axis=1).astype(np.float64)
    if profile.max() <= 0:
        return []

    threshold = max(w * 0.25, profile.max() * 0.35)
    rows = profile >= threshold

    centers = []
    start = None
    for y in range(h + 1):
        on = y < h and rows[y]
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start <= max(3, interline * 0.8):
                centers.append((start + y - 1) / 2.0)
            start = None
    return centers


def _group_staves(centers, interline):
    """先把譜線五條一組併成「譜表」。譜表內的線距就是行距。"""
    if not centers:
        return []
    staves, current = [], [centers[0]]
    for prev, cur in zip(centers, centers[1:]):
        if cur - prev > interline * 1.8:
            staves.append(current)
            current = [cur]
        else:
            current.append(cur)
    staves.append(current)
    # 少於 4 條的是雜訊（五線譜有時會有一條沒被偵測到，所以放寬到 4）
    return [s for s in staves if len(s) >= 4]


def _group_systems(centers, interline):
    """把譜表併成「系統」（鋼琴譜一個系統是上下兩行的大譜表）。

    不能用固定倍率當門檻 —— 排版鬆緊差很多，寫死的話不是全部併成一個、
    就是每個譜表各自獨立。改成**從這一頁自己的間距分布找切點**：
    譜表之間的間距會明顯分成兩群（系統內 / 系統間），取兩群之間最大的落差當界線。
    """
    staves = _group_staves(centers, interline)
    if len(staves) <= 1:
        return [sum(staves, [])]

    gaps = [staves[i + 1][0] - staves[i][-1] for i in range(len(staves) - 1)]
    ordered = sorted(gaps)
    # 找排序後相鄰間距的最大跳躍，那就是兩群的分界
    jump, threshold = 0.0, max(gaps) + 1.0
    for a, b in zip(ordered, ordered[1:]):
        if b - a > jump:
            jump, threshold = b - a, (a + b) / 2.0

    # 落差不明顯（例如整頁只有單行譜表）就每個譜表各自成一個系統
    if jump < interline * 1.5:
        return [s for s in staves]

    systems, current = [], list(staves[0])
    for gap, staff in zip(gaps, staves[1:]):
        if gap > threshold:
            systems.append(current)
            current = list(staff)
        else:
            current.extend(staff)
    systems.append(current)
    return [s for s in systems if len(s) >= 4]


def find_barlines(ink, interline):
    """找出整頁的小節線，回傳 [(x, y_top, y_bottom), ...]。

    **靠長度跟符桿區分**：實測鋼琴譜的小節線高約 15–16 個行距（貫穿大譜表的
    上下兩行），符桿只有 3.8 個行距。用 6 倍行距的垂直核心做開運算，
    符桿會被完全清掉，剩下的細長直線就是小節線。

    這比「先分系統再找小節線」可靠得多 —— 系統的分界本來就是靠小節線界定的，
    先分系統等於用比較弱的訊號去推比較強的訊號。

    ⚠ **已知限制：只對大譜表（雙行）成立，單行譜會整個失效。**
    單行譜的小節線只有 4 個行距高，6 倍的核心會把它整條抹掉，
    實測〈うまぴょい伝説〉180 個小節只找到 27 個框。
    試過改成「兩端貼齊譜表」的判準，結果符桿混進來、系統也被切碎
    （André 第 1 頁從 47 個小節變成 93 個），比現在更糟，所以退回這一版。
    要修得對，得先能可靠地分辨符頭 —— 見 `工作紀錄.md` 2026-08-13。
    """
    kernel_h = max(5, int(interline * BARLINE_MIN_INTERLINE))
    opened = cv2.morphologyEx(
        ink.astype(np.uint8) * 255, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    )

    count, _, stats, _ = cv2.connectedComponentsWithStats((opened > 0).astype(np.uint8), 8)
    candidates = []
    for i in range(1, count):
        x, y, w, h = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                      stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
        if w > interline * 1.2:      # 太寬的是實心色塊，不是線
            continue
        candidates.append((x + w / 2.0, float(y), float(y + h), float(h)))

    candidates.sort(key=lambda c: (c[1], c[0]))
    return candidates


def _systems_from_barlines(candidates, interline):
    """把候選直線依垂直重疊分群成系統，再在**每個系統內**篩出真正的小節線。

    高度門檻要一個系統一個系統地算，不能整頁共用：同一頁的系統高度可能不一樣
    （例如譜號中途從低音變高音，那一行就比較矮），全頁一個門檻會讓矮的系統
    整行的小節線都被濾掉。
    """
    if not candidates:
        return []

    groups = []
    for x, top, bottom, height in candidates:
        placed = False
        for g in groups:
            overlap = min(bottom, g["bottom"]) - max(top, g["top"])
            # 用比較短的那一個當分母：系統內的符桿也要能歸進所屬的系統，
            # 這樣算門檻時才知道這個系統實際上有多高
            if overlap > min(height, g["bottom"] - g["top"]) * 0.5:
                g["items"].append((x, height))
                g["top"] = min(g["top"], top)
                g["bottom"] = max(g["bottom"], bottom)
                placed = True
                break
        if not placed:
            groups.append({"top": top, "bottom": bottom, "items": [(x, height)]})

    groups.sort(key=lambda g: g["top"])
    merge = max(2.0, interline * BARLINE_MERGE_INTERLINE)

    # 頁面層級的參考高度：真正的小節線都貫穿整個系統，是全頁最高的那一群。
    # 沒有這道下限的話，只有符桿、完全沒有小節線的群組會拿自己最高的符桿
    # 當基準，門檻就形同虛設，整群符桿都會被當成小節線。
    page_reference = float(np.percentile([h for _, _, _, h in candidates], 95))
    floor = page_reference * SYSTEM_MIN_RATIO

    systems = []
    for g in groups:
        reference = max(h for _, h in g["items"])
        if reference < floor:
            continue            # 這一群裡根本沒有貫穿系統的線，不是系統
        threshold = max(interline * BARLINE_MIN_INTERLINE,
                        reference * BARLINE_HEIGHT_RATIO, floor)
        bars = _merge_close(sorted(x for x, h in g["items"] if h >= threshold), merge)
        if len(bars) >= 2:      # 至少兩條線才切得出小節
            systems.append({"top": g["top"], "bottom": g["bottom"], "bars": bars})
    return systems


def _merge_close(values, tolerance):
    """把靠得很近的 x 併成一條（小節線有粗細，重複記號是雙線）。"""
    if not values:
        return []
    out, group = [], [values[0]]
    for prev, cur in zip(values, values[1:]):
        if cur - prev <= tolerance:
            group.append(cur)
        else:
            out.append(float(np.mean(group)))
            group = [cur]
    out.append(float(np.mean(group)))
    return out


def system_frames(straight, interline):
    """回傳 [{top, bottom, bars}] —— **以譜線為骨架**，小節線只塞進去負責細切。

    以前是反過來的（`_systems_from_barlines` 直接定義系統），單行譜的小節線
    找不到就等於整頁沒有系統。譜線偵測不受那個問題影響，所以拿它當骨架穩得多。
    """
    centers = _staff_line_rows(straight, interline)
    groups = [g for g in (_group_systems(centers, interline) if centers else []) if g]
    frames = [{"top": min(g), "bottom": max(g), "bars": []} for g in groups]

    bar_systems = _systems_from_barlines(find_barlines(straight, interline), interline)
    if not frames:
        # 連譜線都找不到，只好退回舊路徑
        return [{"top": s["top"], "bottom": s["bottom"], "bars": list(s["bars"])}
                for s in bar_systems]

    # **用小節線把大譜表接回去。** 譜線分群看的是行距，鋼琴大譜表上下兩行之間的
    # 間隔未必比行間大多少，`_group_systems` 常常把它們拆成兩個；但小節線是**貫穿
    # 上下兩行**的，一條線跨到哪就表示那幾行是同一個系統。
    # 不做這一步的話 André 第 1 頁會從 8 個系統變成 14 個、小節從 47 掉到 31。
    for s in bar_systems:
        covered = [f for f in frames
                   if f["top"] >= s["top"] - interline and f["bottom"] <= s["bottom"] + interline]
        if len(covered) > 1:
            merged = {"top": min(f["top"] for f in covered),
                      "bottom": max(f["bottom"] for f in covered), "bars": []}
            frames = [f for f in frames if f not in covered] + [merged]
            frames.sort(key=lambda f: f["top"])

    for s in bar_systems:
        middle = (s["top"] + s["bottom"]) / 2.0
        best = min(frames, key=lambda f: abs((f["top"] + f["bottom"]) / 2.0 - middle))
        if best["top"] - interline * 2 <= middle <= best["bottom"] + interline * 2:
            best["bars"].extend(s["bars"])

    tolerance = max(2.0, interline * BARLINE_MERGE_INTERLINE)
    for frame in frames:
        frame["bars"] = _merge_close(sorted(frame["bars"]), tolerance)
    return frames
