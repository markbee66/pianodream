"""評估結果的輸出：終端機報告 + JSON。"""

import json
from pathlib import Path

DIM_LABELS = {
    "note_accuracy": "音符正確率",
    "timing_stability": "節奏穩定度",
    "tempo_control": "速度控制",
    "chord_evenness": "和弦整齊度",
    "dynamics": "力度層次",
    "articulation": "圜滑一致性",
    "hand_balance": "左右手平衡",
}


def print_report(result, meta=None):
    line = "=" * 60
    print(line)
    print("  鋼琴演奏評估報告")
    print(line)

    if meta:
        for key, value in meta.items():
            print(f"  {key}: {value}")
        print("-" * 60)

    overall = result["overall"]
    print(f"\n  總分 {overall if overall is not None else '-'} / 100    等級 {result['grade']}\n")

    print("  各維度：")
    for key, label in DIM_LABELS.items():
        val = result["dimensions"].get(key)
        if val is None:
            print(f"    {label:<8} —      （資料不足）")
            continue
        print(f"    {label:<8} {val:>5.1f}  {_bar(val)}")

    print("\n  量測細節：")
    raw = result["raw"]
    if raw.get("note_accuracy"):
        r = raw["note_accuracy"]
        print(f"    彈對 {r['matched']} 音、漏彈 {r['missed']} 音、多彈 {r['extra']} 音")
    if raw.get("timing_stability"):
        r = raw["timing_stability"]
        print(
            f"    起音偏差 標準差 {r['std_ms']:.1f} ms / 平均 {r['mean_abs_ms']:.1f} ms / 最大 {r['max_abs_ms']:.1f} ms"
        )
    if raw.get("tempo_control"):
        r = raw["tempo_control"]
        print(
            f"    速度 約 {r['bpm']:.1f} BPM（區間 {r['bpm_min']:.0f}–{r['bpm_max']:.0f}，變異係數 {r['cv']:.3f}）"
        )
    if raw.get("chord_evenness"):
        r = raw["chord_evenness"]
        print(
            f"    和弦 {r['n_chords']} 組，平均散佈 {r['mean_spread_ms']:.1f} ms（最差 {r['worst_spread_ms']:.1f} ms）"
        )
    if raw.get("dynamics"):
        r = raw["dynamics"]
        print(
            f"    力度 平均 {r['mean']:.0f}、範圍 {r['min']:.0f}–{r['max']:.0f}（p10–p90 幅度 {r['p10_p90_range']:.1f}）"
        )
    if raw.get("articulation"):
        r = raw["articulation"]
        print(f"    圜滑度 平均比值 {r['mean_ratio']:.2f} → {r['style']}")
    if raw.get("hand_balance"):
        r = raw["hand_balance"]
        ideal = r.get("ideal_range", [1.1, 1.45])
        print(
            f"    右手力度中位數 {r['right_median_velocity']:.0f}／左手 {r['left_median_velocity']:.0f}"
            f"（比 {r['ratio']:.2f}，理想 {ideal[0]:.2f}–{ideal[1]:.2f} → {r.get('note', '')}）"
        )
    if raw.get("pedal"):
        r = raw["pedal"]
        print(f"    踏板 {r['n_presses']} 次踩下（共 {r['n_events']} 個 CC64 事件）")
    else:
        print("    踏板 未偵測到 CC64 資料")

    spots = result.get("problem_spots") or []
    if spots:
        print("\n  最該練的地方：")
        for i, s in enumerate(spots, 1):
            loc = f"第 {s['beat']:.2f} 拍" if s.get("beat") is not None else "—"
            print(f"    {i}. [{s['type']}] {loc}  {s['detail']}")

    print("\n  建議：")
    for advice in build_advice(result):
        print(f"    • {advice}")
    print(line)


def build_advice(result):
    """依據分數最低的維度給出具體練習建議。"""
    dims = {k: v for k, v in result["dimensions"].items() if v is not None}
    if not dims:
        return ["資料不足，無法給建議。"]

    advice_map = {
        "note_accuracy": "先放慢速度把音彈準，寧可慢也不要漏音或多音；用分手練習找出錯音段落。",
        "timing_stability": "開節拍器用比目標慢 20% 的速度練，注意每個音的落點而不是整體感覺。",
        "tempo_control": "速度忽快忽慢，通常是難的樂段拖慢、簡單的樂段趕快；把最難的兩小節單獨抽出來練到與整體同速。",
        "chord_evenness": "和弦不夠整齊：手指先貼鍵，用手腕帶動一次下沉，避免逐音滾奏。",
        "dynamics": "力度太平板，缺乏層次；標出樂句的漸強漸弱，刻意練 p 與 f 的對比。",
        "articulation": "圜滑/斷奏不一致；檢查譜上的圜滑線，legato 要讓前一音撐到下一音落下。",
        "hand_balance": "左右手音量失衡；旋律聲部（通常右手）應明顯高於伴奏，練習只放大單手。",
    }

    ranked = sorted(dims.items(), key=lambda kv: kv[1])
    out = [advice_map[k] for k, v in ranked[:3] if v < 85 and k in advice_map]

    if not out:
        out.append("各維度表現都穩定，可以往上加速度或加強音樂表現（rubato、音色變化）。")
    return out


def save_json(result, path, meta=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta or {}, **result}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=float), encoding="utf-8"
    )
    return path


def _bar(value, width=20):
    filled = int(round(value / 100 * width))
    return "█" * filled + "·" * (width - filled)
