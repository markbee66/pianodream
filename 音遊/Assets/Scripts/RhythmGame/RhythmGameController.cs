using System.Collections.Generic;
using System.IO;
using PianoUI;
using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 落下式音遊的主控。掛在跟 PianoKeyboardUI 同一個物件上按 Play 就能玩。
    ///
    ///     data/charts/*.json  →  音符從上往下掉  →  落到 88 鍵鍵盤上  →  按鍵判定
    ///
    /// 譜面資料是 Python 那邊 run.py score build 產出來的，這裡直接讀檔，
    /// 不需要開伺服器。難度 1（只有右手）和難度 2（雙手）各是一個檔案。
    ///
    /// 判定線就是琴鍵的上緣 —— 音符底部碰到琴鍵頂端的那一刻，就是該按下去的時候。
    /// </summary>
    [RequireComponent(typeof(PianoKeyboardUI))]
    [DisallowMultipleComponent]
    public class RhythmGameController : MonoBehaviour
    {
        [Header("譜面")]
        [Tooltip("譜面資料夾。相對路徑是相對 Assets 資料夾算的。\n" +
                 "預設會指到專案根目錄的 data/charts，也就是 run.py score build 的輸出位置")]
        public string chartFolder = "../../data/charts";

        [Tooltip("要玩哪一首。留空就自動用資料夾裡的第一個檔案。\n" +
                 "填檔名即可（例如 小星星_lv1.json），也可以填完整路徑")]
        public string chartFile = "";

        [Header("玩法")]
        [Tooltip("音符從出現到落到判定線要幾秒。越小越難")]
        public float approachSeconds = 2.0f;

        [Tooltip("開始前的倒數秒數，讓第一顆音符有時間落下來")]
        public float leadInSeconds = 3.0f;

        [Tooltip("自動演奏：電腦自己按，用來確認譜面和畫面對不對")]
        public bool autoPlay = false;

        [Tooltip("按下琴鍵時發出鋼琴聲")]
        public bool playSound = true;

        [Header("判定寬容度（秒）")]
        public float perfectWindow = 0.05f;
        public float greatWindow = 0.10f;
        public float goodWindow = 0.16f;

        [Header("長音")]
        [Tooltip("多長的音要「按住」才算完整。比這個短的按一下就好")]
        public float holdThreshold = 0.35f;
        [Tooltip("提前這麼多秒放開仍然算按滿（手指要移到下一個音，本來就會提早一點）")]
        public float holdReleaseWindow = 0.15f;
        [Tooltip("按住比例低於這個就算沒接好，成績降一級")]
        [UnityEngine.Range(0.1f, 1f)] public float holdKeepRatio = 0.6f;

        [Header("外觀")]
        public Color rightHandColor = new Color(0.22f, 0.51f, 0.96f, 1f);
        public Color leftHandColor = new Color(0.94f, 0.55f, 0.12f, 1f);
        [Tooltip("黑鍵的音符要壓暗多少。88 鍵很寬，只靠橫向位置很難分辨該按白鍵還是黑鍵，" +
                 "用深淺區分才看得出來（Synthesia 之類的軟體也是這樣做）")]
        [UnityEngine.Range(0.3f, 1f)] public float blackKeyNoteShade = 0.55f;
        [Tooltip("音符寬度佔琴鍵寬的比例")]
        public float noteWidthRatio = 0.86f;
        [Tooltip("裝飾音要畫多窄（相對一般音符）。譜上時值是 0，畫一樣寬會看起來像正常音符")]
        [UnityEngine.Range(0.2f, 1f)] public float graceWidthRatio = 0.5f;
        [Tooltip("辨識不可靠的小節，音符要褪色多少。那些小節的音符時值認錯了，" +
                 "標出來玩家才不會以為是自己彈錯")]
        [UnityEngine.Range(0f, 1f)] public float unreliableFade = 0.65f;
        [Tooltip("落下區域的高度（像素）。也就是判定線往上多高開始看得到音符")]
        public float highwayHeight = 620f;
        [Tooltip("畫出判定線")]
        public bool showHitLine = true;

        // ---- 狀態 ----

        public bool IsPlaying { get; private set; }

        /// <summary>曲子已經跑完了（不是被暫停）。</summary>
        ///
        /// <remarks>
        /// **`IsPlaying == false` 分不出「暫停」和「彈完了」**，而這兩種狀態的
        /// Esc 該做完全相反的事：暫停時要打開暫停選單，彈完時要能離開去換一首。
        /// 少了這個旗標，`PauseMenu` 的 `if (_game.IsPlaying && ...)` 在曲子結束後
        /// 永遠不成立，玩家會卡在跑完的畫面上按不動任何鍵。
        /// </remarks>
        public bool IsFinished { get; private set; }

        public float SongTime { get; private set; }
        public int Score { get; private set; }
        public int Combo { get; private set; }
        public int MaxCombo { get; private set; }
        public ChartData Chart { get; private set; }
        public Judgement LastJudgement { get; private set; }
        public float LastJudgementTime { get; private set; }
        public string StatusMessage { get; private set; } = "";

        public readonly Dictionary<Judgement, int> Counts = new Dictionary<Judgement, int>();

        /// <summary>每一顆音符的判定結果。檢討要靠這個攤回小節上。</summary>
        public readonly Dictionary<ChartNote, Judgement> PerNote =
            new Dictionary<ChartNote, Judgement>();

        /// <summary>目前只練的小節範圍，(0,0) 表示整首。</summary>
        public (int Start, int End) Range { get; private set; }

        private PianoKeyboardUI _keyboard;
        private PianoAudio _audio;
        private RectTransform _highway;
        private Image _hitLine;
        private Sprite _noteSprite;

        private readonly List<FallingNote> _notes = new List<FallingNote>();
        private int _nextSpawn;          // 還沒生出來的第一顆
        private int _nextMiss;           // 還沒檢查過漏按的第一顆
        private int _nextAuto;           // 自動演奏下一顆要按的
        private readonly List<int> _autoHeld = new List<int>();
        private readonly List<float> _autoRelease = new List<float>();
        // 自動演奏按下時，這一次按鍵該用哪個時間去判定。NaN = 這是真人按的
        private float _autoPressTime = float.NaN;
        private float _autoReleaseTime = float.NaN;

        /// <summary>正在被按住、成績還沒定的長音。</summary>
        private struct Hold
        {
            public FallingNote Note;
            public Judgement Grade;      // 按下那一刻的成績，撐滿就是這個
            public float StartedAt;
            public float EndsAt;
        }

        private readonly List<Hold> _holds = new List<Hold>();

        /// <summary>延音踏板（同一個物件上有掛的話）。</summary>
        public SustainPedal Pedal { get; private set; }

        private ReviewScreen _review;

        private const float PixelsPerSecondMin = 40f;

        // ---------------------------------------------------------------

        private void Awake()
        {
            _keyboard = GetComponent<PianoKeyboardUI>();
            Pedal = GetComponent<SustainPedal>();
            _review = GetComponent<ReviewScreen>();
        }

        private void OnEnable()
        {
            if (!Application.isPlaying) return;   // 編輯模式只顯示鍵盤，不跑遊戲
            _keyboard.NotePressed += OnNotePressed;
            _keyboard.NoteReleased += OnNoteReleased;
            _keyboard.Built += OnKeyboardBuilt;
        }

        private void OnDisable()
        {
            if (_keyboard == null) return;
            _keyboard.NotePressed -= OnNotePressed;
            _keyboard.NoteReleased -= OnNoteReleased;
            _keyboard.Built -= OnKeyboardBuilt;
        }

        private void Start()
        {
            if (!Application.isPlaying) return;
            SetupAudio();
            LoadChart();
            // 有選曲畫面的話就交給它決定開哪一首，不要自己搶先開始
            if (Chart != null && GetComponent<SongSelectUI>() == null) BeginSong();
        }

        /// <summary>換一首曲子並立刻開始。選曲畫面用這個。</summary>
        public bool PlayChart(string path)
        {
            var chart = ChartLoader.Load(path);
            if (chart == null)
            {
                StatusMessage = $"這份譜面讀不進來：\n{path}";
                return false;
            }
            Chart = chart;
            ChartPath = path;
            StatusMessage = "";
            // 從選曲畫面選一首，意思一定是「從頭玩整首」。
            // 不清掉的話，之前按過「重練第 20–25 小節」留下的範圍會沿用到新曲子，
            // 變成一選歌就只播那幾小節、幾秒就結束、檢討馬上又跳出來。
            ClearRange();
            WarnIfOutOfRange();
            BeginSong();
            return true;
        }

        /// <summary>停下來並清掉畫面上的音符（回到選曲時用）。</summary>
        public void Stop()
        {
            IsPlaying = false;
            IsFinished = false;   // 回選曲＝這一輪結束了，不是「停在終點」
            ReleaseAllHeld();   // 不放的話琴鍵會卡在按下狀態（藍色）
            ClearNotes();
        }

        /// <summary>
        /// 只練某幾個小節。檢討畫面按「重練這一段」就是呼叫這個。
        ///
        /// 只是把譜面過濾成那個範圍再重開一次，判定與計分邏輯完全不變。
        /// </summary>
        public bool PlayRange(int startMeasure, int endMeasure)
        {
            if (Chart == null) return false;
            Range = (startMeasure, endMeasure);
            BeginSong();
            return true;
        }

        /// <summary>取消範圍限制，回到整首。</summary>
        public void ClearRange() => Range = (0, 0);

        /// <summary>這一輪實際結束的秒數。只練某一段時就是那一段的結尾，不是整首的長度。</summary>
        public float RangeEnd => _rangeEnd;

        // 這一輪實際要用的音符索引與時間範圍。用索引而不是複製一份音符陣列，
        // 是為了讓 PerNote 的鍵一直是同一批物件，檢討才對得回去。
        private int _from, _to;                 // [_from, _to) 的音符索引
        private float _rangeStart, _rangeEnd;   // 這一輪的起訖秒數（原曲的時間軸）

        private void ComputeRange()
        {
            _from = 0;
            _to = Chart.notes.Length;
            _rangeStart = 0f;
            _rangeEnd = Chart.duration_sec;

            if (Range.Start <= 0 || Range.End < Range.Start) return;

            int first = -1, last = -1;
            for (int i = 0; i < Chart.notes.Length; i++)
            {
                int m = Chart.notes[i].measure;
                if (m < Range.Start || m > Range.End) continue;
                if (first < 0) first = i;
                last = i;
            }
            if (first < 0)
            {
                ClearRange();       // 這個範圍裡沒有音符，退回整首
                return;
            }

            _from = first;
            _to = last + 1;
            _rangeStart = Chart.notes[first].t;
            _rangeEnd = Chart.notes[last].t + Chart.notes[last].d;
        }

        /// <summary>目前這首曲子的檔案路徑。</summary>
        public string ChartPath { get; private set; } = "";

        private void OnKeyboardBuilt()
        {
            // 鍵盤在改 Inspector 參數時會重建，落下區也要跟著重生，
            // 否則音符會掛在已經被刪掉的舊鍵盤底下。
            if (!Application.isPlaying || Chart == null) return;
            BuildHighway();
            RespawnVisibleNotes();
        }

        private void SetupAudio()
        {
            if (!playSound) return;
            _audio = GetComponent<PianoAudio>();
            if (_audio == null) _audio = gameObject.AddComponent<PianoAudio>();
        }

        // ---------- 載入 ----------

        public void LoadChart()
        {
            string folder = ChartLoader.ResolveFolder(chartFolder);
            string path = ResolveChartPath(folder);

            if (path == null)
            {
                StatusMessage =
                    $"找不到譜面檔。\n請先在 Python 那邊產生譜面：\n" +
                    $"  run.py score build <專案名>\n" +
                    $"程式會去這裡找：\n  {folder}";
                Debug.LogWarning("[音遊] " + StatusMessage);
                return;
            }

            Chart = ChartLoader.Load(path);
            if (Chart == null)
            {
                StatusMessage = $"譜面檔讀不進來：\n{path}";
                return;
            }

            ChartPath = path;
            StatusMessage = "";
            WarnIfOutOfRange();
        }

        private string ResolveChartPath(string folder)
        {
            if (!string.IsNullOrWhiteSpace(chartFile))
            {
                if (File.Exists(chartFile)) return chartFile;
                string combined = Path.Combine(folder, chartFile);
                if (File.Exists(combined)) return combined;
                if (!chartFile.EndsWith(".json"))
                {
                    combined = Path.Combine(folder, chartFile + ".json");
                    if (File.Exists(combined)) return combined;
                }
                Debug.LogWarning($"[音遊] 指定的譜面檔不存在：{chartFile}，改用資料夾裡的第一個");
            }

            var all = ChartLoader.ListCharts(folder);
            return all.Count > 0 ? all[0] : null;
        }

        private void WarnIfOutOfRange()
        {
            Chart.GetPitchRange(out int low, out int high);
            var missing = new List<int>();
            foreach (var note in Chart.notes)
                if (_keyboard.GetKey(note.midi) == null && !missing.Contains(note.midi))
                    missing.Add(note.midi);

            if (missing.Count == 0) return;
            Debug.LogWarning(
                $"[音遊] 譜面音域 {low}–{high} 有 {missing.Count} 個音不在目前的鍵盤上，" +
                $"這些音會被略過。把 PianoKeyboardUI 的「音域」改成 88 鍵就能全部涵蓋。");
        }

        // ---------- 開始 / 重來 ----------

        public void BeginSong()
        {
            if (Chart == null) return;

            BuildHighway();
            ClearNotes();
            ComputeRange();

            Score = 0;
            Combo = 0;
            MaxCombo = 0;
            Counts.Clear();
            PerNote.Clear();
            LastJudgement = Judgement.None;
            _nextSpawn = _from;
            _nextMiss = _from;
            _nextAuto = _from;
            // 只清清單不夠 —— 鍵還按在畫面上。上一輪如果是彈到一半重開，
            // 那幾個鍵會一直維持按下狀態（藍色）。
            ReleaseAllHeld();
            _holds.Clear();

            // 只練某一段時，從那一段的開頭起算，不要讓玩家先空等好幾十秒
            SongTime = _rangeStart - Mathf.Max(0f, leadInSeconds);
            IsPlaying = true;
            IsFinished = false;
            if (_review != null) _review.Reset();
        }

        public void Restart() => BeginSong();

        public void TogglePause()
        {
            // 彈完之後不能再被切回播放：SongTime 已經過了結尾，一恢復就會在
            // 下一幀立刻再結束一次，中間那一幀還會把 ReleaseAllHeld() 再跑一遍。
            if (Chart != null && !IsFinished) IsPlaying = !IsPlaying;
        }

        // ---------- 場景 ----------

        private void BuildHighway()
        {
            var root = _keyboard.KeyboardRoot;
            if (root == null) return;

            if (_highway != null && _highway.parent == root.parent) return;   // 已經有了

            if (_noteSprite == null)
                _noteSprite = UIShapes.RoundedRect(6, true, true, "RhythmGame_Note");

            var go = new GameObject("NoteHighway", typeof(RectTransform));
            _highway = (RectTransform)go.transform;
            _highway.SetParent(root.parent, false);

            // 跟鍵盤根物件用完全一樣的錨點與位置，這樣音符的 x 座標可以直接
            // 沿用 PianoKey 的座標，不必再做一次換算。
            _highway.anchorMin = root.anchorMin;
            _highway.anchorMax = root.anchorMax;
            _highway.pivot = root.pivot;
            _highway.sizeDelta = root.sizeDelta;
            _highway.anchoredPosition = root.anchoredPosition;

            // 排在鍵盤前面，音符才會被琴鍵擋住（落到鍵上就消失在鍵後）
            _highway.SetSiblingIndex(root.GetSiblingIndex());

            if (showHitLine) CreateHitLine();
        }

        private void CreateHitLine()
        {
            float y = HitLineY();
            var go = new GameObject("HitLine", typeof(RectTransform), typeof(Image));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_highway, false);
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.zero;
            rt.pivot = new Vector2(0f, 0.5f);
            rt.sizeDelta = new Vector2(_highway.sizeDelta.x, 3f);
            rt.anchoredPosition = new Vector2(0f, y);

            _hitLine = go.GetComponent<Image>();
            _hitLine.color = new Color(1f, 0.35f, 0.35f, 0.85f);
            _hitLine.raycastTarget = false;
        }

        /// <summary>判定線的高度 = 白鍵上緣。音符底部碰到這裡就是該按的時候。</summary>
        private float HitLineY() => _keyboard.WhiteKeySize.y;

        private float PixelsPerSecond =>
            Mathf.Max(PixelsPerSecondMin, highwayHeight / Mathf.Max(0.2f, approachSeconds));

        // ---------- 每幀 ----------

        private void Update()
        {
            if (!Application.isPlaying || !IsPlaying || Chart == null) return;

            SongTime += Time.deltaTime;

            SpawnDueNotes();
            MoveNotes();
            // 自動演奏一定要排在漏按檢查**之前**。反過來的話，只要有一幀卡超過
            // goodWindow（0.16 秒，載入譜面或大量音符同時生成時很容易發生），
            // 這一幀該按的音會先被判成 Miss，電腦才按下去 —— 明明是自動演奏卻掉分。
            RunAutoPlay(autoPlay);
            ResolveFinishedHolds();
            CheckMisses();

            if (SongTime > _rangeEnd + goodWindow + 1f)
            {
                IsPlaying = false;
                IsFinished = true;
                // SongTime 從這一刻起就不再前進，ReleaseExpired() 再也不會
                // 放開剩下的鍵 —— 最後彈的那幾個音會一直是藍的。
                ReleaseAllHeld();
            }
        }

        private void SpawnDueNotes()
        {
            // 只生成快要進入畫面的音符。整首歌幾百顆一次生完會很浪費。
            float horizon = SongTime + approachSeconds;
            while (_nextSpawn < _to && Chart.notes[_nextSpawn].t <= horizon)
            {
                SpawnNote(Chart.notes[_nextSpawn]);
                _nextSpawn++;
            }
        }

        private void SpawnNote(ChartNote data)
        {
            var key = _keyboard.GetKey(data.midi);
            if (key == null) return;   // 音域外，WarnIfOutOfRange 已經提醒過了

            var go = new GameObject("Note", typeof(RectTransform), typeof(Image), typeof(FallingNote));
            go.transform.SetParent(_highway, false);

            float width = (key.IsBlack ? _keyboard.BlackKeySize.x : _keyboard.WhiteKeySize.x)
                          * noteWidthRatio;
            // 裝飾音在譜上時值是 0，產譜面時給了最短可彈長度，但畫成一樣寬會
            // 看起來像正常音符。畫窄一點，玩家一眼知道那是裝飾音。
            if (data.grace) width *= graceWidthRatio;
            float height = Mathf.Max(10f, data.d * PixelsPerSecond);

            // 左右手用色相分（藍/橘），黑白鍵用明度分。兩個維度分開才不會互相干擾 ——
            // 如果黑鍵也換成另一個色相，四種顏色混在一起反而認不出哪隻手。
            var color = data.hand == "L" ? leftHandColor : rightHandColor;
            if (key.IsBlack) color = Shade(color, blackKeyNoteShade);
            // 這一小節的拍數對不上拍號 = 音符時值被認錯了，玩家看到的不是譜上
            // 寫的東西。壓低彩度標出來，免得他以為是自己彈錯。
            if (!IsMeasureReliable(data.measure)) color = Desaturate(color, unreliableFade);

            var note = go.GetComponent<FallingNote>();
            note.Init(data, new Vector2(width, height), color, _noteSprite,
                      isHold: data.d >= holdThreshold);
            _notes.Add(note);
        }

        /// <summary>把顏色壓暗但保留色相，這樣還看得出是哪一隻手。</summary>
        private static Color Shade(Color c, float factor)
        {
            return new Color(c.r * factor, c.g * factor, c.b * factor, c.a);
        }

        /// <summary>壓低彩度往灰色靠，但不改亮度 —— 左右手還是分得出來。</summary>
        private static Color Desaturate(Color c, float amount)
        {
            float grey = c.r * 0.299f + c.g * 0.587f + c.b * 0.114f;
            return new Color(Mathf.Lerp(c.r, grey, amount),
                             Mathf.Lerp(c.g, grey, amount),
                             Mathf.Lerp(c.b, grey, amount), c.a);
        }

        /// <summary>這一小節的辨識可不可靠。查不到資料時一律當成可靠 ——
        /// 舊的譜面 JSON 沒有這個欄位，不該因此整首變灰。</summary>
        private bool IsMeasureReliable(int measure)
        {
            if (Chart == null || Chart.measures == null) return true;
            // 小節通常是連號的，先賭 measure-1 這個位置，省掉整條掃描
            int guess = measure - 1;
            if (guess >= 0 && guess < Chart.measures.Length
                && Chart.measures[guess].n == measure)
                return Chart.measures[guess].ok;

            foreach (var m in Chart.measures)
                if (m.n == measure) return m.ok;
            return true;
        }

        private void MoveNotes()
        {
            float hitY = HitLineY();
            float pps = PixelsPerSecond;

            for (int i = _notes.Count - 1; i >= 0; i--)
            {
                var note = _notes[i];
                var key = _keyboard.GetKey(note.Data.midi);
                if (key == null) { RemoveNote(i); continue; }

                // 距離判定時間還有幾秒 → 換算成離判定線多高
                float remaining = note.Data.t - SongTime;
                float y = hitY + remaining * pps;
                note.SetPosition(key.CenterX, y);

                // 整顆都掉到判定線底下了就回收 —— 但**成績還沒定的音符不能丟**。
                //
                // 回收條件是幾何的（離判定線 40px，換算成時間只有 0.1 秒左右），
                // 判定的存活期是時間的（goodWindow 0.16 秒，卡頓時還會更久）。
                // 兩個尺度不一樣，短音符就會在還沒被判定的時候先被銷毀，
                // 按下去找不到候選，接著被 CheckMisses 判成 Miss。
                // うまぴょい伝説 裡一串 48 毫秒的音就是這樣憑空掉了一顆。
                //
                // 用 PerNote 當條件最穩：它是「這顆音已經有結論」的唯一權威，
                // 不管結論是 Perfect 還是 Miss。沒有結論的音符一律留著，
                // CheckMisses 過期之後自然會補上結論，下一幀就回收得掉。
                bool decided = note.Judged || PerNote.ContainsKey(note.Data);
                if (note.State != NoteState.Holding && decided
                    && y + note.Data.d * pps < hitY - 40f)
                    RemoveNote(i);
            }
        }

        private void RemoveNote(int index)
        {
            var note = _notes[index];
            _notes.RemoveAt(index);
            if (note != null) Destroy(note.gameObject);
        }

        private void CheckMisses()
        {
            // 音符依時間排序，所以只要從前面往後掃到「還沒過期」為止就好
            while (_nextMiss < _to && Chart.notes[_nextMiss].t + goodWindow < SongTime)
            {
                var data = Chart.notes[_nextMiss];
                var note = FindNote(data);
                // 正在按住中的不算漏按 —— 它已經被按到了，成績等放開才定
                if (note != null && note.State == NoteState.Waiting)
                    ApplyJudgement(note, Judgement.Miss);
                else if (note == null && !PerNote.ContainsKey(data)
                         && _keyboard.GetKey(data.midi) != null)
                {
                    // 物件不在畫面上了，而且確實沒有任何判定紀錄 = 真的沒人按。
                    //
                    // 一定要查 PerNote：畫面上找不到不代表沒被判定 ——
                    // MoveNotes 會把落到判定線下方的音符回收掉，一幀卡久一點
                    // （或短音符）就會在同一幀先被判 Perfect、再被回收，
                    // 這裡就會把同一顆音再判一次 Miss，判定數因此超過音符數。
                    ApplyJudgement(null, Judgement.Miss);
                    PerNote[data] = Judgement.Miss;
                }
                _nextMiss++;
            }
        }

        private FallingNote FindNote(ChartNote data)
        {
            foreach (var note in _notes)
                if (note.Data == data) return note;
            return null;
        }

        // ---------- 判定 ----------

        /// <summary>譜上這個時間點要求的力度（0–127 換算成 0–1）。
        ///
        /// 強弱記號是**整段的性質**，不是單一音符的 —— 一個 f 一直生效到下一個記號，
        /// 所以查時間點就夠，不必去找是哪一個音符。這樣也不用動到判定路徑。
        ///
        /// 力度來自第二引擎（Audiveris）讀出的強弱記號；homr 一個都不產，
        /// 所以在有這個資料之前整首都是預設的 mf。
        /// </summary>
        private float ScoreVelocity(float when)
        {
            var notes = Chart != null ? Chart.notes : null;
            if (notes == null || notes.Length == 0) return 1f;

            int vel = 0;
            // notes 依 t 排序，所以掃到超過就可以停
            foreach (var note in notes)
            {
                if (note.t > when) break;
                if (note.vel > 0) vel = note.vel;
            }
            if (vel <= 0) vel = notes[0].vel;
            return vel > 0 ? Mathf.Clamp01(vel / 127f) : 1f;
        }

        private void OnNotePressed(int midi)
        {
            if (playSound && _audio != null)
                _audio.Play(midi, ScoreVelocity(SongTime),
                            Pedal != null && Pedal.IsDown);
            if (!IsPlaying || Chart == null) return;

            // 自動演奏按下去的那一刻，判定要用**音符自己的時間**，不是當下的 SongTime。
            // 一幀如果卡超過 goodWindow（0.16 秒 —— GC、大量音符同時生成、載入貼圖
            // 都可能造成），SongTime 會一次跳過音符的判定視窗，FindBestCandidate
            // 就找不到候選，這一次按鍵完全不算，接著被判 Miss。
            // 電腦不是「彈晚了」，是幀率的問題，不該因此扣分。
            float pressedAt = float.IsNaN(_autoPressTime) ? SongTime : _autoPressTime;

            var best = FindBestCandidate(midi, pressedAt, out float diff);
            if (best == null) return;   // 這個時間點沒有這個音，當作彈錯 —— 不扣分也不加分

            var grade = Classify(diff);

            // 短音按一下就結案；長音要按住，成績等放開（或撐到結束）才定。
            if (!best.IsHold)
            {
                ApplyJudgement(best, grade);
                return;
            }

            best.BeginHold();
            _holds.Add(new Hold
            {
                Note = best,
                Grade = grade,
                StartedAt = SongTime,
                EndsAt = best.Data.t + best.Data.d,
            });
        }

        /// <summary>
        /// 放開琴鍵。長音就是在這裡結算的。
        ///
        /// **踩著延音踏板的時候放開不算中斷** —— 真鋼琴就是這樣，
        /// 踏板踩著手指離鍵，音還是繼續響。
        /// </summary>
        private void OnNoteReleased(int midi)
        {
            if (!IsPlaying || Chart == null) return;
            if (Pedal != null && Pedal.IsDown) return;

            for (int i = _holds.Count - 1; i >= 0; i--)
            {
                if (_holds[i].Note == null || _holds[i].Note.Data.midi != midi) continue;
                // 自動演奏放開時，按滿的時間以音符自己的長度為準（同樣不受幀率影響）
                float releasedAt = float.IsNaN(_autoReleaseTime) ? SongTime : _autoReleaseTime;
                FinishHold(i, releasedAt);
                return;   // 一次放開只結算一顆
            }
        }

        /// <summary>撐到音符結束都沒放開的，時間到就給原本的成績。</summary>
        private void ResolveFinishedHolds()
        {
            for (int i = _holds.Count - 1; i >= 0; i--)
            {
                if (_holds[i].Note == null) { _holds.RemoveAt(i); continue; }
                if (SongTime < _holds[i].EndsAt) continue;
                FinishHold(i, _holds[i].EndsAt);   // 按滿了
            }
        }

        private void FinishHold(int index, float releasedAt)
        {
            var hold = _holds[index];
            _holds.RemoveAt(index);
            if (hold.Note == null) return;

            var data = hold.Note.Data;
            float needed = Mathf.Max(0.01f, data.d);
            float held = Mathf.Clamp(releasedAt - data.t, 0f, needed);

            // 提早一點點放開仍算按滿 —— 手指要移到下一個音，本來就會提早離鍵
            if (held >= needed - holdReleaseWindow)
            {
                ApplyJudgement(hold.Note, hold.Grade);
                return;
            }

            float ratio = held / needed;
            ApplyJudgement(hold.Note, ratio >= holdKeepRatio ? Downgrade(hold.Grade)
                                                             : Judgement.Miss);
        }

        private static Judgement Downgrade(Judgement grade)
        {
            switch (grade)
            {
                case Judgement.Perfect: return Judgement.Great;
                case Judgement.Great: return Judgement.Good;
                default: return Judgement.Good;
            }
        }

        /// <summary>找出這個音高在容許誤差內、時間最接近 pressedAt 的那一顆。</summary>
        private FallingNote FindBestCandidate(int midi, float pressedAt, out float diff)
        {
            FallingNote best = null;
            float bestDiff = float.MaxValue;

            foreach (var note in _notes)
            {
                // 只找還沒被按到的。正在按住中的（Holding）不能再被按一次，
                // 否則同一顆音會被判定兩遍。
                if (note.State != NoteState.Waiting || note.Data.midi != midi) continue;
                float d = Mathf.Abs(note.Data.t - pressedAt);
                if (d > goodWindow || d >= bestDiff) continue;
                bestDiff = d;
                best = note;
            }

            diff = bestDiff;
            return best;
        }

        private Judgement Classify(float diff)
        {
            if (diff <= perfectWindow) return Judgement.Perfect;
            if (diff <= greatWindow) return Judgement.Great;
            return Judgement.Good;
        }

        private void ApplyJudgement(FallingNote note, Judgement result)
        {
            if (note != null)
            {
                note.MarkJudged(result);
                PerNote[note.Data] = result;
            }

            Counts.TryGetValue(result, out int count);
            Counts[result] = count + 1;

            if (result == Judgement.Miss)
            {
                Combo = 0;
            }
            else
            {
                Combo++;
                if (Combo > MaxCombo) MaxCombo = Combo;
                // 連擊加成上限 8 倍，不然後段分數會爆炸到看不出差別
                int baseScore = result == Judgement.Perfect ? 100
                              : result == Judgement.Great ? 70 : 40;
                Score += baseScore * Mathf.Min(8, 1 + Combo / 10);
            }

            LastJudgement = result;
            LastJudgementTime = Time.time;
        }

        // ---------- 自動演奏 ----------

        /// <summary>
        /// 自動演奏。play=false 時只推進進度、不按鍵。
        ///
        /// **進度一定要一直推進，不能只在開著的時候推**：索引如果停在自動演奏
        /// 關掉的那一刻，之後中途按 F1 打開，這個迴圈會從那裡一路追到現在，
        /// 在同一幀把中間累積的幾百顆音全部按下去 —— 畫面上就是突然一堆
        /// 不相干的琴鍵被按下。
        /// </summary>
        private void RunAutoPlay(bool play)
        {
            // 先處理放開，再處理按下 —— 這一幀該放的音要先放掉，
            // 才不會擋到同一幀要按的下一個音。
            // 這一段不受 play 影響：中途關掉自動演奏時，已經按下去的鍵
            // 還是要照排定的時間放開，否則會一直卡在按下狀態。
            ReleaseExpired();

            while (_nextAuto < _to && Chart.notes[_nextAuto].t <= SongTime)
            {
                var data = Chart.notes[_nextAuto];
                if (play && _keyboard.GetKey(data.midi) != null) AutoStrike(data);
                _nextAuto++;
            }
        }

        /// <summary>
        /// 替自動演奏按下一個音。
        ///
        /// 同一個音重複出現、而前一次還按著的時候，一定要先放開再按 ——
        /// PianoKey.Press() 在已按下狀態會直接 return（那是對的，真鋼琴也要抬指再擊），
        /// 不先放開的話這一次按鍵完全不會發出事件，那顆音就變成 Miss。
        /// 快速的同音反覆很常見（Alkan 那首 379 音裡就有 47 處），不處理的話
        /// 自動演奏會莫名其妙掉一堆分。
        /// </summary>
        private void AutoStrike(ChartNote data)
        {
            int held = _autoHeld.IndexOf(data.midi);
            if (held >= 0)
            {
                _keyboard.ReleaseNote(data.midi);
                _autoHeld.RemoveAt(held);
                _autoRelease.RemoveAt(held);
            }

            // 讓判定用音符自己的時間，而不是這一幀剛好跑到哪
            _autoPressTime = data.t;
            _keyboard.PressNote(data.midi);
            _autoPressTime = float.NaN;

            _autoHeld.Add(data.midi);
            // 按滿整個音長 —— 長音要按住才算完整，只按 0.25 秒就放開的話
            // 自動演奏會把每個長音都判成「沒接好」。
            // 留一點點空隙給同音反覆，不然放開和下一次按下會撞在同一幀。
            _autoRelease.Add(SongTime + Mathf.Max(0.04f, data.d - 0.01f));
        }

        /// <summary>把還按著的鍵全部放掉。
        ///
        /// `ReleaseExpired()` 是靠 `SongTime` 超過排定的放開時間才放鍵，但**曲子
        /// 結束時 SongTime 就不再前進了** —— 最後那幾個鍵永遠等不到自己的放開時間，
        /// 就一直卡在按下狀態（琴鍵維持藍色），下一首開始前都不會消失。
        ///
        /// 所以停止、結束、重新開始這三個時機都要無條件清乾淨。
        /// </summary>
        private void ReleaseAllHeld()
        {
            for (int i = _autoHeld.Count - 1; i >= 0; i--)
            {
                if (_keyboard != null) _keyboard.ReleaseNote(_autoHeld[i]);
            }
            _autoHeld.Clear();
            _autoRelease.Clear();
        }

        private void ReleaseExpired()
        {
            for (int i = _autoHeld.Count - 1; i >= 0; i--)
            {
                if (_autoRelease[i] > SongTime) continue;
                // 用排定的放開時間結算，不是這一幀跑到哪
                _autoReleaseTime = _autoRelease[i];
                _keyboard.ReleaseNote(_autoHeld[i]);
                _autoReleaseTime = float.NaN;
                _autoHeld.RemoveAt(i);
                _autoRelease.RemoveAt(i);
            }
        }

        // ---------- 其他 ----------

        private void ClearNotes()
        {
            foreach (var note in _notes)
                if (note != null) Destroy(note.gameObject);
            _notes.Clear();
        }

        private void RespawnVisibleNotes()
        {
            ClearNotes();
            // 把生成指標退回目前該看得到的位置，下一幀 SpawnDueNotes 會補回來
            _nextSpawn = 0;
            while (_nextSpawn < _to && Chart.notes[_nextSpawn].t < SongTime)
                _nextSpawn++;
        }

        public int TotalJudged
        {
            get
            {
                int sum = 0;
                foreach (var pair in Counts) sum += pair.Value;
                return sum;
            }
        }

        /// <summary>準確率 0–1。Perfect 算滿分、Great 0.7、Good 0.4。</summary>
        public float Accuracy
        {
            get
            {
                int total = TotalJudged;
                if (total == 0) return 0f;
                float earned = Get(Judgement.Perfect) * 1f
                             + Get(Judgement.Great) * 0.7f
                             + Get(Judgement.Good) * 0.4f;
                return earned / total;
            }
        }

        public int Get(Judgement j) => Counts.TryGetValue(j, out int v) ? v : 0;
    }
}


