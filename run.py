"""鋼琴演奏評估 AI — 命令列入口。

    python run.py ports                              列出電子琴 (MIDI) 裝置
    python run.py record -o my_take.mid              從電子琴錄一段演奏
    python run.py analyze -s 譜.mid -p 演奏.mid       分析並評分
    python run.py play    -s 譜.mid                  錄音後直接分析（一條龍）

    python run.py score new 小星星 --type photo      建立樂譜專案（照片／數字記譜／字母記譜）
    python run.py score add 小星星 p1.jpg p2.jpg     加入樂譜（順序就是頁序）
    python run.py score build 小星星                 辨識 -> 合併 -> 產出 MusicXML 與音遊譜面
    python run.py web                                網頁介面（上傳、拖曳排序、看回饋）
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import align as align_mod
from src import features, io_utils, report, scoring

ROOT = Path(__file__).resolve().parent


def cmd_ports(args):
    from src.midi_input import list_ports

    ports = list_ports()
    if not ports:
        print("找不到 MIDI 輸入裝置。請確認電子琴已用 USB 接上並開機。")
        return 1
    print("可用的 MIDI 輸入裝置：")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")
    return 0


def cmd_test(args):
    from src.midi_input import monitor

    seen = monitor(port_name=args.port, duration=args.duration)
    return 0 if seen["note"] > 0 else 1


def cmd_record(args):
    from src.midi_input import record

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    record(out, port_name=args.port, silence_timeout=args.silence)
    return 0


def cmd_analyze(args):
    measures = _parse_measures(args.measures)
    result, meta = analyze(args.score, args.performance, method=args.method,
                           level=args.level, measures=measures, verbose=True)
    report.print_report(result, meta)
    _show_review(args, result, meta, measures)
    if args.json:
        path = report.save_json(result, args.json, _clean_meta(meta))
        print(f"\nJSON 已寫入：{path}")
    return 0


def cmd_play(args):
    from src.midi_input import record

    measures = _parse_measures(args.measures)
    if measures:
        print(f"只練第 {measures[0]}–{measures[1]} 小節 —— 彈這一段就好。\n")

    out = Path(args.output or ROOT / "data" / "performances" / "latest_take.mid")
    out.parent.mkdir(parents=True, exist_ok=True)
    record(out, port_name=args.port, silence_timeout=args.silence)

    print()
    result, meta = analyze(args.score, out, method=args.method, level=args.level,
                           measures=measures, verbose=True)
    report.print_report(result, meta)
    _show_review(args, result, meta, measures)
    if args.json:
        report.save_json(result, args.json, _clean_meta(meta))
    return 0


def _parse_measures(text):
    """把 "8-11" 或 "8" 解析成 (起, 迄)。"""
    if not text:
        return None
    parts = str(text).replace("~", "-").replace("–", "-").split("-")
    try:
        nums = [int(p.strip()) for p in parts if p.strip()]
    except ValueError:
        raise ValueError(f"小節範圍要寫成 8-11 或 8 這種格式（你給的是 {text}）")
    if not nums:
        raise ValueError(f"看不懂的小節範圍：{text}")
    start = nums[0]
    end = nums[1] if len(nums) > 1 else start
    if end < start:
        start, end = end, start
    return start, end


def _clean_meta(meta):
    return {k: v for k, v in meta.items() if not k.startswith("_")}


def _show_review(args, result, meta, measures):
    """彈完後的檢討：找出弱段、在譜上圈起來、給重練的指令。"""
    if getattr(args, "no_review", False) or measures is not None:
        return      # 已經在練某一段了，不用再叫他練一次

    from src import review

    build_info = _find_build_info(args.score)
    data = review.build(result, meta.get("_score"), build_info=build_info,
                        stem=Path(args.score).stem)
    sections = data.get("_sections") or []
    if not sections:
        return

    print("\n" + "-" * 60)
    print("練習檢討 —— 這幾段建議重彈：")
    print(review.format_report(data))
    print("\n  重彈某一段（只錄、只評那幾小節）：")
    top = sections[0]
    print(f"    run.py play -s \"{args.score}\" --measures {top.start}-{top.end}")


def _find_build_info(score_path):
    """從樂譜檔回頭找它是哪個專案建出來的，才拿得到小節位置對照表。"""
    import json

    name = Path(score_path).stem
    manifest = ROOT / "data" / "projects" / name / "manifest.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("build")
    except (OSError, ValueError):
        return None


def analyze(score_path, performance_path, method="dualdtw", level=None,
            measures=None, verbose=False):
    """完整分析流程：載入 -> 對齊 -> 抽特徵 -> 評分。

    level 給 1 時只留右手（練單手也能拿到準確評分）；不給就是整份譜，
    行為與加這個參數之前完全相同。
    measures 給 (起, 迄) 時只評那幾個小節 —— 檢討完要重練某一段時用。
    """
    score, score_na = io_utils.load_score(score_path)
    performance, perf_na = io_utils.load_performance(performance_path)

    if level is not None:
        score_na = _filter_level(score, score_na, level, verbose)
    if measures is not None:
        score_na, perf_na = _filter_measures(score, score_na, perf_na, measures,
                                             method, verbose)

    if verbose:
        print(f"[1/4] 載入完成：譜面 {len(score_na)} 音、演奏 {len(perf_na)} 音")

    alignment, method_used = align_mod.align(score_na, perf_na, method=method, verbose=verbose)
    stats = align_mod.alignment_stats(alignment)
    if verbose:
        print(
            f"[2/4] 對齊完成（{method_used}）："
            f"配對 {stats['match']}、漏彈 {stats['deletion']}、多彈 {stats['insertion']}"
        )
        # 指定了小節範圍卻多出一大堆音，幾乎一定是拿整首的錄音來評這一段。
        # 不講的話使用者只會看到很低的分數，不知道原因。
        if measures is not None and stats["insertion"] > len(score_na):
            print(
                f"\n提醒：演奏裡有 {stats['insertion']} 個音不在第 {measures[0]}–{measures[1]} "
                f"小節的範圍內，分數會很低。\n"
                f"      --measures 是給「只彈那一段」用的，請用 "
                f"run.py play --measures {measures[0]}-{measures[1]} 重錄那一段。\n"
            )

    notes, extras = features.build_note_table(score_na, perf_na, alignment)
    tempo = features.estimate_tempo_curve(notes)
    features.annotate_deviations(notes, tempo)
    if verbose:
        bpm = f"{tempo['bpm']:.1f}" if tempo else "-"
        print(f"[3/4] 特徵抽取完成：估計速度 {bpm} BPM")

    pedal = io_utils.extract_pedal(performance)
    result = scoring.score_performance(notes, extras, tempo, pedal_events=pedal)
    if verbose:
        print("[4/4] 評分完成\n")

    meta = {
        "樂譜": str(score_path),
        "演奏": str(performance_path),
        "對齊方法": method_used,
    }
    if level is not None:
        from src.score_input import difficulty

        meta["難度"] = f"{level}（{difficulty.level_name(level)}）"
    if measures is not None:
        meta["範圍"] = f"第 {measures[0]}–{measures[1]} 小節"
    meta["_score"] = score
    return result, meta


def _filter_measures(score, score_na, perf_na, measures, method, verbose):
    """只留下指定小節範圍內的音符，演奏也一起裁到對應的時間窗。

    兩種用法都要能用：
      run.py play --measures 20-24   只錄那一段 -> 演奏本來就只有那一段
      run.py analyze --measures 20-24  拿整首的錄音來看某一段

    第二種如果只裁譜面、不裁演奏，範圍外的音會全部被算成「多彈」，
    分數會低到毫無意義（實測 14.9 分）。所以先用整首去對齊，找出那幾小節
    實際被彈在哪個時間區間，再把演奏裁到那一段。
    """
    import numpy as np

    from src import review

    start, end = measures
    starts = review.measure_starts(score)
    if not starts:
        raise ValueError("這份樂譜讀不出小節資訊，沒辦法只練某一段")
    if start < 1 or end > len(starts):
        raise ValueError(f"這份樂譜只有 {len(starts)} 個小節，指定的 {start}–{end} 超出範圍")

    lo = starts[start - 1]
    hi = starts[end] if end < len(starts) else float("inf")
    mask = (score_na["onset_beat"] >= lo - 1e-6) & (score_na["onset_beat"] < hi - 1e-6)
    if not mask.any():
        raise ValueError(f"第 {start}–{end} 小節裡沒有任何音符")

    sliced = score_na[mask]
    if verbose:
        print(f"只評第 {start}–{end} 小節（{int(mask.sum())} 個音，第 {lo:g} 拍起）")

    # 演奏的音數跟這一段差不多，表示本來就只錄了這一段，不用裁
    if len(perf_na) <= len(sliced) * 1.3:
        return sliced, perf_na

    window = _performance_window(score_na, perf_na, set(sliced["id"].tolist()), method)
    if window is None:
        if verbose:
            print("  （對不出這一段在錄音裡的位置，改用整段錄音比對）")
        return sliced, perf_na

    lo_sec, hi_sec = window
    keep = (perf_na["onset_sec"] >= lo_sec - 0.35) & (perf_na["onset_sec"] <= hi_sec + 0.35)
    if not keep.any():
        return sliced, perf_na

    if verbose:
        print(f"  錄音裁到第 {lo_sec:.1f}–{hi_sec:.1f} 秒"
              f"（{int(keep.sum())}/{len(perf_na)} 個音）")
    return sliced, perf_na[keep]


def _performance_window(score_na, perf_na, wanted_ids, method):
    """用整首對齊找出「那幾小節」實際被彈在錄音的哪個時間區間。"""
    try:
        alignment, _ = align_mod.align(score_na, perf_na, method=method, verbose=False)
    except Exception:
        return None

    perf_by_id = {str(row["id"]): row for row in perf_na}
    times = []
    for pair in alignment:
        if pair.get("label") != "match":
            continue
        if str(pair.get("score_id")) not in wanted_ids:
            continue
        row = perf_by_id.get(str(pair.get("performance_id")))
        if row is not None:
            times.append((float(row["onset_sec"]),
                          float(row["onset_sec"]) + float(row["duration_sec"])))
    if len(times) < 2:
        return None
    return min(t[0] for t in times), max(t[1] for t in times)


def _filter_level(score, score_na, level, verbose):
    """依難度篩掉不練的手。譜裡分不出左右手時照原樣放行，不要無聲地丟掉音符。"""
    from src.score_input import difficulty

    with_staff = difficulty.note_array_with_staff(score)
    if len(with_staff) != len(score_na):
        # 兩次取出來的音符數對不上就不敢亂篩，寧可用整份譜
        if verbose:
            print("提醒：這份樂譜分不出左右手，--level 略過不套用")
        return score_na
    try:
        filtered = difficulty.filter_by_level(with_staff, level)
    except ValueError as exc:
        raise ValueError(f"{exc}\n不指定 --level 就會用整份樂譜。") from exc

    if len(filtered) == len(score_na) and level != max(difficulty.LEVELS):
        print(f"提醒：這份樂譜只有右手，難度 {level} 與整份譜相同")
    return filtered


def build_parser():
    parser = argparse.ArgumentParser(
        description="鋼琴演奏評估 AI（有譜輔助 / 電子琴 MIDI 輸入）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ports", help="列出 MIDI 輸入裝置")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("test", help="連線測試：即時顯示彈下的音與踏板")
    p.add_argument("--port", help="MIDI 裝置名稱（可只給部分字串）")
    p.add_argument("--duration", type=float, default=30.0, help="測試幾秒")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("record", help="從電子琴錄一段演奏成 MIDI")
    p.add_argument("-o", "--output", required=True, help="輸出的 MIDI 檔路徑")
    p.add_argument("--port", help="MIDI 裝置名稱（可只給部分字串）")
    p.add_argument("--silence", type=float, default=4.0, help="停手幾秒後自動結束")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("analyze", help="分析一份已錄好的演奏")
    p.add_argument("-s", "--score", required=True, help="樂譜檔 (MusicXML 或 MIDI)")
    p.add_argument("-p", "--performance", required=True, help="演奏 MIDI")
    p.add_argument("--method", default="dualdtw",
                   choices=["dualdtw", "automatic", "anchor", "fallback"],
                   help="對齊演算法")
    p.add_argument("--level", type=int, choices=[1, 2],
                   help="難度：1=只評右手、2=雙手。不給就是整份樂譜")
    p.add_argument("--measures", "-m",
                   help="只評某一段，例如 8-11。檢討完要重練某一段時用")
    p.add_argument("--no-review", action="store_true", help="不要產生練習檢討")
    p.add_argument("--json", help="把結果另存成 JSON")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("play", help="錄音 + 立刻分析（一條龍）")
    p.add_argument("-s", "--score", required=True, help="樂譜檔 (MusicXML 或 MIDI)")
    p.add_argument("-o", "--output", help="演奏 MIDI 儲存路徑")
    p.add_argument("--port", help="MIDI 裝置名稱")
    p.add_argument("--silence", type=float, default=4.0)
    p.add_argument("--method", default="dualdtw",
                   choices=["dualdtw", "automatic", "anchor", "fallback"])
    p.add_argument("--level", type=int, choices=[1, 2],
                   help="難度：1=只評右手、2=雙手")
    p.add_argument("--measures", "-m",
                   help="只練某一段，例如 8-11。只錄、只評那幾小節")
    p.add_argument("--no-review", action="store_true", help="不要產生練習檢討")
    p.add_argument("--json")
    p.set_defaults(func=cmd_play)

    from src.score_input.cli import add_parsers as add_score_parsers

    add_score_parsers(sub)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        sys.exit(args.func(args))
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        # 裝置沒接、檔案不存在、格式不對、樂譜語法錯 —— 這些是預期中的使用者錯誤，
        # 給一行清楚的訊息就好，不要丟整串 traceback。
        print(f"\n錯誤：{exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中止。", file=sys.stderr)
        sys.exit(130)
