"""把多段 MusicXML 接成一份。

照片是一頁一份、長曲的記譜檔也可能分好幾段，但 run.py 的 `-s` 只吃單一檔案，
所以要先接起來。

作法是拿第一份當骨幹，把後面各份每個 part 的 <measure> 依序接上去並重編號碼。
接縫處如果調號 / 拍號 / 譜號跟前一段結尾一樣就刪掉，否則每翻一頁譜上都會多冒出
一組重複的記號。

自己寫而不是依賴 relieur（papoteur-mga/relieur，只有 8 個 commit），因為這段邏輯
很短、而且我們需要在合併後立刻用 partitura 驗一次能不能讀 —— 那才是真正的驗收。
"""

import math
from pathlib import Path

from lxml import etree

from . import musicxml_fix

# MusicXML 的 <attributes> 底下這幾個元素是「延續性」的：沒有重新指定就沿用前面的。
# 接縫處重複出現同樣的值，看譜的人會以為這裡轉調或變拍。
_STICKY = ("key", "time", "clef")

# 這些元素底下的時值都是以 <divisions> 為單位，換算刻度時要一起縮放
_DURATION_PATHS = (
    ("note", "duration"),
    ("backup", "duration"),
    ("forward", "duration"),
    ("figured-bass", "duration"),
    ("direction", "offset"),
)


def _collect_divisions(part):
    values = set()
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            text = attributes.findtext("divisions")
            if text:
                try:
                    value = int(float(text))
                except ValueError:
                    continue
                if value > 0:
                    values.add(value)
    return values


def _normalize_divisions(part):
    """把整個 part 的時間刻度統一成同一個 divisions。

    homr 是一頁一頁分別辨識的，每頁會自己挑一個 divisions（實測有 2 也有 4）。
    接在一起之後同一個 part 裡就出現兩種刻度，partitura 會直接拒收：
    「Note array from parts with multiple divisions is not supported」。

    解法是取所有值的最小公倍數當共同刻度，把每個小節的時值依比例放大，
    然後只在開頭留一個 <divisions>。用最小公倍數才能保證縮放倍率是整數，
    不會因為四捨五入把附點或三連音的長度弄歪。
    """
    values = _collect_divisions(part)
    if len(values) <= 1:
        return next(iter(values), None)

    target = math.lcm(*values)
    current = None

    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            element = attributes.find("divisions")
            if element is not None:
                try:
                    current = int(float(element.text))
                except (TypeError, ValueError):
                    current = current or target
                attributes.remove(element)
        factor = target // (current or target)
        if factor == 1:
            continue
        for parent_tag, child_tag in _DURATION_PATHS:
            for parent in measure.findall(parent_tag):
                for child in parent.findall(child_tag):
                    try:
                        child.text = str(int(round(float(child.text) * factor)))
                    except (TypeError, ValueError):
                        continue

    _set_leading_divisions(part, target)
    return target


def _set_leading_divisions(part, target):
    """在第一小節補一個 <divisions>。MusicXML 規定它必須是 <attributes> 的第一個子元素。"""
    measures = part.findall("measure")
    if not measures:
        return
    first = measures[0]
    attributes = first.find("attributes")
    if attributes is None:
        attributes = etree.Element("attributes")
        first.insert(0, attributes)
    element = etree.Element("divisions")
    element.text = str(target)
    attributes.insert(0, element)


class MergeError(RuntimeError):
    """合併失敗，訊息給使用者看。"""


def _parse(path):
    try:
        parser = etree.XMLParser(remove_blank_text=False, recover=True, resolve_entities=False)
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise MergeError(f"讀不了樂譜檔 {Path(path).name}：{exc}") from exc
    root = tree.getroot()
    if root.tag == "score-timewise":
        raise MergeError(
            f"{Path(path).name} 是 score-timewise 格式，本系統只處理 score-partwise。"
            f"用 MuseScore 重新匯出一次就會是 partwise。"
        )
    musicxml_fix.fix_orphan_chords(root)
    return tree


def _parts(root):
    return {p.get("id"): p for p in root.findall("part")}


def _signature(measure):
    """取出這一小節在 <attributes> 裡宣告的延續性記號，用來比對接縫。"""
    out = {}
    for attributes in measure.findall("attributes"):
        for tag in _STICKY:
            for element in attributes.findall(tag):
                key = (tag, element.get("number"))
                out[key] = etree.tostring(element, method="c14n2")
    return out


def _running_signature(part):
    """整個 part 跑到最後時，各記號的現行值。"""
    state = {}
    for measure in part.findall("measure"):
        state.update(_signature(measure))
    return state


def _strip_redundant(measure, state):
    """刪掉跟前一段結尾相同的調號 / 拍號 / 譜號宣告。"""
    for attributes in measure.findall("attributes"):
        for tag in _STICKY:
            for element in list(attributes.findall(tag)):
                key = (tag, element.get("number"))
                if key in state and state[key] == etree.tostring(element, method="c14n2"):
                    attributes.remove(element)
        # 清空的 <attributes> 留著也沒意義
        if len(attributes) == 0:
            measure.remove(attributes)


def merge_musicxml(paths, out_path, validate=True):
    """依給定順序合併，回傳 (輸出路徑, 統計)。

    paths 的順序就是最終的頁序 —— 呼叫端負責排好，這裡不重排。
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise MergeError("沒有東西可以合併")
    for p in paths:
        if not p.exists():
            raise MergeError(f"找不到檔案：{p}")

    base_tree = _parse(paths[0])
    base_root = base_tree.getroot()
    base_parts = _parts(base_root)
    if not base_parts:
        raise MergeError(f"{paths[0].name} 裡面沒有任何聲部（part）")

    state = {pid: _running_signature(part) for pid, part in base_parts.items()}
    skipped = []

    for path in paths[1:]:
        root = _parse(path).getroot()
        parts = _parts(root)
        if not parts:
            skipped.append(f"{path.name}（沒有任何聲部）")
            continue

        # 用 id 對，對不上就退回用出現順序 —— 不同頁分別辨識出來的 part id
        # 未必一致，但鋼琴譜的聲部順序是穩定的。
        pairs = _pair_parts(base_parts, parts, path, skipped)
        for base_part, incoming in pairs:
            pid = base_part.get("id")
            for measure in incoming.findall("measure"):
                _strip_redundant(measure, state.get(pid, {}))
                state.setdefault(pid, {}).update(_signature(measure))
                base_part.append(measure)

    divisions = set()
    for part in base_parts.values():
        value = _normalize_divisions(part)
        if value:
            divisions.add(value)
        for number, measure in enumerate(part.findall("measure"), start=1):
            measure.set("number", str(number))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_tree.write(str(out_path), encoding="UTF-8", xml_declaration=True, pretty_print=False)

    stats = {
        "sources": len(paths),
        "measures": max(len(p.findall("measure")) for p in base_parts.values()),
        "parts": len(base_parts),
        "divisions": sorted(divisions),
        "skipped": skipped,
    }
    if validate:
        stats["notes"] = _validate(out_path)
    return out_path, stats


def _pair_parts(base_parts, parts, path, skipped):
    common = set(base_parts) & set(parts)
    if common:
        pairs = [(base_parts[pid], parts[pid]) for pid in base_parts if pid in common]
        missing = set(base_parts) - common
        if missing:
            skipped.append(f"{path.name}（缺少聲部 {', '.join(sorted(missing))}）")
        return pairs

    base_list, incoming_list = list(base_parts.values()), list(parts.values())
    if len(base_list) != len(incoming_list):
        skipped.append(
            f"{path.name}（聲部數 {len(incoming_list)} 與第一段的 {len(base_list)} 不一致，"
            f"只接前 {min(len(base_list), len(incoming_list))} 個）"
        )
    return list(zip(base_list, incoming_list))


def _count_measures(path):
    root = _parse(path).getroot()
    parts = root.findall("part")
    return max((len(p.findall("measure")) for p in parts), default=0)


def _validate(path):
    """合併完立刻用 partitura 讀一次 —— 讀不動的話產物就是廢的，要當場知道。"""
    import partitura as pt

    try:
        score = pt.load_score(str(path))
        note_array = score.note_array()
    except Exception as exc:  # partitura 會丟各種底層例外，一律當成「產物不能用」
        raise MergeError(
            f"合併出來的樂譜 partitura 讀不動，後面的評分會失敗：{exc}\n"
            f"檔案留在 {path}，可以用 MuseScore 開開看是哪裡壞了。"
        ) from exc
    if len(note_array) == 0:
        raise MergeError(f"合併出來的樂譜一個音符也沒有：{path}")
    return len(note_array)
