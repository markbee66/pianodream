using System.Collections.Generic;
using System.IO;
using PianoUI;
using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 選曲畫面。
    ///
    /// 沒有這個的話，換曲子要停掉遊戲、去 Inspector 打檔名、再按 Play，
    /// 每加一首新譜都要來一次。
    ///
    /// **每一項都可以用滑鼠點**，不是只能用鍵盤 —— Unity 的 Game 視窗沒有被點過
    /// 就沒有鍵盤焦點，只做鍵盤操作的話會出現「畫面在那裡但按什麼都沒反應」的死結。
    ///
    /// 譜面是**執行時**從硬碟掃出來的，所以在 Python 那邊做完新譜之後，
    /// 按「重新掃描」就會出現，不用重開 Unity。
    /// </summary>
    [RequireComponent(typeof(RhythmGameController))]
    public class SongSelectUI : MonoBehaviour
    {
        [Header("按鍵（滑鼠也可以，這些只是快捷鍵）")]
        public KeyCode upKey = KeyCode.UpArrow;
        public KeyCode downKey = KeyCode.DownArrow;
        public KeyCode playKey = KeyCode.Return;
        public KeyCode backKey = KeyCode.Escape;
        public KeyCode refreshKey = KeyCode.F5;

        [Tooltip("一次顯示幾首")]
        public int visibleRows = 9;

        public bool IsOpen { get; private set; }

        private RhythmGameController _game;
        private PianoKeyboardUI _keyboard;

        private RectTransform _panel;
        private Text _header, _hint, _empty, _status;
        private readonly List<Button> _rowButtons = new List<Button>();
        private readonly List<Text> _rowTexts = new List<Text>();
        private readonly List<string> _paths = new List<string>();
        private readonly List<string> _labels = new List<string>();
        private int _index;
        private int _scroll;
        private bool _built;
        // 這個面板是在哪一個 frame 被打開的。`Input.GetKeyDown()` 在整個 frame
        // 內都回傳 true，所以剛打開的那一 frame 不能吃按鍵 —— 否則把這個面板
        // 打開的那一次 Esc 會馬上又被這裡讀到一次。見 Open() 的註解。
        private int _openedFrame = -1;
        private Sprite _rowSprite;

        private static readonly Color RowNormal = new Color(1f, 1f, 1f, 0.05f);
        private static readonly Color RowSelected = new Color(1f, 0.85f, 0.35f, 0.16f);
        private static readonly Color TextDim = new Color(1f, 1f, 1f, 0.62f);
        private static readonly Color TextBright = new Color(1f, 0.87f, 0.42f, 1f);

        private void Awake()
        {
            _game = GetComponent<RhythmGameController>();
            _keyboard = GetComponent<PianoKeyboardUI>();
        }

        private void Update()
        {
            if (!Application.isPlaying) return;
            if (!_built && !Build()) return;

            if (IsOpen) { HandleSelectKeys(); return; }

            // 彈到一半的 Esc 歸暫停選單管（先暫停，不要一按就丟掉這一次的進度）。
            // 沒有掛暫停選單時才由這裡直接回選曲。
            var pause = GetComponent<PauseMenu>();
            if (pause != null) return;
            if (Input.GetKeyDown(backKey)) Open();
        }

        // ---------- 建立 ----------

        private bool Build()
        {
            var root = _keyboard != null ? _keyboard.KeyboardRoot : null;
            if (root == null || root.parent == null) return false;   // 鍵盤還沒好

            _rowSprite = UIShapes.RoundedRect(6, true, true, "SongSelect_Row");

            var go = new GameObject("SongSelect", typeof(RectTransform), typeof(Image));
            _panel = (RectTransform)go.transform;
            _panel.SetParent(root.parent, false);
            _panel.anchorMin = Vector2.zero;
            _panel.anchorMax = Vector2.one;
            _panel.offsetMin = Vector2.zero;
            _panel.offsetMax = Vector2.zero;
            // 完全不透明。留透明度的話白色琴鍵會透上來，底下的文字變成灰底灰字。
            var bg = go.GetComponent<Image>();
            bg.color = new Color(0.06f, 0.07f, 0.10f, 1f);
            bg.raycastTarget = true;

            _header = MakeText("Header", new Vector2(0.5f, 1f), new Vector2(0f, -46f),
                               32, TextAnchor.UpperCenter, new Color(1f, 1f, 1f, 0.95f));
            _empty = MakeText("Empty", new Vector2(0.5f, 0.5f), new Vector2(0f, 40f),
                              18, TextAnchor.MiddleCenter, new Color(1f, 0.78f, 0.55f, 1f));
            _status = MakeText("Status", new Vector2(0.5f, 0f), new Vector2(0f, 168f),
                               17, TextAnchor.LowerCenter, new Color(0.6f, 0.9f, 0.7f, 1f));
            _hint = MakeText("Hint", new Vector2(0.5f, 0f), new Vector2(0f, 46f),
                             16, TextAnchor.LowerCenter, new Color(1f, 1f, 1f, 0.45f));

            for (int i = 0; i < Mathf.Max(1, visibleRows); i++) MakeRow(i);
            BuildActionBar();

            _built = true;
            Open();
            return true;
        }

        private void MakeRow(int i)
        {
            var go = new GameObject($"Row{i}", typeof(RectTransform), typeof(Image), typeof(Button));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 1f);
            rt.pivot = new Vector2(0.5f, 1f);
            rt.sizeDelta = new Vector2(920f, 40f);
            rt.anchoredPosition = new Vector2(0f, -110f - i * 44f);

            var img = go.GetComponent<Image>();
            img.sprite = _rowSprite;
            img.type = Image.Type.Sliced;
            img.color = RowNormal;

            var text = MakeText($"Row{i}Label", new Vector2(0.5f, 0.5f), Vector2.zero,
                                20, TextAnchor.MiddleCenter, TextDim);
            var trt = (RectTransform)text.transform;
            trt.SetParent(rt, false);
            trt.anchorMin = Vector2.zero;
            trt.anchorMax = Vector2.one;
            trt.offsetMin = trt.offsetMax = Vector2.zero;

            int captured = i;
            var button = go.GetComponent<Button>();
            button.targetGraphic = img;
            // 點一下就選中並直接開始。多做一次「先選再按開始」只會多一步。
            button.onClick.AddListener(() => ClickRow(captured));

            _rowButtons.Add(button);
            _rowTexts.Add(text);
        }

        private void BuildActionBar()
        {
            MakeButton("加入新樂譜（拍照 / 記譜）", new Vector2(-170f, 108f), 250f,
                       new Color(0.22f, 0.45f, 0.85f, 1f), OpenScoreInput);
            MakeButton("重新掃描", new Vector2(110f, 108f), 160f,
                       new Color(1f, 1f, 1f, 0.12f), Refresh);
        }

        private Button MakeButton(string label, Vector2 pos, float width, Color color,
                                  UnityEngine.Events.UnityAction onClick)
        {
            var go = new GameObject($"Btn_{label}", typeof(RectTransform), typeof(Image), typeof(Button));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0f);
            rt.pivot = new Vector2(0.5f, 0f);
            rt.sizeDelta = new Vector2(width, 42f);
            rt.anchoredPosition = pos;

            var img = go.GetComponent<Image>();
            img.sprite = _rowSprite;
            img.type = Image.Type.Sliced;
            img.color = color;

            var text = MakeText($"{label}Label", new Vector2(0.5f, 0.5f), Vector2.zero,
                                17, TextAnchor.MiddleCenter, new Color(1f, 1f, 1f, 0.95f));
            text.text = label;   // MakeText 只負責樣式，內容要自己填（曲目列是 RenderList 補的）
            var trt = (RectTransform)text.transform;
            trt.SetParent(rt, false);
            trt.anchorMin = Vector2.zero;
            trt.anchorMax = Vector2.one;
            trt.offsetMin = trt.offsetMax = Vector2.zero;

            var button = go.GetComponent<Button>();
            button.targetGraphic = img;
            button.onClick.AddListener(onClick);
            return button;
        }

        private Text MakeText(string name, Vector2 anchor, Vector2 offset,
                              int size, TextAnchor align, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = anchor;
            rt.pivot = anchor;
            rt.sizeDelta = new Vector2(1100f, 44f);
            rt.anchoredPosition = offset;

            var text = go.GetComponent<Text>();
            text.font = UIShapes.BuiltinFont();
            text.fontSize = size;
            text.alignment = align;
            text.color = color;
            text.raycastTarget = false;   // 別擋到底下按鈕的點擊
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        // ---------- 掃描譜面 ----------

        /// <summary>辨識可靠度的提示。完全可靠就不顯示，免得每一列都多一段字。
        ///
        /// 「可靠」= 那一小節的拍數對得上拍號、而且真的有音符。對不上就表示
        /// 音符時值被認錯了，玩家看到的不是譜上寫的東西 —— 與其讓他以為是
        /// 自己彈錯，不如先講清楚。實測〈蕭邦 冬風練習曲〉只有 10/88 可靠。
        /// </summary>
        private static string ReliabilityTag(ChartData chart)
        {
            int total = chart.measures != null ? chart.measures.Length : 0;
            if (total <= 0) return "";
            int ok = chart.reliable_measures;
            if (ok >= total) return "";        // 全部可靠就不用講

            int percent = Mathf.RoundToInt(100f * ok / total);
            return $"　⚠ 辨識可靠 {ok}/{total}（{percent}%）";
        }

        /// <summary>重新掃描譜面資料夾。在 Python 那邊產出新譜之後按這個就會出現。</summary>
        public void Refresh()
        {
            int before = _paths.Count;
            _paths.Clear();
            _labels.Clear();

            foreach (var path in ChartLoader.ListCharts(_game.chartFolder))
            {
                var chart = ChartLoader.Load(path);
                if (chart == null) continue;
                _paths.Add(path);
                // 任何一份譜面都帶著專案根目錄，記下來給「加入新樂譜」用 ——
                // 那個按鈕不需要有歌正在播就該能按
                if (!string.IsNullOrEmpty(chart.root)) _cachedRoot = chart.root;
                _labels.Add($"{chart.title}　難度 {chart.level}（{chart.level_name}）" +
                            $"　{chart.note_count} 音　{chart.duration_sec:0.0} 秒" +
                            ReliabilityTag(chart));
            }

            // 記住原本選的那首，重新掃描後盡量停在同一首
            int keep = _paths.IndexOf(_game.ChartPath);
            _index = keep >= 0 ? keep : Mathf.Clamp(_index, 0, Mathf.Max(0, _paths.Count - 1));

            if (_status != null && before != _paths.Count && before > 0)
                _status.text = _paths.Count > before
                    ? $"找到 {_paths.Count - before} 首新的曲子"
                    : $"少了 {before - _paths.Count} 首";
            RenderList();
        }

        private void RenderList()
        {
            bool has = _paths.Count > 0;
            _empty.gameObject.SetActive(!has);
            if (!has)
            {
                _empty.text =
                    "還沒有任何譜面。\n\n" +
                    "按下面的「加入新樂譜」，會打開樂譜輸入的網頁介面，\n" +
                    "在那裡上傳拍好的樂譜照片或記譜檔，辨識完再回來按「重新掃描」。\n\n" +
                    $"譜面會放在：\n{ChartLoader.ResolveFolder(_game.chartFolder)}";
            }

            _scroll = Mathf.Clamp(_scroll, Mathf.Max(0, _index - _rowButtons.Count + 1),
                                  Mathf.Max(0, _index));
            _scroll = Mathf.Clamp(_scroll, 0, Mathf.Max(0, _paths.Count - _rowButtons.Count));

            for (int i = 0; i < _rowButtons.Count; i++)
            {
                int item = _scroll + i;
                bool exists = item < _paths.Count;
                _rowButtons[i].gameObject.SetActive(exists);
                if (!exists) continue;

                bool selected = item == _index;
                _rowTexts[i].text = _labels[item];
                _rowTexts[i].color = selected ? TextBright : TextDim;
                _rowTexts[i].fontStyle = selected ? FontStyle.Bold : FontStyle.Normal;
                _rowButtons[i].image.color = selected ? RowSelected : RowNormal;
            }

            // 一次只顯示 visibleRows 首，畫面上原本沒有任何「還有更多」的線索，
            // 看起來就像譜面只有這幾首。標題直接寫出範圍與總數。
            if (has && _paths.Count > _rowButtons.Count)
            {
                int first = _scroll + 1;
                int last = Mathf.Min(_scroll + _rowButtons.Count, _paths.Count);
                string more = _scroll + _rowButtons.Count < _paths.Count ? "　▼ 下面還有" : "";
                string up = _scroll > 0 ? "▲ 上面還有　" : "";
                _header.text = $"選曲　{up}{first}–{last} / 共 {_paths.Count} 首{more}";
            }
            else if (has)
            {
                _header.text = $"選曲　共 {_paths.Count} 首";
            }
        }

        // ---------- 操作 ----------

        private void HandleSelectKeys()
        {
            // 剛被打開的那一 frame 不吃按鍵。檢討畫面是用 Esc 關閉的，而它關閉時
            // 會把這個面板打開 —— 同一個 frame 裡 GetKeyDown(Escape) 仍然是 true，
            // 於是下面的 backKey 分支立刻又觸發，整首歌被重打一次。
            if (Time.frameCount == _openedFrame) return;

            if (Input.GetKeyDown(refreshKey)) { Refresh(); return; }

            if (_paths.Count > 0)
            {
                // 滾輪捲動清單。只綁方向鍵的話，用滑鼠點選的人完全不會發現
                // 畫面外還有曲子 —— 22 份譜面只看得到前 9 首。
                float wheel = Input.mouseScrollDelta.y;
                if (Mathf.Abs(wheel) > 0.01f) Scroll(wheel > 0f ? -1 : 1);

                if (Input.GetKeyDown(upKey)) Move(-1);
                if (Input.GetKeyDown(downKey)) Move(1);
                // 數字鍵盤的 Enter 是另一個鍵碼，兩個都收才不會「按了沒反應」
                if (Input.GetKeyDown(playKey) || Input.GetKeyDown(KeyCode.KeypadEnter))
                    PlaySelected();
            }

            if (Input.GetKeyDown(backKey) && _game.Chart != null) Close(resume: true);
        }

        private void ClickRow(int row)
        {
            int item = _scroll + row;
            if (item < 0 || item >= _paths.Count) return;
            _index = item;
            RenderList();
            PlaySelected();
        }

        /// <summary>只捲動畫面，不改變選中的是哪一首。給滾輪用。</summary>
        public void Scroll(int delta)
        {
            if (_paths.Count <= _rowButtons.Count) return;     // 全部塞得下就不用捲
            int limit = Mathf.Max(0, _paths.Count - _rowButtons.Count);
            int wanted = Mathf.Clamp(_scroll + delta, 0, limit);
            if (wanted == _scroll) return;
            // RenderList() 會把 _scroll 夾回「選中的那一首要看得到」的範圍，
            // 所以捲動時把選擇一起帶著走，否則滾輪會像沒有反應。
            _index = Mathf.Clamp(_index + (wanted - _scroll), 0, _paths.Count - 1);
            _scroll = wanted;
            RenderList();
        }

        /// <summary>上下移動選擇。公開是為了讓別的輸入來源也能驅動選單。</summary>
        public void Move(int delta)
        {
            if (_paths.Count == 0) return;
            _index = Mathf.Clamp(_index + delta, 0, _paths.Count - 1);
            RenderList();
        }

        /// <summary>開始目前選中的曲子。</summary>
        public bool PlaySelected()
        {
            if (_index < 0 || _index >= _paths.Count) return false;
            if (!_game.PlayChart(_paths[_index])) return false;
            Close(resume: false);
            return true;
        }

        public string SelectedPath =>
            _index >= 0 && _index < _paths.Count ? _paths[_index] : "";

        public int Count => _paths.Count;

        // ---------- 加入新樂譜 ----------

        /// <summary>
        /// 打開樂譜輸入的網頁介面。
        ///
        /// 辨識樂譜是 Python 那邊做的（homr OMR + 記譜解析），Unity 不重做一套；
        /// 這個按鈕負責把那個工具叫起來，辨識完回來按「重新掃描」就接上了。
        /// </summary>
        public void OpenScoreInput()
        {
            // 用目前載入的譜面帶出真實根目錄；沒載入譜面時退回推算
            string root = ProjectRoot(_game != null ? _game.Chart : null);
            string bat = Path.Combine(root, "加樂譜.bat");

            try
            {
                if (File.Exists(bat))
                {
                    // **不要用 UseShellExecute = true 直接指向 .bat。**
                    //
                    // 那條路走的是 ShellExecuteEx，會經過 Windows 的
                    // Attachment Execution Service 與 SmartScreen —— 只要那個 .bat
                    // 帶著「從網路來的」標記（Zone.Identifier），或是放在被判定為
                    // 非本機區域的位置，每次按這個按鈕都會先跳一個
                    // 「開啟檔案 - 安全性警告 / 發行者無法驗證」要人按「執行」。
                    //
                    // 改成叫 cmd.exe 去跑那個 .bat：cmd 讀批次檔是自己解譯，
                    // 不經過 ShellExecute，那兩個檢查都不會被觸發。
                    // cmd.exe 是系統目錄裡有簽章的檔案，本身也不會被攔。
                    //
                    // Unity 編輯器本身沒有主控台，但 CreateProcess 在父行程沒有
                    // 主控台時會自動配一個給子行程，所以視窗照樣看得到 ——
                    // 那個視窗就是伺服器，關掉它等於停掉服務，跟雙擊 .bat 一樣。
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        Arguments = "/c \"" + bat + "\"",
                        WorkingDirectory = root,
                        UseShellExecute = false,
                    });
                    _status.text = "已開啟樂譜輸入介面（看瀏覽器）。\n" +
                                   "辨識完回到這裡按「重新掃描」，新曲子就會出現。";
                }
                else
                {
                    _status.text = $"找不到 加樂譜.bat\n應該在：{root}\n" +
                                   "請手動執行：run.py web";
                    Debug.LogWarning($"[音遊] 找不到 {bat}");
                }
            }
            catch (System.Exception e)
            {
                _status.text = "打不開樂譜輸入介面，請手動雙擊專案資料夾裡的 加樂譜.bat";
                Debug.LogError($"[音遊] 啟動樂譜輸入失敗：{e.Message}");
            }
        }

        /// <summary>專案根目錄。Application.dataPath 是 音遊/Assets，往上兩層就是。</summary>
        /// <summary>專案根目錄（放著 加樂譜.bat、data/、run.py 的那一層）。
        ///
        /// **不能只靠 `Application.dataPath` 往上推。** 那假設了「音遊專案就放在
        /// 專案根目錄底下」，但實際佈局是 Unity 專案在 C:/UnityProjects/音遊GD、
        /// Assets 用 junction 接回 Google Drive 上的原始碼 —— 往上兩層只會得到
        /// C:/UnityProjects，既找不到 加樂譜.bat 也找不到檢討要用的樂譜照片。
        /// C# 在 .NET Standard 2.1 下沒有 ResolveLinkTarget 可以穿透 junction。
        ///
        /// 所以優先用譜面 JSON 裡的 `root` —— 那是 Python 產譜面時寫進去的
        /// 絕對路徑，它那一端本來就知道真實位置。沒有載入譜面（或舊格式的
        /// 譜面沒有這個欄位）才退回原本的推算。
        /// </summary>
        public static string ProjectRoot(ChartData chart = null)
        {
            string fromChart = chart != null ? chart.root : null;
            if (!string.IsNullOrEmpty(fromChart) && Directory.Exists(fromChart))
                return fromChart;
            // 沒有正在玩的譜面時，用掃描清單時記下來的那一個。
            // **這一條不能省**：`Chart` 是「目前載入的譜面」，第一次用或譜面
            // 資料夾是空的時候它是 null —— 而那正是最想按「加入新樂譜」的時候。
            if (!string.IsNullOrEmpty(_cachedRoot) && Directory.Exists(_cachedRoot))
                return _cachedRoot;

            // 連一份譜面都沒有時，讀譜面資料夾裡的標記檔（Python 產譜面時寫的）
            string marker = Path.Combine(ChartLoader.ResolveFolder(null), ".project-root");
            if (File.Exists(marker))
            {
                string root = File.ReadAllText(marker).Trim();
                if (!string.IsNullOrEmpty(root) && Directory.Exists(root))
                {
                    _cachedRoot = root;
                    return root;
                }
            }
            return Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        }

        /// <summary>掃描譜面時記下來的專案根目錄。任何一份譜面都帶著它。</summary>
        private static string _cachedRoot;

        // ---------- 開關 ----------

        public void Open()
        {
            IsOpen = true;
            // 記下是哪一個 frame 開的。`Input.GetKeyDown()` 在**整個 frame 內**
            // 都回傳 true，而檢討畫面與這裡是兩個獨立的 MonoBehaviour ——
            // 曲子結束後按一次 Esc，ReviewScreen 先關閉並呼叫 Open()，
            // 接著同一個 frame 裡這裡的 Update 又看到同一個 Esc，
            // 於是 Close(resume:true) -> BeginSong()，整首**重打一次**。
            _openedFrame = Time.frameCount;
            _panel.gameObject.SetActive(true);
            _game.Stop();
            _header.text = "選曲";
            _hint.text = "用滑鼠點一首就開始　"
                         + $"（滾輪可以捲、[↑↓] 選、[{playKey}] 開始）"
                         + (_game.Chart != null ? $"　[{backKey}] 回到遊戲" : "");
            if (_status != null) _status.text = "";
            Refresh();
        }

        public void Close(bool resume)
        {
            IsOpen = false;
            _panel.gameObject.SetActive(false);
            if (!resume || _game.Chart == null || _game.IsPlaying) return;

            // **繼續，不是重來。** 以前這裡直接呼叫 BeginSong()，而
            // RhythmGameController 的 Restart() 就是 BeginSong() —— 它會歸零
            // Score / Combo / MaxCombo / Counts / PerNote 並重建整條音符高速公路。
            // Update() 的註解寫著「不要一按就丟掉這一次的進度」，程式卻正好相反：
            // 從選曲畫面按 Esc 返回，這一次彈的成績整個消失。
            //
            // 有進度就只是被暫停，用 TogglePause() 放開；真的還沒開始才 BeginSong()。
            if (_game.SongTime > 0f) _game.TogglePause();
            else _game.BeginSong();
        }
    }
}
