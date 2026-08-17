"""把樂譜轉成 Unity 音遊用的譜面（chart）。

輸出格式就是「落下式音符」需要的全部資訊 —— 什麼時間、哪一個琴鍵：

    { "t": 1.5, "d": 0.5, "midi": 60, "hand": "R", "measure": 2, "beat": 1.0 }

`t` 是這個音該落到鍵盤線的秒數、`midi` 決定落在哪一個鍵。
音遊/Assets/Scripts/PianoUI/PianoKeyboardUI.cs 的 GetKey(midi) 已經能給出
任一鍵在畫面上的座標，所以 Unity 端只要照 `t` 倒推生成時間往下掉就好。

Unity 端的落下動畫與判定不在這個檔案的範圍內，這裡只負責把資料算對。
"""

import bisect
import json
import math
from pathlib import Path

import numpy as np

from . import difficulty as diff
from . import tempo as tempo_mod

DEFAULT_BPM = 100.0
ROOT = Path(__file__).resolve().parents[2]
CHARTS_DIR = ROOT / "data" / "charts"


def _measure_table(score, bpm):
    """每一小節的起始四分音符位置與拍數。Unity 端畫小節線用。

    同時記下**名目長度**（拍號要求幾拍），`_Grid` 要拿它把認錯長度的小節壓回格線。
    """
    rows = []
    parts = getattr(score, "parts", None) or [score]
    part = parts[0]
    measures = list(getattr(part, "measures", []) or [])
    if not measures:
        return rows

    for number, measure in enumerate(measures, start=1):
        try:
            start_q = float(part.quarter_map(measure.start.t))
            end_q = float(part.quarter_map(measure.end.t))
        except (AttributeError, TypeError, ValueError):
            continue
        rows.append({
            "n": number,
            "t": round(start_q * 60.0 / bpm, 4),   # 之後會被 _Clock 依速度地圖改寫
            "quarter": round(start_q, 4),
            "beats": round(end_q - start_q, 4),
            "nominal": round(_nominal_beats(part, measure, end_q - start_q), 4),
        })
    return rows


def _notes_per_measure(onsets, grid, measures):
    """每一小節有幾個音。要在建時鐘**之前**就算得出來，所以只用四分音符位置。

    `_tag_measures()` 是拿秒數去對的，那要等時鐘建好；這裡用格線位置直接二分搜尋。
    """
    starts = [float(row["quarter"]) for row in measures]
    numbers = [int(row["n"]) for row in measures]
    counts = {}
    for onset in onsets:
        position = grid.to_nominal(float(onset))
        index = bisect.bisect_right(starts, position) - 1
        if index < 0:
            index = 0
        if index < len(numbers):
            counts[numbers[index]] = counts.get(numbers[index], 0) + 1
    return counts


def _velocity_map(musicxml, measures):
    """每一小節該用多大的力度彈。回傳 {小節號: 0-127}，沒有任何強弱記號就回 {}。

    強弱記號是**從這裡開始生效**，一直到下一個記號為止，所以要往後帶。
    來源是 `<sound dynamics="N">` —— 那是第二引擎（Audiveris）讀出來寫進去的，
    homr 自己一個都不產（實測 12 首 `<dynamics>` 全部是 0）。

    用途有兩個：音遊依它調音量，評分拿它當「譜上要求的力度」跟實際演奏比對
    （`scoring.py` 的 dynamics 維度，權重 0.13）。
    """
    from lxml import etree as _etree

    try:
        root = _etree.parse(str(musicxml)).getroot()
    except Exception:      # noqa: BLE001 - 讀不到就當成沒有強弱記號
        return {}

    marked = {}
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            try:
                number = int(measure.get("number"))
            except (TypeError, ValueError):
                continue
            node = measure.find("direction/sound[@dynamics]")
            if node is None:
                node = measure.find(".//sound[@dynamics]")
            if node is None:
                continue
            try:
                marked[number] = max(1, min(127, int(round(float(node.get("dynamics"))))))
            except (TypeError, ValueError):
                continue

    if not marked:
        return {}

    resolved, current = {}, DEFAULT_VELOCITY
    for row in measures:
        number = int(row["n"])
        current = marked.get(number, current)
        resolved[number] = current
    return resolved


def _nominal_beats(part, measure, fallback):
    """這一小節照拍號應該有幾個四分音符。讀不到就沿用實際長度（等於不做事）。

    拍號宣告之前的小節，`time_signature_map()` 回傳的是 **nan** —— 〈拍照測試〉
    的拍號印在第 10 小節，前 9 個小節全部拿到 nan。`nan <= 0` 是 False，
    只擋 <= 0 會讓 nan 一路傳進格線，整份譜面的時間變成 nan。
    """
    try:
        signature = part.time_signature_map(measure.start.t)
        beats, beat_type = float(signature[0]), float(signature[1])
    except Exception:      # noqa: BLE001 - 拿不到拍號不該擋住產譜面
        return float(fallback)
    if not (math.isfinite(beats) and math.isfinite(beat_type)):
        return float(fallback)
    if beats <= 0 or beat_type <= 0:
        return float(fallback)
    return beats * 4.0 / beat_type


class _Grid:
    """把「實際的四分音符位置」對到「名目格線上的位置」。

    小節長度認錯的殺傷力不在那一小節，在**它會傳染**：時間軸是照實際音符位置
    往後累加的，所以一個被認長 2 拍的小節會讓後面每一個音符都晚 2 拍。
    〈山魔王的宮殿〉中段 7 個超長小節合計多出 20.8 拍，後面 46 個小節
    （超過半首）整個被推歪 —— 使用者的說法是「中段開始大量出問題」。

    實測 12 首有 7 首在漂移：李斯特 +13.4%、Bach +12.2%、蕭邦 +11.4%、
    山魔王 +6.0%、Rush E +4.2%、拍照測試 -4.3%、Andre -2.4%。

    修法是逐小節做線性對應：把每一小節的實際區間壓到它拍號該有的區間，
    錯誤就只留在那一小節裡，後面照樣對得上。長度本來就正確的小節倍率是 1，
    完全不受影響 —— 另外 5 首的結果保證一個位元都不變。

    **第一與最後一小節不動**：弱起小節與收尾小節本來就允許不滿，
    硬拉成整小節反而是把對的改錯（validate 也是這樣放行的）。
    """

    def __init__(self, rows):
        self._from, self._to, self._scale = [], [], []
        if not rows:
            return

        last = len(rows) - 1
        cursor = 0.0
        for index, row in enumerate(rows):
            actual = float(row["beats"])
            nominal = float(row.get("nominal") or actual)
            if index == 0 or index == last:
                nominal = actual            # 弱起與收尾維持原樣
            if actual <= 0:
                nominal = max(nominal, 0.0)

            self._from.append(float(row["quarter"]))
            self._to.append(cursor)
            self._scale.append(nominal / actual if actual > 1e-9 else 1.0)
            row["grid_quarter"] = round(cursor, 4)
            row["grid_beats"] = round(nominal, 4)
            cursor += nominal

    def to_nominal(self, quarter):
        """實際位置 -> 格線位置。沒有任何小節被壓縮時這是恆等函式。"""
        if not self._from:
            return quarter
        index = bisect.bisect_right(self._from, quarter) - 1
        index = max(0, min(index, len(self._from) - 1))
        return self._to[index] + (quarter - self._from[index]) * self._scale[index]

    @property
    def active(self):
        """有沒有任何一小節真的被壓縮或拉長。"""
        return any(abs(s - 1.0) > 1e-9 for s in self._scale)


def expand_measure_map(measure_map, order):
    """反覆展開之後，把小節框對照表也照著展開。

    展開後第 30 小節可能是照片上的第 22 小節。小節框是以**原始編號**建的，
    不跟著改的話檢討畫面會圈到錯的地方，而且反覆出來的那一段完全圈不到。

    做法是把同一個框複製成多筆、換上展開後的編號 —— 下游完全不用改。
    `order[i]` 是展開後第 i+1 小節來自原本的第幾小節。
    """
    if not measure_map or not order:
        return measure_map

    by_origin = {}
    for entry in measure_map:
        try:
            by_origin.setdefault(int(entry["measure"]), []).append(entry)
        except (KeyError, TypeError, ValueError):
            continue

    expanded = []
    for position, source in enumerate(order, start=1):
        for entry in by_origin.get(int(source), []):
            clone = dict(entry)
            clone["measure"] = position
            clone["source_measure"] = int(source)
            expanded.append(clone)
    return expanded


def _review_pages(measure_map, project_dir):
    """把小節位置對照表整理成 chart JSON 要的形狀（依頁分組）。

    音遊的檢討畫面要把彈不好的小節在譜上圈紅，而 Unity 只讀 chart JSON ——
    不會去翻專案的 manifest。所以位置資訊要一起寫進來。

    圖檔路徑存成**相對專案根目錄**，Unity 端用 Application.dataPath 往上兩層就能接，
    絕對路徑換一台電腦就壞掉。
    """
    if not measure_map:
        return []

    root = ROOT.resolve()
    pages, order = {}, []
    for entry in measure_map:
        filename = entry.get("file")
        if not filename or not entry.get("corners"):
            continue
        if filename not in pages:
            try:
                rel = (Path(project_dir) / filename).resolve().relative_to(root)
                image = str(rel).replace("\\", "/")
            except (ValueError, TypeError):
                image = filename
            pages[filename] = {"image": image, "measures": []}
            order.append(filename)
        # 四個角攤平成 8 個數字：Unity 的 JsonUtility 沒辦法反序列化巢狀陣列
        # （float[][]），而且對不上不會報錯、只會靜靜讀成空的
        flat = []
        for x, y in entry["corners"]:
            flat.extend([round(float(x), 1), round(float(y), 1)])
        pages[filename]["measures"].append({"n": int(entry["measure"]), "corners": flat})
    return [pages[f] for f in order]


def build_chart(musicxml, level, bpm=DEFAULT_BPM, title="",
                measure_map=None, project_dir=None, tempo_map=None,
                bad_measures=None):
    """讀 MusicXML 產出一個難度的譜面資料。

    tempo_map：[(小節, BPM)]，曲子中途換速度時用。給 None 就整首一個速度。
    """
    import partitura as pt

    score = pt.load_score(str(musicxml))
    note_array = diff.note_array_with_staff(score)
    if len(note_array) == 0:
        raise ValueError(f"樂譜沒有任何音符：{musicxml}")

    selected = diff.filter_by_level(note_array, level)

    field = "onset_quarter" if "onset_quarter" in note_array.dtype.names else "onset_beat"
    dur_field = "duration_quarter" if "duration_quarter" in note_array.dtype.names else "duration_beat"

    measures = _measure_table(score, bpm)
    # 先把認錯長度的小節壓回拍號的格線，再交給速度地圖換算成秒。
    # 順序不能顛倒：格線是以四分音符為單位的，換成秒之後就分不出
    # 「這一段慢是因為速度慢」還是「因為小節被認長了」。
    grid = _Grid(measures)
    for row in measures:
        row["quarter"] = row.pop("grid_quarter", row["quarter"])
        row["beats"] = row.pop("grid_beats", row["beats"])
        row.pop("nominal", None)

    # 速度快到彈不出來的段落先降下來，**再**建時鐘 —— 時鐘一建立時間就定了。
    # 需要「每小節幾個音」與「每小節幾拍」才算得出密度，兩份資料都只有這裡有。
    counts = _notes_per_measure(selected[field], grid, measures)
    tempo_map, capped = tempo_mod.cap_by_density(
        tempo_map or [(1, float(bpm))],
        {int(r["n"]): float(r["beats"]) for r in measures},
        counts,
        measures[-1]["n"] if measures else 0,
    )
    clock = _Clock(tempo_map, measures, bpm)

    order = np.argsort(selected[field], kind="stable")
    notes = []
    for row in selected[order]:
        onset_q = grid.to_nominal(float(row[field]))
        end_q = grid.to_nominal(float(row[field]) + max(float(row[dur_field]), 1e-3))
        start = clock.seconds(onset_q)
        # 裝飾音在樂譜上的時值是 0，partitura 照實給 0，換算成秒就是 0.001 ——
        # 在音遊裡等於一個看不見也打不到的點。實測山魔王 13 個、Bach 149 個。
        # 給它一個彈得出來的最短長度（跟 _trim_same_key_overlaps 用同一個常數），
        # 並標記出來讓 Unity 可以畫得小一點。
        duration = clock.seconds(max(end_q, onset_q + 1e-3)) - start
        grace = duration < GRACE_MAX_SECONDS
        note = {
            "t": round(start, 4),
            "d": round(max(duration, MIN_DURATION if grace else 1e-3), 4),
            "midi": int(row["pitch"]),
            "hand": diff.hand_of(row["staff"]),
            "beat": round(onset_q, 4),
        }
        if grace:
            note["grace"] = True
        notes.append(note)

    _trim_same_key_overlaps(notes)
    for row in measures:
        row["t"] = round(clock.seconds(row["quarter"]), 4)
    _tag_measures(notes, measures)

    # 力度要等 _tag_measures() 之後才知道每個音在第幾小節
    velocities = _velocity_map(musicxml, measures)
    for note in notes:
        note["vel"] = velocities.get(note.get("measure"), DEFAULT_VELOCITY)

    # 誠實標出**我們自己辨識得可不可靠**。拍數不對的小節表示那裡的音符時值
    # 認錯了，音遊照樣讓玩家去打就是在給錯的東西；標出來至少玩家知道
    # 「這一段不能信」。蕭邦有 91% 的小節落在這一類。
    unreliable = {int(n) for n in (bad_measures or []) if str(n).lstrip("-").isdigit()}
    # 完全沒有音符的小節也算不可靠 —— 那是辨識在那裡什麼都沒認到，
    # 玩起來就是一段莫名其妙的空白（〈Rush E〉第 2 小節就是這樣，開頭停了 3.7 秒）。
    played = {n["measure"] for n in notes if n.get("measure")}
    for row in measures:
        number = int(row["n"])
        row["ok"] = number not in unreliable and number in played

    duration = max((n["t"] + n["d"] for n in notes), default=0.0)
    return {
        "title": title or Path(musicxml).stem,
        "level": level,
        "level_name": diff.level_name(level),
        "bpm": float(bpm),
        "duration_sec": round(duration, 3),
        "note_count": len(notes),
        # 我們自己辨識得可靠的小節比例。選曲畫面用它提醒使用者，
        # 而不是靜靜地把 91% 小節都錯的譜面當成可玩的呈現。
        "reliable_measures": sum(1 for m in measures if m.get("ok", True)),
        # 因為音符密度不可能而被降速的段落。這是**推算**的，不是譜上讀到的。
        "tempo_capped": capped,
        "hands": sorted({n["hand"] for n in notes}),
        "measures": measures,
        "notes": notes,
        # 中途換速度時每一段的起點。只有一段就是整首同一個速度。
        "tempo_map": [{"measure": m, "bpm": round(b, 2)} for m, b in clock.segments],
        # 檢討畫面用：每一頁樂譜照片，以及每個小節在上面的四個角
        "pages": _review_pages(measure_map, project_dir),
        # 專案根目錄的**絕對路徑**。`pages[].image` 是相對它的。
        #
        # Unity 端本來用 `Application.dataPath/../..` 推，那假設了「音遊專案就在
        # 專案根目錄底下」。實際佈局是 Unity 專案在 C:/UnityProjects/音遊GD、
        # Assets 用 junction 接回這裡 —— 往上兩層只會得到 C:/UnityProjects，
        # 既找不到 加樂譜.bat 也找不到樂譜照片。而 C# 在 .NET Standard 2.1 下
        # 沒有 ResolveLinkTarget 可以穿透 junction，所以由知道真實路徑的
        # 這一端寫進來最可靠。
        "root": ROOT.resolve().as_posix(),
    }


class _Clock:
    """四分音符 -> 秒。曲子中途換速度時要**分段積分**，不能整首乘同一個係數。

    速度地圖是以「第幾小節」表示的，所以先用小節表把它換成「第幾個四分音符」，
    之後就只跟四分音符打交道，跟拍號無關（2/2、6/8 都不會算錯）。
    """

    def __init__(self, tempo_map, measures, default_bpm):
        self.segments = list(tempo_map) if tempo_map else [(1, float(default_bpm))]
        starts = {int(m["n"]): float(m["quarter"]) for m in measures}

        points = []
        for measure, bpm in self.segments:
            quarter = starts.get(int(measure))
            if quarter is None or bpm <= 0:
                continue
            points.append((quarter, 60.0 / float(bpm)))
        if not points or points[0][0] > 0:
            points.insert(0, (0.0, 60.0 / float(default_bpm)))
        points.sort(key=lambda p: p[0])

        # 每個轉折點累積到那裡為止的秒數，之後查詢就是一次二分搜尋加一段乘法
        self._quarters = [p[0] for p in points]
        self._rates = [p[1] for p in points]
        self._elapsed = [0.0]
        for i in range(1, len(points)):
            span = self._quarters[i] - self._quarters[i - 1]
            self._elapsed.append(self._elapsed[i - 1] + span * self._rates[i - 1])

    def seconds(self, quarter):
        i = bisect.bisect_right(self._quarters, quarter) - 1
        i = max(0, min(i, len(self._quarters) - 1))
        return self._elapsed[i] + (quarter - self._quarters[i]) * self._rates[i]


#: 譜上沒有標強弱時用的力度。取 mf，因為那是「沒有特別指示」的預設演奏強度。
DEFAULT_VELOCITY = 80

MIN_GAP = 0.03      # 同一顆鍵上，前一個音要在下一個音之前多久放開
MIN_DURATION = 0.05
# 短於這個就當成裝飾音。樂譜上裝飾音的時值是 0（partitura 照實給 0），
# 而任何真正彈得出來的音都不會短於 20 毫秒。
GRACE_MAX_SECONDS = 0.02


def _trim_same_key_overlaps(notes):
    """同一顆琴鍵上，把前一個音縮短到不蓋住下一個音。

    一根手指沒辦法在同一顆鍵上同時按住兩個音 —— 要彈下一個音，前一個一定得先放開。
    但辨識出來的時值常常會overlap（Alkan 那份 379 音裡有 62 處，最嚴重的蓋過
    0.545 秒），原因是 OMR 把音符時值認長了、或聲部分離不完美。

    不修的話有兩個後果：畫面上音符長條會互相穿透；而且長音判定會要求玩家
    「按住到某個時間點」，但那個時間點之後還要立刻再按同一顆鍵 —— 做不到，
    自動演奏也會因此掉分。
    """
    by_pitch = {}
    for note in notes:
        by_pitch.setdefault(note["midi"], []).append(note)

    trimmed = 0
    doomed = []
    for group in by_pitch.values():
        group.sort(key=lambda n: (n["t"], -n["d"]))

        # 1 幾乎同時響的同一顆鍵 = **同一次按鍵**，要合併不是縮短。
        #   鋼琴編曲常常把同一個音同時寫在兩手（旋律音也在左手和弦裡），
        #   但一根手指只能按一次。舊版在這裡會把前一個縮成 0.05 秒的碎片
        #   （limit 比它自己的起點還早），畫面上就變成兩個音疊在同一顆鍵上。
        #   實測〈うまぴょい伝説〉有 5 組左右手同時撞在同一顆鍵。
        keep = []
        for note in group:
            # 門檻是 MIN_DURATION + MIN_GAP，不是只有 MIN_GAP：同一顆鍵要再彈一次，
            # 前一個音至少得響 MIN_DURATION、再空出 MIN_GAP 才放得下。比這更近的
            # 兩個音在鍵盤上就是同一次按鍵 —— 硬留下來會變成「前音被壓成 0.05 秒
            # 的碎片、卻仍然蓋住後音」，實測留下 1 處這樣的重疊。
            if keep and note["t"] - keep[-1]["t"] < MIN_DURATION + MIN_GAP:
                previous = keep[-1]
                end = max(previous["t"] + previous["d"], note["t"] + note["d"])
                previous["d"] = round(end - previous["t"], 4)
                # 右手是旋律，顯示上以它為準
                if note["hand"] == "R":
                    previous["hand"] = "R"
                doomed.append(id(note))
                trimmed += 1
                continue
            keep.append(note)

        # 2 剩下的照舊：前一個音要在下一個按下之前放開
        for cur, nxt in zip(keep, keep[1:]):
            limit = nxt["t"] - MIN_GAP
            if cur["t"] + cur["d"] <= limit:
                continue
            cur["d"] = round(max(MIN_DURATION, limit - cur["t"]), 4)
            trimmed += 1

    if doomed:
        dead = set(doomed)
        notes[:] = [n for n in notes if id(n) not in dead]
    return trimmed


def _tag_measures(notes, measures):
    """替每個音標上小節編號，Unity 端要顯示進度或做段落練習時用得到。"""
    if not measures:
        for note in notes:
            note["measure"] = 0
        return
    starts = [m["t"] for m in measures]
    for note in notes:
        index = np.searchsorted(starts, note["t"] + 1e-6, side="right") - 1
        note["measure"] = measures[max(0, index)]["n"]


def write_charts(musicxml, out_dir=None, levels=None, bpm=DEFAULT_BPM, title="", stem=None,
                 measure_map=None, project_dir=None, tempo_map=None,
                 bad_measures=None):
    """產出各難度的 chart JSON，回傳 {難度: 路徑}。"""
    import partitura as pt

    musicxml = Path(musicxml)
    out_dir = Path(out_dir) if out_dir else CHARTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or musicxml.stem

    if levels is None:
        note_array = diff.note_array_with_staff(pt.load_score(str(musicxml)))
        levels = diff.levels_available(note_array)

    written = {}
    for level in levels:
        chart = build_chart(musicxml, level, bpm=bpm, title=title,
                            measure_map=measure_map, project_dir=project_dir,
                            tempo_map=tempo_map, bad_measures=bad_measures)
        path = out_dir / f"{stem}_lv{level}.json"
        path.write_text(json.dumps(chart, ensure_ascii=False, indent=1), encoding="utf-8")
        written[level] = path

    # 一個記著專案根目錄的小檔案，放在譜面資料夾裡。
    #
    # Unity 端的「加入新樂譜」要知道根目錄才找得到 加樂譜.bat，而它不能用
    # `Application.dataPath` 往上推 —— Unity 專案可能不在專案資料夾底下
    # （目前就是：專案在 C:/UnityProjects/音遊GD、Assets 用 junction 接回來）。
    # 每份譜面的 JSON 裡都有 `root`，但**一份譜面都還沒有的時候**就沒地方讀了，
    # 而那正是最想按「加入新樂譜」的時候。
    try:
        (out_dir / ".project-root").write_text(ROOT.resolve().as_posix(),
                                               encoding="utf-8")
    except OSError:
        pass      # 寫不進去不影響譜面本身

    # 這一次沒產出的難度，把上一次留下的檔案刪掉。
    # 不刪的話它會一直躺在選曲畫面上：Bach 平均律原本被誤判成分得出手，
    # 產了一份裝著全曲 98% 音符的「難度 1」；修好之後系統只給難度 2，
    # 但那份壞掉的舊檔還在，使用者照樣選得到、照樣彈不了。
    #
    # 用檔名比對而不是 glob —— 曲名裡的 [ ] ? 會被 glob 當成萬用字元。
    keep = {p.name for p in written.values()}
    for stale in out_dir.glob("*.json"):
        name = stale.name
        if name in keep:
            continue
        if name.startswith(f"{stem}_lv") and name[len(stem) + 3:-5].isdigit():
            stale.unlink()
    return written


def describe(chart):
    return (f"難度 {chart['level']}（{chart['level_name']}）："
            f"{chart['note_count']} 音、{chart['duration_sec']:.1f} 秒、"
            f"{len(chart['measures'])} 小節、{'+'.join(chart['hands'])}")
