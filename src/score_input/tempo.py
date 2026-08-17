"""偵測樂譜的速度（BPM），並串成整首的速度地圖。

音遊的音符要落在正確的時間點、評分要算對節奏，都需要知道曲子多快。
但 **homr 完全不輸出速度資訊** —— 產出的 MusicXML 裡沒有 `<sound tempo>`、
沒有 `<metronome>`、連 `<words>` 都沒有（實測三份譜都是 0）。

速度其實印在譜上，只是以「圖上的文字」存在，所以要用 OCR 去讀：

    節拍器記號   ♩ = 96      → 直接就是答案
    速度術語     Moderato    → 查表換算成大概的 BPM

兩個都找不到就**回報偵測失敗**，讓上層去問使用者。猜一個預設值丟給他，
音遊的譜面會整個對不上而使用者不知道為什麼 —— 不如老實說不知道。

判讀分兩層，各自成檔；名字在這裡原樣再匯出，呼叫端只要認得 `tempo` 就好：

    tempo_text.py   一段字串 -> BPM（術語表、節拍器正則、MusicXML 欄位）
    tempo_page.py   一張照片 -> 頁上所有的速度記號與漸變術語（OCR + 符頭判讀）

這個檔案負責把那些**點**串成整首的**線**：階梯（節拍器記號）、斜坡（漸快漸慢），
再加上一道「快到人類彈不出來就降下來」的守門。
"""

from pathlib import Path

from .tempo_page import (HALF_NOTE, HOLLOW_HEAD_FILL,  # noqa: F401
                         QUARTER_NOTE, _GRADUAL_WORDS, _measure_below,
                         _measure_boxes, beat_unit, from_image,
                         gradual_on_page, marks_on_page)
from .tempo_text import (BPM_MAX, BPM_MIN, TEMPO_MODIFIERS,  # noqa: F401
                         TEMPO_WORDS, TempoResult, _METRONOME, _sane,
                         from_musicxml, from_text)

DEFAULT_BPM = 100.0

# 斜坡結束時速度變成幾倍。只在後面找不到明確的節拍器記號時才用得到 ——
# 有記號的話終點速度就是那個記號，不必猜。
#
# 這個倍率是**推算的**，依坑 10 的原則不得提高信心，報告裡會標成「推算」。
GRADUAL_FACTOR = {"faster": 1.25, "slower": 0.80}

# 找不到下一個速度事件時，斜坡鋪多少小節。取 8 是因為漸快漸慢在譜上
# 通常是一到兩個樂句的事；鋪太長會讓整首後段都被一個記號帶著跑。
GRADUAL_SPAN = 8

# 一秒最多幾個音才彈得出來。人類鋼琴家的極限大約 12–16。
# 全 12 首校準（最密 8 秒視窗）：Rush E 39.8、李斯特 31.0、蕭邦 19.0、
# 拍照測試 17.2、うまぴょい 16.0（信心 1.00）、Andre 12.2。
# 訂 18 能擋住不可能的，又不碰已知良好的曲子。
MAX_NOTES_PER_SECOND = 18.0

# 只有**持續**這麼久的段落才降速。短暫的密集爆發是正常演奏的一部分。
# 這一條不可省：李斯特的速度地圖有大量單小節段落，逐段降速會產生鋸齒狀速度，
# 比不修更不連貫。
MIN_CAPPED_SECONDS = 4.0


# ---------------------------------------------------------------------------
# 速度地圖
# ---------------------------------------------------------------------------

def build_tempo_map(pages, default_bpm):
    """把各頁找到的記號串成 [(小節, BPM)]，依小節排序、去掉重複。

    第一段一定從第 1 小節開始 —— 開頭沒有記號的話就用 default_bpm 補上，
    不然前幾小節會沒有速度可用。
    """
    events = []
    for marks in pages:
        for mark in marks:
            if mark.get("measure"):
                events.append((int(mark["measure"]), float(mark["bpm"])))

    events.sort(key=lambda e: e[0])
    cleaned = []
    for measure, bpm in events:
        if cleaned and abs(cleaned[-1][1] - bpm) < 1e-6:
            continue                               # 跟上一段一樣，不是變速
        if cleaned and cleaned[-1][0] >= measure:
            # 兩個**不同**的速度不可能同一瞬間生效，所以這是小節定位的解析度不夠
            # （同一行裡靠得很近的兩個記號會落進同一格）。往後挪一小節保住資訊，
            # 直接覆蓋掉的話那個速度就永遠消失了。
            measure = cleaned[-1][0] + 1
        cleaned.append((measure, bpm))

    if not cleaned or cleaned[0][0] > 1:
        # 開頭沒有記號就補一段預設速度；但如果補進去的跟第一個記號一樣快，
        # 那不是變速，是同一段被切成兩截
        if cleaned and abs(cleaned[0][1] - float(default_bpm)) < 1e-6:
            cleaned[0] = (1, cleaned[0][1])
        else:
            cleaned.insert(0, (1, float(default_bpm)))
    return cleaned


def apply_gradual(tempo_map, gradual, default_bpm, last_measure=None):
    """把漸快／漸慢鋪成斜坡，回傳新的 [(小節, BPM)]。

    `_Clock` 是分段常速積分的，沒有斜坡的概念 —— 但**斜坡可以用夠密的階梯逼近**，
    所以這裡直接在區間內逐小節插值。這樣 `chart.py` 與 Unity 的譜面格式都不用改
    （Unity 其實根本沒讀 tempo_map，音符時間在產譜時就積分成秒了）。

    終點速度怎麼來：
      * 斜坡後面有明確的節拍器記號 -> 就用那個，不必猜
      * 沒有 -> 用 GRADUAL_FACTOR 推算，並鋪 GRADUAL_SPAN 個小節
      * `a tempo` / `Tempo I` -> 回到這一段斜坡開始之前的速度

    沒有任何漸變記號時原封不動回傳，所以其餘 10 首的結果保證一個位元都不變。
    """
    events = [g for g in gradual or [] if g.get("measure")]
    if not events:
        return list(tempo_map)

    steps = sorted((int(m), float(b)) for m, b in tempo_map)
    marked = {m for m, _ in steps}

    def bpm_at(measure):
        """這一小節當下的速度。**要看已經鋪好的斜坡**，不能只看原始階梯 ——
        連續好幾個 accel（〈山魔王的宮殿〉整首 22 個）時，每一段都必須從
        前一段的終點接著往上，否則每遇到一個 accel 速度就掉回原速。
        """
        keys = [m for m in points if m <= measure]
        return points[max(keys)] if keys else float(default_bpm)

    events.sort(key=lambda g: int(g["measure"]))
    end_of_piece = int(last_measure) if last_measure else (
        max([m for m, _ in steps] + [int(g["measure"]) for g in events]) + GRADUAL_SPAN)

    # 連著出現的同方向記號是**同一道斜坡**的提醒，不是各自獨立的一次變速。
    # 〈山魔王的宮殿〉整首印了 22 個 (accel)，那是「這個漸快還在繼續」的意思；
    # 一個記號套一次 1.25 倍會複利到上限，而這首實際大約只到兩倍。
    groups, current = [], None
    for event in events:
        kind, measure = event["kind"], int(event["measure"])
        if kind == "reset":
            if current:
                groups.append(current)
                current = None
            groups.append({"kind": "reset", "start": measure, "end": measure})
            continue
        crossed_mark = current and any(current["end"] < m <= measure for m in marked)
        if current and current["kind"] == kind and not crossed_mark:
            current["end"] = measure               # 同一道斜坡，往後延伸
        else:
            if current:
                groups.append(current)
            current = {"kind": kind, "start": measure, "end": measure}
    if current:
        groups.append(current)

    points = dict(steps)
    before_ramp = None          # `a tempo` 要回到的速度

    for index, group in enumerate(groups):
        start, kind = group["start"], group["kind"]

        if kind == "reset":
            if before_ramp is not None:
                points[start] = before_ramp
                before_ramp = None
            continue

        # 斜坡的終點：下一群漸變記號、下一個明確的節拍器記號，或最後一個提醒
        # 再往後 GRADUAL_SPAN 個小節，取最近的那一個
        later = [g["start"] for g in groups[index + 1:] if g["start"] > group["end"]]
        next_mark = [m for m in marked if m > start]
        tail = min(group["end"] + GRADUAL_SPAN, end_of_piece)
        candidates = [m for m in later + next_mark + [tail] if m > start]
        end = min(candidates) if candidates else 0
        if end <= start:
            continue

        start_bpm = bpm_at(start)
        if before_ramp is None:
            before_ramp = start_bpm

        if next_mark and min(next_mark) == end:
            end_bpm = bpm_at(end)          # 後面有記號，終點不用猜
        else:
            end_bpm = start_bpm * GRADUAL_FACTOR[kind]
        end_bpm = max(BPM_MIN, min(BPM_MAX, end_bpm))
        if abs(end_bpm - start_bpm) < 1e-6:
            continue

        # 逐小節等比插值 —— 等比而不是等差，因為速度感是乘法的
        span = end - start
        for step in range(span + 1):
            measure = start + step
            if measure in marked and measure != start:
                continue               # 別蓋掉譜上明確印出來的記號
            ratio = step / span
            points[measure] = start_bpm * ((end_bpm / start_bpm) ** ratio)

    out = []
    for measure in sorted(points):
        bpm = max(BPM_MIN, min(BPM_MAX, float(points[measure])))
        if out and abs(out[-1][1] - bpm) < 1e-6:
            continue                   # 跟上一段一樣就不是變速
        out.append((measure, bpm))
    return out


def cap_by_density(tempo_map, measure_beats, notes_per_measure, last_measure):
    """速度快到音樂變得彈不出來時，把那一段降下來。回傳 (新的地圖, [被降速的段落])。

    **一個讓音樂密到人類彈不出來的速度，不是演奏指示。** 這是通用的守門條件，
    任何誤讀的節拍器記號造成的荒謬速度都會被擋下來。

    〈Rush E〉的譜上幾乎每小節都印一個遞增的節拍器記號（實測 38 個，最高到
    400 BPM —— 那是這首曲子的惡搞）。全部當成演奏速度套用之後，第 96–164 小節
    （佔全曲 41%）變成每秒 28.3 個音，而人類極限大約 12–16。使用者的說法是
    「沒有連貫起來的感覺」：那一段既打不到也看不清，然後在 m165 突然掉回 120。

    兩個條件同時成立才動：

      1. 密度超過 MAX_NOTES_PER_SECOND
      2. 這一段**持續** MIN_CAPPED_SECONDS 以上

    第 2 條不可省 —— 只看密度的話，李斯特那些單小節的速度段落會各自降速，
    產生鋸齒狀的速度曲線，比不修更糟。

    `measure_beats` / `notes_per_measure` 是 {小節號: 值}。降速是**推算**的，
    呼叫端要把它記在報告的「推算」那一行（見交接書坑 10）。
    """
    if not tempo_map or not notes_per_measure:
        return list(tempo_map), []

    points = sorted((int(m), float(b)) for m, b in tempo_map)
    capped = []
    out = []
    for index, (start, bpm) in enumerate(points):
        end = points[index + 1][0] - 1 if index + 1 < len(points) else int(last_measure)
        if end < start or bpm <= 0:
            out.append((start, bpm))
            continue

        beats = sum(float(measure_beats.get(m, 0.0)) for m in range(start, end + 1))
        seconds = beats * 60.0 / bpm
        notes = sum(int(notes_per_measure.get(m, 0)) for m in range(start, end + 1))
        if seconds < MIN_CAPPED_SECONDS or notes <= 0:
            out.append((start, bpm))
            continue

        density = notes / seconds
        if density <= MAX_NOTES_PER_SECOND:
            out.append((start, bpm))
            continue

        # 密度與速度成正比，所以要達到上限就把速度乘上 上限/實際
        slowed = max(BPM_MIN, bpm * MAX_NOTES_PER_SECOND / density)
        out.append((start, slowed))
        capped.append({"from": start, "to": end, "bpm": round(bpm, 1),
                       "new_bpm": round(slowed, 1), "density": round(density, 1)})

    return out, capped


# ---------------------------------------------------------------------------

def detect(musicxml=None, images=(), notation_bpm=None):
    """綜合各種來源判斷速度。回傳 TempoResult。

    優先順序：記譜檔明寫的 > 樂譜檔內建的 > 圖上的節拍器記號 > 圖上的速度術語。
    全部都沒有就回 bpm=None，由上層去問使用者。
    """
    if notation_bpm:
        return TempoResult(float(notation_bpm), "notation",
                           f"BPM={notation_bpm:g}", 1.0)

    if musicxml and Path(musicxml).exists():
        result = from_musicxml(musicxml)
        if result.ok:
            return result

    best = TempoResult()
    for image in images:
        found = from_image(image)
        if found.ok and found.confidence > best.confidence:
            best = found
        if best.source == "metronome":
            break   # 節拍器記號是確定值，不必再看其他頁
    return best
