"""第二個 OMR 引擎：**只拿它讀 homr 讀不到的東西**。

homr 對整類記號完全不輸出 —— 實測 12 首的 MusicXML 裡這些全部是 0：

    wedge（漸強漸弱）　dynamics（p/f/mf）　ending（一二號結尾）
    octave-shift（8va）　pedal（踏板）　sound（速度）

我們為此自己寫了 8va 的虛線幾何偵測與速度記號的 OCR，但強弱、漸強漸弱、
一二號結尾至今是空白 —— 而「結尾會錯」正是使用者回報的問題之一。

Audiveris 是成熟的古典 OMR，這些本來就在它的能力範圍內。實測蕭邦 4 頁：

    dynamics 61　octave-shift 29　wedge 21　ending 17　pedal 85　sound 146

反過來，**音符層面 homr 明顯更強**（articulations 2318 vs 267、slur 582 vs 166、
tie 172 vs 13），而且在有標準答案的 André 上是 100% vs 89.8%。

所以這裡**不是**要換引擎，是分工：

    homr       -> 音符、音高、節奏、連結線、圜滑線
    Audiveris  -> 強弱、漸強漸弱、一二號結尾、8va、踏板

回傳的記號帶著**頁內第幾小節**，由呼叫端換算成合併檔的小節 ——
跟 `enrich.apply_ottavas()` 同一套（見那裡的註解：小節數對不上時按比例換算，
「差一小節的錯誤，遠比整段錯輕」）。
"""

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

# Audiveris 一頁大約 20-40 秒，複雜的譜留寬一點
PAGE_TIMEOUT = 900

#: 解壓縮後找不到這些就當作這一頁失敗
_SCORE_SUFFIX = ".xml"


class AudiverisError(RuntimeError):
    """Audiveris 跑失敗。訊息是給使用者看的。"""


def _default_home():
    """找 Audiveris 的安裝位置。找不到回 None（表示這台機器沒裝）。"""
    override = os.environ.get("AUDIVERIS_HOME")
    candidates = [Path(override)] if override else []
    candidates += [
        Path(r"C:\audiveris\Audiveris"),
        Path(r"C:\Program Files\Audiveris"),
        Path.home() / "Audiveris",
    ]
    for base in candidates:
        if (base / "Audiveris.exe").exists():
            return base
    return None


class AudiverisEngine:
    """https://github.com/Audiveris/audiveris

    走 subprocess 的理由跟 homr 一樣（見 omr_engine 模組開頭），另外它是 Java 程式，
    本來就只能這樣叫。
    """

    name = "audiveris"

    def __init__(self, home=None, timeout=PAGE_TIMEOUT):
        self.home = Path(home) if home else _default_home()
        self.timeout = timeout

    def available(self):
        return bool(self.home) and (self.home / "Audiveris.exe").exists()

    def transcribe(self, image, out_dir=None):
        """辨識一張圖，回傳 .musicxml 路徑（已經解開 .mxl 並修好 divisions）。"""
        if not self.available():
            raise AudiverisError(
                "找不到 Audiveris。可以設定環境變數 AUDIVERIS_HOME 指向安裝資料夾。"
            )
        image = Path(image)
        if not image.exists():
            raise AudiverisError(f"找不到圖檔：{image}")

        target_dir = Path(out_dir) if out_dir else image.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / f"{image.stem}.audiveris.musicxml"

        # Audiveris 對非 ASCII 路徑同樣不可靠，一律先搬到純 ASCII 的暫存區
        workspace = Path(tempfile.mkdtemp(prefix="aud_", dir=tempfile.gettempdir()))
        try:
            staged = workspace / f"page{image.suffix.lower()}"
            shutil.copyfile(image, staged)
            out = workspace / "out"
            try:
                proc = subprocess.run(
                    [str(self.home / "Audiveris.exe"), "-batch", "-export",
                     "-output", str(out), str(staged)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self.timeout, cwd=str(self.home),
                )
            except subprocess.TimeoutExpired as exc:
                raise AudiverisError(f"Audiveris 超過 {self.timeout} 秒沒跑完。") from exc
            except OSError as exc:
                raise AudiverisError(f"叫不起 Audiveris：{exc}") from exc

            produced = sorted(out.rglob("*.mxl"))
            if not produced:
                raise AudiverisError(
                    "Audiveris 沒有產出樂譜檔。常見原因是**行距解析度不足** —— "
                    "它會直接拒收（log 裡是 ScaleBuilder.checkResolution / Sheet ignored）。"
                    + _tail(proc.stdout or proc.stderr)
                )

            xml = _unpack(produced[0])
            unify_divisions(xml)
            final.write_bytes(xml.read_bytes())
            return final
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


def _unpack(mxl_path):
    """.mxl 是壓縮過的 MusicXML，解出裡面那份 .xml。"""
    with zipfile.ZipFile(mxl_path) as bundle:
        names = [n for n in bundle.namelist()
                 if n.endswith(_SCORE_SUFFIX) and "META-INF" not in n]
        if not names:
            raise AudiverisError("Audiveris 的 .mxl 裡沒有樂譜檔")
        out = mxl_path.with_suffix(".musicxml")
        out.write_bytes(bundle.read(names[0]))
        return out


def unify_divisions(path):
    """把一個 part 裡多種 divisions 統一成最小公倍數，時值等比放大。回傳改了幾小節。

    partitura 不支援「同一個 part 有多個 divisions」，會直接拋
    `Note array from parts with multiple divisions is not supported`。
    Audiveris 的輸出常常是這樣（實測 André 是 8 與 4 兩種），所以產出當下就修掉，
    理由跟 `musicxml_fix.sanitize_file()` 一樣：寫到磁碟上的檔案本身要是合法的。
    """
    import math

    tree = etree.parse(str(path))
    root = tree.getroot()
    changed = 0
    for part in root.findall("part"):
        values = []
        for measure in part.findall("measure"):
            for attributes in measure.findall("attributes"):
                text = attributes.findtext("divisions")
                if text:
                    try:
                        values.append(int(float(text)))
                    except ValueError:
                        pass
        values = [v for v in values if v > 0]
        if len(set(values)) < 2:
            continue

        lcm = 1
        for value in set(values):
            lcm = lcm * value // math.gcd(lcm, value)

        current, first = values[0], True
        for measure in part.findall("measure"):
            for attributes in measure.findall("attributes"):
                node = attributes.find("divisions")
                if node is None:
                    continue
                try:
                    current = int(float(node.text))
                except (TypeError, ValueError):
                    pass
                if first:
                    node.text = str(lcm)
                    first = False
                else:
                    attributes.remove(node)
            factor = lcm // current if current else 1
            if factor != 1:
                for node in measure.iter("duration"):
                    try:
                        node.text = str(int(round(float(node.text) * factor)))
                    except (TypeError, ValueError):
                        pass
                changed += 1
    if changed:
        tree.write(str(path), encoding="UTF-8", xml_declaration=True)
    return changed


def read_annotations(musicxml):
    """從 Audiveris 的輸出讀出「homr 讀不到的那些記號」。

    回傳 dict，每一項都帶著**頁內第幾小節**（1 起算），由呼叫端換算成合併檔的小節：

        octave  [{measure, shift, staff}]      8va / 8vb，shift 是半音數
        dynamic [{measure, mark}]              p / f / mf ...
        wedge   [{measure, kind}]              crescendo / diminuendo / stop
        ending  [{measure, numbers, kind}]     一二號結尾
        pedal   [{measure, kind}]              start / stop / change
    """
    found = {"octave": [], "dynamic": [], "wedge": [], "ending": [], "pedal": []}
    try:
        root = etree.parse(str(musicxml)).getroot()
    except (OSError, etree.XMLSyntaxError):
        return found

    for part in root.findall("part"):
        # 8va 要配成**區間**（從哪一小節到哪一小節），因為 rules.apply_ottavas()
        # 收的是區間。MusicXML 用 type="up"/"down" 開始、type="stop" 結束。
        open_shifts = {}
        for index, measure in enumerate(part.findall("measure"), start=1):
            for direction_node in measure.findall("direction"):
                staff = (direction_node.findtext("staff") or "1").strip()
                for shift in direction_node.findall(".//octave-shift"):
                    kind = shift.get("type")
                    number = shift.get("number") or "1"
                    key = (staff, number)
                    if kind == "stop":
                        started = open_shifts.pop(key, None)
                        if started is not None:
                            found["octave"].append({
                                "staff": staff,
                                "shift": started["shift"],
                                "from": started["measure"],
                                "to": index,
                            })
                        continue

                    try:
                        steps = int(shift.get("size") or 8)
                    except ValueError:
                        steps = 8
                    # size=8 是一個八度、15 是兩個八度。
                    #
                    # **MusicXML 的 type 是反直覺的**，寫錯會讓整段差一個八度而且
                    # 不會有任何錯誤訊息：type 指的是「印刷時音符被往哪個方向移」，
                    # 不是聲音的方向。
                    #
                    #   8va（聽起來比較高）-> 音符被畫低了 -> type="down" -> 要 +12
                    #   8vb（聽起來比較低）-> 音符被畫高了 -> type="up"   -> 要 -12
                    #
                    # 驗證：〈李斯特 鐘〉整首幾乎都是 8va，Audiveris 回報的正是
                    # type="down"，而我們自製的虛線偵測器在同一份譜上是 +12。
                    octaves = 1 if steps <= 8 else 2
                    sign = 1 if kind == "down" else -1
                    open_shifts[key] = {"measure": index, "shift": sign * 12 * octaves}

        # 到頁尾還沒收掉的，就讓它延續到最後一小節
        last = len(part.findall("measure"))
        for started in open_shifts.values():
            found["octave"].append({
                "staff": "1", "shift": started["shift"],
                "from": started["measure"], "to": last,
            })

        for index, measure in enumerate(part.findall("measure"), start=1):
            for node in measure.findall(".//dynamics"):
                marks = [child.tag for child in node if isinstance(child.tag, str)]
                if marks:
                    found["dynamic"].append({"measure": index, "mark": marks[0]})

            for node in measure.findall(".//wedge"):
                found["wedge"].append({"measure": index, "kind": node.get("type")})

            for node in measure.findall(".//ending"):
                found["ending"].append({
                    "measure": index,
                    "numbers": node.get("number") or "",
                    "kind": node.get("type"),
                })

            for node in measure.findall(".//pedal"):
                found["pedal"].append({"measure": index, "kind": node.get("type")})

    return found


def _tail(text, lines=6):
    if not text:
        return ""
    kept = [ln for ln in text.strip().splitlines() if ln.strip()][-lines:]
    return "\n（Audiveris 的最後幾行輸出）\n  " + "\n  ".join(kept) if kept else ""
