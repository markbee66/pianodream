"""把音符級偏差彙總成可解釋的評分維度。

每個維度都是「原始物理量 -> 0~100 分」的明確映射，門檻集中在 THRESHOLDS，
方便之後依教學程度（初學/檢定/演奏級）調整，不需要動邏輯。
"""

import numpy as np

from .features import chord_groups

# (滿分門檻, 零分門檻)：原始值 <= 滿分門檻 得 100，>= 零分門檻 得 0，中間線性
THRESHOLDS = {
    "timing_stability_ms": (15.0, 120.0),   # 起音偏差標準差
    "tempo_control_cv": (0.03, 0.30),       # 局部速度的變異係數
    "chord_evenness_ms": (12.0, 90.0),      # 和弦內起音散佈
    "articulation_cv": (0.15, 0.80),        # 圜滑度一致性
    "hand_balance_ratio": (0.10, 0.60),     # 偏離「旋律該比伴奏突出」的程度
}

# 右手(旋律) 力度 ÷ 左手(伴奏) 力度的理想區間。
# 這是不對稱的：左手蓋過右手是明顯缺點，右手稍微更突出則是好事。
HAND_RATIO_IDEAL = (1.10, 1.45)

# 力度層次是「越大越好」，另外處理
DYNAMIC_RANGE_TARGET = (10.0, 45.0)  # velocity 的 p90-p10，低於 10 太平板，45 以上滿分

WEIGHTS = {
    "note_accuracy": 0.30,
    "timing_stability": 0.20,
    "tempo_control": 0.15,
    "chord_evenness": 0.12,
    "dynamics": 0.13,
    "articulation": 0.05,
    "hand_balance": 0.05,
}


def score_performance(notes, extras, tempo, pedal_events=None):
    """回傳完整評估結果 dict。"""
    played = [n for n in notes if n["played"]]
    total_score_notes = len(notes)
    missed = [n for n in notes if not n["played"]]

    dims = {}
    raw = {}

    # 1. 音符正確率 -----------------------------------------------------
    denom = total_score_notes + len(extras)
    accuracy = len(played) / denom if denom else 0.0
    raw["note_accuracy"] = {
        "matched": len(played),
        "missed": len(missed),
        "extra": len(extras),
        "ratio": accuracy,
    }
    dims["note_accuracy"] = round(accuracy * 100, 1)

    # 2. 節奏穩定度 -----------------------------------------------------
    devs = np.array([n["attack_deviation"] for n in played if "attack_deviation" in n])
    if len(devs) >= 3:
        dev_ms = float(np.std(devs) * 1000.0)
        raw["timing_stability"] = {
            "std_ms": dev_ms,
            "mean_abs_ms": float(np.mean(np.abs(devs)) * 1000.0),
            "max_abs_ms": float(np.max(np.abs(devs)) * 1000.0),
        }
        dims["timing_stability"] = _to_score(dev_ms, *THRESHOLDS["timing_stability_ms"])
    else:
        raw["timing_stability"] = None
        dims["timing_stability"] = None

    # 3. 速度控制 -------------------------------------------------------
    if tempo is not None and len(tempo["spb_smooth"]) >= 3:
        spb = tempo["spb_smooth"]
        cv = float(np.std(spb) / np.mean(spb)) if np.mean(spb) > 0 else np.nan
        raw["tempo_control"] = {
            "bpm": tempo["bpm"],
            "cv": cv,
            "bpm_min": float(60.0 / np.max(spb)),
            "bpm_max": float(60.0 / np.min(spb)),
        }
        dims["tempo_control"] = _to_score(cv, *THRESHOLDS["tempo_control_cv"])
    else:
        raw["tempo_control"] = None
        dims["tempo_control"] = None

    # 4. 和弦整齊度 -----------------------------------------------------
    chords = chord_groups(notes)
    if chords:
        spreads = np.array([c["spread_ms"] for c in chords])
        raw["chord_evenness"] = {
            "n_chords": len(chords),
            "mean_spread_ms": float(np.mean(spreads)),
            "worst_spread_ms": float(np.max(spreads)),
        }
        dims["chord_evenness"] = _to_score(
            float(np.mean(spreads)), *THRESHOLDS["chord_evenness_ms"]
        )
    else:
        raw["chord_evenness"] = None
        dims["chord_evenness"] = None

    # 5. 力度層次 -------------------------------------------------------
    vels = np.array([n["velocity"] for n in played], dtype=float)
    if len(vels) >= 3:
        vrange = float(np.percentile(vels, 90) - np.percentile(vels, 10))
        raw["dynamics"] = {
            "mean": float(np.mean(vels)),
            "std": float(np.std(vels)),
            "p10_p90_range": vrange,
            "min": float(vels.min()),
            "max": float(vels.max()),
        }
        lo, hi = DYNAMIC_RANGE_TARGET
        dims["dynamics"] = round(float(np.clip((vrange - lo) / (hi - lo), 0, 1) * 100), 1)
    else:
        raw["dynamics"] = None
        dims["dynamics"] = None

    # 6. 圜滑度一致性 ---------------------------------------------------
    arts = np.array(
        [n["articulation"] for n in played if np.isfinite(n.get("articulation", np.nan))]
    )
    if len(arts) >= 3:
        art_cv = float(np.std(arts) / np.mean(arts)) if np.mean(arts) > 0 else np.nan
        raw["articulation"] = {
            "mean_ratio": float(np.mean(arts)),
            "cv": art_cv,
            "style": _articulation_style(float(np.mean(arts))),
        }
        dims["articulation"] = _to_score(art_cv, *THRESHOLDS["articulation_cv"])
    else:
        raw["articulation"] = None
        dims["articulation"] = None

    # 7. 左右手平衡 -----------------------------------------------------
    right = [n["velocity"] for n in played if n["hand"] == "R"]
    left = [n["velocity"] for n in played if n["hand"] == "L"]
    if len(right) >= 3 and len(left) >= 3:
        ratio = float(np.median(right) / max(np.median(left), 1e-6))
        lo, hi = HAND_RATIO_IDEAL
        if lo <= ratio <= hi:
            imbalance = 0.0
        elif ratio < lo:
            imbalance = abs(float(np.log(max(ratio, 1e-6) / lo)))  # 旋律被伴奏蓋住
        else:
            imbalance = abs(float(np.log(ratio / hi)))             # 右手過度突出、左手太虛
        raw["hand_balance"] = {
            "right_median_velocity": float(np.median(right)),
            "left_median_velocity": float(np.median(left)),
            "ratio": ratio,
            "ideal_range": list(HAND_RATIO_IDEAL),
            "note": "旋律被伴奏蓋過" if ratio < lo else ("左手過弱" if ratio > hi else "平衡良好"),
        }
        dims["hand_balance"] = _to_score(imbalance, *THRESHOLDS["hand_balance_ratio"])
    else:
        raw["hand_balance"] = None
        dims["hand_balance"] = None

    # 8. 踏板（有資料才算，不列入總分） ---------------------------------
    if pedal_events:
        presses = [e for e in pedal_events if e[1] >= 64]
        raw["pedal"] = {
            "n_events": len(pedal_events),
            "n_presses": len(presses),
            "used": len(presses) > 0,
        }
    else:
        raw["pedal"] = None

    overall = _weighted_overall(dims)

    return {
        "dimensions": dims,
        "raw": raw,
        "overall": overall,
        "grade": _grade(overall),
        "problem_spots": find_problem_spots(notes, extras, chords),
    }


def find_problem_spots(notes, extras, chords, top_n=8):
    """挑出最該練的地方，給使用者具體位置而不是只有分數。"""
    spots = []

    for n in notes:
        if not n["played"]:
            spots.append(
                {
                    "type": "漏彈",
                    "beat": n["onset_beat"],
                    "pitch": n["pitch"],
                    "detail": f"譜上 {_pitch_name(n['pitch'])} 沒有彈到",
                    "severity": 3.0,
                }
            )

    for e in extras:
        spots.append(
            {
                "type": "多彈",
                "beat": None,
                "pitch": e["pitch"],
                "detail": f"第 {e['onset_sec']:.2f} 秒多彈了 {_pitch_name(e['pitch'])}",
                "severity": 2.5,
            }
        )

    for n in notes:
        dev = n.get("attack_deviation")
        if dev is not None and abs(dev) > 0.08:
            spots.append(
                {
                    "type": "拖拍" if dev > 0 else "搶拍",
                    "beat": n["onset_beat"],
                    "pitch": n["pitch"],
                    "detail": f"{_pitch_name(n['pitch'])} 偏差 {dev * 1000:+.0f} ms",
                    "severity": min(abs(dev) * 10, 3.0),
                }
            )

    for c in chords:
        if c["spread_ms"] > 40:
            spots.append(
                {
                    "type": "和弦不齊",
                    "beat": c["onset_beat"],
                    "pitch": None,
                    "detail": f"{c['size']} 個音散佈 {c['spread_ms']:.0f} ms",
                    "severity": min(c["spread_ms"] / 40, 3.0),
                }
            )

    spots.sort(key=lambda s: -s["severity"])
    return spots[:top_n]


def _to_score(value, best, worst):
    """原始物理量映射到 0~100（越小越好的指標）。"""
    if value is None or not np.isfinite(value):
        return None
    if worst <= best:
        return 100.0
    return round(float(np.clip((worst - value) / (worst - best), 0, 1) * 100), 1)


def _weighted_overall(dims):
    total_w = 0.0
    acc = 0.0
    for key, weight in WEIGHTS.items():
        val = dims.get(key)
        if val is None:
            continue
        acc += val * weight
        total_w += weight
    return round(acc / total_w, 1) if total_w > 0 else None


def _grade(score):
    if score is None:
        return "-"
    for threshold, label in [(90, "A"), (80, "B"), (70, "C"), (60, "D")]:
        if score >= threshold:
            return label
    return "E"


def _articulation_style(mean_ratio):
    if mean_ratio >= 1.05:
        return "偏圜滑 (legato / 有重疊)"
    if mean_ratio >= 0.85:
        return "正常"
    if mean_ratio >= 0.55:
        return "偏斷奏"
    return "非常斷 (staccato)"


PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _pitch_name(midi_pitch):
    return f"{PITCH_NAMES[int(midi_pitch) % 12]}{int(midi_pitch) // 12 - 1}"
