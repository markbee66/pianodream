"""逐小節走過一個 part，一路把 divisions 與拍號帶著走。

從 `repair.py` 拆出來的，因為三種修法（`timesig.py`、`tuplets.py`）跟對外的
`repair.py` 全都要用它，而它自己只有幾十行、跟任何一種修法都無關。
"""

# 允許的拍號分母。3/16、7/32 這種在鋼琴譜幾乎不會出現，推出來多半是算錯了
BEAT_TYPES = (2, 4, 8)
MAX_BEATS = 12


def _walk(part, initial=(4, 4)):
    """逐小節回傳 (小節元素, 目前的 divisions, 目前的拍號, 這一小節有沒有宣告拍號)。

    initial 是「這一頁開始時的拍號」。多頁的譜只在第一頁印拍號，後面幾頁沿用；
    不把它傳進來的話，第 2 頁會被當成 4/4，然後被「修」成別的東西。
    """
    divisions, (beats, beat_type) = 1.0, initial
    for measure in part.findall("measure"):
        value = measure.findtext("attributes/divisions")
        if value:
            try:
                divisions = float(value)
            except ValueError:
                pass
        declared = measure.find("attributes/time")
        if declared is not None:
            try:
                beats = int(declared.findtext("beats"))
                beat_type = int(declared.findtext("beat-type"))
            except (TypeError, ValueError):
                pass
        yield measure, divisions, (beats, beat_type), declared is not None


def _expected(signature):
    """這個拍號要求一小節有幾個四分音符。"""
    beats, beat_type = signature
    return beats * 4.0 / beat_type if beat_type else 0.0


def _signature_for(duration, beat_type):
    """把「幾個四分音符」換回拍號。先試著沿用原本的分母。"""
    for candidate in (beat_type,) + BEAT_TYPES:
        beats = duration * candidate / 4.0
        if abs(beats - round(beats)) < 1e-6 and 1 <= round(beats) <= MAX_BEATS:
            return int(round(beats)), candidate
    return None
