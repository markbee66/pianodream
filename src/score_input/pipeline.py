"""把各步驟串成完整流程。

命令列與網頁介面都呼叫這裡，不各自實作一遍 —— 網頁只是外殼，
兩邊的行為必須一模一樣，否則「用網頁做出來的譜」跟「用指令做出來的譜」
會有微妙的差異，那種 bug 很難查。

    加入檔案 ─► Gate A（照片品質 / 記譜語法）─► 辨識或解析 ─► 依序合併
                                                                  │
                                          Gate B（樂理合法性）◄───┘
                                                  │
                          data/scores/<名稱>.musicxml + data/charts/<名稱>_lv<N>.json

這個檔案只留**順序與決策**：先做什麼、失敗了怎麼辦、結果怎麼組成報告。
兩組實作細節各自成檔，因為它們自己就有一整套要解釋的道理：

    pagemap.py   照片位置 -> 第幾小節（小節框、速度地圖、曲名、逐頁修正）
    enrich.py    在合併檔上補 homr 讀不到的東西（8va、記譜符號、強弱、拍號對齊）
"""

from datetime import datetime
from pathlib import Path

from . import chart as chart_mod
from . import (difficulty, enrich, merge, notation, omr_engine, pagemap,
               quality, repeats, tempo, validate)
from .pagemap import prepared_image
from .project import Project, ProjectError

ROOT = Path(__file__).resolve().parents[2]
SCORES_DIR = ROOT / "data" / "scores"
CHARTS_DIR = ROOT / "data" / "charts"

DEFAULT_BPM = 100.0

#: 沒有取名字的專案會用這個開頭。看到它就表示「名字是系統編的，
#: 譜上讀到什麼就用什麼」。
AUTO_NAME_PREFIX = "未命名_"


class BuildError(RuntimeError):
    """建構失敗，訊息是給使用者看的說明。"""


# ---------------------------------------------------------------------------
# Gate A
# ---------------------------------------------------------------------------

def check_item(project, item, force=False):
    """檢查單一項目。已經檢查過就直接沿用，除非 force。"""
    if item.get("check") and not force:
        return item["check"]

    path = project.path_of(item)
    if item["kind"] == "image":
        overlay = project.dir / f"{Path(item['file']).stem}_check.jpg"
        # 前處理只跑一次：Gate A、辨識、版面偵測三邊都用同一張整理過的圖，
        # 三邊看到的東西不一致才是真的難查。
        result = quality.check_image(
            path, overlay_path=overlay,
            prepared=prepared_image(project, item, force=force),
        ).as_dict()
    else:
        result = _check_text(project, path)

    project.set_check(item["index"], result)
    return result


def _check_text(project, path):
    """記譜檔的 Gate A 就是語法檢查。回饋格式跟照片一致，網頁才能共用同一種卡片。"""
    kind = project.source_type if project.source_type in {"jianpu", "letter"} else None
    try:
        score = notation.parse_file(path, notation=kind)
    except notation.NotationSyntaxError as exc:
        return {
            "verdict": "reject",
            "issues": [
                {
                    "code": "SYNTAX",
                    "level": "reject",
                    "message": f"第 {e.line} 行" + (f"第 {e.col} 字" if e.col else "") + f"：{e.message}",
                    "hint": e.hint,
                }
                for e in exc.errors
            ],
            "detail": notation.format_errors(exc.errors),
            "summary": None,
        }

    summary = notation.summarize(score)
    return {
        "verdict": "warn" if score.warnings else "ok",
        "issues": [
            {"code": "NOTATION_WARN", "level": "warn", "message": w, "hint": ""}
            for w in score.warnings
        ],
        "detail": "",
        "summary": summary,
    }


def check_project(project, force=False, on_progress=None):
    """對整個專案跑 Gate A。"""
    results = []
    total = len(project.items)
    for i, item in enumerate(project.items, start=1):
        if on_progress:
            on_progress(i, total, item, None)
        result = check_item(project, item, force=force)
        results.append(result)
        if on_progress:
            on_progress(i, total, item, result)
    return results


# ---------------------------------------------------------------------------
# 辨識 / 解析 -> MusicXML
# ---------------------------------------------------------------------------

def _parse_text_item(project, item):
    """記譜檔轉 MusicXML。語法在 Gate A 已經驗過，這裡只負責產檔。"""
    path = project.path_of(item)
    kind = project.source_type if project.source_type in {"jianpu", "letter"} else None
    score = notation.parse_file(path, notation=kind)
    out = project.dir / f"{Path(item['file']).stem}.musicxml"
    notation.to_musicxml(score, out, part_name=project.name)
    summary = notation.summarize(score)
    return {
        "status": "ok",
        "musicxml": out.name,
        "engine": "notation",
        "measures": summary["measures"],
        "notes": summary["notes"],
        "bpm": score.bpm,
        "error": None,
    }


def transcribe_project(project, engine=None, force=False, on_progress=None):
    """把每個項目變成一份 MusicXML。一項失敗不影響其他項。"""
    is_photo = project.source_type == "photo"
    engine = engine or (omr_engine.get_engine() if is_photo else None)
    total = len(project.items)
    results = []

    for i, item in enumerate(project.items, start=1):
        existing = item.get("parse")
        if existing and existing.get("status") == "ok" and not force:
            results.append(existing)
            if on_progress:
                on_progress(i, total, item, existing)
            continue

        if on_progress:
            on_progress(i, total, item, None)

        verdict = (item.get("check") or {}).get("verdict")
        if verdict == "reject":
            result = {"status": "skipped", "musicxml": None,
                      "error": "這一項沒有通過品質檢查，先修好再辨識"}
        elif is_photo:
            try:
                xml = engine.transcribe(prepared_image(project, item, force=force))
                result = {"status": "ok", "musicxml": Path(xml).name,
                          "engine": engine.name, "error": None}
            except omr_engine.OmrError as exc:
                result = {"status": "failed", "musicxml": None,
                          "engine": engine.name, "error": str(exc)}
        else:
            try:
                result = _parse_text_item(project, item)
            except notation.NotationSyntaxError as exc:
                result = {"status": "failed", "musicxml": None,
                          "engine": "notation", "error": str(exc)}

        project.set_parse(item["index"], result)
        results.append(result)
        if on_progress:
            on_progress(i, total, item, result)

    return results


# ---------------------------------------------------------------------------
# 完整建構
# ---------------------------------------------------------------------------

def build(project, bpm=None, engine=None, force=False, on_progress=None,
          scores_dir=None, charts_dir=None):
    """Gate A -> 辨識/解析 -> 合併 -> Gate B -> 產出樂譜與音遊譜面。"""
    if not project.items:
        raise BuildError(
            f"專案「{project.name}」還沒有任何內容。"
            f"先用 `run.py score add {project.name} <檔案>` 加進來。"
        )

    _note(on_progress, "check", "檢查品質")
    check_project(project, force=force)

    rejected = project.rejected()
    if rejected:
        raise BuildError(_rejection_message(project, rejected))

    _note(on_progress, "parse", "辨識 / 解析")
    transcribe_project(project, engine=engine, force=force, on_progress=on_progress)

    ok_items = [it for it in project.items
                if (it.get("parse") or {}).get("status") == "ok"]
    if not ok_items:
        raise BuildError(
            "沒有任何一項成功產出樂譜。逐項原因：\n"
            + "\n".join(
                f"  第 {it['index']} 項 {it['file']}：{(it.get('parse') or {}).get('error', '未知')}"
                for it in project.items
            )
        )

    failed = [it for it in project.items if it not in ok_items]

    _note(on_progress, "repair", "修正拍數")
    repairs = pagemap.repair_pages(project, ok_items)

    _note(on_progress, "merge", "合併")
    sources = [project.dir / it["parse"]["musicxml"] for it in ok_items]
    scores_dir = Path(scores_dir) if scores_dir else SCORES_DIR
    out_path = scores_dir / f"{project.name}.musicxml"
    try:
        out_path, merge_stats = merge.merge_musicxml(sources, out_path)
    except merge.MergeError as exc:
        raise BuildError(str(exc)) from exc

    _note(on_progress, "validate", "樂理檢查")
    # 拍號要一頁一頁往下接：多頁的譜只在第一頁印拍號，後面幾頁沿用。
    # 每一頁都從 4/4 重新起算的話，那些沒印拍號的頁會整頁報錯（實測 12 首裡 23 頁）。
    reports = []
    carried = (4, 4)
    for it in ok_items:
        report = validate.check_musicxml(project.dir / it["parse"]["musicxml"],
                                         label=f"第 {it['index']} 項 {it['file']}",
                                         initial=carried)
        carried = report["stats"].get("next_time") or carried
        reports.append(report)
    for item, report in zip(ok_items, reports):
        item["parse"].update({
            "measures": report["stats"].get("measures"),
            "notes": report["stats"].get("notes"),
            "confidence": report["confidence"],
            "problems": report["problems"],
        })
    sequence_problems = validate.check_sequence(reports)
    merged_report = validate.check_musicxml(out_path, label=project.name)

    _note(on_progress, "layout", "找出小節位置")
    measure_map = pagemap.detect_layout(project, ok_items, force=force,
                                        total_measures=merge_stats.get("measures"))

    # 以下三步都要等版面出來才知道記號涵蓋哪幾小節，而且都套在**合併檔**上。
    # 理由見 `enrich.py` 的模組說明。
    _note(on_progress, "ottava", "套用八度記號")
    ottava_notes = enrich.apply_ottavas(project, ok_items, out_path)

    _note(on_progress, "symbols", "讀譜上的記譜符號")
    page_symbols = enrich.apply_page_symbols(
        pagemap.read_page_symbols(project, ok_items), out_path)

    time_fixes = enrich.align_signatures(out_path)

    # 第二個引擎（Audiveris）：**只讀 homr 讀不到的東西**。
    # homr 對 dynamics / wedge / ending / octave-shift / pedal 一律輸出 0，
    # 而音符層面 homr 明顯更強（André 100% vs 89.8%），所以這裡不碰音符。
    _note(on_progress, "second", "第二引擎讀結構記號")
    second_marks = enrich.second_engine_marks(project, ok_items, force=force)
    dynamics_written = enrich.apply_dynamics(out_path, second_marks) if second_marks else 0
    ottava_check = enrich.cross_check_ottavas(ok_items, second_marks) if second_marks else {}

    # 反覆展開要排在**所有小節層級的修正之後**：拍號校正、8va、強弱都是以
    # 原始小節編號在做，展開會把編號整個改掉。展開之後小節框對照表也要跟著展開，
    # 否則檢討畫面會圈到錯的位置。
    _note(on_progress, "repeats", "展開反覆記號")
    repeat_info = repeats.expand_file(out_path)
    if repeat_info["expanded"] != repeat_info["original"]:
        measure_map = chart_mod.expand_measure_map(measure_map, repeat_info["order"])

    merged_report = validate.check_musicxml(out_path, label=project.name)

    _note(on_progress, "title", "讀出曲名")
    detected_title = pagemap.detect_title(project, ok_items)

    _note(on_progress, "tempo", "偵測速度")
    detected = tempo.detect(
        musicxml=out_path,
        images=[project.path_of(it) for it in ok_items if it["kind"] == "image"],
        notation_bpm=_guess_bpm(project),
    )

    # 使用者明講的最大；否則用偵測到的；都沒有就先用預設值，
    # 但把 needs_bpm 標起來讓上層去問 —— 悄悄套一個猜的值，
    # 音遊譜面會整個對不上而使用者不知道為什麼。
    if bpm:
        used_bpm, needs_bpm = float(bpm), False
        detected = tempo.TempoResult(float(bpm), "manual", "你指定的", 1.0)
    elif detected.ok:
        used_bpm, needs_bpm = float(detected.bpm), False
    else:
        used_bpm, needs_bpm = DEFAULT_BPM, True

    _note(on_progress, "chart", "產生音遊譜面")
    charts_dir = Path(charts_dir) if charts_dir else CHARTS_DIR
    # 檔名沿用專案名（換名字會讓舊譜面變孤兒），但**顯示的曲名用譜上讀到的**
    shown_title = (detected_title.title
                   if project.name.startswith(AUTO_NAME_PREFIX) and detected_title.ok
                   else project.name)
    # 中途換速度的曲子要用速度地圖，不能整首一個 BPM。
    # 〈うまぴょい伝説〉四頁上印了 8 個節拍器記號，只取一個等於整首都用錯速度。
    tempo_map = pagemap.detect_tempo_map(project, ok_items, used_bpm,
                                         last_measure=merge_stats.get("measures"))

    charts = chart_mod.write_charts(
        out_path, out_dir=charts_dir, bpm=used_bpm, title=shown_title, stem=project.name,
        measure_map=measure_map, project_dir=project.dir, tempo_map=tempo_map,
        # Gate B 已經知道哪些小節拍數不對 —— 把它帶進譜面，讓遊戲能誠實
        # 標出「這一段我們辨識得不可靠」，而不是靜靜地讓玩家去打錯的音符。
        bad_measures=(merged_report.get("stats") or {}).get("bad_measures"),
    )

    info = {
        "musicxml": str(out_path),
        "charts": {str(k): str(v) for k, v in charts.items()},
        "levels": sorted(charts),
        "hands": _hand_report(out_path),
        "title": detected_title.as_dict(),
        "title_text": detected_title.describe(),
        "shown_title": shown_title,
        "bpm": used_bpm,
        "tempo_map": [{"measure": m, "bpm": round(b, 2)} for m, b in tempo_map],
        "tempo": detected.as_dict(),
        "tempo_text": detected.describe(),
        "needs_bpm": needs_bpm,
        "project_dir": str(project.dir),
        "measure_map": measure_map,
        "measure_map_count": len(measure_map),
        # 每一頁的規則層報告。**讀到的**與**推算的**分開記，因為推算的等於猜的，
        # 不該讓信心分數看起來比實際可靠。
        "rules": [dict(index=it["index"], **r.as_dict()) for it, r in repairs],
        "ottava_notes": ottava_notes,
        "page_symbols": page_symbols,
        # 第二引擎的成果。強弱是**新增的能力**（homr 一個都不產）；
        # 8va 是交叉比對報告，不套用（理由見 enrich.cross_check_ottavas）。
        "dynamics_written": dynamics_written,
        "second_engine": {k: len(v) for k, v in (second_marks or {}).items()},
        "ottava_cross_check": ottava_check,
        # 反覆展開：譜上寫著反覆卻不展開的話，那些曲子只會彈一遍
        "repeats": repeat_info,
        "time_fixes": time_fixes,
        "measures": merge_stats.get("measures"),
        "notes": merge_stats.get("notes"),
        "confidence": merged_report["confidence"],
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    project.set_build(info)

    return {
        "build": info,
        "merge": merge_stats,
        "reports": reports,
        "sequence_problems": sequence_problems,
        "merged_report": merged_report,
        "failed": [{"index": it["index"], "file": it["file"],
                    "error": (it.get("parse") or {}).get("error")} for it in failed],
    }


def _hand_report(musicxml):
    """左右手分得出來嗎？分不出來要講清楚，不要讓「難度 1」無聲消失。

    左右手來自譜上的上下行，不是音高 —— 上面那行中途換低音譜號的情況很常見，
    那些音仍然是右手彈。辨識引擎沒認出大譜表的括弧時，兩行會被壓成一行，
    這個資訊就真的不見了，只能誠實說沒有。
    """
    try:
        import partitura as pt
        note_array = difficulty.note_array_with_staff(pt.load_score(str(musicxml)))
    except Exception as exc:      # noqa: BLE001
        return {"ok": False, "reason": f"讀不了樂譜：{exc}"}

    collapsed = difficulty.collapsed_grand_staff(note_array)
    reaches = difficulty.unplayable_reaches(note_array)
    return {
        "ok": not collapsed,
        "collapsed": bool(collapsed),
        "unplayable": len(reaches),
        "reason": ("辨識引擎把大譜表壓成一行，左右手的資訊沒有留下來，"
                   "所以只有難度 2（雙手）。左右手是看譜上畫在哪一行決定的，"
                   "猜不得 —— 硬用音高分會產生一隻手同時按超過八度的譜。")
        if collapsed else "",
    }


def song_title(project):
    """要顯示給玩家看的曲名。

    優先用**譜上讀到的**；使用者自己取的名字只有在「不是系統自動編的」時候
    才蓋過它 —— 使用者當然可以自己命名，但不該被逼著非取不可。
    """
    build = project.data.get("build") or {}
    detected = (build.get("title") or {}).get("title")
    if project.name.startswith(AUTO_NAME_PREFIX) and detected:
        return detected
    return project.name


def _rejection_message(project, rejected):
    lines = [f"有 {len(rejected)} 項沒有通過品質檢查，沒有產出樂譜：", ""]
    for item in rejected:
        what = "重拍這一頁" if item["kind"] == "image" else "修正這個檔案"
        lines.append(f"  第 {item['index']} 項　{item.get('original_name', item['file'])}")
        for issue in (item.get("check") or {}).get("issues", []):
            if issue["level"] != "reject":
                continue
            lines.append(f"      - {issue['message']}")
            if issue.get("hint"):
                lines.append(f"        → {issue['hint']}")
        lines.append(f"      ({what}之後執行："
                     f" run.py score replace {project.name} {item['index']} <新檔案>)")
        lines.append("")
    return "\n".join(lines).rstrip()


def _guess_bpm(project):
    """記譜檔自己會寫 BPM，照片沒有這個資訊。"""
    for item in project.items:
        bpm = (item.get("parse") or {}).get("bpm")
        if bpm:
            return bpm
    return None


def _note(on_progress, stage, label):
    if on_progress:
        on_progress(0, 0, {"stage": stage, "label": label}, None)


# ---------------------------------------------------------------------------
# 狀態查詢（CLI 與網頁共用）
# ---------------------------------------------------------------------------

def status(project):
    return {
        "name": project.name,
        "source_type": project.source_type,
        "created": project.data.get("created"),
        "items": project.summary(),
        "build": project.data.get("build"),
        "counts": {
            "total": len(project.items),
            "ok": sum(1 for it in project.items
                      if (it.get("check") or {}).get("verdict") == "ok"),
            "warn": sum(1 for it in project.items
                        if (it.get("check") or {}).get("verdict") == "warn"),
            "reject": len(project.rejected()),
            "unchecked": len(project.unchecked()),
        },
    }


def make_charts(project, levels=None, bpm=None, charts_dir=None):
    """只重產音遊譜面，不重跑辨識。調 BPM 的時候用這個。"""
    build_info = project.data.get("build")
    if not build_info:
        raise BuildError(
            f"專案「{project.name}」還沒有建構過。"
            f"先執行 `run.py score build {project.name}`。"
        )
    musicxml = Path(build_info["musicxml"])
    if not musicxml.exists():
        raise BuildError(f"找不到樂譜檔 {musicxml}，請重新執行 build。")

    explicit = bpm is not None
    bpm = float(bpm or build_info.get("bpm") or DEFAULT_BPM)

    # 速度地圖要沿用 build 那次讀到的，不然只是重產一次譜面就會把它弄丟：
    # 〈うまぴょい伝説〉頁面上有 8 個節拍器記號、蕭邦有 2 段，掉回單一 BPM
    # 等於整首後半都用錯速度，而且沒有任何訊息。
    # 使用者親自指定速度時例外 —— 那就是要整首照他說的走。
    tempo_map = None
    if not explicit:
        stored = build_info.get("tempo_map") or []
        tempo_map = [(int(seg["measure"]), float(seg["bpm"])) for seg in stored] or None

    charts = chart_mod.write_charts(
        musicxml, out_dir=Path(charts_dir) if charts_dir else CHARTS_DIR,
        levels=levels, bpm=bpm,
        # 曲名用 build 時決定的那個（自動命名的專案會用譜上讀到的標題），
        # 不能退回資料夾名稱 —— 那會讓選曲畫面上的曲名在調速度之後變回代號。
        title=build_info.get("shown_title") or project.name,
        stem=project.name,
        measure_map=build_info.get("measure_map"),
        project_dir=build_info.get("project_dir"),
        tempo_map=tempo_map,
    )
    build_info["charts"] = {str(k): str(v) for k, v in charts.items()}
    build_info["levels"] = sorted(charts)
    build_info["bpm"] = bpm
    if explicit:
        # 使用者親自指定了，就不再是「偵測不到，先湊一個」的狀態
        build_info["needs_bpm"] = False
        build_info["tempo"] = tempo.TempoResult(bpm, "manual", "你指定的", 1.0).as_dict()
        build_info["tempo_text"] = f"{bpm:g} BPM（你指定的）"
        # 記下來的地圖要跟真的寫進譜面的一致，否則下次不帶 --bpm 重產時
        # 會把使用者剛推翻掉的舊地圖又撿回來。
        build_info["tempo_map"] = [{"measure": 1, "bpm": bpm}]
    project.set_build(build_info)
    return charts


def delete_project(project, charts_dir=None):
    """刪掉專案，連它產出的樂譜與音遊譜面一起。回傳 (刪掉了哪些檔案, 提醒)。

    只砍專案資料夾是不夠的：`data/charts/<名字>_lv*.json` 會繼續留在選曲畫面上，
    使用者刪掉專案之後照樣選得到那首曲子 —— 而重新辨識所需的原始照片已經沒了，
    那份譜面永遠修不好。`data/scores/<名字>.musicxml` 同理，評分還是讀得到。

    樂譜檔認 build 紀錄裡的那一份路徑，不用檔名猜 ——
    使用者自己放進 data\\scores 的同名譜不該被連坐。
    譜面檔則是純產物，照 `<名字>_lv<數字>.json` 掃就好。
    """
    import shutil
    import time

    build_info = project.data.get("build") or {}
    removed = []

    for path in (build_info.get("charts") or {}).values():
        path = Path(path)
        if path.exists():
            path.unlink()
            removed.append(str(path))

    charts_dir = Path(charts_dir) if charts_dir else CHARTS_DIR
    stem = project.name
    if charts_dir.exists():
        for stale in charts_dir.glob("*.json"):
            name = stale.name
            if name.startswith(f"{stem}_lv") and name[len(stem) + 3:-5].isdigit():
                stale.unlink()
                removed.append(str(stale))

    musicxml = build_info.get("musicxml")
    if musicxml and Path(musicxml).exists():
        Path(musicxml).unlink()
        removed.append(str(musicxml))

    # `ignore_errors=True` 會把失敗吞掉，回報「刪掉了」但東西還在 ——
    # Windows 上很常見（OneDrive 同步、防毒掃描、檔案總管開著那個資料夾都會
    # 短暫抓住 handle）。實測就遇過內容清空了、資料夾本身留下來的情況。
    # 所以重試幾次，最後**照實回報**還在不在，不要假裝成功。
    for _ in range(4):
        shutil.rmtree(project.dir, ignore_errors=True)
        if not project.dir.exists():
            break
        time.sleep(0.3)

    if project.dir.exists():
        # 內容已經清光了，專案在清單上也消失了（列表看的是 manifest.json）。
        # 剩一個空資料夾不影響任何功能，所以**不當成失敗** ——
        # 這裡丟例外的話，使用者會看到「刪除失敗」而東西其實已經刪掉了。
        # 但也不能不講，不然下次同名建立時會覺得奇怪。
        return removed, (f"{project.dir} 這個空資料夾刪不掉（多半是同步工具或"
                         f"檔案總管開著），內容都清乾淨了，不影響使用。")
    removed.append(str(project.dir))
    return removed, ""


__all__ = [
    "BuildError", "Project", "ProjectError", "build", "check_project",
    "check_item", "delete_project", "difficulty", "make_charts",
    "prepared_image", "status", "transcribe_project",
]
