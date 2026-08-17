"""把乾淨的樂譜圖變成「手機拍出來的樣子」，用來量 OMR 在真實條件下的正確率。

為什麼需要這支工具：`tools/omr_accuracy.py` 量的是 300 DPI 的 PDF 直接轉圖，
那是 OMR 的最佳情況，不是使用者的情況。使用者是拿手機對著紙拍。但拍下來的照片
沒有標準答案 —— Mutopia 的 MIDI 對應的是那份 PDF，不是我手上的照片。

所以反過來做：拿有標準答案的 PDF，**故意把它弄成照片的樣子**，標準答案照樣有效。

模擬的每一項都是實際會發生的事，不是隨機加噪音：

    取景    紙不會填滿畫面，桌面會入鏡（homr 一律把圖縮到寬 1920，
            所以紙佔畫面多少，直接決定譜線在模型眼裡有多大）
    透視    手不會剛好在正上方，四個角會被拉歪
    傾斜    紙不會擺得剛好正
    打光    光源在某一側，一邊亮一邊暗，還可能有手的陰影
    對焦    近拍時景深很淺，邊緣容易糊
    感光    ISO 拉高的顆粒
    JPEG    手機一定會存成 JPEG

用固定 seed，同一個 preset 每次產生的結果完全一樣 —— 不然量出來的正確率
會隨機浮動，比較就沒有意義。

    python tools\\photo_sim.py 乾淨圖.png 輸出.jpg --preset normal
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 每個 preset 是一組「拍成什麼樣」的參數。
#
#   page_frac      紙的寬度佔畫面多少（0.95 = 幾乎貼邊，0.62 = 拿很遠拍）
#   rotate_deg     旋轉幾度
#   warp           四個角各自被拉開多少（佔畫面比例）
#   light_drop     最暗的地方剩多少亮度（1.0 = 打光完全均勻）
#   blur_sigma     失焦程度，單位是「相對於行距」，所以跟解析度無關
#   noise          感光顆粒的標準差（灰階值）
#   jpeg           JPEG 品質
#   long_edge      輸出長邊多少像素（手機通常 3000-4000，這裡取保守值）
PRESETS = {
    # 很認真拍的：正對、光均勻、對焦準。這是使用者做得到的最好情況
    "good": dict(page_frac=0.92, rotate_deg=0.8, warp=0.004, light_drop=0.92,
                 blur_sigma=0.10, noise=1.5, jpeg=92, long_edge=2600),
    # 隨手拍的：稍微歪、稍微斜、光偏一邊。這是預設會遇到的情況
    "normal": dict(page_frac=0.80, rotate_deg=3.0, warp=0.018, light_drop=0.70,
                   blur_sigma=0.22, noise=3.0, jpeg=85, long_edge=2400),
    # 拍得不好但還看得懂：拿得遠、歪得明顯、一邊有陰影
    "rough": dict(page_frac=0.66, rotate_deg=6.5, warp=0.038, light_drop=0.52,
                  blur_sigma=0.38, noise=5.0, jpeg=78, long_edge=2200),
}


def _imwrite(path, img, quality=92):
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError(f"寫不出檔案：{path}")
    buf.tofile(str(path))


def _imread(path):
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"讀不進來：{path}")
    return img


def _interline_of(gray):
    """粗估行距，只為了讓模糊程度跟解析度無關。"""
    from src.score_input.quality import _binarize, estimate_interline

    interline, _ = estimate_interline(_binarize(gray))
    return interline if interline > 0 else 16.0


def _desk_background(shape, rng):
    """桌面。刻意做成有輕微紋理的中灰色，不是純色 ——
    純色背景會讓「找出紙的範圍」變得比真實情況簡單，量出來的分數會虛高。"""
    h, w = shape
    base = rng.integers(95, 135)
    small = rng.normal(base, 14, (max(2, h // 40), max(2, w // 40)))
    bg = cv2.resize(small.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    bg += rng.normal(0, 4, (h, w))
    return np.clip(bg, 0, 255).astype(np.uint8)


def _lighting(shape, drop, rng):
    """單一側光源造成的亮度梯度，再加一塊柔和的陰影（例如手或書脊）。

    回傳一張 0-1 的乘法遮罩。用乘法而不是加法：真實的打光不均是反射率乘上
    照度，加法會把黑色的墨水也一起提亮，看起來就假了。
    """
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    yy /= h
    xx /= w

    angle = rng.uniform(0, 2 * np.pi)
    ramp = np.cos(angle) * xx + np.sin(angle) * yy       # -1 .. 1
    ramp = (ramp - ramp.min()) / max(1e-6, float(np.ptp(ramp)))   # 0 .. 1
    mask = drop + (1.0 - drop) * ramp

    # 一塊柔和的陰影
    cx, cy = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9)
    r = rng.uniform(0.25, 0.5)
    blob = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
    mask *= 1.0 - 0.35 * (1.0 - drop) * blob
    return np.clip(mask, 0.05, 1.0).astype(np.float32)


def simulate(src, dst, preset="normal", seed=0):
    """把 src 弄成照片的樣子存到 dst，回傳實際用的參數。"""
    cfg = dict(PRESETS[preset])
    rng = np.random.default_rng(seed)
    page = _imread(src)

    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    interline = _interline_of(gray)

    ph, pw = page.shape[:2]
    # 畫面比紙大，大多少由 page_frac 決定。多出來的就是桌面。
    frame_w = int(round(pw / cfg["page_frac"]))
    frame_h = int(round(ph / cfg["page_frac"]))
    scale = 1.0

    canvas = _desk_background((frame_h, frame_w), rng)
    canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)

    # 紙放在畫面中間附近，位置略有偏移（手不會拿得剛好正中）
    ox = (frame_w - pw) // 2 + int(rng.integers(-frame_w // 40, frame_w // 40 + 1))
    oy = (frame_h - ph) // 2 + int(rng.integers(-frame_h // 40, frame_h // 40 + 1))
    ox = int(np.clip(ox, 0, frame_w - pw))
    oy = int(np.clip(oy, 0, frame_h - ph))

    # 紙比桌面亮一點，而且有一圈很淡的陰影 —— 邊界不會是完美的階梯
    canvas[oy:oy + ph, ox:ox + pw] = page
    cv2.rectangle(canvas, (ox - 2, oy - 2), (ox + pw + 1, oy + ph + 1),
                  (70, 70, 70), 3)
    canvas = cv2.GaussianBlur(canvas, (0, 0), 1.2)

    # --- 透視 + 旋轉，一次算完 ---
    h, w = canvas.shape[:2]
    jitter = cfg["warp"] * min(h, w)
    src_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst_pts = src_pts + rng.uniform(-jitter, jitter, (4, 2)).astype(np.float32)
    warped = cv2.warpPerspective(
        canvas, cv2.getPerspectiveTransform(src_pts, dst_pts), (w, h),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    angle = float(rng.uniform(-cfg["rotate_deg"], cfg["rotate_deg"]))
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    warped = cv2.warpAffine(warped, rot, (w, h), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REPLICATE)

    # --- 打光 ---
    mask = _lighting(warped.shape[:2], cfg["light_drop"], rng)
    out = warped.astype(np.float32) * mask[:, :, None]

    # --- 對焦（相對於行距，所以跟解析度無關）---
    sigma = cfg["blur_sigma"] * interline * scale
    if sigma > 0.05:
        out = cv2.GaussianBlur(out, (0, 0), sigma)

    # --- 感光顆粒 ---
    out += rng.normal(0, cfg["noise"], out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)

    # --- 相機解析度 + JPEG ---
    long_edge = max(out.shape[:2])
    if long_edge != cfg["long_edge"]:
        f = cfg["long_edge"] / long_edge
        out = cv2.resize(out, None, fx=f, fy=f,
                         interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    _imwrite(dst, out, cfg["jpeg"])
    cfg["actual_rotate"] = round(angle, 2)
    cfg["size"] = f"{out.shape[1]}x{out.shape[0]}"
    return cfg


def main():
    parser = argparse.ArgumentParser(description="把乾淨的樂譜圖模擬成手機拍的照片")
    parser.add_argument("src", help="乾淨的圖檔")
    parser.add_argument("dst", help="輸出的 .jpg")
    parser.add_argument("--preset", default="normal", choices=sorted(PRESETS))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    cfg = simulate(args.src, args.dst, args.preset, args.seed)
    print(f"{Path(args.dst).name}　{args.preset}　{cfg['size']}　"
          f"旋轉 {cfg['actual_rotate']}°　紙佔畫面 {cfg['page_frac']:.0%}")


if __name__ == "__main__":
    main()
