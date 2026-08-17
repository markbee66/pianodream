using UnityEngine;

namespace PianoUI
{
    /// <summary>
    /// 程式產生的 UI 圖形（圓角矩形、圓形）。
    /// 這樣專案不需要匯入任何美術資源就能跑起來。
    /// </summary>
    public static class UIShapes
    {
        private static Sprite _whiteKeySprite;
        private static Sprite _blackKeySprite;
        private static Sprite _circleSprite;
        private static Font _font;
        private static bool _fontLookupDone;

        /// <summary>白鍵：只有下緣圓角（下緣是琴鍵的「前端」）。</summary>
        public static Sprite WhiteKey()
        {
            if (_whiteKeySprite == null) _whiteKeySprite = RoundedRect(10, false, true, "PianoUI_WhiteKey");
            return _whiteKeySprite;
        }

        /// <summary>黑鍵：下緣圓角，半徑小一點。</summary>
        public static Sprite BlackKey()
        {
            if (_blackKeySprite == null) _blackKeySprite = RoundedRect(7, false, true, "PianoUI_BlackKey");
            return _blackKeySprite;
        }

        public static Sprite Circle()
        {
            if (_circleSprite == null) _circleSprite = MakeCircle(48, "PianoUI_Circle");
            return _circleSprite;
        }

        /// <summary>
        /// 產生九宮格切片（9-slice）的圓角矩形，拉伸時圓角不會變形。
        /// </summary>
        public static Sprite RoundedRect(int radius, bool roundTop, bool roundBottom, string name)
        {
            radius = Mathf.Max(1, radius);
            // 中間留 2px 給九宮格的中央格，border 兩邊加起來不能等於或超過 size
            int size = radius * 2 + 8;
            int border = radius + 3;

            var tex = new Texture2D(size, size, TextureFormat.RGBA32, false)
            {
                name = name,
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear
            };

            var pixels = new Color32[size * size];
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    float a = CornerCoverage(x + 0.5f, y + 0.5f, size, radius, roundTop, roundBottom);
                    pixels[y * size + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255f));
                }
            }
            tex.SetPixels32(pixels);
            tex.Apply(false, false);

            var sprite = Sprite.Create(
                tex,
                new Rect(0, 0, size, size),
                new Vector2(0.5f, 0.5f),
                100f,
                0,
                SpriteMeshType.FullRect,
                new Vector4(border, border, border, border));
            sprite.name = name;
            return sprite;
        }

        private static float CornerCoverage(float fx, float fy, int size, int r, bool roundTop, bool roundBottom)
        {
            float ny = 0f;
            if (roundBottom && fy < r) ny = r - fy;
            else if (roundTop && fy > size - r) ny = fy - (size - r);
            if (ny <= 0f) return 1f;

            float nx = 0f;
            if (fx < r) nx = r - fx;
            else if (fx > size - r) nx = fx - (size - r);
            if (nx <= 0f) return 1f;

            // 距離圓心多遠 → 邊緣做一點抗鋸齒
            float dist = Mathf.Sqrt(nx * nx + ny * ny);
            return Mathf.Clamp01(r - dist + 0.5f);
        }

        public static Sprite MakeCircle(int diameter, string name)
        {
            diameter = Mathf.Max(4, diameter);
            var tex = new Texture2D(diameter, diameter, TextureFormat.RGBA32, false)
            {
                name = name,
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear
            };

            float c = diameter * 0.5f;
            float radius = c - 0.5f;
            var pixels = new Color32[diameter * diameter];
            for (int y = 0; y < diameter; y++)
            {
                for (int x = 0; x < diameter; x++)
                {
                    float dx = x + 0.5f - c;
                    float dy = y + 0.5f - c;
                    float a = Mathf.Clamp01(radius - Mathf.Sqrt(dx * dx + dy * dy) + 0.5f);
                    pixels[y * diameter + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255f));
                }
            }
            tex.SetPixels32(pixels);
            tex.Apply(false, false);

            var sprite = Sprite.Create(tex, new Rect(0, 0, diameter, diameter), new Vector2(0.5f, 0.5f), 100f);
            sprite.name = name;
            return sprite;
        }

        /// <summary>
        /// 取 Unity 內建字型。不同版本名稱不同，取不到就回 null（標籤會自動略過，不會壞掉）。
        /// </summary>
        public static Font BuiltinFont()
        {
            if (_fontLookupDone) return _font;
            _fontLookupDone = true;

            string[] candidates = { "LegacyRuntime.ttf", "Arial.ttf" };
            foreach (var n in candidates)
            {
                try
                {
                    _font = Resources.GetBuiltinResource<Font>(n);
                }
                catch
                {
                    _font = null;
                }
                if (_font != null) break;
            }
            return _font;
        }
    }
}
