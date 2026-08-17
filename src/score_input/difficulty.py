"""難度分級。

同一份譜可以按難度拆出不同的練習／遊玩內容：

    難度 1　只有右手（旋律）
    難度 2　右手 + 左手

**MusicXML 永遠保留完整內容**，難度只在取用的時候篩選。這樣譜只有一份、
不會有「哪一份才是最新的」問題，之後要加難度 3（例如把和弦簡化成單音、
去掉裝飾音）也只要在這個檔案裡多加一個 filter，其他程式都不用動。

分級同時給兩邊用：
  - Unity 音遊：每個難度產一份 chart JSON
  - 評分 pipeline：run.py analyze --level 1 就能只練右手也拿到準確評分
"""

from collections import Counter

import numpy as np

RIGHT_STAFF = 1     # MusicXML 慣例：staff 1 是上方譜表（右手）
LEFT_STAFF = 2

# 判斷「單一譜表其實是被壓扁的大譜表」用的兩個條件。
#
# 音域寬度單獨看**不管用**：實測單行旋律 29–32 半音，而 Alkan 的大譜表也才 32。
# 真正分得開的是「有沒有同時響、又離很遠的音」—— 大譜表的低音部與旋律天生就
# 隔著一個八度以上，單行旋律則永遠只有一個音在響：
#
#     士兵進行曲（大譜表被壓成一行）  音域 43   寬音程 19.7%
#     透明背景 1-4（單行旋律）        音域 15-32 寬音程  0.0%
#     André / Alkan（有 staff 可對照）音域 48/32 寬音程 27.1% / 100%
#
# 0% 對上 19.7%，中間空得很乾淨，門檻放 10% 兩邊都有很大餘裕。
GRAND_STAFF_SPAN = 24           # 基本的合理性檢查，不是主要判準
WIDE_INTERVAL = 19              # 半音。一個八度加五度，超過就不是同一隻手彈的
WIDE_RATIO = 0.10               # 有多少比例的發音時刻出現這種寬音程

# 一隻手最多張得開幾個半音。超過就表示這幾個音不可能是同一隻手彈的，
# 拿來檢查「分手分得對不對」。
HAND_SPAN = 12

# 單一譜表佔到這個比例以上，就當作「大譜表被壓扁、左右手資訊沒留下來」。
# 不是 100%：辨識引擎常常零星丟幾個音到另一行，那不算真的分出手來。
COLLAPSED_RATIO = 0.90

# 音域超過這麼多半音，就不可能是單一樂器的旋律線（長笛/人聲頂多三個八度）。
# 實測：單行旋律譜 29–32、被壓扁的大譜表 43–51，中間空得很開。
SINGLE_LINE_MAX_SPAN = 40

LEVELS = {
    1: {"name": "只有右手", "staves": (RIGHT_STAFF,)},
    2: {"name": "右手 + 左手", "staves": None},   # None = 全部
}

DEFAULT_LEVEL = max(LEVELS)


def level_name(level):
    return LEVELS.get(level, {}).get("name", f"難度 {level}")


def note_array_with_staff(score):
    """取 note array 並盡量帶上 staff 欄位。

    partitura 預設不放 staff，要另外要求；從 MIDI 載入的譜則根本沒有譜表概念，
    這種情況全部當成右手（也就是只有難度 2 可用），不要假裝分得出左右手。
    """
    try:
        note_array = score.note_array(include_staff=True)
    except (TypeError, ValueError, KeyError):
        note_array = score.note_array()

    if "staff" in (note_array.dtype.names or ()):
        return note_array

    from numpy.lib import recfunctions as rfn

    return rfn.append_fields(
        note_array, "staff",
        np.full(len(note_array), RIGHT_STAFF, dtype=np.int32), usemask=False,
    )


def collapsed_grand_staff(note_array):
    """辨識引擎是不是把大譜表壓成了單一譜表（左右手資訊整個不見）。

    **不要在這種時候硬猜左右手。** 左右手是譜上寫的：畫在下面那行就是左手，
    跟音高高低無關（上面那行中途換成低音譜號的情況很常見，那些音仍然是右手彈）。

    試過兩種猜法，兩種都不能用，實測〈士兵進行曲〉：

        照音高切（中央 C）   13 個時刻同一隻手要同時按超過八度，最寬跨 31 個半音
        照譜號切             54 個 —— 更糟，而且兩種譜號的音高中位數都是 66，
                             表示 homr 標的譜號根本沒有跟著上下行走

    兩隻手的音域本來就重疊：左手彈 [60, 67] 的和弦時，照音高切會把它跟右手的
    旋律 76 全部歸成右手，變成一隻手要同時按 60、67、76。**那種難度 1 是彈不了的。**

    所以這裡只負責「認出資訊不見了」，讓上層誠實地說沒有左右手，
    而不是生一份人類做不到的譜。
    """
    if len(note_array) == 0:
        return False

    counts = Counter(int(s) for s in note_array["staff"])
    biggest = max(counts.values())
    # 判準是「某一個譜表佔了絕大多數」，不是「只有一個譜表」。
    # 舊版寫成 staves == {1}，結果 Bach 平均律有 29 個音落在 staff 2（全曲 1549 個），
    # 檢查就沒觸發，照樣產出一份裝了 98% 音符的「難度 1」—— 等於完全沒分手。
    if biggest / len(note_array) < COLLAPSED_RATIO:
        return False
    return _looks_like_grand_staff(note_array)


def _looks_like_grand_staff(note_array):
    """這些音是不是原本畫在兩行上的。

    兩個各自獨立的證據，**任一個成立就算**：

      音域太寬    單一樂器的旋律線跨不了那麼廣（長笛、人聲頂多三個八度）
      寬音程比例  同時響、又隔超過一個八度加五度 —— 那是兩隻手

    只看寬音程會漏掉賦格：Bach 平均律的兩隻手在音域上是交錯的，
    同時出現大音程的比例只有 4.7%，但它的音域有 51 個半音，
    任何單行樂器都寫不出來。實測三份「單一譜表佔九成以上」的譜音域都 ≥43，
    而真正的單行旋律譜是 29–32，中間空得很開。
    """
    pitches = note_array["pitch"].astype(int)
    span = int(pitches.max()) - int(pitches.min())
    if span < GRAND_STAFF_SPAN:
        return False
    if span >= SINGLE_LINE_MAX_SPAN:
        return True

    onsets = note_array["onset_beat"]
    wide = total = 0
    for onset in np.unique(onsets):
        group = pitches[onsets == onset]
        total += 1
        if group.size >= 2 and int(group.max() - group.min()) >= WIDE_INTERVAL:
            wide += 1
    return total > 0 and wide / total >= WIDE_RATIO


def unplayable_reaches(note_array, staves=None):
    """找出「同一隻手要同時按超過一個手掌張得開的距離」的時刻。

    分手分錯的時候，這是最直接看得出來的症狀 —— 使用者就是這樣發現的：
    「士兵進行曲有一些地方人類的右手根本按不到」。
    """
    if "staff" not in (note_array.dtype.names or ()):
        return []
    pitches = note_array["pitch"].astype(int)
    onsets = np.round(note_array["onset_beat"].astype(float), 4)
    hands = note_array["staff"].astype(int)

    bad = []
    for onset in np.unique(onsets):
        for hand in np.unique(hands):
            mask = (onsets == onset) & (hands == hand)
            if mask.sum() < 2:
                continue
            group = pitches[mask]
            span = int(group.max() - group.min())
            if span > HAND_SPAN:
                bad.append({"beat": float(onset), "staff": int(hand),
                            "span": span, "pitches": sorted(int(p) for p in group)})
    return bad


def levels_available(note_array):
    """這份譜實際能產出哪幾個難度。

    只有右手的譜（例如只寫了 R: 的記譜檔）產不出難度 2 —— 硬產一份跟難度 1
    一模一樣的檔案只會讓使用者困惑。
    """
    if "staff" not in (note_array.dtype.names or ()):
        return [DEFAULT_LEVEL]
    staves = set(int(s) for s in note_array["staff"])
    if staves <= {RIGHT_STAFF}:
        return [DEFAULT_LEVEL]
    # 名義上有兩行、實際上幾乎全擠在同一行時也不能給難度 1 ——
    # 那份「只有右手」會裝著全曲 98% 的音，比沒有還糟。
    if collapsed_grand_staff(note_array):
        return [DEFAULT_LEVEL]
    return sorted(LEVELS)


def filter_by_level(note_array, level):
    """篩出這個難度要用的音符。"""
    if level not in LEVELS:
        raise ValueError(f"沒有難度 {level}（可用 {sorted(LEVELS)}）")
    staves = LEVELS[level]["staves"]
    if staves is None or "staff" not in (note_array.dtype.names or ()):
        return note_array

    mask = np.isin(note_array["staff"], staves)
    if not mask.any():
        raise ValueError(
            f"這份譜在難度 {level}（{level_name(level)}）之下一個音符都不剩。"
            f"確認譜裡真的有 staff {staves} 的內容。"
        )
    return note_array[mask]


def hand_of(staff):
    return "R" if int(staff) == RIGHT_STAFF else "L"
