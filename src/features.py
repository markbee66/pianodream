"""音符級演奏特徵抽取。

對齊之後，每個「有彈到」的音符都能算出它相對譜面的偏差。
特徵命名沿用 score-informed 演奏分析文獻的慣例：
    attack_deviation  起音偏差（秒）— 相對「局部平滑速度曲線」而非死板節拍器
    ioi_ratio         實際音間隔 / 譜面音間隔
    articulation      實際發聲長度 / 譜面應有長度（>1 圜滑、<1 斷奏）
    velocity          MIDI 力度
"""

import numpy as np

from .io_utils import index_by_id

MIDDLE_C = 60  # 左右手分界（沒有 staff 資訊時的近似）


def build_note_table(score_na, perf_na, alignment):
    """把對齊結果攤平成一張逐音符的表。

    回傳 (notes, extras)：
        notes  — list[dict]，每個譜面音符一筆（含漏彈，perf 欄位為 None）
        extras — list[dict]，多彈的音（譜上沒有）
    """
    s_idx = index_by_id(score_na)
    p_idx = index_by_id(perf_na)

    matched_pairs = {}
    deletions = set()
    extras = []

    for a in alignment:
        if a["label"] == "match":
            matched_pairs[a["score_id"]] = a["performance_id"]
        elif a["label"] == "deletion":
            deletions.add(a["score_id"])
        elif a["label"] == "insertion":
            row = p_idx.get(a["performance_id"])
            if row is not None:
                extras.append(
                    {
                        "performance_id": a["performance_id"],
                        "pitch": int(row["pitch"]),
                        "onset_sec": float(row["onset_sec"]),
                        "velocity": int(row["velocity"]),
                    }
                )

    notes = []
    for row in score_na:
        sid = str(row["id"])
        note = {
            "score_id": sid,
            "pitch": int(row["pitch"]),
            "onset_beat": float(row["onset_beat"]),
            "duration_beat": float(row["duration_beat"]),
            "hand": "R" if int(row["pitch"]) >= MIDDLE_C else "L",
            "played": False,
            "onset_sec": np.nan,
            "duration_sec": np.nan,
            "velocity": np.nan,
        }
        pid = matched_pairs.get(sid)
        if pid is not None and pid in p_idx:
            prow = p_idx[pid]
            note.update(
                played=True,
                performance_id=pid,
                onset_sec=float(prow["onset_sec"]),
                duration_sec=float(prow["duration_sec"]),
                velocity=int(prow["velocity"]),
            )
        elif sid in deletions:
            note["missed"] = True
        notes.append(note)

    notes.sort(key=lambda n: (n["onset_beat"], n["pitch"]))
    return notes, extras


def estimate_tempo_curve(notes, smooth_window=5):
    """從已彈到的音符估出「局部平滑速度曲線」。

    做法：以譜面同時起音的音群為錨點（取實際起音中位數），算相鄰錨點的
    每拍秒數，中位數濾波平滑後再積分回時間軸。

    回傳 dict：anchors_beat, anchors_sec, spb（每拍秒數）, predict(beat)->sec
    """
    played = [n for n in notes if n["played"]]
    if len(played) < 2:
        return None

    # 依譜面拍點分組（同一拍起音 = 和弦/同時音）
    groups = {}
    for n in played:
        groups.setdefault(round(n["onset_beat"], 6), []).append(n["onset_sec"])

    beats = np.array(sorted(groups))
    secs = np.array([float(np.median(groups[b])) for b in beats])

    if len(beats) < 2:
        return None

    # 保證單調（演奏中偶發亂序不該讓映射反轉）
    secs = np.maximum.accumulate(secs)

    d_beat = np.diff(beats)
    d_sec = np.diff(secs)
    with np.errstate(divide="ignore", invalid="ignore"):
        spb = np.where(d_beat > 0, d_sec / d_beat, np.nan)

    spb = _fill_nan(spb)
    spb_smooth = _median_filter(spb, smooth_window)

    # 積分回時間，再把整體平移對齊實際起點，避免累積偏移
    pred = np.concatenate([[0.0], np.cumsum(spb_smooth * d_beat)])
    pred = pred + (np.mean(secs - pred))

    def predict(beat_values):
        return np.interp(np.asarray(beat_values, dtype=float), beats, pred)

    return {
        "anchors_beat": beats,
        "anchors_sec": secs,
        "spb": spb,
        "spb_smooth": spb_smooth,
        "predict": predict,
        "bpm": float(60.0 / np.median(spb_smooth)) if np.median(spb_smooth) > 0 else float("nan"),
    }


def annotate_deviations(notes, tempo):
    """把 attack_deviation / articulation / ioi_ratio 寫回每個音符。"""
    if tempo is None:
        return notes

    played = [n for n in notes if n["played"]]
    if not played:
        return notes

    beats = np.array([n["onset_beat"] for n in played])
    predicted = tempo["predict"](beats)
    spb_at = np.interp(beats, tempo["anchors_beat"][:-1], tempo["spb_smooth"]) if len(
        tempo["anchors_beat"]
    ) > 1 else np.full(len(beats), np.median(tempo["spb_smooth"]))

    for n, pred_sec, spb in zip(played, predicted, spb_at):
        n["predicted_sec"] = float(pred_sec)
        n["attack_deviation"] = float(n["onset_sec"] - pred_sec)
        expected_dur = n["duration_beat"] * spb
        n["expected_duration_sec"] = float(expected_dur)
        n["articulation"] = (
            float(n["duration_sec"] / expected_dur) if expected_dur > 1e-6 else np.nan
        )

    # 音間隔比：相鄰「錨點」之間，實際 vs 譜面
    for i in range(1, len(played)):
        prev, cur = played[i - 1], played[i]
        d_beat = cur["onset_beat"] - prev["onset_beat"]
        d_sec = cur["onset_sec"] - prev["onset_sec"]
        if d_beat > 1e-6 and np.median(tempo["spb_smooth"]) > 0:
            cur["ioi_ratio"] = float(d_sec / (d_beat * np.median(tempo["spb_smooth"])))

    return notes


def chord_groups(notes, min_size=2):
    """找出譜面同時起音且都彈到的音群，回傳每群的實際起音散佈（毫秒）。"""
    groups = {}
    for n in notes:
        if n["played"]:
            groups.setdefault(round(n["onset_beat"], 6), []).append(n)

    result = []
    for beat, members in sorted(groups.items()):
        if len(members) < min_size:
            continue
        onsets = np.array([m["onset_sec"] for m in members])
        result.append(
            {
                "onset_beat": beat,
                "size": len(members),
                "spread_ms": float((onsets.max() - onsets.min()) * 1000.0),
                "onset_sec": float(onsets.min()),
            }
        )
    return result


def _fill_nan(arr):
    arr = np.asarray(arr, dtype=float)
    if np.all(np.isnan(arr)):
        return np.ones_like(arr)
    idx = np.arange(len(arr))
    good = ~np.isnan(arr)
    return np.interp(idx, idx[good], arr[good])


def _median_filter(arr, window):
    arr = np.asarray(arr, dtype=float)
    if window <= 1 or len(arr) <= 2:
        return arr.copy()
    window = min(window, len(arr))
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(arr, half, mode="edge")
    return np.array([np.median(padded[i : i + window]) for i in range(len(arr))])
