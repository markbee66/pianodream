"""OMR 引擎：把樂譜照片變成 MusicXML。

目前只有一個實作（homr），但介面留成可抽換的形狀 —— 日後要加 Audiveris
或別的引擎，只要再寫一個有 transcribe() 的類別，上層完全不用改。

**為什麼走 subprocess 而不是直接 import**：homr 的相依套件 musicxml 開啟內建的
XSD 檔時沒有指定編碼，在繁體中文版 Windows（預設 cp950）會直接 UnicodeDecodeError
掛掉。設 PYTHONUTF8=1 可以解決，但這個環境變數必須在直譯器啟動時就存在，
在程式裡設已經來不及。跑子行程剛好能帶乾淨的環境進去，順便還拿到兩個好處：
一頁跑爆不會拖垮整批，而且記憶體用完就隨行程還給系統。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import grandstaff, musicxml_fix

# 一頁最多跑多久。CPU 上一頁大約 15–40 秒，留寬一點給複雜的譜。
PAGE_TIMEOUT = 900


def _is_ascii(path):
    return str(path).isascii()


def _ascii_workspace():
    """找一個路徑不含非 ASCII 字元的暫存資料夾。

    homr 內部用 cv2.imread 讀圖，而 OpenCV 在 Windows 上讀不了含中文的路徑
    （會回報「file format is not supported」，訊息完全看不出真正原因）。
    這個專案本身就放在「桌面\\專題\\暑假\\ai運算」底下，所以這不是邊緣情況。
    """
    candidates = [Path(tempfile.gettempdir())]
    drive = Path(sys.executable).drive or "C:"
    candidates.append(Path(f"{drive}\\omr_tmp" if os.name == "nt" else "/tmp/omr_tmp"))
    for base in candidates:
        if not _is_ascii(base):
            continue
        try:
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="omr_", dir=base))
        except OSError:
            continue
    raise OmrError(
        "找不到路徑不含中文的暫存資料夾，homr 無法處理。"
        "可以把專案搬到純英文路徑（例如 C:\\piano-ai）再試。"
    )


class OmrError(RuntimeError):
    """辨識失敗。訊息是給使用者看的，不是 traceback。"""


class HomrEngine:
    """https://github.com/liebharc/homr —— 專門處理相機拍的譜。

    輸出寫在輸入圖檔旁邊、同名的 .musicxml。
    """

    name = "homr"

    def __init__(self, python=None, timeout=PAGE_TIMEOUT):
        self.python = str(python or sys.executable)
        self.timeout = timeout

    def _env(self):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"      # 見模組開頭：不設這個在中文 Windows 會掛
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def available(self):
        try:
            proc = subprocess.run(
                [self.python, "-c", "import homr; print(homr.__name__)"],
                capture_output=True, text=True, timeout=120, env=self._env(),
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def transcribe(self, image, out_dir=None):
        """辨識一張圖，回傳產生的 .musicxml 路徑。

        一律先把圖複製到純 ASCII 的暫存路徑再交給 homr（原因見 _ascii_workspace），
        跑完再把結果搬回來。永遠走同一條路徑，不做「中文才繞路」的特例分支 ——
        那種只在特定環境才執行的程式碼是 bug 的溫床。
        """
        image = Path(image)
        if not image.exists():
            raise OmrError(f"找不到圖檔：{image}")

        target_dir = Path(out_dir) if out_dir else image.parent
        final = target_dir / f"{image.stem}.musicxml"

        workspace = _ascii_workspace()
        try:
            staged = workspace / f"page{image.suffix.lower()}"
            shutil.copyfile(image, staged)
            proc = self._run(staged)
            produced = staged.with_suffix(".musicxml")

            if proc.returncode != 0:
                raise OmrError(_explain_failure(proc))
            if not produced.exists():
                raise OmrError(
                    "homr 跑完了但沒有產出樂譜檔 —— 通常表示這張圖裡找不到可以辨識的五線譜。"
                    + _tail(proc.stdout or proc.stderr)
                )

            # 大譜表被壓成一行的話，拆開上下兩行各辨識一次再拼回來。
            # 詳細理由見 grandstaff.py —— 簡單說：homr 的大括號合併是成功的，
            # 失敗的是 transformer 沒有吐出任何「下面那一行」的符號，
            # 於是整份被寫成單行，左手全掛在 staff 1。
            positions = staged.with_suffix(".txt")
            if grandstaff.looks_collapsed(produced, positions):
                recovered = grandstaff.run_split_pass(
                    self, staged, positions, produced, workspace)
                if recovered is not None:
                    produced = recovered

            target_dir.mkdir(parents=True, exist_ok=True)
            final.write_bytes(produced.read_bytes())
            # 產出當下就修掉會害 partitura 掛掉的違規寫法。修在這裡而不是在讀的人
            # 身上，寫到磁碟上的檔案本身才是合法的 —— 使用者拿它去 analyze、
            # 用 MuseScore 開，都不會再踩到同一顆地雷。
            musicxml_fix.sanitize_file(final)

            # homr 順便會畫一張圖標出它切出來的譜表與小節線。留著給使用者對照，
            # 辨識結果怪怪的時候可以一眼看出是「切錯」還是「認錯」。
            teaser = staged.with_name(f"{staged.stem}_teaser.png")
            if teaser.exists():
                (target_dir / f"{image.stem}_teaser.png").write_bytes(teaser.read_bytes())
            return final
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def transcribe_raw(self, image):
        """對一張**已經在純 ASCII 路徑上**的圖跑一次 homr，回傳產出的 .musicxml。

        給 `grandstaff` 拆開重辨識用 —— 那條路自己準備好圖了，不需要再搬一次，
        也不該再遞迴進入「壓扁就拆開」的判斷。
        """
        image = Path(image)
        proc = self._run(image)
        produced = image.with_suffix(".musicxml")
        if proc.returncode != 0 or not produced.exists():
            return None
        musicxml_fix.sanitize_file(produced)
        return produced

    def _run(self, staged):
        try:
            return subprocess.run(
                # --write-staff-positions 會多寫一個同名 .txt，記著每個譜表的
                # 位置與「是不是合併過的大譜表」。壓扁的時候要靠它把上下行切開。
                [self.python, "-m", "homr.main", "--write-staff-positions", str(staged)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, env=self._env(), cwd=str(staged.parent),
            )
        except subprocess.TimeoutExpired as exc:
            raise OmrError(
                f"辨識超過 {self.timeout} 秒還沒跑完，已中止。"
                f"這張圖可能太大或內容太複雜，可以縮小解析度再試。"
            ) from exc
        except OSError as exc:
            raise OmrError(f"叫不起 homr：{exc}") from exc


def _explain_failure(proc):
    """把 homr 的錯誤翻成使用者看得懂的話，附上原始輸出最後幾行備查。"""
    text = (proc.stderr or "") + (proc.stdout or "")
    lowered = text.lower()

    if "unicodedecodeerror" in lowered and "cp950" in lowered:
        return ("homr 讀取內建設定檔時編碼出錯。這是子行程沒有帶到 PYTHONUTF8=1，"
                "屬於程式問題請回報。" + _tail(text))
    if "file format is not supported" in lowered or "can't open/read file" in lowered:
        return ("homr 讀不到圖檔。如果路徑含中文，表示暫存機制沒有生效（程式問題請回報）；"
                "否則請確認檔案沒有損毀，格式是 JPG 或 PNG。" + _tail(text))
    if "no module named" in lowered and "homr" in lowered:
        return ("還沒安裝 homr。請執行："
                "\n  .\\.venv\\Scripts\\python.exe -m pip install homr"
                "\n  .\\.venv\\Scripts\\python.exe -m homr.main --init")
    if "out of memory" in lowered or "memoryerror" in lowered:
        return "記憶體不足。把圖片縮小一點（長邊 3000px 以內就夠用）再試一次。"
    if "downloading" in lowered or "urlopen" in lowered or "connection" in lowered:
        return ("模型檔還沒下載完或下載失敗。先執行一次："
                "\n  .\\.venv\\Scripts\\python.exe -m homr.main --init")
    return "辨識過程出錯。" + _tail(text)


def _tail(text, lines=6):
    if not text:
        return ""
    kept = [ln for ln in text.strip().splitlines() if ln.strip()][-lines:]
    return "\n（homr 的最後幾行輸出）\n  " + "\n  ".join(kept) if kept else ""


def get_engine(name="homr", **kwargs):
    engines = {"homr": HomrEngine}
    if name not in engines:
        raise OmrError(f"不認得的 OMR 引擎 {name!r}（可用 {sorted(engines)}）")
    return engines[name](**kwargs)


def transcribe_pages(images, engine=None, on_progress=None):
    """依序辨識多張圖。

    一頁失敗只記下來、繼續跑下一頁 —— 八頁的譜跑到第三頁掛掉就整批放棄，
    使用者要重跑全部，那很浪費。回傳每一頁的結果 dict。
    """
    engine = engine or get_engine()
    results = []
    total = len(images)

    for i, image in enumerate(images, start=1):
        started = time.time()
        if on_progress:
            on_progress(i, total, Path(image).name, "running", None)
        try:
            xml = engine.transcribe(image)
            result = {
                "status": "ok",
                "musicxml": Path(xml).name,
                "engine": engine.name,
                "seconds": round(time.time() - started, 1),
                "error": None,
            }
        except OmrError as exc:
            result = {
                "status": "failed",
                "musicxml": None,
                "engine": engine.name,
                "seconds": round(time.time() - started, 1),
                "error": str(exc),
            }
        results.append(result)
        if on_progress:
            on_progress(i, total, Path(image).name, result["status"], result)

    return results
