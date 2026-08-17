"""Gate A：拍攝品質檢查。

辨識失敗大多不是模型不夠強，是照片本身就不能看。與其讓 OMR 跑三分鐘吐出一堆
垃圾，不如先花一秒判斷這張能不能用，並且**具體告訴使用者哪裡不對、怎麼重拍**。

這個模組只負責**判斷與說明**。實際的量測在 `imaging.py`、標註圖在 `overlay.py`
—— 那兩層被 `layout` / `preprocess` / `ocr` / `title` / `tempo` 共用，
跟 Gate A 的門檻是不同的東西：門檻會隨著校準一直改，量測本身不會。
為了不動既有的呼叫端，兩邊的名字在這裡原樣再匯出。
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2

from .imaging import (HORIZONTAL_TURN_RATIO, INTERLINE_TURN_MIN,  # noqa: F401
                      NORM_INTERLINE, QUARTER_TURN_RATIO, _binarize,
                      _horizontal_lines, _imread, _imwrite, _noise_floor,
                      _rotate_gray, _runs, content_bbox, deskew,
                      detect_quarter_turn, detect_staves, estimate_interline,
                      measure_blur, measure_lighting, measure_perspective,
                      measure_skew)
from .overlay import _CJK_FONTS, _draw_overlay, _load_font  # noqa: F401

# 門檻集中在這裡，要調鬆緊改這一區就好，不用動邏輯
#
# ## 這些數字是怎麼來的（2026-08-13 重新校準）
#
# 原本的門檻是拿 300 DPI 的乾淨排版圖訂的，套到手機照片上整組失效。
# 用 `tools/omr_bench.py` 對照 Mutopia 的標準答案量過之後：
#
#     拍攝條件   前處理後辨識率   當時的判定
#     good           100.0%       ❌ 退件
#     normal         100.0%       ❌ 退件
#     rough        3.2 / 31.5%    ❌ 退件
#
# **每一張都退件，包括辨識率 100% 的。** 使用者拍了一張完全可用的照片，
# 系統卻叫他重拍 —— 這比不檢查還糟。
#
# 而且量出來的數字根本沒有鑑別力：清晰度在「可用」的照片上是 8~178，
# 在「不可用」的上面是 31~172，兩區完全重疊，任何門檻都是在擲骰子。
# （Laplacian 變異數對感光雜訊極度敏感，糊照片的顆粒反而讓它變高。）
#
# 所以改成：**只退真正沒救的**，其餘一律給提醒。歪斜與透視更是直接降級 ——
# 前處理會把紙拉正，為了系統自己會修的問題叫人重拍毫無道理。
THRESHOLDS = {
    "interline_reject": 6.0,      # 行距低於這個，五條譜線已經糊成一條灰帶
    "interline_warn": 10.0,
    # 清晰度只留一個「幾乎沒有任何高頻」的地板。實測可用照片最低到 8，
    # 訂在 3 是為了擋住整片糊掉的圖，不是為了分辨對焦準不準 —— 它做不到。
    "blur_reject": 3.0,
    "blur_warn": 40.0,
    "skew_reject": 8.0,           # 度。只影響提醒的措辭，不退件
    "skew_warn": 3.0,
    "perspective_warn": 1.15,     # 左右三分之一的行距比值
    "clip_warn": 0.05,            # 壓成全黑的像素比例（白紙不算過曝，見 measure_lighting）
    "dark_reject": 55.0,          # 亮度中位數
    "dark_warn": 95.0,
    "glare_warn": 0.04,           # 譜表橫帶裡被反光沖掉的比例
    "staff_coverage_warn": 0.60,  # 譜表橫向覆蓋率
    "min_staves": 1,
}

_BORDER_FRAC = 0.01     # 內容碰到邊緣多近算「被切到」


@dataclass
class Issue:
    code: str
    level: str      # "reject" | "warn"
    message: str
    hint: str

    def as_dict(self):
        return {"code": self.code, "level": self.level,
                "message": self.message, "hint": self.hint}


@dataclass
class QualityReport:
    verdict: str = "ok"
    interline_px: float = 0.0
    blur: float = 0.0
    skew_deg: float = 0.0
    perspective_ratio: float = 1.0
    clip_ratio: float = 0.0
    brightness: float = 255.0
    glare_ratio: float = 0.0
    staff_count: int = 0
    staff_coverage: float = 0.0
    width: int = 0
    height: int = 0
    issues: list = field(default_factory=list)
    overlay: str = ""
    _staff_lines: list = field(default_factory=list, repr=False)
    _content_box: tuple = field(default=None, repr=False)
    _deskewed: float = 0.0

    def as_dict(self):
        return {
            "verdict": self.verdict,
            "interline_px": round(self.interline_px, 2),
            "blur": round(self.blur, 1),
            "skew_deg": round(self.skew_deg, 2),
            "perspective_ratio": round(self.perspective_ratio, 3),
            "clip_ratio": round(self.clip_ratio, 4),
            "brightness": round(self.brightness, 1),
            "glare_ratio": round(self.glare_ratio, 4),
            "staff_count": self.staff_count,
            "staff_coverage": round(self.staff_coverage, 3),
            "size": [self.width, self.height],
            "issues": [i.as_dict() for i in self.issues],
            "overlay": self.overlay,
        }

    def summary_line(self):
        mark = {"ok": "✅", "warn": "⚠", "reject": "❌"}[self.verdict]
        if not self.issues:
            return f"{mark} 可以使用（行距 {self.interline_px:.1f}px、清晰度 {self.blur:.0f}）"
        return f"{mark} " + "；".join(i.message for i in self.issues)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _lighting_of_original(original_gray, original_ink, skew, interline_hint):
    """在**原始照片**上量曝光與反光。

    前處理的攤平打光會把亮度統一到 200 左右，量整理後的圖等於在問一個
    已經被改過的答案。這裡自己在原圖上做一次「轉正 → 找譜線 → 量」，
    多花不到一秒，換來的是「太暗」「反光」這兩項回饋不會憑空消失。
    """
    interline, _ = estimate_interline(original_ink)
    if interline <= 0:
        interline = interline_hint if interline_hint > 0 else 10.0

    straight = deskew(original_ink, skew)
    straight_gray = _rotate_gray(original_gray, skew)
    lines = _horizontal_lines(straight, interline)
    _, _, staff_lines = detect_staves(lines, interline)
    return measure_lighting(straight_gray, straight, staff_lines, interline)


def check_image(path, overlay_path=None, thresholds=None, prepared=None):
    """檢查一張照片能不能拿去做 OMR。

    **量的是前處理後的圖，不是原始照片。** 原因是實測出來的：整張照片裡桌面
    往往佔三到四成，而桌面是有紋理的，量清晰度時它會把數字整個帶偏 ——
    一張辨識率 10% 的糊照片量到 250，比一張辨識率 100% 的清楚照片（207）還高。
    所有門檻在那種數字上都沒有意義。

    改成先把紙找出來、拉正、裁到樂譜再量之後，量到的才是 homr 真正會看到的東西，
    掃描檔與手機照片的數字也才有可比性。

    prepared: 已經整理好的圖。同一次建構裡辨識、版面偵測也要用它，
              傳進來就不必重跑一次（前處理一張要一兩秒）。

    **有三項例外，必須量原始照片**：傾斜、曝光、反光。

    傾斜是因為前處理會把紙拉正，量整理後的圖永遠是 0 度，但「你拍歪了」還是該
    告訴使用者。曝光與反光則是攤平打光那一步會把亮度統一拉到 200 左右 ——
    量整理後的圖，一張全黑的照片會顯示「亮度 200、沒有反光」。
    但攤平救得回**亮度**，救不回**已經被壓成純黑的細節**，也接不回被反光沖斷的
    譜線；那些是真正的損失，一定要照實講。
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    original = _imread(path)
    original_gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)

    if prepared is None:
        from . import preprocess      # 這裡才 import：preprocess 反過來要用 quality
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = preprocess.prepare(path, Path(tmp) / "prep.png")
            img = _imread(result.path)
    else:
        img = _imread(prepared)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    report = QualityReport(width=w, height=h)

    ink = _binarize(gray)
    interline, thickness = estimate_interline(ink)
    report.interline_px = interline

    # ---- 只有原圖才問得出來的三件事：拍歪、曝光、反光 ----
    original_ink = _binarize(original_gray)
    report.skew_deg = measure_skew(original_ink)
    report.clip_ratio, report.brightness, report.glare_ratio = _lighting_of_original(
        original_gray, original_ink, report.skew_deg, interline)

    if interline <= 0:
        report.issues.append(Issue(
            "NO_STAFF", "reject",
            "這張圖裡找不到五線譜",
            "確認拍的是五線譜、整頁都在畫面內，而且沒有嚴重反光或失焦。"
            "如果這是簡譜或數字譜，請改用文字記譜的方式輸入。",
        ))
        report.verdict = "reject"
        report.overlay = _draw_overlay(img, report, overlay_path) if overlay_path else ""
        return report

    # 前處理已經拉正過了，但沒拉成功時還是要自己轉一次才找得到譜線
    residual = measure_skew(ink)
    straight = deskew(ink, residual)
    # 譜線的 y 座標是在轉正後的座標系裡，所以量反光的灰階圖也要一起轉正
    straight_gray = _rotate_gray(gray, residual)

    lines_img = _horizontal_lines(straight, interline)
    report.staff_count, report.staff_coverage, report._staff_lines = detect_staves(
        lines_img, interline
    )
    report.blur = measure_blur(gray, interline, ink)
    report.perspective_ratio = measure_perspective(straight, interline)
    report._content_box = content_bbox(ink)
    report._deskewed = residual

    _judge(report, th)
    if overlay_path:
        report.overlay = _draw_overlay(img, report, overlay_path)
    return report


def _judge(report, th):
    """把量到的數字翻成使用者看得懂的話。"""
    add = report.issues.append

    # 模糊、太小、太歪都會連帶讓譜表偵測失敗。這種時候要講的是根本原因，
    # 講「找不到五線譜」等於叫使用者去查一個他自己造成的症狀。
    # **只有退件等級的原因才算「根本原因」。**
    #
    # 原本歪斜與透視也列在這裡，但那兩項自己只是警告（前處理會把紙拉正），
    # 結果變成：三張刻意裁切過頭的測試圖因為「有透視問題」就把 NO_STAFF
    # 整個壓掉，最後一個退件理由都沒有 —— 系統照樣收下並產出十份廢譜面。
    #
    # 前處理修得掉的問題，不該用來掩蓋前處理修不掉的問題。
    root_cause = (
        report.blur < th["blur_reject"]
        or report.interline_px < th["interline_reject"]
        or report.brightness < th["dark_reject"]
    )

    # 「一條完整譜表都找不到」現在是**退件**條件。
    #
    # 以前只當提醒，理由是「譜表偵測比 homr 還弱，數不到不代表沒救」。
    # 2026-08-16 用全部 45 頁重新校準之後，那句話已經不成立：
    #
    #     staff_count = 0 只出現在兩個專案
    #       拍照測試（三張刻意裁切過頭的測試圖）[0,0,0]  -> 最終只有 10% 小節可靠
    #       Bach 平均律（行距 6px 的掃描件）[16,14,12,0,0] -> 20%
    #     其餘 10 個專案每一頁都是 4~20 條，可靠度 43~100%
    #
    # 也就是說它現在分得很乾淨。而且**放行的代價很具體**：那三張測試圖
    # 本來就是要驗證系統會不會擋下來的，結果系統不但沒擋，還替它們建了
    # 一個「曲子」、產出十份沒辦法彈的譜面。
    #
    # 這種圖的共同點是**樂譜被裁掉太多**：譜表左右被切斷、小節不完整，
    # 就算轉正、對焦都完美也還原不出正確的樂譜。
    if report.staff_count < th["min_staves"]:
        if not root_cause:
            add(Issue(
                "NO_STAFF", "reject",
                "找不到任何一條完整的五線譜 —— 樂譜可能被裁切掉太多了",
                "整頁樂譜都要在畫面內，四周留一點空白；不要只拍一部分或拍到一半。"
                "如果這是簡譜（數字譜），請改用文字記譜的方式輸入。",
            ))
    elif report.staff_coverage < th["staff_coverage_warn"]:
        add(Issue(
            "STAFF_CUT", "warn",
            f"譜線只有 {report.staff_coverage:.0%} 是完整的，可能被切到或被擋住",
            "把整頁樂譜都框進畫面，避免手指、書脊陰影壓在譜上。",
        ))

    if report.interline_px < th["interline_reject"]:
        add(Issue(
            "TOO_SMALL", "reject",
            f"譜行間距只有 {report.interline_px:.1f}px，太小了認不出音符",
            "靠近一點重拍，或把相機解析度調高。建議行距至少 12px —— "
            "一般手機正對著 A4 譜拍滿畫面就夠了。",
        ))
    elif report.interline_px < th["interline_warn"]:
        add(Issue(
            "SMALL", "warn",
            f"譜行間距 {report.interline_px:.1f}px 偏小，辨識率會下降",
            "如果結果不理想，靠近一點重拍。",
        ))

    if report.blur < th["blur_reject"]:
        add(Issue(
            "BLUR", "reject",
            f"對焦不清楚（清晰度 {report.blur:.0f}，需要 {th['blur_reject']:.0f} 以上）",
            "手機先點一下螢幕上的譜對焦，等對焦框變綠再按快門；"
            "光線不足時手震會特別嚴重，找亮一點的地方拍。",
        ))
    elif report.blur < th["blur_warn"]:
        add(Issue(
            "SOFT", "warn",
            f"照片有點糊（清晰度 {report.blur:.0f}）",
            "可以先試著辨識，結果不好再重拍。",
        ))

    # 歪與透視都只給提醒，**不退件** —— 前處理會把紙找出來拉正，實測拿掉那一步
    # 辨識率從 98.8% 掉到 88.6%，也就是說它真的在做事。為了一個系統自己會修的
    # 問題叫使用者重拍，只是在浪費使用者的時間。
    # 前處理失敗時，殘留的歪斜會讓譜表偵測失敗，那時 NO_STAFF 會自己跳出來。
    skew = abs(report.skew_deg)
    if skew > th["skew_reject"]:
        add(Issue(
            "SKEW", "warn",
            f"照片歪了 {skew:.1f}°（系統會自動轉正）",
            "下次把譜擺正、相機正對著拍，可以少掉一次自動校正的誤差。",
        ))
    elif skew > th["skew_warn"]:
        add(Issue(
            "TILT", "warn",
            f"照片有點歪（{skew:.1f}°）",
            "系統會自動校正，但擺正一點結果更準。",
        ))

    if report.perspective_ratio > th["perspective_warn"]:
        add(Issue(
            "PERSPECTIVE", "warn",
            f"拍攝角度太斜（左右譜行間距差 {(report.perspective_ratio - 1) * 100:.0f}%）",
            "從正上方拍，不要從側邊或斜角拍。書本翻開時中間會拱起來，可以壓平一點。",
        ))

    if report.clip_ratio > th["clip_warn"]:
        add(Issue(
            "DARK_CLIP", "warn",
            f"有 {report.clip_ratio:.0%} 的畫面壓成全黑，那裡的譜線救不回來",
            "找亮一點的地方拍，或把手機的曝光補償往上調。",
        ))

    if report.brightness < th["dark_reject"]:
        add(Issue(
            "TOO_DARK", "reject",
            f"照片太暗了（亮度中位數 {report.brightness:.0f}）",
            "在亮一點的地方拍，或開燈。太暗的照片雜訊很重，譜線會跟雜訊混在一起。",
        ))
    elif report.brightness < th["dark_warn"]:
        add(Issue(
            "DARK", "warn",
            f"照片偏暗（亮度中位數 {report.brightness:.0f}）",
            "光線再亮一點結果會更穩。",
        ))

    if report.glare_ratio > th["glare_warn"]:
        add(Issue(
            "GLARE", "warn",
            f"譜表上有大約 {report.glare_ratio:.0%} 的面積被反光沖掉了",
            "關掉閃光燈，換個角度避開燈的倒影，或是把譜移到光源側邊。",
        ))

    box = report._content_box
    if box:
        margin_x = report.width * _BORDER_FRAC
        margin_y = report.height * _BORDER_FRAC
        touched = []
        if box[0] <= margin_x:
            touched.append("左")
        if box[1] <= margin_y:
            touched.append("上")
        if box[2] >= report.width - margin_x:
            touched.append("右")
        if box[3] >= report.height - margin_y:
            touched.append("下")
        if touched:
            add(Issue(
                "CROPPED", "warn",
                f"內容碰到畫面的{''.join(touched)}邊，可能被切掉",
                "退後一點，讓整頁樂譜四周都留一些空白再拍。",
            ))

    levels = {i.level for i in report.issues}
    report.verdict = "reject" if "reject" in levels else ("warn" if levels else "ok")
