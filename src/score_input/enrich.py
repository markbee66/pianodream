"""在**合併檔**上補進 homr 讀不到的東西。

從 `pipeline.py` 拆出來的。這幾步有一個共同點，所以放在一起：**它們都必須等到
版面偵測完成、而且要套在合併之後的整份譜上**。

    八度記號    homr 完全不產生 <octave-shift>，整段 8va 的音就這樣低一個八度
    記譜符號    譜上印的連音數字、踏板、強弱、指法（OCR 讀）
    強弱記號    homr 一律輸出 0 個 <dynamics>，由第二引擎 Audiveris 補
    拍號對齊    整首看才有足夠樣本判斷「這一段的長度其實是別處用過的拍號」

為什麼不能逐頁做：8va 一次可以橫跨好幾個系統，逐頁套的話跨頁的那一段會斷掉；
而要知道記號落在第幾小節就得先有小節框，版面偵測排在合併之後。
接在逐頁修正裡的話，首次建構時 layout 還不存在、重建時才有，會變成時有時無。

小節的對法一律走 `pagemap.scaled()`，理由見那個模組的開頭。
"""

from lxml import etree

from . import audiveris, repair, rules
from .pagemap import prepared_image, scaled

#: 強弱記號 -> MIDI 力度。音遊調音量、評分當「譜上要求的力度」用。
#: 數值取一般音源的慣例，只求相對關係對，不必精確。
DYNAMIC_VELOCITY = {
    "ppp": 20, "pp": 33, "p": 49, "mp": 64,
    "mf": 80, "f": 96, "ff": 112, "fff": 127,
    "sf": 112, "sfz": 112, "fp": 96, "fz": 112, "rf": 100, "rfz": 100,
}

_PARSER_ARGS = dict(remove_blank_text=False, recover=True, resolve_entities=False)


def _open(path):
    """讀合併檔。讀不了就回 None —— 補記號是加分項，不該擋住建構。"""
    try:
        return etree.parse(str(path), etree.XMLParser(**_PARSER_ARGS))
    except (OSError, etree.XMLSyntaxError):
        return None


def _write(tree, path):
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=False)


# ---------------------------------------------------------------------------
# 八度記號
# ---------------------------------------------------------------------------

def apply_ottavas(project, ok_items, merged_path):
    """把每一頁讀到的 8va / 8vb 套到合併檔的音高上。回傳移了幾個音。

    homr **完全不產生八度記號** —— 合併檔裡 `<octave-shift>` 是 0 個，
    整段 8va 的音就這樣低了一個八度。〈李斯特 鐘〉13 頁上印了 48 條，
    幾乎每一行右手都有，那正是使用者說「狀況奇差」聽起來的樣子。

    小節的對法：用**頁內第幾格**，不是譜上印的號碼。格數與小節數不一致時
    按比例換算 —— 差一小節的八度錯誤，遠比整段錯八度輕。
    """
    from . import layout

    spans, offset = [], 0
    for item in ok_items:
        measures = int((item.get("parse") or {}).get("measures") or 0)
        if item["kind"] != "image":
            offset += measures
            continue
        page = item.get("layout") or {}
        try:
            page_spans = layout.ottava_spans(prepared_image(project, item), page)
        except Exception:      # noqa: BLE001 - 讀不到八度記號不該擋住建構
            page_spans = []
        boxes = len(page.get("measures") or [])
        for span in page_spans:
            spans.append({
                "staff": span["staff"], "shift": span["shift"],
                "from": offset + scaled(span["index_from"], boxes, measures),
                "to": offset + scaled(span["index_to"], boxes, measures),
            })
        if page_spans:
            item["ottavas"] = page_spans
        offset += measures

    if not spans:
        return 0
    project.save()

    tree = _open(merged_path)
    if tree is None:
        return 0
    moved = rules.apply_ottavas(tree.getroot(), spans)
    if moved:
        _write(tree, merged_path)
    return moved


# ---------------------------------------------------------------------------
# 譜上印的記譜符號
# ---------------------------------------------------------------------------

def apply_page_symbols(collected, merged_path):
    """把 `pagemap.read_page_symbols()` 收來的符號套到合併檔上。回傳 {類別: 數量}。

    目前只有連音數字會真的改動樂譜。其餘（踏板、強弱、指法）先只回報數量 ——
    強弱已經由第二引擎（Audiveris）以更可靠的方式讀進來了。
    """
    counts = {k: len(v) for k, v in collected.items()}
    if not collected.get("tuplets"):
        return counts

    tree = _open(merged_path)
    if tree is None:
        return counts

    changed = rules.apply_tuplet_digits(tree.getroot(), collected["tuplets"])
    if changed:
        _write(tree, merged_path)
    counts["tuplets_applied"] = changed
    return counts


# ---------------------------------------------------------------------------
# 第二引擎（Audiveris）
# ---------------------------------------------------------------------------

def second_engine_marks(project, ok_items, force=False):
    """跑第二個引擎（Audiveris），**只取 homr 讀不到的那些記號**。

    homr 對 wedge / dynamics / ending / octave-shift / pedal 一律輸出 0
    （實測 12 首全部是 0），而 Audiveris 在蕭邦 4 頁就抓到 61 個強弱、
    29 個 8va、21 個漸強漸弱、17 個一二號結尾、85 個踏板。

    反過來音符層面 homr 明顯更強，所以這裡**不碰音符**，只補結構記號。

    小節對映跟 `apply_ottavas()` 同一套：Audiveris 認出的小節數不一定等於
    homr 的（蕭邦 4 頁是 94 vs 88），所以用「頁內第幾格 + 累計偏移 + 比例換算」。

    沒裝 Audiveris 就整個跳過 —— 這一步是加分，不該變成必要相依。
    """
    engine = audiveris.AudiverisEngine()
    if not engine.available():
        return {}

    merged = {"octave": [], "dynamic": [], "wedge": [], "ending": [], "pedal": []}
    offset = 0
    for item in ok_items:
        measures = int((item.get("parse") or {}).get("measures") or 0)
        if item["kind"] != "image":
            offset += measures
            continue

        stored = item.get("audiveris")
        if stored and not force:
            found, total = stored.get("marks") or {}, stored.get("measures") or measures
        else:
            try:
                xml = engine.transcribe(prepared_image(project, item),
                                        out_dir=project.dir)
                found = audiveris.read_annotations(xml)
                total = _count_measures(xml)
            except audiveris.AudiverisError as exc:
                item["audiveris"] = {"error": str(exc)[:200]}
                offset += measures
                continue
            item["audiveris"] = {"marks": found, "measures": total,
                                 "counts": {k: len(v) for k, v in found.items()}}

        for kind, entries in (found or {}).items():
            for entry in entries:
                index = int(entry.get("measure") or 0)
                if not index:
                    continue
                moved = dict(entry)
                moved["measure"] = offset + scaled(index, total, measures)
                merged.setdefault(kind, []).append(moved)
        offset += measures

    project.save()
    return merged


def apply_dynamics(merged_path, marks):
    """把第二引擎讀到的強弱記號寫進合併檔。回傳寫了幾個。

    homr **完全不輸出 `<dynamics>`**（實測 12 首全部是 0），所以樂譜裡沒有任何
    「這一段該多大聲」的資訊。評分的 dynamics 維度（權重 0.13）因此只能看演奏
    自己的力度變化，沒有辦法說「這裡譜上寫 f 但你彈得很輕」。

    寫成標準的 `<direction><direction-type><dynamics>`，MuseScore 打開看得到，
    partitura 也讀得到。**不改音符**，只加 direction —— 音高與時值仍然全部
    來自 homr。
    """
    entries = [m for m in (marks.get("dynamic") or []) if m.get("measure")]
    if not entries:
        return 0

    # 同一小節有好幾個時取第一個：強弱記號本來就是「從這裡開始」，
    # 一小節內的細微差別遠不如「整段的相對強弱」重要。
    wanted = {}
    for entry in sorted(entries, key=lambda e: int(e["measure"])):
        wanted.setdefault(int(entry["measure"]), entry.get("mark"))

    tree = _open(merged_path)
    if tree is None:
        return 0

    written = 0
    for part in tree.getroot().findall("part"):
        for measure in part.findall("measure"):
            try:
                number = int(measure.get("number"))
            except (TypeError, ValueError):
                continue
            mark = wanted.get(number)
            if not mark or mark not in DYNAMIC_VELOCITY:
                continue
            if measure.find("direction/direction-type/dynamics") is not None:
                continue                      # 已經有了就不重複寫

            direction = etree.Element("direction")
            direction.set("placement", "below")
            direction_type = etree.SubElement(direction, "direction-type")
            dynamics = etree.SubElement(direction_type, "dynamics")
            etree.SubElement(dynamics, mark)
            # <sound dynamics> 讓讀取端不必自己查表
            sound = etree.SubElement(direction, "sound")
            sound.set("dynamics", str(DYNAMIC_VELOCITY[mark]))
            measure.insert(0, direction)
            written += 1

    if written:
        _write(tree, merged_path)
    return written


def cross_check_ottavas(ok_items, marks):
    """拿第二引擎的 8va 跟我們自己的虛線偵測比對。**只報告，不套用。**

    兩個方法完全獨立（一個是影像幾何、一個是成熟 OMR），所以一致的區間是
    交接書坑 10 說的那種真證據 —— 跟「自己修補自己推高信心」完全不同。

    不套用的理由：實測〈李斯特 鐘〉Audiveris 回報 25 段 8va 與 **10 段 8vb**，
    而我們的偵測器（有能力認 8vb，正則涵蓋 8vb/8ba/15mb）一段 8vb 都沒找到。
    那 10 段很可能是誤判，套下去會讓整段低一個八度。我們自己的偵測器已經驗證過
    （李斯特移了 1442 個音），所以維持它當唯一的套用來源。
    """
    theirs = marks.get("octave") or []
    if not theirs:
        return {}

    ours = []
    offset = 0
    for item in ok_items:
        measures = int((item.get("parse") or {}).get("measures") or 0)
        if item["kind"] != "image":
            offset += measures
            continue
        boxes = len((item.get("layout") or {}).get("measures") or [])
        for span in item.get("ottavas") or []:
            ours.append({
                "shift": span.get("shift"),
                "from": offset + scaled(span["index_from"], boxes, measures),
                "to": offset + scaled(span["index_to"], boxes, measures),
            })
        offset += measures

    agree = conflict = only_theirs = 0
    for span in theirs:
        overlapping = [o for o in ours
                       if not (o["to"] < span["from"] or o["from"] > span["to"])]
        if not overlapping:
            only_theirs += 1
        elif any(o["shift"] == span["shift"] for o in overlapping):
            agree += 1
        else:
            conflict += 1

    return {"ours": len(ours), "theirs": len(theirs), "agree": agree,
            "conflict": conflict, "only_second_engine": only_theirs}


def _count_measures(musicxml):
    try:
        root = etree.parse(str(musicxml)).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    part = root.find("part")
    return len(part.findall("measure")) if part is not None else 0


# ---------------------------------------------------------------------------
# 拍號
# ---------------------------------------------------------------------------

def align_signatures(merged_path):
    """在合併檔上把「跟這首別處用過的拍號對得起來」的段落改回去。回傳改了幾處。

    拍號也要在合併檔上再看一次。逐頁看的時候一段只有十幾個小節，統計不出東西；
    整首看才知道「這一段的長度眾數其實是這首曲子別處用過的拍號」。
    """
    tree = _open(merged_path)
    if tree is None:
        return 0
    changed = repair.align_to_known_signatures(tree.getroot())
    if changed:
        _write(tree, merged_path)
    return changed
