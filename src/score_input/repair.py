"""用「小節拍數一定要對」這條硬規則，把辨識結果修回來。

## 為什麼值得做

Gate B 會報「這一小節 2 拍，但拍號 4/4 要求 4 拍」。乍看是辨識爛，實際去查
會發現錯的往往**不是那幾十個小節，是一個記號**：

    André 小奏鳴曲   第 25 小節之後全部量到 2 拍，被宣告成 4/4
                     → 那裡開始是 Rondo，本來就是 2/4。**一個拍號認錯，33 個
                       小節陪葬**。跟標準答案比對，那 33 小節的音其實 100% 正確。

    Alkan 前奏曲     每一小節都剛好多 0.5 拍，10 小節無一倖免
                     → 譜上第一小節就印著三連音，而 homr 整份輸出裡
                       `<time-modification>` 是 0 個。三個八分音符寫成 1.5 拍
                       而不是 1 拍，剛好每小節多 0.5。

兩者都是**系統性**的單一錯誤，不是隨機認錯。所以可以修，而且修完是真的變對，
不是把數字擦掉 —— 修錯的地方會讓小節更對不上，自己就會露餡。

## 三種修法，順序不能顛倒

    1 補完連音  這個聲部裡已經有連音記號，旁邊還有一串同樣時值卻沒標的音，
                補完之後總長剛好對得上，就補完（`fix_partial_tuplets`）
    2 找出連音  一個連音記號都沒有，但小節剛好多出「一個音的長度」，而且找得到
                三個等長的連續音符，就把那三個標成三連音（`fix_missing_tuplets`）
    3 拍號      剩下的：一整段小節長度一致、卻跟宣告的拍號不合，就改宣告

**1 在 2 前面**是因為證據強度不同：1 手上有譜面自己給的證據（旁邊那幾個音
已經被 homr 標成連音了），2 是純推測。

**先修拍號會出事。** 第一版是那個順序，結果 Alkan 每小節多 0.5 拍被「修」成
9/8 —— 十個小節通通符合拍號了，Gate B 也不再抱怨，但音樂是錯的：三連音的時值
還是 1.5 拍，chart 產出的秒數照樣不對。**驗證通過了，問題卻沒解決**，
而且從此再也看不見。

連音修的是時值本身（真的把音改對），拍號修的是標籤（把量尺換掉）。
永遠先試前者，剩下真的解釋不了的才動標籤。

修完一定重算一次；沒有讓情況變好就整個放棄，寧可不修。
"""

from pathlib import Path

from lxml import etree

# 三種修法各自成檔。名字在這裡原樣再匯出 —— `rules.py` 與各種工具都認
# `repair.xxx`，拆檔不該逼它們跟著改。
from .timesig import (TIME_KNOWN_AGREE, TIME_RUN_AGREE,  # noqa: F401
                      TIME_RUN_MIN, _write_time, align_to_known_signatures,
                      dedupe_time_signatures, fix_time_signatures,
                      normalize_time_signatures)
from .timing import (TOLERANCE, is_complete,  # noqa: F401
                     measure_duration as _duration_of, voice_durations,
                     voice_notes)
from .tuplets import (_find_triplet, _mark_triplet,  # noqa: F401
                      _scale_divisions, _scan, fix_missing_tuplets,
                      fix_partial_tuplets)
from .walk import BEAT_TYPES, MAX_BEATS, _expected, _signature_for, _walk  # noqa: F401


# ---------------------------------------------------------------------------
# 對外
# ---------------------------------------------------------------------------

def measure_health(root, initial=(4, 4)):
    """有多少比例的小節拍數是對的。修之前修之後各算一次，用來判斷有沒有變好。

    判準走 `timing.is_complete()`：游標對、或每個聲部各自都對。
    後者不可省 —— `fix_partial_tuplets()` 修的正是「聲部對了但游標沒對」的
    小節，用純游標判斷的話它永遠看不到自己的成果，會被下面的退回機制丟掉。
    """
    good = total = 0
    for part in root.findall("part"):
        rows = list(_walk(part, initial))
        for index, (measure, divisions, signature, _) in enumerate(rows):
            if index == len(rows) - 1:
                continue                    # 最後一小節不完整是正常的
            total += 1
            if is_complete(measure, divisions, _expected(signature)):
                good += 1
    return (good / total if total else 1.0), good, total


def final_signature(root, initial=(4, 4)):
    """整份跑完之後的現行拍號。多頁的譜要把它接到下一頁去。"""
    signature = initial
    for part in root.findall("part"):
        for _, _, current, _ in _walk(part, initial):
            signature = current
        break        # 各聲部的拍號一致，看第一個就夠
    return signature


def repair_file(path, initial=(4, 4)):
    """就地修一份 MusicXML，回傳做了什麼。**沒有變好就不寫回去。**

    initial 是這一頁開始時的拍號；`next` 是這一頁結束時的，多頁時要往下接。
    """
    path = Path(path)
    parser = etree.XMLParser(remove_blank_text=False, recover=True, resolve_entities=False)
    try:
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError):
        return {"ok": False, "before": 0.0, "after": 0.0, "time": 0, "tuplet": 0,
                "partial_tuplet": 0, "measures": 0, "next": initial}

    root = tree.getroot()
    # 這一步要跑在所有量測之前：檔案自己互相矛盾的話，下面每一步讀到的拍號都不一樣
    conflicts = dedupe_time_signatures(root)
    before, _, total = measure_health(root, initial)

    # 順序不能顛倒，見模組開頭：先把時值修對，剩下解釋不了的才改拍號。
    # 補完只標了一半的連音要排在「完全沒標」的前面 —— 它手上有譜面自己給的
    # 證據（旁邊那幾個音已經是連音了），而 fix_missing_tuplets() 是純推測。
    partial = fix_partial_tuplets(root, initial)
    tuplets = fix_missing_tuplets(root, initial)
    times = fix_time_signatures(root, initial)
    after, _, _ = measure_health(root, initial)
    guessed = (times or tuplets or partial) and after > before + 1e-9

    if not guessed and (tuplets or times or partial):
        # 猜的那幾步沒有讓情況變好就整個丟掉，重讀一份乾淨的
        tree = etree.parse(str(path), parser)
        root = tree.getroot()
        dedupe_time_signatures(root)    # 這一步不是猜的，重讀之後要補回來
        partial = tuplets = times = 0
        after = before

    # 這一步不是猜的：長度完全等價，只是換成慣用的寫法，所以無條件套用
    normalized = normalize_time_signatures(root)

    if guessed or normalized or conflicts:
        tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=False)

    return {
        "ok": bool(guessed or normalized or conflicts),
        "before": before,
        "after": after,
        "time": times,
        "tuplet": tuplets,
        "partial_tuplet": partial,
        "normalized": normalized,
        "conflicts": conflicts,
        "measures": total,
        "next": final_signature(root, initial),
    }
