using System.Collections.Generic;
using System.IO;
using PianoUI;
using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 彈完之後的練習檢討。
    ///
    /// 分數本身不會讓人變強 —— 要知道**是哪幾小節沒彈好**，而且看得到位置。
    /// 所以這裡把每一顆音的判定結果攤回小節上，找出最弱的幾段，
    /// 直接在**你自己拍的那張樂譜照片**上圈紅，然後可以只重練那一段。
    ///
    /// 小節在照片上的位置是 Python 那邊算好寫進 chart JSON 的
    /// （src/score_input/layout.py 偵測小節線）；文字記譜沒有照片，
    /// 那就只顯示文字檢討。
    /// </summary>
    [RequireComponent(typeof(RhythmGameController))]
    public class ReviewScreen : MonoBehaviour
    {
        [Tooltip("一小節要有幾成以上的音沒打好才算「弱」")]
        [UnityEngine.Range(0.05f, 1f)] public float weakRatio = 0.25f;

        [Tooltip("相鄰的弱小節間隔幾小節以內就併成同一段")]
        public int mergeGap = 2;

        [Tooltip("最多列出幾段")]
        public int maxSections = 3;

        public bool IsOpen { get; private set; }

        private RhythmGameController _game;
        private PianoKeyboardUI _keyboard;
        private SongSelectUI _select;

        private RectTransform _panel;
        private Text _title, _summary, _detail, _hint;
        private RawImage _sheet;
        private Texture2D _sheetTexture;
        private readonly List<Section> _sections = new List<Section>();
        private int _shown = -1;
        private bool _built;
        private bool _reported;
        // 這個面板是在哪一個 frame 被打開的 —— 剛打開的那一 frame 不吃按鍵，
        // 理由見 Update() 裡的註解。
        private int _openedFrame = -1;

        private struct Section
        {
            public int Start, End, Bad, Total;
            public string Label => Start == End ? $"第 {Start} 小節" : $"第 {Start}–{End} 小節";
        }

        private void Awake()
        {
            _game = GetComponent<RhythmGameController>();
            _keyboard = GetComponent<PianoKeyboardUI>();
            _select = GetComponent<SongSelectUI>();
        }

        private void Update()
        {
            if (!Application.isPlaying) return;
            if (!_built && !Build()) return;

            // 選曲畫面開著的時候絕對不能跳檢討 —— 那時遊戲是停著的，
            // 條件會意外成立，使用者會看到「選了歌卻跳出上一輪的檢討」。
            bool selecting = _select != null && _select.IsOpen;

            // 曲子跑完、而且真的有判定資料，就自動跳檢討。
            // 用 RangeEnd 不是 duration_sec —— 只練某一段時曲子在那一段的結尾就停了，
            // 拿整首的長度來比會永遠不成立，檢討就跳不出來。
            if (!_reported && !selecting && _game.Chart != null && !_game.IsPlaying
                && _game.TotalJudged > 0
                && _game.SongTime >= _game.RangeEnd)
            {
                _reported = true;
                Open();
            }

            if (!IsOpen) return;
            // 剛打開的那一 frame 不吃按鍵：`Input.GetKeyDown()` 在整個 frame 內
            // 都是 true，上面才剛 Open()，同一輪就把它關掉的話使用者根本看不到。
            if (Time.frameCount == _openedFrame) return;

            if (Input.GetKeyDown(KeyCode.Escape)) Close();
            if (Input.GetKeyDown(KeyCode.Tab) && _sections.Count > 1)
                ShowSection((_shown + 1) % _sections.Count);
        }

        /// <summary>開始新一輪時要重置，否則第二次彈完不會再跳檢討。</summary>
        public void Reset()
        {
            _reported = false;
            // 這是「歌要開始了」才會被呼叫的，同樣不能順手把選曲畫面叫出來
            if (IsOpen) Close(toSelect: false);
        }

        // ---------- 分析 ----------

        private void Analyse()
        {
            _sections.Clear();
            var chart = _game.Chart;
            if (chart == null) return;

            // 每個小節有幾顆音、其中幾顆沒打好
            var total = new Dictionary<int, int>();
            var bad = new Dictionary<int, int>();
            foreach (var pair in _game.PerNote)
            {
                int m = pair.Key.measure;
                total.TryGetValue(m, out int t);
                total[m] = t + 1;
                if (pair.Value == Judgement.Miss || pair.Value == Judgement.Good)
                {
                    bad.TryGetValue(m, out int b);
                    bad[m] = b + 1;
                }
            }

            var weak = new List<int>();
            foreach (var pair in total)
            {
                bad.TryGetValue(pair.Key, out int b);
                if (pair.Value > 0 && (float)b / pair.Value >= weakRatio) weak.Add(pair.Key);
            }
            weak.Sort();
            if (weak.Count == 0) return;

            // 相鄰的弱小節併成一段 —— 樂句是連著的，一小節一小節分開練沒意義
            var current = new Section { Start = weak[0], End = weak[0] };
            for (int i = 1; i < weak.Count; i++)
            {
                if (weak[i] - current.End <= mergeGap + 1) current.End = weak[i];
                else { _sections.Add(Fill(current, total, bad)); current = new Section { Start = weak[i], End = weak[i] }; }
            }
            _sections.Add(Fill(current, total, bad));

            _sections.Sort((a, b) => b.Bad.CompareTo(a.Bad));
            if (_sections.Count > maxSections) _sections.RemoveRange(maxSections, _sections.Count - maxSections);
        }

        private static Section Fill(Section s, Dictionary<int, int> total, Dictionary<int, int> bad)
        {
            for (int m = s.Start; m <= s.End; m++)
            {
                total.TryGetValue(m, out int t);
                bad.TryGetValue(m, out int b);
                s.Total += t;
                s.Bad += b;
            }
            return s;
        }

        // ---------- 畫面 ----------

        private bool Build()
        {
            var root = _keyboard != null ? _keyboard.KeyboardRoot : null;
            if (root == null || root.parent == null) return false;

            var go = new GameObject("ReviewScreen", typeof(RectTransform), typeof(Image));
            _panel = (RectTransform)go.transform;
            _panel.SetParent(root.parent, false);
            _panel.anchorMin = Vector2.zero;
            _panel.anchorMax = Vector2.one;
            _panel.offsetMin = _panel.offsetMax = Vector2.zero;
            var bg = go.GetComponent<Image>();
            bg.color = new Color(0.06f, 0.07f, 0.10f, 1f);
            bg.raycastTarget = true;

            _title = MakeText("Title", new Vector2(0.5f, 1f), new Vector2(0f, -34f),
                              30, TextAnchor.UpperCenter, new Color(1f, 1f, 1f, 0.95f));
            _summary = MakeText("Summary", new Vector2(0.5f, 1f), new Vector2(0f, -78f),
                                19, TextAnchor.UpperCenter, new Color(1f, 0.87f, 0.42f, 1f));
            _detail = MakeText("Detail", new Vector2(0.5f, 1f), new Vector2(0f, -110f),
                               17, TextAnchor.UpperCenter, new Color(1f, 1f, 1f, 0.6f));
            _hint = MakeText("Hint", new Vector2(0.5f, 0f), new Vector2(0f, 22f),
                             16, TextAnchor.LowerCenter, new Color(1f, 1f, 1f, 0.45f));

            var sheetGo = new GameObject("Sheet", typeof(RectTransform), typeof(RawImage));
            var srt = (RectTransform)sheetGo.transform;
            srt.SetParent(_panel, false);
            srt.anchorMin = new Vector2(0.5f, 0f);
            srt.anchorMax = new Vector2(0.5f, 1f);
            srt.pivot = new Vector2(0.5f, 0.5f);
            srt.offsetMin = new Vector2(-460f, 140f);
            srt.offsetMax = new Vector2(460f, -150f);
            _sheet = sheetGo.GetComponent<RawImage>();
            _sheet.raycastTarget = false;

            _built = true;
            _panel.gameObject.SetActive(false);
            return true;
        }

        private Text MakeText(string name, Vector2 anchor, Vector2 offset,
                              int size, TextAnchor align, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = anchor;
            rt.pivot = anchor;
            rt.sizeDelta = new Vector2(1150f, 60f);
            rt.anchoredPosition = offset;

            var text = go.GetComponent<Text>();
            text.font = UIShapes.BuiltinFont();
            text.fontSize = size;
            text.alignment = align;
            text.color = color;
            text.raycastTarget = false;
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        // ---------- 開關 ----------

        public void Open()
        {
            if (!_built && !Build()) return;
            _openedFrame = Time.frameCount;
            Analyse();

            IsOpen = true;
            _panel.gameObject.SetActive(true);
            _title.text = $"{_game.Chart.title}　準確率 {_game.Accuracy * 100f:0.0}%"
                          + $"　最高連擊 {_game.MaxCombo}";

            if (_sections.Count == 0)
            {
                _summary.text = "這次沒有明顯要重練的段落。";
                _detail.text = "";
                _sheet.enabled = false;
                _hint.text = "[Esc] 回到選曲";
                return;
            }
            ShowSection(0);
        }

        private void ShowSection(int index)
        {
            _shown = Mathf.Clamp(index, 0, _sections.Count - 1);
            var section = _sections[_shown];

            _summary.text = $"建議重練：{section.Label}"
                            + $"（{section.Total} 音裡有 {section.Bad} 個沒打好）";
            _detail.text = _sections.Count > 1
                ? $"第 {_shown + 1} / {_sections.Count} 段"
                : "";

            bool drawn = DrawSheet(section);
            _sheet.enabled = drawn;
            if (!drawn)
                _detail.text += (_detail.text.Length > 0 ? "　" : "")
                                + "（這份譜不是照片來源，沒有圖可以圈）";

            _hint.text = (_sections.Count > 1 ? "[Tab] 看下一段　" : "")
                         + $"[R] 只重練{section.Label}　[Esc] 回到選曲";
        }

        private void Update_Replay()
        {
            // 由 RhythmGameHUD 轉呼叫，避免兩邊都吃 R 鍵
        }

        /// <summary>只重練目前顯示的那一段。</summary>
        public bool ReplayShownSection()
        {
            if (!IsOpen || _shown < 0 || _shown >= _sections.Count) return false;
            var section = _sections[_shown];
            Close(toSelect: false);      // 直接開始練，不要繞去選曲畫面
            _game.PlayRange(section.Start, section.End);
            return true;
        }

        /// <summary>
        /// 關掉檢討畫面。
        ///
        /// toSelect 一定要分開：關掉檢討 ≠ 回到選曲。綁在一起的話，
        /// 按「重練這一段」會先把選曲畫面叫出來（順帶把遊戲 Stop 掉），
        /// 才輪到 PlayRange 開始播 —— 玩家看到的就是跳回目錄。
        /// </summary>
        public void Close(bool toSelect = true)
        {
            IsOpen = false;
            if (_panel != null) _panel.gameObject.SetActive(false);
            if (toSelect && _select != null) _select.Open();
        }

        // ---------- 在譜上圈紅 ----------

        private bool DrawSheet(Section section)
        {
            var chart = _game.Chart;
            if (chart == null || !chart.HasPages) return false;

            // 找出這一段落在哪一頁（用起始小節決定）
            ChartPage page = null;
            foreach (var p in chart.pages)
            {
                if (p.measures == null) continue;
                foreach (var m in p.measures)
                    if (m.n >= section.Start && m.n <= section.End) { page = p; break; }
                if (page != null) break;
            }
            if (page == null) return false;

            var texture = LoadPage(page.image);
            if (texture == null) return false;

            var painted = Paint(texture, page, section);
            _sheet.texture = painted;
            FitSheet(painted);
            return true;
        }

        private Texture2D LoadPage(string relative)
        {
            string path = Path.Combine(SongSelectUI.ProjectRoot(_game != null ? _game.Chart : null), relative.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(path))
            {
                Debug.LogWarning($"[音遊] 找不到樂譜圖：{path}");
                return null;
            }
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            return tex.LoadImage(File.ReadAllBytes(path)) ? tex : null;
        }

        /// <summary>
        /// 只裁出有問題的那幾行，壓暗周圍、把該練的小節框紅。
        ///
        /// **一定要裁**：整頁 A4 是直式的，塞進 16:9 的畫面之後只剩中間一小條，
        /// 音符小到看不出是什麼 —— 檢討看不清楚就完全沒有用。
        /// 裁成「整頁寬 × 問題所在的那幾行」，剛好是寬扁的形狀，填滿畫面又讀得清楚。
        /// </summary>
        private Texture2D Paint(Texture2D source, ChartPage page, Section section)
        {
            int w = source.width, h = source.height;

            var boxes = new List<MeasureBox>();
            foreach (var m in page.measures)
                if (m.n >= section.Start && m.n <= section.End && m.IsValid) boxes.Add(m);
            if (boxes.Count == 0) return source;

            // 先算出所有目標小節在「貼圖座標」下的範圍
            // （圖檔原點在左上、Unity 貼圖原點在左下，y 要翻過來）
            var rects = new List<RectInt>();
            int bandLow = int.MaxValue, bandHigh = int.MinValue;
            foreach (var box in boxes)
            {
                float minX = float.MaxValue, maxX = float.MinValue;
                float minY = float.MaxValue, maxY = float.MinValue;
                for (int i = 0; i < 4; i++)
                {
                    var c = box.Corner(i);
                    minX = Mathf.Min(minX, c.x); maxX = Mathf.Max(maxX, c.x);
                    minY = Mathf.Min(minY, c.y); maxY = Mathf.Max(maxY, c.y);
                }
                var r = new RectInt(
                    Mathf.Clamp(Mathf.FloorToInt(minX), 0, w - 1),
                    Mathf.Clamp(Mathf.FloorToInt(h - 1 - maxY), 0, h - 1),
                    Mathf.Max(1, Mathf.CeilToInt(maxX - minX)),
                    Mathf.Max(1, Mathf.CeilToInt(maxY - minY)));
                rects.Add(r);
                bandLow = Mathf.Min(bandLow, r.yMin);
                bandHigh = Mathf.Max(bandHigh, r.yMax);
            }

            // 上下各留一個系統左右的空間，看得到前後文
            int pad = Mathf.Max(40, (bandHigh - bandLow) / 2);
            int cropY = Mathf.Clamp(bandLow - pad, 0, h - 1);
            int cropH = Mathf.Clamp(bandHigh + pad, 1, h) - cropY;

            var pixels = source.GetPixels(0, cropY, w, cropH);

            // 壓暗非目標區域
            var keep = new bool[w * cropH];
            foreach (var r in rects)
                for (int y = Mathf.Max(r.yMin, cropY); y < Mathf.Min(r.yMax, cropY + cropH); y++)
                    for (int x = r.xMin; x < Mathf.Min(r.xMax, w); x++)
                        keep[(y - cropY) * w + x] = true;

            for (int i = 0; i < pixels.Length; i++)
            {
                if (keep[i]) continue;
                var p = pixels[i];
                pixels[i] = new Color(p.r * 0.40f + 0.12f, p.g * 0.40f + 0.13f,
                                      p.b * 0.40f + 0.15f, p.a);
            }

            int thickness = Mathf.Max(3, w / 300);
            var red = new Color(0.92f, 0.24f, 0.24f, 1f);
            foreach (var r in rects)
                DrawRect(pixels, w, cropH,
                         r.xMin, r.yMin - cropY, r.xMax - 1, r.yMax - 1 - cropY,
                         thickness, red);

            var result = new Texture2D(w, cropH, TextureFormat.RGBA32, false);
            result.SetPixels(pixels);
            result.Apply(false, false);
            if (_sheetTexture != null) Destroy(_sheetTexture);
            _sheetTexture = result;
            return result;
        }

        private static void DrawRect(Color[] px, int w, int h,
                                     int x0, int y0, int x1, int y1, int t, Color c)
        {
            for (int k = 0; k < t; k++)
            {
                Line(px, w, h, x0, x1, y0 + k, true, c);
                Line(px, w, h, x0, x1, y1 - k, true, c);
                Line(px, w, h, y0, y1, x0 + k, false, c);
                Line(px, w, h, y0, y1, x1 - k, false, c);
            }
        }

        private static void Line(Color[] px, int w, int h, int a, int b, int fixedAt,
                                 bool horizontal, Color c)
        {
            if (horizontal)
            {
                if (fixedAt < 0 || fixedAt >= h) return;
                for (int x = Mathf.Max(0, a); x <= Mathf.Min(w - 1, b); x++) px[fixedAt * w + x] = c;
            }
            else
            {
                if (fixedAt < 0 || fixedAt >= w) return;
                for (int y = Mathf.Max(0, a); y <= Mathf.Min(h - 1, b); y++) px[y * w + fixedAt] = c;
            }
        }

        private void FitSheet(Texture2D texture)
        {
            // 維持原圖比例塞進可用範圍，不要拉伸變形
            var rt = (RectTransform)_sheet.transform;
            var parent = (RectTransform)_panel;
            float availW = parent.rect.width - 120f;
            float availH = parent.rect.height - 300f;
            if (availW <= 0f || availH <= 0f) return;

            float scale = Mathf.Min(availW / texture.width, availH / texture.height);
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(texture.width * scale, texture.height * scale);
            rt.anchoredPosition = new Vector2(0f, -20f);
        }
    }
}
