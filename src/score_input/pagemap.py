"""把「照片上的位置」對到「第幾小節」。

從 `pipeline.py` 拆出來的。這一組函式共用同一個難題，所以放在一起：

    照片 -> 前處理 -> 版面偵測（小節框）-> 頁內第幾格 -> 全曲第幾小節

**關鍵是一律用「頁內第幾格」，不是譜上印的號碼。** 合併檔的小節是照順序編的，
而辨識偶爾會在某一頁漏掉或多切幾個小節（李斯特第 3 頁 18 格對 15 小節），
印出來的號碼在那之後就跟合併檔對不上了。`layout.py` 更是讀得到號碼就給全曲編號、
讀不到就退回每頁 1..N，兩種混在一起 —— 〈Rush E〉十頁裡 p2/p6/p10 是每頁編號，
照號碼套的話第 6 頁（全曲第 84 小節起）的速度記號會全部砸到開頭十幾小節。

所以每一個「把頁上的東西套到樂譜」的功能都走同一條路：
`scaled(頁內第幾格, 這頁幾格, 這頁幾小節) + 前面幾頁的累計偏移`。
`enrich.py` 的八度記號、記譜符號、第二引擎也是用這裡的 `scaled()`。
"""

from pathlib import Path

from . import layout, ocr, preprocess, rules, tempo, title as title_mod


def prepared_image(project, item, force=False):
    """回傳這一項要送進辨識與版面偵測的圖檔。

    照片會先經過 `preprocess.prepare()`（透視矯正、攤平打光、裁到樂譜）。
    **Gate A 檢查的仍然是原圖** —— 使用者要重拍的是原圖，回饋當然要針對原圖；
    但辨識吃整理過的版本，因為 homr 一律把圖縮到寬 1920，紙上的白邊等於白白
    佔掉譜線的解析度。

    整理失敗就退回原圖：前處理只是加分項，不該讓一張本來能辨識的照片變成不能。
    """
    if item["kind"] != "image":
        return project.path_of(item)

    out = project.dir / f"{Path(item['file']).stem}_prep.png"
    stored = item.get("prep")
    if stored and not stored.get("error") and out.exists() and not force:
        return out

    try:
        report = preprocess.prepare(project.path_of(item), out)
    except Exception as exc:      # noqa: BLE001 - 前處理不該擋住辨識
        item["prep"] = {"error": str(exc)}
        project.save()
        return project.path_of(item)

    item["prep"] = dict(report.as_dict(), file=report.path.name)
    project.save()
    return report.path


def scaled(index, boxes, measures):
    """頁內第 index 格 -> 這一頁 MusicXML 的第幾小節（1 起算）。"""
    if boxes <= 0 or measures <= 0:
        return max(1, index)
    if boxes == measures:
        return index
    return min(measures, max(1, int(round(index * measures / boxes))))


# ---------------------------------------------------------------------------
# 小節框
# ---------------------------------------------------------------------------

def detect_layout(project, ok_items, force=False, total_measures=None):
    """找出每個小節畫在照片的哪裡，串成一份跨頁的對照表。

    練習檢討要「把彈不好的小節在譜上圈紅」，就需要這份對照表。
    只有照片來源才有 —— 文字記譜沒有圖可以圈。

    小節編號優先用**譜上印的號碼**（`layout` 會 OCR 每個系統左端的數字），
    讀不到才退回跨頁累加。兩邊如果數不一樣就記下來，讓上層知道對照可能有偏移。

    **跑兩遍**：每一頁最後一個系統要知道「下一頁從第幾小節開始」才算得出它有幾個
    小節，所以先掃一遍拿到各頁的起點，再回頭正式偵測一次。
    """
    images = [(item, prepared_image(project, item, force=force))
              for item in ok_items if item["kind"] == "image"]
    if not images:
        return []

    # 第一遍：只為了拿到每一頁印出來的起始小節號
    firsts = []
    for _, image in images:
        try:
            firsts.append(layout.detect(image).first_measure)
        except Exception:      # noqa: BLE001 - 偵測失敗不該擋住整個建構
            firsts.append(0)
    # 第一頁一定從第 1 小節開始（頁序就是曲子的順序），不管它上面印了什麼號碼。
    # 第一個系統照慣例不印 "1"，讀到的會是第二行的號碼，直接拿來用會少算一整行。
    firsts[0] = 1

    entries = []
    for position, (item, image) in enumerate(images):
        # 下一頁的起點；最後一頁改用「總小節數 + 1」當右邊界
        following = None
        if position + 1 < len(images):
            following = firsts[position + 1] or None
        elif total_measures:
            following = total_measures + 1

        stored = item.get("layout")
        if stored and not force:
            page = stored
        else:
            try:
                page = layout.detect(
                    image, first_measure=firsts[position] or None,
                    next_first_measure=following,
                ).as_dict()
            except Exception as exc:      # 偵測失敗不該擋住整個建構
                page = {"error": str(exc), "measures": []}
            item["layout"] = page
            project.save()

        for box in page.get("measures", []):
            # 譜上印的號碼優先；讀不到才退回跨頁累加。
            # 累加是從**上一格的號碼**往下數，不是 len(entries)+1 ——
            # 前面有頁面漏掉小節時，那兩個數字會不一樣，而錨點才是可信的。
            number = box.get("number")
            if not number:
                number = (entries[-1]["measure"] + 1) if entries else 1
            entries.append({
                "measure": number,
                "item": item["index"],
                "file": image.name,
                "corners": box["corners"],
                "system": box["system"],
                "exact": box.get("exact", True),
            })

    _warn_duplicate_measures(entries)
    return entries


def _warn_duplicate_measures(entries):
    """同一個小節號指到兩個地方時出聲。

    練習檢討是靠小節號去查「圈在照片的哪裡」的，一號兩地就會圈錯位置，
    而且**不會有任何錯誤訊息** —— 只是紅框畫在別的地方，看起來像辨識爛掉。
    所以寧可吵一點也要講出來。
    """
    seen = {}
    clashes = []
    for entry in entries:
        key = entry["measure"]
        if key in seen and seen[key] != entry["file"]:
            clashes.append((key, seen[key], entry["file"]))
        seen.setdefault(key, entry["file"])
    if clashes:
        shown = "、".join(f"第 {n} 小節（{a} 與 {b}）" for n, a, b in clashes[:5])
        print(f"⚠ 小節位置對照表有 {len(clashes)} 個重複編號：{shown}"
              f"{'…' if len(clashes) > 5 else ''}\n"
              f"  檢討畫面圈紅的位置可能會指到錯的那一頁。")
    return clashes


# ---------------------------------------------------------------------------
# 速度地圖與曲名
# ---------------------------------------------------------------------------

def detect_tempo_map(project, ok_items, default_bpm, last_measure=None):
    """掃過每一頁的節拍器記號與漸快／漸慢術語，串成速度地圖。

    要用 `item["layout"]`（小節框）才能把記號對到第幾小節，所以一定要排在
    `detect_layout()` 後面。

    節拍器記號是**階梯**（這裡開始是 120），漸變術語是**斜坡**（從這裡開始變快），
    兩者分開偵測再合起來：斜坡在 `apply_gradual()` 裡用夠密的階梯逼近。

    偵測回來的是**頁內第幾格**，這裡才換成全曲小節 —— 理由見模組開頭。
    〈Rush E〉照號碼套的話第 6 頁的 12 個記號會全部砸到開頭十幾小節，
    前段被塞進 65～90 BPM，聽起來就是「莫名其妙的停頓」。
    """
    pages, gradual, offset = [], [], 0
    for item in ok_items:
        measures = int((item.get("parse") or {}).get("measures") or 0)
        if item["kind"] != "image":
            offset += measures
            continue
        image = prepared_image(project, item)
        layout_page = item.get("layout") or {}
        boxes = len(layout_page.get("measures") or [])

        def to_global(entry, offset=offset, boxes=boxes, measures=measures):
            index = entry.get("index")
            if not index:
                return None
            return offset + scaled(int(index), boxes, measures)

        marks = tempo.marks_on_page(image, layout_page)
        for mark in marks:
            mark["measure"] = to_global(mark)
        if marks:
            item["tempo_marks"] = marks
        pages.append(marks)

        changes = tempo.gradual_on_page(image, layout_page)
        for change in changes:
            change["measure"] = to_global(change)
        if changes:
            item["tempo_gradual"] = changes
        gradual.extend(changes)

        offset += measures

    if any(pages) or gradual:
        project.save()
    steps = tempo.build_tempo_map(pages, default_bpm)
    return tempo.apply_gradual(steps, gradual, default_bpm, last_measure)


def detect_title(project, ok_items):
    """從**第一頁**讀出曲名。後面幾頁不會印標題，讀了只會讀到別的東西。"""
    for item in ok_items:
        if item["kind"] != "image":
            continue
        result = title_mod.detect(prepared_image(project, item))
        item["title"] = result.as_dict()
        project.save()
        return result
    return title_mod.TitleResult(reason="這個專案沒有照片，曲名只能自己取")


# ---------------------------------------------------------------------------
# 逐頁的規則層修正
# ---------------------------------------------------------------------------

def repair_pages(project, ok_items):
    """依序修每一頁的拍數，把拍號往下一頁接。

    在合併**之前**做，因為每一頁都要留下自己的 Gate B 報告；等合併完才修的話，
    使用者在逐頁清單上看到的還是修之前那一堆「拍數不對」。

    拍號要往下接：多頁的譜只有第一頁印拍號，第 2 頁沒有宣告就得沿用，
    不然會被當成 4/4 然後「修」成別的東西。
    """
    signature = (4, 4)
    results = []
    for item in ok_items:
        path = project.dir / item["parse"]["musicxml"]
        if not path.exists():
            continue
        rule_report = rules.apply(path, initial=signature)
        signature = rule_report.next_signature
        item["parse"]["rules"] = rule_report.as_dict()
        project.save()
        results.append((item, rule_report))
    return results


def read_page_symbols(project, ok_items):
    """把每一頁 OCR 讀到的記譜符號收成 {類別: [記號]}，小節號已換算成全曲編號。

    **這條路本來是斷的。** `rules.read_page_symbols()` 定義在 rules.py，但
    全專案沒有任何地方呼叫它 —— 唯一提到它的是 `rules.apply()` 的 docstring，
    而 `repair_pages()` 呼叫 `rules.apply(path, initial=...)` 時
    `page_symbols` 與 `ottavas` 兩個參數都沒傳。所以連音數字、踏板、強弱、
    指法整條讀取路徑從來沒有生效過（8va 是走 `enrich.apply_ottavas()` 另一條路
    才活著）。

    最具體的損失是**連音數字**：交接書記載〈うまぴょい伝説〉第 1 小節印著 5，
    `repair.py` 卻只認得三連音，從那五個音裡挑三個湊成三連音 —— 加總對了、
    音樂錯了。`rules.apply_tuplet_digits()` 正是為此寫的，但它拿不到資料。
    """
    collected = {"tuplets": [], "octave": [], "pedal": [], "dynamics": [], "fingering": []}
    offset = 0
    for item in ok_items:
        measures = int((item.get("parse") or {}).get("measures") or 0)
        if item["kind"] != "image":
            offset += measures
            continue

        layout_page = item.get("layout") or {}
        boxes = len(layout_page.get("measures") or [])
        try:
            page_text = ocr.read_page(prepared_image(project, item))
            interline = float((item.get("check") or {}).get("interline_px") or 0)
            found = rules.read_page_symbols(page_text, layout_page, interline or 16.0)
        except Exception:      # noqa: BLE001 - 讀符號是加分項，不該擋住建構
            offset += measures
            continue

        for kind, entries in found.items():
            for entry in entries:
                index = entry.get("measure")
                if not index:
                    continue
                moved = dict(entry)
                moved["measure"] = offset + scaled(int(index), boxes, measures)
                collected[kind].append(moved)
        offset += measures

    return collected
