"""量 OMR 的辨識正確率：拿辨識結果跟「標準答案」比對。

沒有標準答案就只能說「跑得完」，說不出「認得準不準」。這支工具讓那個數字
可以重複量、也能寫進報告。

標準答案哪裡來：[Mutopia Project](https://www.mutopiaproject.org) 的每一首曲子
都同時提供 PDF 樂譜和對應的 MIDI，兩者出自同一份 LilyPond 原始碼，
所以 MIDI 就是那份 PDF 的正確答案。

    python tools\\omr_accuracy.py 樂譜.pdf 標準答案.mid

流程：PDF 逐頁轉圖 -> OMR -> 合併 -> 跟標準答案比對。

比對用兩個指標，因為單看一個都會騙人：

  音高組成       兩邊的音高多重集合重疊多少。跟時間無關，
                 純粹看「該有的音有沒有被認出來」。
  對齊後配對率   用 parangonar 做音符級對齊（跟評分 pipeline 同一套），
                 算配對 / 漏認 / 多認。這個看得出音符的前後關係對不對。

「依序逐音比對」刻意不用 —— 和弦內的音序本來就是任意的，而且只要前面漏一個音
後面全部錯位，那個數字會低到毫無意義。
"""

import argparse
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render_pdf(pdf, out_dir, dpi=300):
    import pypdfium2 as pdfium

    pages = []
    doc = pdfium.PdfDocument(str(pdf))
    try:
        for i in range(len(doc)):
            path = out_dir / f"page_{i + 1:02d}.png"
            doc[i].render(scale=dpi / 72).to_pil().save(path)
            pages.append(path)
    finally:
        doc.close()
    return pages


def transcribe(pages, work_dir, engine_name="homr"):
    from src.score_input import merge, omr_engine

    engine = omr_engine.get_engine(engine_name)
    if not engine.available():
        raise SystemExit(f"引擎 {engine_name!r} 還不能用（available() 回報 False）")
    print(f"  引擎：{engine.name}")
    produced = []
    for i, page in enumerate(pages, start=1):
        print(f"  [{i}/{len(pages)}] 辨識 {page.name} ...", flush=True)
        try:
            produced.append(engine.transcribe(page))
        except omr_engine.OmrError as exc:
            print(f"      失敗：{str(exc).splitlines()[0]}")
    if not produced:
        raise SystemExit("沒有任何一頁辨識成功")

    merged = work_dir / "merged.musicxml"
    merged, stats = merge.merge_musicxml(produced, merged)
    return merged, stats


def warn_if_expanded(omr_xml, omr, truth):
    """辨識結果的音符數是標準答案的整數倍時出聲。

    `data/scores/*.musicxml` 是 **pipeline 的產物，反覆記號已經展開**
    （`repeats.expand_file()`），而 Mutopia 的標準答案 MIDI 是 LilyPond 直接輸出的，
    **不展開反覆**。拿前者來比會得到「音符數剛好 2 倍、一半被判成漏認」的假數字。

    這個工具正確的用法是**餵 PDF**，它會自己重跑一次辨識（那條路不經過 pipeline，
    所以不會展開）。但 `data/scores/` 底下的檔案看起來就是「這首的樂譜」，
    很容易被直接拿來用 —— 我自己就這樣誤判過一次，還因此以為校正集壞了。
    """
    if len(truth) == 0:
        return
    ratio = len(omr) / len(truth)
    if ratio < 1.8:
        return
    print()
    print(f"  ⚠ 辨識結果的音符數是標準答案的 {ratio:.1f} 倍。")
    print("    如果輸入是 data/scores/*.musicxml，那是 pipeline 的產物，**反覆記號已經展開**，")
    print("    而標準答案 MIDI 沒有展開 —— 這樣比出來的數字沒有意義。")
    print("    正確用法是餵原始 PDF，讓這支工具自己重跑辨識。")
    print()


def warn_if_truth_truncated(truth_mid, loaded):
    """載入器丟掉標準答案裡的音時出聲。

    `partitura` 的演奏載入器**表示不了「同一個音高還在響就再彈一次」**，
    遇到重疊的同音會靜靜丟掉後來那些，只在 stderr 印一堆
    `ignoring MIDI message note_off`（量測腳本通常把它濾掉了）。

    這在踩著踏板的琶音上非常常見 —— 排譜的人把音長寫到超過下一次觸發。
    〈幻想即興曲〉實測 3050 個 note_on 裡有 **1051 個（34%）是重疊的同音**，
    載進來只剩 2003。標準答案少了三分之一，後果是：

        「多認」暴增   homr 正確認出來的音，在標準答案裡找不到對應 -> 被算成多認
        分母變小       正確率的分母是被截斷過的，數字不能跟其他曲子比

    實測那次回報「多認 991」，而被丟掉的是 1051 個 —— 幾乎全部是這個原因，
    不是 homr 真的多生了音（它總共只產出 2927 個，比真實的 3050 還少）。

    照 `warn_if_expanded()` 的精神：**不擋，但一定要講**，
    因為這種失真不會有任何錯誤訊息。
    """
    try:
        import mido
    except ImportError:
        return
    try:
        midi = mido.MidiFile(str(truth_mid))
    except Exception:      # noqa: BLE001 - 這只是加註，讀不了就算了
        return

    total = sum(1 for track in midi.tracks for msg in track
                if msg.type == "note_on" and msg.velocity > 0)
    if total <= len(loaded) + 1:
        return

    dropped = total - len(loaded)
    print()
    print(f"  ⚠ 標準答案有 {total} 個音，載入後只剩 {len(loaded)} 個（少了 {dropped}）。")
    print("    partitura 表示不了「同一個音高還在響就再彈一次」，重疊的同音會被丟掉，")
    print("    踩踏板的琶音最常見。**「多認」會因此虛高、正確率的分母會偏小**，")
    print("    這一首的數字不能直接跟其他曲子比。")
    print()


def compare(omr_xml, truth_mid):
    from src import align as align_mod, io_utils

    # 一定要走 io_utils 的載入路徑，不要自己呼叫 partitura ——
    # io_utils._ensure_fields 會補上 parangonar 需要的 is_grace 欄位，
    # 少了它 DualDTW 會拋錯而退回精度差很多的 fallback，量出來的數字就是錯的。
    _, omr = io_utils.load_score(omr_xml)
    _, truth = io_utils.load_performance(truth_mid)

    warn_if_expanded(omr_xml, omr, truth)
    warn_if_truth_truncated(truth_mid, truth)

    co = Counter(int(p) for p in omr["pitch"])
    ct = Counter(int(p) for p in truth["pitch"])
    overlap = sum((co & ct).values())

    alignment, method = align_mod.align(omr, truth, method="dualdtw", verbose=False)
    stats = align_mod.alignment_stats(alignment)

    return {
        "omr_notes": len(omr),
        "truth_notes": len(truth),
        "pitch_overlap": overlap,
        "pitch_recall": overlap / len(truth) if len(truth) else 0.0,
        "match": stats["match"],
        "missed": stats["deletion"],
        "extra": stats["insertion"],
        "method": method,
        "omr_range": (int(omr["pitch"].min()), int(omr["pitch"].max())),
        "truth_range": (int(truth["pitch"].min()), int(truth["pitch"].max())),
    }


def main():
    parser = argparse.ArgumentParser(description="量 OMR 的辨識正確率")
    parser.add_argument("pdf", help="樂譜 PDF（或單張圖檔）")
    parser.add_argument("truth", help="標準答案 MIDI")
    parser.add_argument("--dpi", type=int, default=300, help="PDF 轉圖的解析度")
    parser.add_argument("--keep", help="把中間產物留在這個資料夾")
    parser.add_argument("--engine", default="homr",
                        help="要量哪一個 OMR 引擎（預設 homr）。換引擎的評估靠這個 —— "
                             "同一份 PDF、同一份標準答案，只換引擎，數字才可比。")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    truth = Path(args.truth)
    for p in (pdf, truth):
        if not p.exists():
            raise SystemExit(f"找不到檔案：{p}")

    work = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="omr_acc_"))
    work.mkdir(parents=True, exist_ok=True)

    try:
        print(f"樂譜：{pdf.name}　標準答案：{truth.name}")
        pages = ([pdf] if pdf.suffix.lower() != ".pdf"
                 else render_pdf(pdf, work, args.dpi))
        if pdf.suffix.lower() == ".pdf":
            print(f"  PDF 轉成 {len(pages)} 頁圖檔（{args.dpi} DPI）")

        merged, merge_stats = transcribe(pages, work, args.engine)
        print(f"  合併：{merge_stats['measures']} 小節")

        r = compare(merged, truth)
        print()
        print("=" * 58)
        print(f"  辨識出 {r['omr_notes']} 音，標準答案 {r['truth_notes']} 音")
        print(f"  音高範圍　辨識 {r['omr_range'][0]}-{r['omr_range'][1]}　"
              f"標準 {r['truth_range'][0]}-{r['truth_range'][1]}")
        print()
        print(f"  音高組成正確率　{r['pitch_recall'] * 100:5.1f}%"
              f"　（{r['pitch_overlap']}/{r['truth_notes']}）")
        print(f"  對齊後配對率　　{r['match'] / r['truth_notes'] * 100:5.1f}%"
              f"　（配對 {r['match']}、漏認 {r['missed']}、多認 {r['extra']}）")
        print(f"  對齊方法：{r['method']}")
        print("=" * 58)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
