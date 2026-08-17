"""樂譜專案：管理上傳的檔案與它們的順序。

一個專案 = data/projects/<名稱>/ 一個資料夾 + 一份 manifest.json。

照片專案的項目是「頁」，文字記譜專案的項目是「段」，但兩者共用同一套順序管理 ——
排序、替換、刪除的邏輯跟內容是圖還是字無關。

順序是 manifest 裡的顯式欄位，不是靠檔名隱含。使用者在網頁上拖曳調整後，
實體檔名不會改變（用 sha1 認人），只有 index 重編。
"""

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = ROOT / "data" / "projects"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_SUFFIXES = {".txt", ".jp", ".jianpu"}
PDF_SUFFIXES = {".pdf"}

SOURCE_TYPES = {"photo", "jianpu", "letter"}

# 專案名要當資料夾名用，擋掉 Windows 不接受的字元
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ProjectError(RuntimeError):
    """使用者層級的錯誤：名稱不合法、專案不存在、檔案格式不對等。"""


def _check_name(name):
    name = (name or "").strip()
    if not name:
        raise ProjectError("專案名稱不能空白")
    if _BAD_NAME_CHARS.search(name):
        raise ProjectError(f'專案名稱不能包含 < > : " / \\ | ? * 這些字元：{name}')
    if name in {".", ".."}:
        raise ProjectError(f"專案名稱不能是 {name}")
    return name


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def natural_key(text):
    """自然排序：page2 要排在 page10 前面，所以數字段落要當數字比。

    純字串比較會得到 page10 < page2（因為 '1' < '2'），那就是錯的頁序。
    """
    parts = re.split(r"(\d+)", str(text))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def exif_taken_at(path):
    """讀照片的拍攝時間 (EXIF DateTimeOriginal)，讀不到就回 None。

    連拍的樂譜照片通常檔名沒有規律，但拍攝時間一定是照順序的。
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # 0x9003 = DateTimeOriginal, 0x0132 = DateTime
            for tag in (0x9003, 0x0132):
                raw = exif.get(tag)
                if raw:
                    return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
            # DateTimeOriginal 通常在 Exif IFD 子區塊裡，主表沒有時往下找
            try:
                sub = exif.get_ifd(0x8769)
            except (AttributeError, KeyError):
                return None
            raw = sub.get(0x9003) if sub else None
            if raw:
                return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError, ImportError):
        return None
    return None


def sort_for_import(paths):
    """決定批次匯入的預設順序。

    優先用 EXIF 拍攝時間（連拍的譜一定是照順序拍的），全部都讀不到才退回檔名自然排序。
    只有「全部」都有 EXIF 才用它 —— 混用兩種排序基準會排出很怪的結果。
    """
    paths = [Path(p) for p in paths]
    taken = [exif_taken_at(p) for p in paths]
    if paths and all(t is not None for t in taken):
        return [p for _, p in sorted(zip(taken, paths), key=lambda pair: pair[0])]
    return sorted(paths, key=lambda p: natural_key(p.name))


def kind_of(path):
    """檔案屬於哪一類。PDF 在匯入時會被逐頁拆成圖，不會直接變成項目。"""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    return None


class Project:
    """一份 manifest 的讀寫介面。所有異動都會立刻存檔。"""

    def __init__(self, name, root=None):
        self.name = _check_name(name)
        base = Path(root) if root else PROJECTS_DIR
        self.dir = base / self.name
        self.manifest_path = self.dir / "manifest.json"
        self.data = None

    # ---- 建立 / 載入 ----------------------------------------------------

    @classmethod
    def create(cls, name, source_type, root=None, exist_ok=False):
        if source_type not in SOURCE_TYPES:
            raise ProjectError(
                f"不認得的樂譜來源 {source_type!r}（可用 {sorted(SOURCE_TYPES)}）"
            )
        proj = cls(name, root=root)
        if proj.manifest_path.exists():
            if not exist_ok:
                raise ProjectError(
                    f"專案「{proj.name}」已經存在了。要重來的話先刪掉 {proj.dir}"
                )
            return proj.load()
        proj.dir.mkdir(parents=True, exist_ok=True)
        proj.data = {
            "name": proj.name,
            "source_type": source_type,
            "created": datetime.now().isoformat(timespec="seconds"),
            "items": [],
            "build": None,
        }
        proj.save()
        return proj

    @classmethod
    def load(cls, name=None, root=None, _self=None):
        proj = _self or cls(name, root=root)
        if not proj.manifest_path.exists():
            raise ProjectError(
                f"找不到專案「{proj.name}」。先用 `run.py score new {proj.name}` 建立"
            )
        proj.data = json.loads(proj.manifest_path.read_text(encoding="utf-8"))
        return proj

    @classmethod
    def open_or_create(cls, name, source_type, root=None):
        proj = cls(name, root=root)
        if proj.manifest_path.exists():
            proj = cls.load(_self=proj)
            if proj.source_type != source_type:
                raise ProjectError(
                    f"專案「{proj.name}」原本是 {proj.source_type} 類型，"
                    f"不能混進 {source_type}。請另開一個專案。"
                )
            return proj
        return cls.create(name, source_type, root=root)

    @classmethod
    def list_all(cls, root=None):
        base = Path(root) if root else PROJECTS_DIR
        if not base.exists():
            return []
        names = [d.name for d in base.iterdir() if (d / "manifest.json").exists()]
        return sorted(names, key=natural_key)

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 屬性 ------------------------------------------------------------

    @property
    def source_type(self):
        return self.data["source_type"]

    @property
    def items(self):
        return self.data["items"]

    def item(self, index):
        for it in self.items:
            if it["index"] == index:
                return it
        raise ProjectError(f"專案「{self.name}」沒有第 {index} 項（共 {len(self.items)} 項）")

    def path_of(self, item):
        return self.dir / item["file"]

    # ---- 異動 ------------------------------------------------------------

    def add(self, paths, sort=True):
        """依順序把檔案複製進專案並附加到清單末端。

        sort=True 時先用 EXIF/檔名決定順序；sort=False 表示呼叫端已經排好了
        （例如網頁上使用者自己拖出來的順序），照單全收。

        回傳 (新增的項目, 因重複而略過的檔名)。
        """
        paths = list(sort_for_import(paths) if sort else [Path(p) for p in paths])
        expanded = []
        for p in paths:
            if not Path(p).exists():
                raise ProjectError(f"找不到檔案：{p}")
            kind = kind_of(p)
            if kind == "pdf":
                expanded.extend(self._split_pdf(p))
            elif kind is None:
                raise ProjectError(
                    f"不支援的檔案格式：{Path(p).suffix}"
                    f"（照片用 {sorted(IMAGE_SUFFIXES)}、記譜用 {sorted(TEXT_SUFFIXES)}、或 PDF）"
                )
            else:
                expanded.append((kind, Path(p)))

        self._check_kinds_match(expanded)

        known = {it["sha1"] for it in self.items}
        added, skipped = [], []
        for kind, src in expanded:
            digest = sha1_of(src)
            if digest in known:
                skipped.append(src.name)
                continue
            known.add(digest)
            index = len(self.items) + 1
            stored = self._store(src, index, kind)
            item = {
                "index": index,
                "kind": kind,
                "file": stored,
                "original_name": src.name,
                "sha1": digest,
                "added": datetime.now().isoformat(timespec="seconds"),
                "check": None,
                "parse": None,
            }
            self.items.append(item)
            added.append(item)
        self.save()
        return added, skipped

    def _check_kinds_match(self, expanded):
        """照片專案只收圖、文字專案只收文字 —— 混在一起合併規則會打架。"""
        want = "text" if self.source_type in {"jianpu", "letter"} else "image"
        wrong = [str(p) for kind, p in expanded if kind != want]
        if wrong:
            label = "數字/字母記譜的文字檔" if want == "text" else "照片"
            raise ProjectError(
                f"專案「{self.name}」是 {self.source_type} 類型，只能加入{label}。"
                f"不合的檔案：{', '.join(wrong[:3])}"
            )

    def _store(self, src, index, kind):
        suffix = src.suffix.lower()
        prefix = "page" if kind == "image" else "part"
        stored = f"{prefix}_{index:02d}{suffix}"
        target = self.dir / stored
        # 換頁時舊檔可能還佔著同一個名字，先讓路
        if target.exists() and sha1_of(target) != sha1_of(src):
            target.unlink()
        shutil.copy2(src, target)
        return stored

    def _split_pdf(self, pdf_path):
        """PDF 逐頁轉成 300 DPI 的 PNG，之後就跟一般照片同路。"""
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ProjectError(
                "要匯入 PDF 需要 pypdfium2，請先執行："
                "\n  .\\.venv\\Scripts\\python.exe -m pip install pypdfium2"
            ) from exc

        out = []
        tmp_dir = self.dir / "_pdf_pages"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            for i in range(len(pdf)):
                # scale 是相對 72 DPI 的倍率，300/72 才會得到 300 DPI
                bitmap = pdf[i].render(scale=300 / 72)
                png = tmp_dir / f"{Path(pdf_path).stem}_p{i + 1:02d}.png"
                bitmap.to_pil().save(png)
                out.append(("image", png))
        finally:
            pdf.close()
        return out

    def reorder(self, new_order):
        """new_order 是現有 index 的排列，例如 [3, 1, 2]。"""
        current = [it["index"] for it in self.items]
        if sorted(new_order) != sorted(current):
            raise ProjectError(
                f"順序清單對不上：專案有第 {current} 項，但你給的是 {list(new_order)}。"
                f"必須剛好每一項都出現一次。"
            )
        by_index = {it["index"]: it for it in self.items}
        self.data["items"] = [by_index[i] for i in new_order]
        self._renumber()
        self.save()
        return self.items

    def remove(self, index):
        item = self.item(index)
        self.items.remove(item)
        self._renumber()
        self.save()
        return item

    def replace(self, index, new_path):
        """換掉某一項（重拍那一頁 / 改好的記譜檔），順序不變。

        檢查與解析結果一併清空，因為它們描述的是舊檔案。
        """
        item = self.item(index)
        new_path = Path(new_path)
        if not new_path.exists():
            raise ProjectError(f"找不到檔案：{new_path}")
        kind = kind_of(new_path)
        if kind == "pdf":
            raise ProjectError("替換單一項目時不能用 PDF，請先轉成圖片")
        self._check_kinds_match([(kind, new_path)])

        old = self.path_of(item)
        if old.exists():
            old.unlink()
        item["kind"] = kind
        item["file"] = self._store(new_path, index, kind)
        item["original_name"] = new_path.name
        item["sha1"] = sha1_of(new_path)
        item["added"] = datetime.now().isoformat(timespec="seconds")
        item["check"] = None
        item["parse"] = None
        self.save()
        return item

    def _renumber(self):
        for i, it in enumerate(self.items, start=1):
            it["index"] = i

    # ---- 檢查與辨識結果 --------------------------------------------------

    def set_check(self, index, result):
        self.item(index)["check"] = result
        self.save()

    def set_parse(self, index, result):
        self.item(index)["parse"] = result
        self.save()

    def set_build(self, info):
        self.data["build"] = info
        self.save()

    def rejected(self):
        """被判定不能用的項目 —— 有這些就不該往下辨識。"""
        return [it for it in self.items if (it.get("check") or {}).get("verdict") == "reject"]

    def unchecked(self):
        return [it for it in self.items if not it.get("check")]

    def summary(self):
        """給 CLI 與網頁共用的一行式狀態摘要。"""
        rows = []
        for it in self.items:
            check = it.get("check") or {}
            parse = it.get("parse") or {}
            rows.append(
                {
                    "index": it["index"],
                    "file": it["file"],
                    "original_name": it.get("original_name", it["file"]),
                    "kind": it["kind"],
                    "verdict": check.get("verdict", "—"),
                    "issues": check.get("issues", []),
                    "parse_status": parse.get("status", "—"),
                    "measures": parse.get("measures"),
                    "notes": parse.get("notes"),
                    "confidence": parse.get("confidence"),
                    "problems": parse.get("problems", []),
                }
            )
        return rows
