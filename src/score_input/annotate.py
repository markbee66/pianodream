"""把小節框畫到照片上，給練習檢討用。

從 `layout.py` 拆出來的。偵測與繪圖是兩件事：偵測要顧的是「找不找得到」，
繪圖要顧的是「看不看得懂」，而且只有檢討畫面會用到它。
"""

import cv2
import numpy as np

from .imaging import _imread


def highlight(image, boxes, out_path, labels=None,
              color=(60, 60, 235), thickness_ratio=0.006, dim=0.45):
    """把指定的小節框起來存成一張圖。

    沒被選到的區域壓暗，讓要練的地方一眼就跳出來 —— 只畫紅框的話，
    在滿滿的音符裡還是要找一下。
    """
    img = _imread(image)
    h, w = img.shape[:2]

    if dim > 0 and boxes:
        # 先整張壓暗，再把選中的小節用原圖貼回去
        darkened = (img.astype(np.float32) * (1.0 - dim)
                    + 255.0 * dim * 0.15).astype(np.uint8)
        mask = np.zeros((h, w), np.uint8)
        for box in boxes:
            pts = np.array([box.corners], dtype=np.int32)
            cv2.fillPoly(mask, pts, 255)
        canvas = np.where(mask[..., None] > 0, img, darkened)
    else:
        canvas = img.copy()

    thickness = max(2, int(min(w, h) * thickness_ratio))
    for box in boxes:
        pts = np.array([box.corners], dtype=np.int32)
        cv2.polylines(canvas, pts, isClosed=True, color=color, thickness=thickness)

    if labels:
        canvas = _draw_labels(canvas, boxes, labels, color)

    out_path = str(out_path)
    ext = out_path[out_path.rfind("."):] if "." in out_path else ".jpg"
    ok, buf = cv2.imencode(ext, canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if ok:
        buf.tofile(out_path)
    return out_path


def _draw_labels(canvas, boxes, labels, color):
    """在每個框上方標小節號與問題。用 PIL 才畫得出中文。"""
    from PIL import Image, ImageDraw

    from .overlay import _load_font

    pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil, "RGBA")
    size = max(14, canvas.shape[1] // 60)
    font = _load_font(size)
    rgb = (color[2], color[1], color[0])

    for box in boxes:
        text = labels.get(box.index)
        if not text:
            continue
        x = min(p[0] for p in box.corners)
        y = min(p[1] for p in box.corners)
        bbox = draw.textbbox((0, 0), text, font=font)
        pad = size // 4
        draw.rectangle([x, y - bbox[3] - pad * 2, x + bbox[2] + pad * 2, y],
                       fill=rgb + (235,))
        draw.text((x + pad, y - bbox[3] - pad), text, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
