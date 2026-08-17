"""樂譜與演奏檔的載入。

樂譜支援 MusicXML / MIDI，演奏支援 MIDI。
統一輸出成 partitura 的 note array，欄位名稱在下游固定使用。
"""

from pathlib import Path

import numpy as np
import partitura as pt

SCORE_SUFFIXES = {".musicxml", ".xml", ".mxl", ".mid", ".midi", ".krn", ".mei"}
PERF_SUFFIXES = {".mid", ".midi"}


def load_score(path):
    """載入樂譜，回傳 (score, note_array)。

    note_array 主要欄位：onset_beat, duration_beat, pitch, voice, id
    """
    path = Path(path)
    if path.suffix.lower() not in SCORE_SUFFIXES:
        raise ValueError(f"不支援的樂譜格式：{path.suffix}（可用 {sorted(SCORE_SUFFIXES)}）")

    if path.suffix.lower() in {".mid", ".midi"}:
        # MIDI 沒有真正的樂譜語意，用固定量化值把它當成譜面讀進來
        score = pt.load_score_midi(str(path), assign_note_ids=True, quiet=True)
    else:
        score = pt.load_score(str(path))

    note_array = _ensure_fields(score.note_array())
    if len(note_array) == 0:
        raise ValueError(f"樂譜沒有任何音符：{path}")
    return score, note_array


def _ensure_fields(note_array):
    """補上 parangonar 需要、但從 MIDI 載入的樂譜不會有的欄位。

    MusicXML 有裝飾音等記譜資訊，MIDI 沒有；缺欄位會讓 parangonar 直接拋錯。
    """
    defaults = {"is_grace": (False, bool), "voice": (1, int)}
    missing = {k: v for k, v in defaults.items() if k not in (note_array.dtype.names or ())}
    if not missing:
        return note_array

    from numpy.lib import recfunctions as rfn

    return rfn.append_fields(
        note_array,
        list(missing),
        [np.full(len(note_array), val, dtype=dt) for val, dt in missing.values()],
        usemask=False,
    )


def load_performance(path, apply_pedal=False):
    """載入演奏 MIDI，回傳 (performance, note_array)。

    note_array 主要欄位：onset_sec, duration_sec, pitch, velocity, id

    partitura 預設會用延音踏板 (CC64) 把音符長度延長到踏板放開為止。
    分析「圜滑度 / 觸鍵」時要的是**手指實際按住鍵盤的長度**，不是被踏板
    延長的發聲長度，所以預設把踏板延長關掉（threshold 設在 128 = 永不觸發）。
    踏板本身另外用 extract_pedal() 單獨評估。
    """
    path = Path(path)
    if path.suffix.lower() not in PERF_SUFFIXES:
        raise ValueError(f"演奏檔必須是 MIDI：{path.suffix}")

    performance = pt.load_performance_midi(str(path))
    if not apply_pedal:
        for ppart in performance.performedparts:
            ppart.sustain_pedal_threshold = 128

    note_array = performance.note_array()
    if len(note_array) == 0:
        raise ValueError(f"演奏沒有任何音符：{path}")
    return performance, note_array


def extract_pedal(performance):
    """取出延音踏板 (CC64) 事件，回傳 [(time_sec, value), ...]。

    沒有踏板資料時回傳空 list，下游的踏板維度會自動略過。
    """
    events = []
    for ppart in performance.performedparts:
        for ctrl in getattr(ppart, "controls", []) or []:
            if ctrl.get("number") == 64:
                events.append((float(ctrl["time"]), int(ctrl["value"])))
    events.sort(key=lambda e: e[0])
    return events


def index_by_id(note_array):
    """把 note array 轉成 {id: row} 方便用 alignment 的 id 查詢。"""
    return {str(row["id"]): row for row in note_array}


def as_float(value, default=np.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
