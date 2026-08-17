"""`run.py score ...` 的實作。

放在這裡而不是 run.py 裡，是為了讓 run.py 保持一眼看得完的長度 ——
它原本就只是一個指令分派表。
"""

import sys
from pathlib import Path

from . import difficulty, pipeline
from .project import Project, ProjectError

MARK = {"ok": "[可用]", "warn": "[注意]", "reject": "[不能用]", "—": "[未檢查]"}
PARSE_MARK = {"ok": "完成", "failed": "失敗", "skipped": "略過", "pending": "-", "—": "-"}


def _open(name):
    return Project.load(name)


# ---------------------------------------------------------------------------

def cmd_new(args):
    project = Project.create(args.name, args.type)
    print(f"建立專案「{project.name}」（{args.type}）：{project.dir}")
    hint = {
        "photo": "接著把樂譜照片依頁序加進來："
                 f"\n  run.py score add {project.name} 照片1.jpg 照片2.jpg",
        "jianpu": "接著把數字記譜的 .txt 加進來："
                  f"\n  run.py score add {project.name} 小星星.txt",
        "letter": "接著把字母記譜的 .txt 加進來："
                  f"\n  run.py score add {project.name} 小星星.txt",
    }[args.type]
    print(hint)
    return 0


def cmd_add(args):
    project = _open(args.name)
    # 使用者在命令列上打的順序就是頁序；--sort 才交給程式用 EXIF / 檔名決定
    added, skipped = project.add(args.files, sort=args.sort)
    if skipped:
        print(f"略過 {len(skipped)} 個重複的檔案：{', '.join(skipped[:5])}")
    if not added:
        print("沒有新增任何項目。")
        return 1
    print(f"加入 {len(added)} 項到「{project.name}」：")
    for item in added:
        print(f"  第 {item['index']:>2} 項　{item.get('original_name')}　-> {item['file']}")
    print(f"\n確認順序：run.py score list {project.name}")
    return 0


def cmd_list(args):
    if not args.name:
        names = Project.list_all()
        if not names:
            print("還沒有任何樂譜專案。用 `run.py score new <名稱> --type photo` 建立。")
            return 1
        print("樂譜專案：")
        for name in names:
            info = pipeline.status(Project.load(name))
            built = "已建構" if info["build"] else "未建構"
            print(f"  {name}　({info['source_type']}、{info['counts']['total']} 項、{built})")
        return 0

    info = pipeline.status(_open(args.name))
    print(f"專案「{info['name']}」　類型 {info['source_type']}　建立於 {info['created']}")
    if not info["items"]:
        print("  （還沒有任何內容）")
        return 0

    print(f"\n{'順序':<5}{'檔案':<26}{'品質':<11}{'辨識':<7}{'小節':>5}{'音符':>6}{'信心':>7}  問題")
    print("-" * 100)
    for row in info["items"]:
        issues = "；".join(i["message"] for i in row["issues"][:2]) or ""
        confidence = f"{row['confidence']:.2f}" if row["confidence"] is not None else "-"
        print(
            f"{row['index']:<5}{row['original_name'][:24]:<26}"
            f"{MARK.get(row['verdict'], row['verdict']):<11}"
            f"{PARSE_MARK.get(row['parse_status'], '-'):<7}"
            f"{row['measures'] or '-':>5}{row['notes'] or '-':>6}{confidence:>7}  {issues[:38]}"
        )

    counts = info["counts"]
    print(f"\n共 {counts['total']} 項：可用 {counts['ok']}、注意 {counts['warn']}、"
          f"不能用 {counts['reject']}、未檢查 {counts['unchecked']}")
    if info["build"]:
        build = info["build"]
        print(f"\n已建構：{build['musicxml']}")
        print(f"  {build.get('measures')} 小節 / {build.get('notes')} 音符　"
              f"信心 {build.get('confidence')}　BPM {build.get('bpm')}")
        for level, path in sorted(build.get("charts", {}).items()):
            print(f"  難度 {level}：{path}")
    return 0


def cmd_check(args):
    project = _open(args.name)
    if not project.items:
        print("這個專案還沒有任何內容。")
        return 1

    def progress(i, total, item, result):
        if result is None:
            print(f"  [{i}/{total}] 檢查 {item['file']} ...", end="\r", flush=True)

    pipeline.check_project(project, force=args.force, on_progress=progress)
    print(" " * 60, end="\r")

    worst = 0
    for item in project.items:
        check = item.get("check") or {}
        verdict = check.get("verdict", "—")
        worst = max(worst, {"ok": 0, "warn": 1, "reject": 2, "—": 0}[verdict])
        print(f"\n第 {item['index']} 項　{item.get('original_name')}　{MARK.get(verdict, verdict)}")
        for issue in check.get("issues", []):
            tag = "!" if issue["level"] == "reject" else "-"
            print(f"    {tag} {issue['message']}")
            if issue.get("hint"):
                print(f"      → {issue['hint']}")
        if check.get("overlay"):
            print(f"    標註圖：{project.dir / check['overlay']}")
        if not check.get("issues"):
            print("    沒有問題")

    if worst == 2:
        print(f"\n有項目不能用。修好之後執行："
              f"\n  run.py score replace {project.name} <順序> <新檔案>")
    return 0 if worst < 2 else 1


def cmd_reorder(args):
    project = _open(args.name)
    try:
        order = [int(x) for x in args.order.replace(" ", "").split(",") if x]
    except ValueError:
        raise ProjectError(f"順序要寫成用逗號分隔的數字，例如 3,1,2（你給的是 {args.order}）")
    project.reorder(order)
    print(f"「{project.name}」的順序已更新：")
    for item in project.items:
        print(f"  第 {item['index']:>2} 項　{item.get('original_name')}")
    return 0


def cmd_remove(args):
    project = _open(args.name)
    item = project.remove(args.index)
    print(f"已移除第 {args.index} 項（{item.get('original_name')}），後面的項目往前遞補。")
    return 0


def cmd_replace(args):
    project = _open(args.name)
    item = project.replace(args.index, args.file)
    print(f"第 {args.index} 項已換成 {item.get('original_name')}，順序不變。")
    print(f"重新檢查：run.py score check {project.name}")
    return 0


def cmd_build(args):
    project = _open(args.name)

    def progress(i, total, item, result):
        if total == 0:
            print(f"\n[{item['stage']}] {item['label']}")
            return
        name = item.get("file", "?")
        if result is None:
            print(f"  [{i}/{total}] {name} ...", end="\r", flush=True)
        else:
            status = PARSE_MARK.get(result.get("status"), result.get("status"))
            extra = f"　{result['error']}" if result.get("error") else ""
            print(f"  [{i}/{total}] {name}　{status}{extra}")

    try:
        outcome = pipeline.build(project, bpm=args.bpm, force=args.force, on_progress=progress)
    except pipeline.BuildError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    from . import validate

    build = outcome["build"]
    print("\n" + "=" * 60)
    print(f"樂譜已產出：{build['musicxml']}")
    print(f"  {build['measures']} 小節 / {build['notes']} 音符")
    if build.get("title_text"):
        print(f"  {build['title_text']}")
    print(f"  速度：{build.get('tempo_text', '')}")
    hands = build.get("hands") or {}
    if hands.get("reason"):
        print(f"  ⚠ {hands['reason']}")

    # 信心是「找不到內部矛盾」，不是「辨識正確」。推算過的地方會讓矛盾消失、
    # 分數跟著變高，所以一定要把「讀到的」與「猜的」分開講，不然那個數字會騙人。
    reports = build.get("rules") or []
    read = {}
    inferred = {}
    for entry in reports:
        for name, count in (entry.get("read") or {}).items():
            read[name] = read.get(name, 0) + count
        for name, count in (entry.get("inferred") or {}).items():
            inferred[name] = inferred.get(name, 0) + count
    if read:
        print("  記譜規則（從譜面讀到）：" + "、".join(f"{k} {v}" for k, v in read.items()))
    if inferred:
        print("  ⚠ 以下是**推算**的，不是譜上讀到的："
              + "、".join(f"{k} {v}" for k, v in inferred.items())
              + "。信心分數只代表「找不到內部矛盾」，推算的地方等於猜的。")

    if build.get("needs_bpm"):
        build = _ask_bpm(project, build)

    report_text = validate.format_report(
        outcome["reports"], outcome["sequence_problems"], show_ok=False
    )
    if report_text:
        print("\n樂理檢查發現的問題：")
        print(report_text)
    else:
        print("\n樂理檢查：沒有發現問題")

    if outcome["failed"]:
        print("\n以下項目沒有成功，最終樂譜不含這些內容：")
        for row in outcome["failed"]:
            print(f"  第 {row['index']} 項 {row['file']}：{row['error']}")

    print("\n音遊譜面：")
    for level, path in sorted(build["charts"].items()):
        print(f"  難度 {level}：{path}")

    # 難度用這份譜真的有的，不能寫死 1 —— 分手失敗的譜只有難度 2，
    # 照著印出來的指令打會直接撞上「這份譜在難度 1 之下一個音符都不剩」。
    levels = sorted(build.get("levels") or build.get("charts") or [difficulty.DEFAULT_LEVEL])
    print(f"\n拿來評分：\n  run.py play -s \"{build['musicxml']}\" --level {levels[0]}")
    return 0


def _ask_bpm(project, build):
    """偵測不到速度時問使用者。

    問到答案就重產音遊譜面。不能互動（例如被腳本呼叫、輸出被導向檔案）時
    不卡住，改成印出補救指令 —— 但一定要講清楚現在用的是猜的值。
    """
    print()
    print("  ⚠ 譜上找不到速度標記（節拍器記號或 Moderato 這類術語），")
    print(f"    音遊譜面先用預設的 {build['bpm']:g} BPM 產出，音符落下的時間點會不準。")

    if not sys.stdin.isatty():
        print(f"\n    知道正確速度的話執行："
              f"\n      run.py score chart {project.name} --bpm <數字>")
        return build

    print("    知道正確速度的話直接輸入（例如 120），不知道就按 Enter 跳過。")
    for _ in range(3):
        try:
            answer = input("    BPM > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return build
        if not answer:
            print(f"    好，維持 {build['bpm']:g} BPM。之後可以用："
                  f" run.py score chart {project.name} --bpm <數字>")
            return build
        try:
            value = float(answer)
        except ValueError:
            print("    請輸入數字。")
            continue
        if not 30 <= value <= 300:
            print("    合理範圍是 30–300。")
            continue

        pipeline.make_charts(project, bpm=value)
        refreshed = pipeline.status(project)["build"]
        print(f"    已用 {value:g} BPM 重新產生音遊譜面。")
        return refreshed
    return build


def cmd_chart(args):
    project = _open(args.name)
    levels = None if args.level == "all" else [int(args.level)]
    charts = pipeline.make_charts(project, levels=levels, bpm=args.bpm)
    import json

    for level, path in sorted(charts.items()):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        print(f"難度 {level}（{data['level_name']}）："
              f"{data['note_count']} 音、{data['duration_sec']:.1f} 秒　-> {path}")
    return 0


def cmd_web(args):
    from web.app import run_server

    run_server(host=args.host, port=args.port, debug=args.debug,
               open_browser=not args.no_browser)
    return 0


# ---------------------------------------------------------------------------

def add_parsers(sub):
    """把 score 與 web 兩組指令掛到 run.py 的 subparser 上。"""
    score = sub.add_parser("score", help="樂譜輸入：照片辨識 / 數字記譜 / 字母記譜")
    score_sub = score.add_subparsers(dest="score_command", required=True)

    p = score_sub.add_parser("new", help="建立樂譜專案")
    p.add_argument("name", help="專案名稱（會當成資料夾名和輸出檔名）")
    p.add_argument("--type", default="photo", choices=["photo", "jianpu", "letter"],
                   help="photo=拍照的五線譜、jianpu=數字記譜、letter=字母記譜")
    p.set_defaults(func=cmd_new)

    p = score_sub.add_parser("add", help="加入檔案（命令列上的順序就是頁序）")
    p.add_argument("name")
    p.add_argument("files", nargs="+", help="圖檔 / PDF / 記譜 .txt")
    p.add_argument("--sort", action="store_true",
                   help="改用 EXIF 拍攝時間或檔名自然排序決定順序")
    p.set_defaults(func=cmd_add)

    p = score_sub.add_parser("list", help="看順序與狀態")
    p.add_argument("name", nargs="?", help="不給名稱就列出所有專案")
    p.set_defaults(func=cmd_list)

    p = score_sub.add_parser("check", help="只跑品質 / 語法檢查")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="已檢查過的也重跑")
    p.set_defaults(func=cmd_check)

    p = score_sub.add_parser("reorder", help="調整順序，例如 3,1,2")
    p.add_argument("name")
    p.add_argument("order")
    p.set_defaults(func=cmd_reorder)

    p = score_sub.add_parser("remove", help="移除某一項")
    p.add_argument("name")
    p.add_argument("index", type=int)
    p.set_defaults(func=cmd_remove)

    p = score_sub.add_parser("replace", help="換掉某一項（重拍那一頁）")
    p.add_argument("name")
    p.add_argument("index", type=int)
    p.add_argument("file")
    p.set_defaults(func=cmd_replace)

    p = score_sub.add_parser("build", help="辨識 / 解析 -> 合併 -> 產出樂譜與音遊譜面")
    p.add_argument("name")
    p.add_argument("--bpm", type=float, help="音遊譜面的速度（記譜檔會自己帶）")
    p.add_argument("--force", action="store_true", help="全部重跑，不沿用之前的結果")
    p.set_defaults(func=cmd_build)

    p = score_sub.add_parser("chart", help="只重產音遊譜面")
    p.add_argument("name")
    p.add_argument("--level", default="all", help="1 / 2 / all")
    p.add_argument("--bpm", type=float)
    p.set_defaults(func=cmd_chart)

    p = sub.add_parser("web", help="開啟網頁介面（上傳、排順序、看回饋）")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--no-browser", action="store_true", help="不要自動打開瀏覽器")
    p.set_defaults(func=cmd_web)
