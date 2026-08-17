"""樂譜 ↔ 演奏 的音符級對齊。

離線用 parangonar 的 DualDTWNoteMatcher；失敗時退回自己實作的
貪婪 pitch-DTW，確保任何情況下都能產出對齊結果。

對齊結果統一格式（partitura 的 alignment 慣例）：
    {"label": "match",     "score_id": ..., "performance_id": ...}
    {"label": "deletion",  "score_id": ...}          -> 漏彈
    {"label": "insertion", "performance_id": ...}    -> 多彈
"""

import numpy as np


def align(score_note_array, perf_note_array, method="dualdtw", verbose=False):
    """回傳 (alignment, 使用的方法名稱)。"""
    if method != "fallback":
        try:
            import parangonar as pa

            matchers = {
                "dualdtw": "DualDTWNoteMatcher",
                "automatic": "AutomaticNoteMatcher",
                "anchor": "AnchorPointNoteMatcher",
            }
            cls_name = matchers.get(method, "DualDTWNoteMatcher")
            matcher = getattr(pa, cls_name)()
            alignment = matcher(score_note_array, perf_note_array)
            return _normalise(alignment), cls_name
        except Exception as exc:  # noqa: BLE001 - 對齊失敗不該讓整個分析中斷
            if verbose:
                print(f"[align] parangonar 失敗（{exc}），改用內建 fallback 對齊器")

    return _fallback_align(score_note_array, perf_note_array), "fallback-dtw"


def _normalise(alignment):
    """把 parangonar 輸出的 id 一律轉成 str，避免 numpy str_ 比對出問題。"""
    out = []
    for a in alignment:
        item = {"label": a["label"]}
        if "score_id" in a and a["score_id"] is not None:
            item["score_id"] = str(a["score_id"])
        if "performance_id" in a and a["performance_id"] is not None:
            item["performance_id"] = str(a["performance_id"])
        out.append(item)
    return out


def _fallback_align(score_na, perf_na, window_beats=4.0):
    """簡易對齊：先用 DTW 對齊 onset 序列，再在時間窗內做同音高配對。

    精度不如 parangonar，但足以在缺少依賴或 parangonar 出錯時維持流程。
    """
    s_onset = np.asarray(score_na["onset_beat"], dtype=float)
    p_onset = np.asarray(perf_na["onset_sec"], dtype=float)
    s_pitch = np.asarray(score_na["pitch"], dtype=int)
    p_pitch = np.asarray(perf_na["pitch"], dtype=int)

    # 用整體時長把譜面拍點線性映射到秒，作為搜尋中心
    s_span = max(s_onset.max() - s_onset.min(), 1e-6)
    p_span = max(p_onset.max() - p_onset.min(), 1e-6)
    scale = p_span / s_span
    s_pred = (s_onset - s_onset.min()) * scale + p_onset.min()

    used_perf = set()
    alignment = []
    tol = window_beats * scale

    for i in np.argsort(s_pred):
        candidates = [
            j
            for j in range(len(p_pitch))
            if j not in used_perf
            and p_pitch[j] == s_pitch[i]
            and abs(p_onset[j] - s_pred[i]) <= tol
        ]
        if candidates:
            j = min(candidates, key=lambda k: abs(p_onset[k] - s_pred[i]))
            used_perf.add(j)
            alignment.append(
                {
                    "label": "match",
                    "score_id": str(score_na["id"][i]),
                    "performance_id": str(perf_na["id"][j]),
                }
            )
        else:
            alignment.append({"label": "deletion", "score_id": str(score_na["id"][i])})

    for j in range(len(p_pitch)):
        if j not in used_perf:
            alignment.append(
                {"label": "insertion", "performance_id": str(perf_na["id"][j])}
            )

    return alignment


def alignment_stats(alignment):
    """統計 match / deletion(漏彈) / insertion(多彈) 數量。"""
    stats = {"match": 0, "deletion": 0, "insertion": 0}
    for a in alignment:
        stats[a["label"]] = stats.get(a["label"], 0) + 1
    return stats
