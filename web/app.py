"""網頁介面：上傳樂譜、拖曳排順序、看回饋、辨識。

只在 127.0.0.1 監聽，是給本機自己用的工具，不是對外服務。

**所有邏輯都呼叫 src/score_input 的同一批函式**，這裡只負責收 HTTP 請求、
轉成那些函式的參數、把結果包成 JSON。網頁與命令列的行為因此保證一致 ——
不然「用網頁做出來的譜」跟「用指令做出來的譜」出現微妙差異會非常難查。

辨識很慢（一頁 15–40 秒），所以 build 丟到背景執行緒跑，前端輪詢進度。
"""

import sys
import tempfile
import threading
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.score_input import notation, pipeline  # noqa: E402
from src.score_input.project import Project, ProjectError  # noqa: E402

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 一次上傳最多 200MB

# 進行中的辨識工作。單人本機工具，用記憶體存就夠了。
_JOBS = {}
_JOBS_LOCK = threading.Lock()

# 各階段在總進度上的起點（%）。階段代號來自 pipeline._note()。
#
# **這是估的不是量的。** homr 辨識佔掉絕大部分時間，所以 parse 一段就吃掉 8→55，
# 後面十個階段擠在剩下的四成裡。而且文字記譜的流程會跳過 layout / symbols /
# second 這幾個階段，照片流程才全部跑到 —— 所以只保證單調遞增，不保證等速。
#
# 之所以放在這裡而不是 pipeline.py：那邊是命令列與網頁共用的，
# 命令列沒有進度條，不該為了網頁的呈現需求多背一張百分比表。
_STAGE_START = {
    "check": 2, "parse": 8, "repair": 55, "merge": 60, "validate": 65,
    "layout": 70, "ottava": 74, "symbols": 77, "second": 82,
    "repeats": 88, "title": 90, "tempo": 92, "chart": 96,
}
_STAGE_ORDER = list(_STAGE_START)


# ---------------------------------------------------------------------------

def _fail(message, code=400):
    return jsonify({"error": str(message)}), code


@app.errorhandler(ProjectError)
def _handle_project_error(exc):
    return _fail(exc)


@app.errorhandler(pipeline.BuildError)
def _handle_build_error(exc):
    return _fail(exc)


def _load(name):
    return Project.load(name)


def _save_uploads(files):
    """把上傳的檔案落到暫存資料夾，回傳依上傳順序排好的路徑。

    每個檔案放進自己的編號子資料夾，而不是在檔名前面加編號 —— 檔名會原封不動
    存進 manifest 當作顯示名稱，多一個 "000_" 前綴使用者會看得莫名其妙。
    """
    staging = Path(tempfile.mkdtemp(prefix="upload_"))
    saved = []
    for index, item in enumerate(files):
        if not item or not item.filename:
            continue
        name = secure_filename(item.filename)
        if not name or name.startswith("."):
            # secure_filename 會把純中文檔名清成空字串，那時給一個保底名稱
            name = f"upload_{index + 1:02d}{Path(item.filename).suffix.lower()}"
        slot = staging / f"{index:03d}"
        slot.mkdir(parents=True, exist_ok=True)
        target = slot / name
        item.save(target)
        saved.append(target)
    return saved


# ---------------------------------------------------------------------------
# 頁面
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


# ---------------------------------------------------------------------------
# 專案
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def list_projects():
    rows = []
    for name in Project.list_all():
        info = pipeline.status(Project.load(name))
        rows.append({
            "name": name,
            "source_type": info["source_type"],
            "count": info["counts"]["total"],
            "built": bool(info["build"]),
        })
    return jsonify(rows)


@app.post("/api/projects")
def create_project():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    source_type = data.get("source_type") or "photo"
    project = Project.create(name, source_type)
    return jsonify(pipeline.status(project)), 201


@app.get("/api/projects/<name>")
def project_status(name):
    return jsonify(pipeline.status(_load(name)))


@app.delete("/api/projects/<name>")
def delete_project(name):
    # 產出的樂譜與譜面要一起清掉，不然刪過的曲子還會留在選曲畫面上，
    # 而且原始照片已經沒了，那份譜面永遠修不好。
    project = _load(name)
    removed, note = pipeline.delete_project(project)
    return jsonify({"deleted": name, "removed": removed, "note": note})


# ---------------------------------------------------------------------------
# 項目：上傳、排序、替換、刪除
# ---------------------------------------------------------------------------

@app.post("/api/projects/<name>/items")
def add_items(name):
    project = _load(name)
    files = request.files.getlist("files")
    if not files:
        return _fail("沒有收到任何檔案")

    paths = _save_uploads(files)
    if not paths:
        return _fail("上傳的檔案都是空的")

    # sort=False：使用者在畫面上排的順序就是頁序，不要再自作主張重排
    added, skipped = project.add(paths, sort=False)
    return jsonify({
        "added": len(added),
        "skipped": skipped,
        "status": pipeline.status(project),
    })


@app.post("/api/projects/<name>/reorder")
def reorder(name):
    project = _load(name)
    order = (request.get_json(silent=True) or {}).get("order")
    if not isinstance(order, list):
        return _fail("要給一個 order 陣列，內容是目前的順序編號")
    project.reorder([int(x) for x in order])
    return jsonify(pipeline.status(project))


@app.delete("/api/projects/<name>/items/<int:index>")
def remove_item(name, index):
    project = _load(name)
    project.remove(index)
    return jsonify(pipeline.status(project))


@app.post("/api/projects/<name>/items/<int:index>/replace")
def replace_item(name, index):
    project = _load(name)
    files = request.files.getlist("files")
    paths = _save_uploads(files)
    if not paths:
        return _fail("沒有收到新的檔案")
    project.replace(index, paths[0])
    pipeline.check_item(project, project.item(index), force=True)
    return jsonify(pipeline.status(project))


@app.get("/api/projects/<name>/raw/<path:filename>")
def raw_file(name, filename):
    """送出專案資料夾裡的檔案（縮圖、標註圖、記譜原文）。"""
    project = _load(name)
    target = (project.dir / filename).resolve()
    if not str(target).startswith(str(project.dir.resolve())) or not target.exists():
        abort(404)
    return send_file(target)


@app.get("/api/projects/<name>/text/<int:index>")
def item_text(name, index):
    """記譜檔的原文，給前端做錯誤行反白。"""
    project = _load(name)
    item = project.item(index)
    if item["kind"] != "text":
        return _fail("這一項不是記譜檔")
    path = project.path_of(item)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="cp950", errors="replace")
    return jsonify({"content": content, "check": item.get("check")})


# ---------------------------------------------------------------------------
# 檢查與建構
# ---------------------------------------------------------------------------

@app.post("/api/projects/<name>/check")
def check(name):
    project = _load(name)
    force = bool((request.get_json(silent=True) or {}).get("force"))
    pipeline.check_project(project, force=force)
    return jsonify(pipeline.status(project))


@app.post("/api/projects/<name>/build")
def build(name):
    """丟到背景跑，前端輪詢 /build/progress。"""
    project = _load(name)
    with _JOBS_LOCK:
        job = _JOBS.get(name)
        if job and job["state"] == "running":
            return jsonify(job)
        _JOBS[name] = {"state": "running", "log": [], "stage": "準備中",
                       "percent": 0, "error": None, "result": None}

    options = request.get_json(silent=True) or {}
    thread = threading.Thread(
        target=_run_build, args=(name, options), daemon=True
    )
    thread.start()
    return jsonify(_JOBS[name])


def _run_build(name, options):
    # 逐項的回呼（total > 0）不帶階段代號，只有階段切換（total == 0）才帶，
    # 所以要自己記著現在在哪一段。用 dict 是為了在閉包裡改得動。
    current = {"stage": None}

    def percent_now(i, total):
        stage = current["stage"]
        if stage not in _STAGE_START:
            return None
        start = _STAGE_START[stage]
        pos = _STAGE_ORDER.index(stage)
        end = (_STAGE_START[_STAGE_ORDER[pos + 1]]
               if pos + 1 < len(_STAGE_ORDER) else 99)
        if total <= 0:
            return start
        return start + (end - start) * min(max(i, 0), total) / total

    def on_progress(i, total, item, result):
        with _JOBS_LOCK:
            job = _JOBS[name]
            if total == 0:
                current["stage"] = item.get("stage")
                job["stage"] = item["label"]
                job["log"].append({"kind": "stage", "text": item["label"]})
            elif result is not None:
                status = result.get("status")
                text = f"[{i}/{total}] {item.get('file')} — " + {
                    "ok": "完成", "failed": "失敗", "skipped": "略過",
                }.get(status, status or "")
                if result.get("error"):
                    text += f"：{result['error'].splitlines()[0]}"
                job["log"].append({"kind": status, "text": text})

            # i 從 1 起算，而「開始處理第 i 項」（result is None）時完成的是 i-1 項。
            value = percent_now(i if result is not None else i - 1, total)
            if value is not None:
                # 只進不退。階段表是估的，倒退會讓人以為當掉了，
                # 停在原地至少還像「這一步比較久」。
                job["percent"] = max(job.get("percent", 0), round(value))

    try:
        project = Project.load(name)
        outcome = pipeline.build(project, bpm=options.get("bpm"),
                                 force=bool(options.get("force")),
                                 on_progress=on_progress)
        from src.score_input import validate

        with _JOBS_LOCK:
            _JOBS[name].update({
                "state": "done",
                "stage": "完成",
                "percent": 100,
                "result": {
                    "build": outcome["build"],
                    "failed": outcome["failed"],
                    "reports": outcome["reports"],
                    "sequence_problems": outcome["sequence_problems"],
                    "report_text": validate.format_report(
                        outcome["reports"], outcome["sequence_problems"], show_ok=False
                    ),
                },
            })
    except (pipeline.BuildError, ProjectError, ValueError, RuntimeError) as exc:
        with _JOBS_LOCK:
            _JOBS[name].update({"state": "error", "stage": "失敗", "error": str(exc)})
    except Exception:  # 沒預期到的例外也要讓前端看得到，不能只是無聲卡住
        with _JOBS_LOCK:
            _JOBS[name].update({
                "state": "error", "stage": "失敗",
                "error": "程式內部錯誤：\n" + traceback.format_exc(limit=4),
            })


@app.get("/api/projects/<name>/build/progress")
def build_progress(name):
    with _JOBS_LOCK:
        job = _JOBS.get(name)
    if not job:
        return jsonify({"state": "idle", "log": [], "stage": "", "percent": 0})
    return jsonify(job)


@app.post("/api/projects/<name>/bpm")
def set_bpm(name):
    """使用者手動指定速度，重產音遊譜面。偵測不到速度時網頁會問。"""
    project = _load(name)
    data = request.get_json(silent=True) or {}
    try:
        bpm = float(data.get("bpm"))
    except (TypeError, ValueError):
        return _fail("請給一個數字")
    if not 30 <= bpm <= 300:
        return _fail("速度的合理範圍是 30–300 BPM")

    pipeline.make_charts(project, bpm=bpm)
    return jsonify(pipeline.status(project))


@app.get("/api/projects/<name>/chart/<int:level>")
def chart_data(name, level):
    """給預覽用的譜面資料（不是下載，是直接讀）。"""
    import json

    project = _load(name)
    build_info = project.data.get("build")
    if not build_info:
        return _fail("這個專案還沒有建構過")
    path = build_info.get("charts", {}).get(str(level))
    if not path or not Path(path).exists():
        return _fail(f"沒有難度 {level} 的譜面")
    return jsonify(json.loads(Path(path).read_text(encoding="utf-8")))


@app.get("/api/projects/<name>/problems/<int:level>")
def measure_problems(name, level):
    """哪些小節辨識得不可靠、為什麼、在照片上的哪裡。

    給網頁把問題小節標紅、並且和原始照片對照用。四份資料本來就都算過了，
    這裡只是把它們接起來 ——

      chart 的 measure.ok      哪幾節不可靠
      validate 的 BAD_DURATION 為什麼（拍數差多少、是多認還是漏認）
      measure_map 的 corners   在哪一張照片的哪個位置
      chart 的 notes           AI 到底讀成了什麼

    理由沒有存進 manifest，所以這裡當場重跑一次 `check_musicxml`（純 XML 解析，
    很快），好處是**已經建好的舊專案不用重新辨識就能用**。

    編號一律用**反覆展開後**的（給愛麗絲是 1–129）。`bad_measures` 與
    `measure_map` 在 pipeline 裡都已經轉成展開後的編號，兩邊一致；
    manifest 的 `measures`（106）是展開前的數字，不要拿來對。
    """
    import json

    from src.score_input import validate

    project = _load(name)
    build_info = project.data.get("build")
    if not build_info:
        return _fail("這個專案還沒有建構過")
    path = build_info.get("charts", {}).get(str(level))
    if not path or not Path(path).exists():
        return _fail(f"沒有難度 {level} 的譜面")
    chart = json.loads(Path(path).read_text(encoding="utf-8"))

    reasons = {}
    musicxml = build_info.get("musicxml")
    if musicxml and Path(musicxml).exists():
        for problem in validate.check_musicxml(musicxml).get("problems", []):
            number = problem.get("measure")
            if number is not None:
                reasons.setdefault(str(number), problem)

    boxes = {}
    for entry in build_info.get("measure_map") or []:
        if entry.get("file") and entry.get("corners"):
            boxes.setdefault(int(entry["measure"]), []).append(
                {"file": entry["file"], "corners": entry["corners"]})

    notes_of = {}
    for note in chart.get("notes") or []:
        if note.get("measure"):
            notes_of.setdefault(int(note["measure"]), []).append(note)

    rows = []
    for measure in chart.get("measures") or []:
        if measure.get("ok", True):
            continue
        number = int(measure["n"])
        problem = reasons.get(str(number)) or {}
        rows.append({
            "n": number,
            "t": measure.get("t"),
            "reason": problem.get("message") or "這一小節的拍數對不上拍號",
            "hint": problem.get("hint") or "",
            "boxes": boxes.get(number, []),
            "notes": sorted(notes_of.get(number, []), key=lambda n: n.get("t", 0)),
        })

    return jsonify({
        "total": len(chart.get("measures") or []),
        "bad": len(rows),
        "measures": rows,
    })


@app.get("/api/projects/<name>/measure-image/<int:level>/<int:number>")
def measure_image(name, level, number):
    """把某一小節從原始照片上裁下來送出去。

    使用者要判斷「AI 讀錯了什麼」，唯一的依據是譜上真正印著什麼。與其叫他
    自己在整頁照片裡找第 63 小節，不如直接把那一小節裁給他看。

    **不能直接用小節框的上下緣去裁**。實測（李斯特第 6 小節）小節框的下緣
    切在低音譜表中間 —— 照著裁會把左手的和弦切掉一半，而踏板記號 `Ped.` 更是
    整個不見。使用者正是要看那些東西才能判斷 AI 錯在哪，切掉就白做了。

    所以下緣改成往下延伸到**下一行開始之前**：踏板、力度、表情記號都住在
    行與行之間的空白帶裡。上緣反而要收緊，框的上緣本來就在譜表之上，
    再往上加就會把前一行垂下來的音符拉進畫面。
    """
    import io

    from PIL import Image

    del level      # 網址帶著難度只是為了跟前端的難度切換一致，裁圖本身用不到
    project = _load(name)
    build_info = project.data.get("build")
    if not build_info:
        return _fail("這個專案還沒有建構過")

    measure_map = build_info.get("measure_map") or []
    entries = [e for e in measure_map
               if int(e.get("measure", -1)) == number and e.get("file") and e.get("corners")]
    if not entries:
        return _fail(f"沒有第 {number} 小節的位置資料", 404)

    # 跨行的小節在對照表裡有兩筆，取第一筆（該小節的開頭）
    entry = entries[0]
    target = (project.dir / entry["file"]).resolve()
    if not str(target).startswith(str(project.dir.resolve())) or not target.exists():
        return _fail("找不到那一頁的照片", 404)

    # 同一頁上每一行（system）的上下緣，用來找出這一行下面的空白帶有多高
    bands = {}
    for other in measure_map:
        if other.get("file") != entry["file"] or not other.get("corners"):
            continue
        system = other.get("system")
        top = min(y for _, y in other["corners"])
        bottom = max(y for _, y in other["corners"])
        if system in bands:
            bands[system] = (min(bands[system][0], top), max(bands[system][1], bottom))
        else:
            bands[system] = (top, bottom)

    xs = [float(x) for x, _ in entry["corners"]]
    ys = [float(y) for _, y in entry["corners"]]
    image = Image.open(target)

    order = sorted(bands)
    here = entry.get("system")
    below = next((bands[s][0] for s in order if here is not None and s > here), None)
    if below is not None:
        # 留一小段空隙，免得把下一行的譜號和高音符切進來
        bottom = min(float(below) - 10.0, max(ys) + (float(below) - max(ys)) * 0.72)
    else:
        # 最後一行下面沒有東西可以參考，就照這一行的高度給一個比例
        bottom = max(ys) + (max(ys) - min(ys)) * 0.45

    pad_x = max(12.0, (max(xs) - min(xs)) * 0.06)
    crop = image.crop((
        max(0, int(min(xs) - pad_x)),
        max(0, int(min(ys) - 8)),
        min(image.width, int(max(xs) + pad_x)),
        min(image.height, int(max(bottom, max(ys) + 8))),
    ))

    buffer = io.BytesIO()
    crop.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@app.get("/api/projects/<name>/download/<kind>")
def download(name, kind):
    project = _load(name)
    build_info = project.data.get("build")
    if not build_info:
        return _fail("這個專案還沒有建構過")

    if kind == "musicxml":
        return send_file(build_info["musicxml"], as_attachment=True,
                         download_name=f"{name}.musicxml")
    if kind.startswith("chart"):
        level = kind.replace("chart", "") or str(max(build_info["levels"]))
        path = build_info.get("charts", {}).get(level)
        if not path:
            return _fail(f"沒有難度 {level} 的譜面")
        return send_file(path, as_attachment=True, download_name=f"{name}_lv{level}.json")
    return _fail(f"不認得的下載類型：{kind}")


# ---------------------------------------------------------------------------
# 記譜格式說明（前端的說明面板直接取用，只有一份來源）
# ---------------------------------------------------------------------------

@app.get("/api/notation-help")
def notation_help():
    return jsonify({
        "jianpu": {
            "title": "數字記譜（簡譜）",
            "example": "# 小星星\n1=C\n4/4\nBPM=100\n\n"
                       "R: 1 1 5 5 | 6 6 5 - | 4 4 3 3 | 2 2 1 -\n"
                       "L: [1, 5,] - [1, 5,] - | [1, 5,] - [5, 2,] - "
                       "| [4, 1] - [1, 5,] - | [5, 2,] - [1, 5,] -",
            "pitch": [["1-7", "音階級數，1 就是調號指定的主音"],
                      ["0", "休止符"],
                      ["1'", "高八度（可以疊：1''）"],
                      ["1,", "低八度"],
                      ["#4 b7 n4", "升 / 降 / 還原"]],
        },
        "letter": {
            "title": "字母記譜",
            "example": "# 小星星\nKEY=C\n4/4\nBPM=100\n\n"
                       "R: C4 C4 G4 G4 | A4 A4 G4 - | F4 F4 E4 E4 | D4 D4 C4 -\n"
                       "L: [C3 G3] - [C3 G3] - | [C3 G3] - [G3 D3] - "
                       "| [F3 C4] - [C3 G3] - | [G3 D3] - [C3 G3] -",
            "pitch": [["C-B", "音名，中央 C 記為 C4"],
                      ["0", "休止符"],
                      ["C5 / C3", "八度寫在後面，不寫就沿用上一個音"],
                      ["C#4 Bb3", "升降記號"]],
        },
        "common": [
            ["R: / L:", "右手 / 左手。沒有 L: 行就是難度 1（只有右手）"],
            ["-", "延長一拍"],
            ["1.", "附點（1.5 拍）；1.. 雙附點"],
            ["1_", "八分音符（一條底線）；1__ 十六分"],
            ["[1 3 5]", "和弦，時值寫在 ] 後面：[1 3 5]_"],
            ["~", "連音線，接到下一個同音高的音"],
            ["|", "小節線。每一行結束也算一條小節線"],
            ["#", "這一行剩下的是註解"],
        ],
        "levels": [[str(k), v["name"]] for k, v in pipeline.difficulty.LEVELS.items()],
    })


def run_server(host="127.0.0.1", port=5000, debug=False, open_browser=True):
    url = f"http://{host}:{port}"
    print("=" * 56)
    print(f"  樂譜輸入介面已啟動：{url}")
    print("  瀏覽器應該會自動打開；沒有的話手動貼上上面的網址。")
    print("  用完按 Ctrl+C 關掉，或直接關掉這個視窗。")
    print("=" * 56)

    if open_browser and not debug:
        # 開在背景執行緒，等伺服器真的能連了再開，不然瀏覽器會先吃到連線失敗
        import threading
        import webbrowser

        def _open():
            import socket
            import time

            for _ in range(50):
                try:
                    with socket.create_connection((host, port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_server()
