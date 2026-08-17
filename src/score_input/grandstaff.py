"""大譜表被壓成一行時，把上下兩行拆開重辨識，再拼回 staff 1 / staff 2。

## 問題長什麼樣

〈士兵進行曲〉辨識出來是這樣：

    score-part  Voice（不是 Piano）　staves 沒有宣告
    33 個小節、259 個音**全部掛在 staff 1**
    譜號在同一行上 F、G、F、G 跳來跳去

使用者的說法最直接：「左手全都被當成右手了」。在音遊裡看得到 —— 左右手是用
顏色分的（右手藍、左手橘），整首變成一個顏色。

## 不是大括號沒認出來

一開始以為是 homr 沒看到大括號。實際跑一次它自己的診斷：

    Found 11 staffs
    Found 6 connected staffs (after merging grand staffs, multiple voices): [1,1,1,1,1,1]

**每個系統都只剩一個 Staff，表示上下兩行已經被 merge 成大譜表了**（`is_grandstaff`
為真），`--write-staff-positions` 寫出來的座標也有 5 個 class=1 的框。
大括號那一關是過的。

真正失敗的是**transformer 的解碼**：`music_xml_generator._voice_has_two_staves()`
看的是「有沒有任何符號的 position 是 lower」，而這張圖一個都沒有。模型把下面
那行的內容也解成上面那行，只是中途換了譜號 —— 所以才會看到 F/G 交替。
那是模型本身的失誤，改不了。

## 作法：拆成兩張單行譜，各辨識一次

    1 跟 homr 要譜表座標（--write-staff-positions），class=1 的框就是大譜表
    2 每個框內找「墨水最少的那一列」當上下行的分界
    3 上行全部堆成一張圖、下行堆成另一張
    4 兩張各送 homr 一次 —— 它對單行譜是拿手的
    5 上行的音給 staff 1、下行的給 staff 2，逐小節併回一個 part

第 2 步刻意**不去找十條譜線**：這種背面透印的照片上，`layout._staff_line_rows()`
五個系統只認得出兩個。而「兩行中間有一條空白」穩定得多，中間 40% 裡墨水最少的
那一列就是分界，五個系統全部切對。

## 什麼時候放棄

上下兩份的小節數不一樣就**整個不採用**，退回原本的結果。硬把數量不同的兩邊
對起來只會把左右手錯開，比全部當右手更糟。實測〈士兵進行曲〉兩邊都是 32 個
小節（原本壓扁的版本是 33 個），對得起來。
"""

from pathlib import Path

import cv2
import numpy as np
from lxml import etree

from .quality import _binarize, _imread, _imwrite, estimate_interline

# 兩行之間的分界只在框的中間這一段裡找。太靠邊會切到譜線本身。
SPLIT_SEARCH_LOW = 0.30
SPLIT_SEARCH_HIGH = 0.70
# 切出來的每一條上下各留幾個行距，homr 需要一點邊才切得出譜表
STRIP_MARGIN = 2.0
# 堆疊時每一條之間空幾個行距，太近會被當成同一個系統
STACK_GAP = 4.0


def read_staff_boxes(positions_path, width, height):
    """讀 homr 的 --write-staff-positions 輸出，回傳大譜表的 (y0, y1)。

    格式是每行 `class cx cy w h`（都正規化過）。class=1 表示這個框是
    **合併過的大譜表**，也就是上下兩行一起。class=0 是單行，不用拆。
    """
    boxes = []
    try:
        lines = Path(positions_path).read_text().splitlines()
    except OSError:
        return boxes
    for line in lines:
        parts = line.split()
        if len(parts) != 5 or parts[0] != "1":
            continue
        try:
            cy, bh = float(parts[2]), float(parts[4])
        except ValueError:
            continue
        y0 = max(0, int((cy - bh / 2) * height))
        y1 = min(height, int((cy + bh / 2) * height))
        if y1 - y0 > 8:
            boxes.append((y0, y1))
    boxes.sort()
    del width
    return boxes


def _split_row(band_ink, interline):
    """大譜表兩行之間「墨水最少」的那一列（相對於框的頂端）。"""
    height = band_ink.shape[0]
    low, high = int(height * SPLIT_SEARCH_LOW), int(height * SPLIT_SEARCH_HIGH)
    profile = band_ink[low:high].sum(axis=1).astype(float)
    if profile.size == 0:
        return height // 2
    window = max(3, int(interline))
    smooth = np.convolve(profile, np.ones(window) / window, mode="same")
    return low + int(np.argmin(smooth))


def split_page(image, positions_path, out_dir):
    """把一頁的上行與下行分別堆成兩張圖。回傳 (上行圖, 下行圖) 或 None。"""
    image = Path(image)
    gray = cv2.cvtColor(_imread(image), cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    interline, _ = estimate_interline(_binarize(gray))
    if interline <= 0:
        return None

    boxes = read_staff_boxes(positions_path, width, height)
    if not boxes:
        return None

    margin = int(interline * STRIP_MARGIN)
    upper, lower = [], []
    for y0, y1 in boxes:
        cut = _split_row(_binarize(gray[y0:y1, :]), interline)
        upper.append(gray[max(0, y0 - margin):y0 + cut, :])
        lower.append(gray[y0 + cut:min(height, y1 + margin), :])
    if not upper or not lower:
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    gap = int(interline * STACK_GAP)
    for name, strips in (("upper", upper), ("lower", lower)):
        pieces = []
        for strip in strips:
            if strip.shape[0] < 4:
                return None
            pieces.append(strip)
            pieces.append(np.full((gap, width), 255, np.uint8))
        stacked = np.vstack(pieces[:-1])
        stacked = cv2.copyMakeBorder(stacked, gap, gap, gap, gap,
                                     cv2.BORDER_CONSTANT, value=255)
        path = out_dir / f"{name}.png"
        _imwrite(path, cv2.cvtColor(stacked, cv2.COLOR_GRAY2BGR))
        paths.append(path)
    return tuple(paths)


# ---------------------------------------------------------------------------
# 把兩份單行譜併成一份大譜表
# ---------------------------------------------------------------------------

def _divisions_of(part):
    value = part.findtext(".//attributes/divisions")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 1


def _scale_durations(measure, factor):
    """整個小節的 duration 乘上倍率，用來把兩邊的 divisions 通分。

    `<divisions>` 也要一起乘 —— 它是這些 duration 的分母，只動分子的話
    這一小節的時值會整個縮放掉。homr 通常只在第 1 小節宣告一次，
    但中途再宣告一次是合法的，不能假設不會發生。
    """
    if factor == 1:
        return
    for tag in ("duration", "divisions"):
        for element in measure.iter(tag):
            try:
                element.text = str(int(round(float(element.text) * factor)))
            except (TypeError, ValueError):
                pass


def _measure_length(measure):
    """這一小節走了多少 duration（跟著游標算，含 backup/forward）。"""
    cursor = longest = 0
    for element in measure:
        if element.tag == "note":
            if element.find("chord") is None and element.find("grace") is None:
                cursor += int(float(element.findtext("duration") or 0))
        elif element.tag == "backup":
            cursor -= int(float(element.findtext("duration") or 0))
        elif element.tag == "forward":
            cursor += int(float(element.findtext("duration") or 0))
        longest = max(longest, cursor)
    return longest


def combine(upper_path, lower_path, out_path):
    """上行 -> staff 1、下行 -> staff 2，逐小節併成一個 part。

    小節數不一樣就回 None —— 對不齊的話左右手會整段錯開，比不拆更糟。
    """
    parser = etree.XMLParser(remove_blank_text=False, recover=True, resolve_entities=False)
    try:
        upper_tree = etree.parse(str(upper_path), parser)
        lower_tree = etree.parse(str(lower_path), parser)
    except (OSError, etree.XMLSyntaxError):
        return None

    upper_part = upper_tree.getroot().find("part")
    lower_part = lower_tree.getroot().find("part")
    if upper_part is None or lower_part is None:
        return None

    upper_measures = upper_part.findall("measure")
    lower_measures = lower_part.findall("measure")
    if not upper_measures or len(upper_measures) != len(lower_measures):
        return None

    # 兩邊的 divisions 可能不同，先通分
    du, dl = _divisions_of(upper_part), _divisions_of(lower_part)
    common = du * dl // _gcd(du, dl)
    up_factor, low_factor = common // du, common // dl

    for measure in upper_measures:
        _scale_durations(measure, up_factor)
    for measure in lower_measures:
        _scale_durations(measure, low_factor)

    for index, (measure, other) in enumerate(zip(upper_measures, lower_measures)):
        _tag_staff(measure, "1")
        _tag_staff(other, "2")

        attributes = measure.find("attributes")
        other_attributes = other.find("attributes")
        # 下行中途換譜號（左手跑到高音譜號很常見）時，上行這一小節可能根本沒有
        # attributes，要補一個才放得下那個譜號
        needs_block = index == 0 or (other_attributes is not None
                                     and other_attributes.find("clef") is not None)
        if attributes is None and needs_block:
            attributes = etree.SubElement(measure, "attributes")
            measure.insert(0, attributes)
        if index == 0:
            # <staves> 與 divisions 宣告一次就好，寫在第 1 小節
            _set_divisions(attributes, common)
            _declare_two_staves(attributes)
        if attributes is not None:
            _adopt_clef(attributes, other_attributes)

        length = _measure_length(measure)
        if length > 0:
            backup = etree.SubElement(measure, "backup")
            etree.SubElement(backup, "duration").text = str(length)
        for element in list(other):
            if element.tag in ("attributes", "print", "barline"):
                continue
            measure.append(element)

    out_path = Path(out_path)
    upper_tree.write(str(out_path), encoding="UTF-8", xml_declaration=True,
                     pretty_print=False)
    return out_path


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a or 1


def _set_divisions(attributes, value):
    element = attributes.find("divisions")
    if element is None:
        element = etree.Element("divisions")
        attributes.insert(0, element)
    element.text = str(value)


def _declare_two_staves(attributes):
    """補上 <staves>2</staves> 與 <part-symbol>brace</part-symbol>。

    MusicXML 的 `<attributes>` 是**有順序**的：
    divisions → key → time → staves → part-symbol → instruments → clef → …
    直接 append 會排到 clef 後面，嚴格一點的讀取端會拒收。
    """
    index = len(attributes)
    for i, child in enumerate(attributes):
        if child.tag == "clef":
            index = i
            break
    for tag, text in (("staves", "2"), ("part-symbol", "brace")):
        if attributes.find(tag) is not None:
            continue
        element = etree.Element(tag)
        element.text = text
        attributes.insert(index, element)
        index += 1


def _adopt_clef(attributes, other_attributes):
    """上行的譜號標 number=1，下行的複製過來標 number=2。"""
    for clef in attributes.findall("clef"):
        clef.set("number", "1")
    if other_attributes is None:
        return
    for clef in other_attributes.findall("clef"):
        copy = etree.fromstring(etree.tostring(clef))
        copy.set("number", "2")
        attributes.append(copy)


def _tag_staff(measure, number):
    """把這一小節裡每個音符/休止符標上 staff。"""
    for note in measure.findall("note"):
        element = note.find("staff")
        if element is None:
            element = etree.SubElement(note, "staff")
        element.text = number


# ---------------------------------------------------------------------------
# 判斷要不要動用這條路
# ---------------------------------------------------------------------------

#: 單一譜表佔到這個比例以上，就算「名義上有兩行、實際上沒分出來」
COLLAPSED_RATIO = 0.90


def looks_collapsed(musicxml, positions_path):
    """辨識結果是不是「大譜表被壓成一行」。

    先決條件：**譜面上真的有大譜表** —— positions 檔裡有 class=1 的框，
    那是 homr 自己合併出來的，可信。沒有這一關的話，真正的單行譜
    （長笛獨奏、旋律譜）會被硬拆成兩半，只會弄壞。

    符合下面任一個就算壓扁：

        沒有宣告 <staves>2</staves>     〈士兵進行曲〉：整份寫成單行的 "Voice"
        宣告了，但幾乎全擠在同一行     Bach 平均律：staff {1: 1520, 2: 29}

    第二條是必要的：只看有沒有宣告的話，零星幾個音落到 staff 2 就能讓檢查失效，
    而那種譜等於完全沒分手。
    """
    if not read_staff_boxes(positions_path, 1, 1_000_000):
        return False
    try:
        root = etree.parse(str(musicxml)).getroot()
    except (OSError, etree.XMLSyntaxError):
        return False

    declared = False
    for staves in root.iter("staves"):
        try:
            declared = declared or int(float(staves.text)) >= 2
        except (TypeError, ValueError):
            continue
    if not declared:
        return True

    counts = {}
    for note in root.iter("note"):
        key = (note.findtext("staff") or "1").strip()
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return False
    return max(counts.values()) / total >= COLLAPSED_RATIO


#: 拆開重辨識之後至少要留住原本這個比例的音符，否則寧可不換
KEEP_NOTES_RATIO = 0.80


def _note_count(path):
    try:
        return sum(1 for n in etree.parse(str(path)).getroot().iter("note")
                   if n.find("rest") is None)
    except (OSError, etree.XMLSyntaxError):
        return 0


def run_split_pass(engine, image, positions_path, original, workspace):
    """拆開重辨識，回傳合併好的 MusicXML 路徑；做不到或會變差就回 None。

    **一定要跟原本的結果比過音符數才採用。** 實測〈拍照測試〉第 1 頁：
    切出來的下行太窄，homr 丟 "No staffs found"，另一邊也只認出 2 個小節，
    合起來 217 個音剩 63 個 —— 分手是分出來了，但曲子少了七成。
    那比「全部當右手」更糟。
    """
    split = split_page(image, positions_path, Path(workspace) / "split")
    if not split:
        return None
    upper_image, lower_image = split
    try:
        upper_xml = engine.transcribe_raw(upper_image)
        lower_xml = engine.transcribe_raw(lower_image)
    except Exception:      # noqa: BLE001 - 拆開這條路失敗就退回原本的結果
        return None
    if not (upper_xml and lower_xml):
        return None

    combined = combine(upper_xml, lower_xml, Path(workspace) / "combined.musicxml")
    if combined is None:
        return None
    before, after = _note_count(original), _note_count(combined)
    if before and after < before * KEEP_NOTES_RATIO:
        return None
    return combined
