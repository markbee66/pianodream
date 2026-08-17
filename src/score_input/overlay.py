"""把 Gate A 的量測結果畫在照片上。

從 `quality.py` 拆出來的。畫圖跟判斷是兩件事：判斷會隨著校準結果一直改門檻，
畫圖只在乎「怎麼讓使用者一眼看到問題在哪」。中文字型那一段也被
`layout.annotate` 借去標小節號，本來就不該藏在 Gate A 裡面。
"""

from pathlib import Path

import cv2
import numpy as np

_CJK_FONTS = [
    r"C:\Windows\Fonts\msjh.ttc",     # 微軟正黑體
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _load_font(size):
    from PIL import ImageFont

    for path in _CJK_FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_overlay(img, report, out_path):
    """把偵測結果畫在照片上，讓使用者一眼看到問題在哪，而不是只看到一句「太模糊」。"""
    from PIL import Image, ImageDraw

    from .imaging import _imwrite

    canvas = img.copy()
    h, w = canvas.shape[:2]

    # 譜線是在「轉正後」的座標系裡找到的，要轉回原圖的角度才會疊在真正的譜線上，
    # 否則使用者看到的綠線會整片飄在旁邊，反而讓人以為偵測壞掉。
    angle = report.skew_deg
    back = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
    thickness = max(1, h // 900)
    for y in report._staff_lines:
        pts = np.array([[0, y, 1], [w, y, 1]], dtype=np.float64).T
        (x0, x1), (y0, y1) = back @ pts
        cv2.line(canvas, (int(x0), int(y0)), (int(x1), int(y1)), (0, 200, 0), thickness)

    if report._content_box:
        x0, y0, x1, y1 = report._content_box
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (255, 170, 0), max(2, h // 700))

    if abs(report.skew_deg) > 0.5:
        # 畫一條沿著偵測到的傾斜角的線，跟水平參考線對比
        cy = h // 2
        dx = w // 2
        dy = int(np.tan(np.deg2rad(report.skew_deg)) * dx)
        cv2.line(canvas, (0, cy - dy), (w, cy + dy), (0, 0, 255), max(2, h // 700))
        cv2.line(canvas, (0, cy), (w, cy), (200, 200, 200), max(1, h // 1200))

    pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil, "RGBA")

    scale = max(14, w // 55)
    font = _load_font(scale)
    small = _load_font(int(scale * 0.8))

    colour = {"ok": (32, 150, 60), "warn": (200, 140, 0), "reject": (200, 40, 40)}[report.verdict]
    label = {"ok": "可以使用", "warn": "可以用，但有問題", "reject": "不能用，請重拍"}[report.verdict]

    lines = [f"{label}　行距 {report.interline_px:.1f}px　清晰度 {report.blur:.0f}"
             f"　傾斜 {report.skew_deg:+.1f}°　譜表 {report.staff_count} 組"]
    for issue in report.issues:
        # 用中文標籤不用 emoji —— 中文字型沒有 emoji 字身，畫出來會是一個空框
        prefix = "【不能用】" if issue.level == "reject" else "【注意】"
        lines.append(f"{prefix} {issue.message}")
        lines.append(f"    → {issue.hint}")

    pad = scale // 2
    heights = []
    for i, text in enumerate(lines):
        f = font if i == 0 else small
        heights.append(draw.textbbox((0, 0), text, font=f)[3] + pad // 2)
    box_h = sum(heights) + pad * 2

    draw.rectangle([0, 0, w, box_h], fill=(255, 255, 255, 235))
    draw.rectangle([0, 0, w, max(4, scale // 5)], fill=colour + (255,))

    y = pad
    for i, text in enumerate(lines):
        f = font if i == 0 else small
        fill = colour if i == 0 else (40, 40, 40)
        draw.text((pad, y), text, font=f, fill=fill)
        y += heights[i]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _imwrite(out_path, cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
    return out_path.name
