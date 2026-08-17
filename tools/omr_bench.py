"""量「照片條件」對 OMR 正確率的影響，以及前處理救回多少。

`tools/omr_accuracy.py` 量的是單一情況（300 DPI 的乾淨 PDF）。這支跑的是一個矩陣：

    拍攝條件 × 有沒有前處理

拍攝條件由 `tools/photo_sim.py` 產生 —— 拿有標準答案的 PDF，故意弄成手機拍的樣子。
這樣「照片的正確率」才有標準答案可以對。

    python tools\\omr_bench.py --pieces andre --conditions clean,normal
    python tools\\omr_bench.py --keep C:\\omr_bench      （留下中間產物可以看）

跑完會印一張表：每一格是「對齊後配對率」，也就是跟標準答案逐音配對成功的比例。

已經算過的組合會直接沿用（用 --fresh 強制重跑）。一頁 OMR 要 30-90 秒，
整個矩陣跑一次是幾十分鐘，沒有快取的話根本沒辦法反覆調參數。
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "data" / "examples" / "測試用樂譜"

PIECES = {
    "alkan": ("alkan_prelude1.pdf", "alkan_prelude1.mid", "Alkan 前奏曲 Op.31-1"),
    "andre": ("andre_sonatine.pdf", "andre_sonatine.mid", "André 小奏鳴曲 Op.34-I"),
}

# clean = 直接用 PDF 轉出來的圖，不模擬拍照。其餘見 photo_sim.PRESETS
CONDITIONS = ["clean", "good", "normal", "rough"]


def render_pdf(pdf, out_dir, dpi=300):
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = sorted(out_dir.glob("page_*.png"))
    if pages:
        return pages

    doc = pdfium.PdfDocument(str(pdf))
    try:
        for i in range(len(doc)):
            path = out_dir / f"page_{i + 1:02d}.png"
            doc[i].render(scale=dpi / 72).to_pil().save(path)
            pages.append(path)
    finally:
        doc.close()
    return pages


def make_condition_pages(clean_pages, condition, out_dir, fresh=False):
    """產生某個拍攝條件下的頁面圖。clean 就是原圖。"""
    if condition == "clean":
        return list(clean_pages)

    from tools import photo_sim

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, page in enumerate(clean_pages):
        dst = out_dir / f"{page.stem}.jpg"
        if fresh or not dst.exists():
            # seed 綁頁碼，同一頁每次都得到一模一樣的照片
            photo_sim.simulate(page, dst, condition, seed=1000 + i)
        pages.append(dst)
    return pages


def preprocess_pages(pages, out_dir, fresh=False, skip=()):
    from src.score_input import preprocess

    out_dir.mkdir(parents=True, exist_ok=True)
    done, reports = [], []
    for page in pages:
        dst = out_dir / f"{page.stem}.png"
        meta = out_dir / f"{page.stem}.json"
        if fresh or not dst.exists() or not meta.exists():
            report = preprocess.prepare(page, dst, skip=skip)
            meta.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=1),
                            encoding="utf-8")
            reports.append(report.summary_line())
        else:
            d = json.loads(meta.read_text(encoding="utf-8"))
            reports.append(f"{'、'.join(d['steps'])}　模型看到的行距 "
                           f"{d['effective_interline'][0]:.1f} → "
                           f"{d['effective_interline'][1]:.1f}px")
        done.append(dst)
    return done, reports


def transcribe(pages, work_dir, fresh=False):
    """辨識並合併。回傳 (merged_path, 失敗頁數)。"""
    from src.score_input import merge, omr_engine

    work_dir.mkdir(parents=True, exist_ok=True)
    merged = work_dir / "merged.musicxml"
    if merged.exists() and not fresh:
        return merged, 0

    engine = omr_engine.get_engine()
    produced, failed = [], 0
    for i, page in enumerate(pages, start=1):
        xml = work_dir / f"{page.stem}.musicxml"
        if xml.exists() and not fresh:
            produced.append(xml)
            continue
        started = time.time()
        print(f"      [{i}/{len(pages)}] {page.name} ...", end="", flush=True)
        try:
            produced.append(engine.transcribe(page, work_dir))
            print(f" {time.time() - started:.0f}s")
        except omr_engine.OmrError as exc:
            failed += 1
            print(f" 失敗（{str(exc).splitlines()[0][:60]}）")

    if not produced:
        return None, failed
    merge.merge_musicxml(produced, merged)
    return merged, failed


def compare(omr_xml, truth_mid):
    """跟標準答案比對。指標與 tools/omr_accuracy.py 一致。"""
    from src import align as align_mod, io_utils

    # 一定要走 io_utils —— 少了它補的 is_grace，parangonar 會退回精度差很多的
    # fallback，量出來的數字是錯的（見工作紀錄 2026-08-12）
    _, omr = io_utils.load_score(omr_xml)
    _, truth = io_utils.load_performance(truth_mid)

    co = Counter(int(p) for p in omr["pitch"])
    ct = Counter(int(p) for p in truth["pitch"])
    overlap = sum((co & ct).values())

    alignment, method = align_mod.align(omr, truth, method="dualdtw", verbose=False)
    stats = align_mod.alignment_stats(alignment)
    n = len(truth)
    return {
        "omr_notes": len(omr),
        "truth_notes": n,
        "pitch_recall": overlap / n if n else 0.0,
        "match_rate": stats["match"] / n if n else 0.0,
        "missed": stats["deletion"],
        "extra": stats["insertion"],
        "method": method,
    }


def run_cell(piece, condition, use_prep, work, fresh):
    pdf_name, mid_name, _ = PIECES[piece]
    clean = render_pdf(EXAMPLES / pdf_name, work / piece / "clean_pages")
    pages = make_condition_pages(clean, condition, work / piece / condition, fresh)

    notes = []
    if use_prep:
        pages, reports = preprocess_pages(pages, work / piece / f"{condition}_prep", fresh)
        notes = reports

    tag = f"{condition}{'_prep' if use_prep else ''}"
    merged, failed = transcribe(pages, work / piece / f"omr_{tag}", fresh)
    if merged is None:
        return {"match_rate": 0.0, "pitch_recall": 0.0, "failed": failed,
                "notes": notes, "dead": True}

    result = compare(merged, EXAMPLES / mid_name)
    result["failed"] = failed
    result["notes"] = notes
    result["dead"] = False
    return result


def ablate(piece, condition, work, fresh):
    """關掉單一前處理步驟，看正確率掉多少 —— 哪一步真的有用只能量出來。

    每一列都是「完整前處理，但少做一件事」。比完整版**高**的那一列，
    就是在扯後腿的步驟。
    """
    from src.score_input import preprocess

    pdf_name, mid_name, title = PIECES[piece]
    clean = render_pdf(EXAMPLES / pdf_name, work / piece / "clean_pages")
    base = make_condition_pages(clean, condition, work / piece / condition, fresh)

    rows = []
    variants = [("完整前處理", ())] + [(f"少了「{s}」", (s,)) for s in preprocess.STEPS]
    for label, skip in variants:
        tag = "full" if not skip else skip[0]
        pages, notes = preprocess_pages(
            base, work / piece / f"ablate_{condition}_{tag}", fresh, skip=skip)
        merged, _ = transcribe(pages, work / piece / f"omr_ablate_{condition}_{tag}", fresh)
        if merged is None:
            rows.append((label, 0.0, 0.0, "完全認不出來"))
            continue
        r = compare(merged, EXAMPLES / mid_name)
        rows.append((label, r["match_rate"], r["pitch_recall"],
                     f"漏 {r['missed']}　多 {r['extra']}"))
        print(f"    {label:<16}配對 {r['match_rate'] * 100:5.1f}%")

    print("\n" + "=" * 66)
    print(f"消融：{title}　拍攝條件 {condition}")
    print("=" * 66)
    baseline = rows[0][1]
    for label, match, recall, extra in rows:
        mark = ""
        if label != "完整前處理":
            diff = (match - baseline) * 100
            mark = f"{diff:+6.1f}" + ("  ← 這一步在扯後腿" if diff > 1.0 else "")
        print(f"{label:<16}{match * 100:6.1f}%　{recall * 100:6.1f}%　{extra:<16}{mark}")
    print("=" * 66)


def main():
    parser = argparse.ArgumentParser(description="量拍攝條件與前處理對 OMR 的影響")
    parser.add_argument("--pieces", default="alkan,andre")
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--no-prep", action="store_true", help="只跑沒有前處理的那一半")
    parser.add_argument("--keep", default=str(ROOT / "data" / "omr_bench"),
                        help="中間產物放哪裡（預設會保留，方便看圖）")
    parser.add_argument("--fresh", action="store_true", help="不要沿用先前的結果")
    parser.add_argument("--ablate", metavar="條件",
                        help="消融實驗：對這個拍攝條件逐一關掉前處理的每個步驟")
    args = parser.parse_args()

    pieces = [p.strip() for p in args.pieces.split(",") if p.strip()]
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for p in pieces:
        if p not in PIECES:
            raise SystemExit(f"不認得的曲子 {p}（可用 {sorted(PIECES)}）")

    work = Path(args.keep)
    work.mkdir(parents=True, exist_ok=True)

    if args.ablate:
        for piece in pieces:
            print(f"\n=== {PIECES[piece][2]} ===")
            ablate(piece, args.ablate, work, args.fresh)
        return

    variants = [False] if args.no_prep else [False, True]

    table = {}
    for piece in pieces:
        print(f"\n=== {PIECES[piece][2]} ===")
        for condition in conditions:
            for use_prep in variants:
                label = f"{condition}{'＋前處理' if use_prep else ''}"
                print(f"  {label}")
                r = run_cell(piece, condition, use_prep, work, args.fresh)
                for note in r["notes"]:
                    print(f"      前處理：{note}")
                table[(piece, condition, use_prep)] = r
                if r["dead"]:
                    print("      → 完全認不出來")
                else:
                    print(f"      → 配對 {r['match_rate'] * 100:.1f}%　"
                          f"音高組成 {r['pitch_recall'] * 100:.1f}%　"
                          f"漏 {r['missed']}　多 {r['extra']}")

    # ---------- 總表 ----------
    print("\n" + "=" * 72)
    print("對齊後配對率（跟標準答案逐音配對成功的比例）")
    print("=" * 72)
    head = f"{'曲子':<14}{'拍攝條件':<10}{'原圖':>9}{'前處理後':>11}{'差':>9}"
    print(head)
    print("-" * 72)
    for piece in pieces:
        for condition in conditions:
            raw = table.get((piece, condition, False))
            prep = table.get((piece, condition, True))
            if raw is None:
                continue
            a = f"{raw['match_rate'] * 100:8.1f}%"
            if prep is None:
                print(f"{piece:<14}{condition:<10}{a:>9}")
                continue
            b = f"{prep['match_rate'] * 100:10.1f}%"
            delta = (prep["match_rate"] - raw["match_rate"]) * 100
            print(f"{piece:<14}{condition:<10}{a:>9}{b:>11}{delta:+8.1f}")
    print("=" * 72)
    print(f"中間產物留在 {work}")


if __name__ == "__main__":
    main()
