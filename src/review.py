"""練習檢討：找出彈不好的段落，在譜上圈出來，並提供重彈的範圍。

評分報告會告訴你「第 12.00 拍那個 F5 沒彈到」，但練琴的時候沒有人是用拍數在找位置的
—— 要的是「第 8 到 11 小節沒彈好，看譜上紅框那一段」。這個模組做的就是這件事：

    評分結果（每個問題有拍數）
        -> 換算成小節
        -> 依嚴重度找出連續的弱段
        -> 在原始樂譜照片上把那幾小節圈紅
        -> 給出重彈那一段的指令

小節位置是 src/score_input/layout.py 從照片上找出來的（辨識出的 MusicXML 沒有座標）。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = ROOT / "out" / "review"

# 一個小節累積到這個嚴重度就算「該練」
WEAK_THRESHOLD = 2.0
# 兩個弱小節之間隔這麼多小節以內就併成同一段
MERGE_GAP = 2
# 最多列出幾段
MAX_SECTIONS = 4


@dataclass
class Section:
    """一段建議重練的範圍。"""

    start: int
    end: int
    severity: float = 0.0
    reasons: list = field(default_factory=list)

    @property
    def length(self):
        return self.end - self.start + 1

    def label(self):
        return f"第 {self.start} 小節" if self.start == self.end \
            else f"第 {self.start}–{self.end} 小節"

    def as_dict(self):
        return {"start": self.start, "end": self.end,
                "severity": round(self.severity, 2), "reasons": self.reasons}


# ---------------------------------------------------------------------------
# 拍 -> 小節
# ---------------------------------------------------------------------------

def measure_starts(score):
    """回傳每個小節的起始拍，index 0 是第 1 小節。

    partitura 的 Part 記得每個小節的起訖（以 division 為單位），
    用 beat_map 換算成拍，就能把評分結果的拍數對回小節。
    """
    parts = getattr(score, "parts", None) or [score]
    part = parts[0]
    starts = []
    for measure in getattr(part, "measures", []) or []:
        try:
            starts.append(float(part.beat_map(measure.start.t)))
        except (AttributeError, TypeError, ValueError):
            continue
    return starts


def beat_to_measure(beat, starts):
    """拍數落在第幾小節（從 1 開始）。超出範圍就夾在頭尾。"""
    if beat is None or not starts:
        return None
    import bisect

    return max(1, min(len(starts), bisect.bisect_right(starts, beat + 1e-6)))


# ---------------------------------------------------------------------------
# 找出弱段
# ---------------------------------------------------------------------------

def measure_severity(result, starts):
    """把評分結果的問題點攤到各小節上，回傳 {小節: (嚴重度, [原因])}。"""
    table = {}
    for spot in result.get("problem_spots", []):
        measure = beat_to_measure(spot.get("beat"), starts)
        if measure is None:
            continue      # 多彈的音沒有譜面位置（本來就不在譜上），跳過
        severity, reasons = table.get(measure, (0.0, []))
        severity += float(spot.get("severity", 1.0))
        reason = f"{spot.get('type')}：{spot.get('detail')}"
        if reason not in reasons:
            reasons.append(reason)
        table[measure] = (severity, reasons)
    return table


def find_sections(severity_table, threshold=WEAK_THRESHOLD,
                  merge_gap=MERGE_GAP, limit=MAX_SECTIONS):
    """把零散的弱小節併成連續的練習段落。

    一個一個小節分開練沒有意義 —— 樂句是連著的，而且問題常常是「這一句都不熟」，
    所以相鄰的弱小節要併起來，中間夾雜一兩個沒問題的小節也一起帶進去。
    """
    weak = sorted(m for m, (s, _) in severity_table.items() if s >= threshold)
    if not weak:
        return []

    sections = []
    current = Section(start=weak[0], end=weak[0])
    for measure in weak[1:]:
        if measure - current.end <= merge_gap + 1:
            current.end = measure
        else:
            sections.append(current)
            current = Section(start=measure, end=measure)
    sections.append(current)

    for section in sections:
        for m in range(section.start, section.end + 1):
            severity, reasons = severity_table.get(m, (0.0, []))
            section.severity += severity
            for r in reasons:
                text = f"第 {m} 小節　{r}"
                if text not in section.reasons:
                    section.reasons.append(text)

    sections.sort(key=lambda s: -s.severity)
    return sections[:limit]


# ---------------------------------------------------------------------------
# 在譜上圈出來
# ---------------------------------------------------------------------------

def render(sections, measure_map, out_dir=None, stem="review"):
    """把弱段在原始樂譜照片上圈紅，一頁一張圖。回傳產生的檔案路徑。"""
    from src.score_input import layout as layout_mod

    if not sections or not measure_map:
        return []

    wanted = {}
    for section in sections:
        for m in range(section.start, section.end + 1):
            wanted.setdefault(m, section)

    by_file = {}
    for entry in measure_map:
        if entry["measure"] not in wanted:
            continue
        by_file.setdefault(entry["file"], []).append(entry)

    if not by_file:
        return []

    out_dir = Path(out_dir) if out_dir else REVIEW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for filename, entries in by_file.items():
        source = _source_path(entries[0], measure_map)
        if source is None or not source.exists():
            continue

        boxes, labels = [], {}
        for entry in entries:
            box = layout_mod.MeasureBox(
                index=entry["measure"], system=entry.get("system", 0),
                corners=[tuple(c) for c in entry["corners"]],
            )
            boxes.append(box)
            section = wanted[entry["measure"]]
            labels[entry["measure"]] = (f"第 {entry['measure']} 小節"
                                        if section.length == 1
                                        else f"{section.label()}")

        target = out_dir / f"{stem}_{Path(filename).stem}.jpg"
        layout_mod.highlight(source, boxes, target, labels=labels)
        written.append(target)

    return sorted(written)


def _source_path(entry, measure_map):
    project_dir = entry.get("project_dir") or _project_dir(measure_map)
    if not project_dir:
        return None
    return Path(project_dir) / entry["file"]


def _project_dir(measure_map):
    for entry in measure_map:
        if entry.get("project_dir"):
            return entry["project_dir"]
    return None


# ---------------------------------------------------------------------------
# 對外
# ---------------------------------------------------------------------------

def build(result, score, build_info=None, out_dir=None, stem="review"):
    """做一份完整的練習檢討。

    build_info 是專案 manifest 裡的 build 區塊（含小節位置對照表）；
    沒有的話（例如樂譜不是從照片辨識來的）就只給文字，不畫圖。
    """
    starts = measure_starts(score)
    table = measure_severity(result, starts)
    sections = find_sections(table)

    measure_map = []
    if build_info:
        project_dir = build_info.get("project_dir")
        for entry in build_info.get("measure_map", []) or []:
            entry = dict(entry)
            entry.setdefault("project_dir", project_dir)
            measure_map.append(entry)

    images = render(sections, measure_map, out_dir=out_dir, stem=stem)

    warning = ""
    if sections and measure_map and starts and len(measure_map) != len(starts):
        warning = (f"提醒：譜面有 {len(starts)} 小節，但照片上只找到 "
                   f"{len(measure_map)} 個小節框，圈起來的位置可能有偏移。")

    return {
        "total_measures": len(starts),
        "sections": [s.as_dict() for s in sections],
        "images": [str(p) for p in images],
        "warning": warning,
        "_sections": sections,
    }


def format_report(review):
    """終端機用的檢討報告。"""
    sections = review.get("_sections") or []
    if not sections:
        return "  這次沒有明顯要重練的段落。"

    lines = []
    for i, section in enumerate(sections, start=1):
        lines.append(f"  {i}. {section.label()}"
                     f"（{section.length} 小節，嚴重度 {section.severity:.1f}）")
        for reason in section.reasons[:4]:
            lines.append(f"       {reason}")
        if len(section.reasons) > 4:
            lines.append(f"       ...另外還有 {len(section.reasons) - 4} 個問題")

    if review.get("images"):
        lines.append("")
        lines.append("  已把這些段落在樂譜上圈起來：")
        for path in review["images"]:
            lines.append(f"    {path}")

    if review.get("warning"):
        lines.append("")
        lines.append("  " + review["warning"])

    return "\n".join(lines)


def save_json(review, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in review.items() if not k.startswith("_")}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
