using PianoUI;
using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 音遊的畫面資訊：曲名、分數、連擊、判定文字、進度條、結算。
    ///
    /// 跟鍵盤一樣全部用程式生，不需要任何 prefab —— 這樣把整個資料夾複製到
    /// 別的專案也能直接跑。
    /// </summary>
    [RequireComponent(typeof(RhythmGameController))]
    public class RhythmGameHUD : MonoBehaviour
    {
        [Tooltip("判定文字顯示多久（秒）")]
        public float judgementHold = 0.55f;

        [Header("按鍵")]
        public KeyCode restartKey = KeyCode.R;
        // 暫停不用 Space —— 那顆留給延音踏板，跟真鋼琴一樣用最大的鍵去踩
        public KeyCode pauseKey = KeyCode.P;
        public KeyCode autoPlayKey = KeyCode.F1;

        private RhythmGameController _game;
        private PianoKeyboardUI _keyboard;
        private SongSelectUI _select;
        private ReviewScreen _review;
        private PauseMenu _pause;

        private Text _title, _score, _combo, _judge, _status, _result, _hint;
        private Image _progressFill;
        private RectTransform _hud;
        private bool _built;

        private static readonly Color[] JudgeColors =
        {
            new Color(1f, 1f, 1f, 0f),                     // None
            new Color(1f, 0.85f, 0.30f, 1f),               // Perfect
            new Color(0.40f, 0.85f, 1f, 1f),               // Great
            new Color(0.55f, 0.90f, 0.55f, 1f),            // Good
            new Color(1f, 0.42f, 0.42f, 1f),               // Miss
        };

        private static readonly string[] JudgeText =
            { "", "PERFECT", "GREAT", "GOOD", "MISS" };

        private void Awake()
        {
            _game = GetComponent<RhythmGameController>();
            _keyboard = GetComponent<PianoKeyboardUI>();
            _select = GetComponent<SongSelectUI>();
            _review = GetComponent<ReviewScreen>();
            _pause = GetComponent<PauseMenu>();
        }

        private void Start()
        {
            if (Application.isPlaying) Build();
        }

        private void Update()
        {
            if (!Application.isPlaying) return;
            if (!_built) Build();
            HandleKeys();
            Refresh();
        }

        private void HandleKeys()
        {
            // 選曲畫面開著的時候不吃按鍵，否則上下鍵選曲會同時觸發重來
            if (_select != null && _select.IsOpen) return;
            // 暫停選單開著時 R 是它的「重來」，這裡不能也接一次
            if (_pause != null && _pause.IsOpen) return;

            // 檢討畫面開著時，R 的意思是「只重練這一段」，不是整首重來
            if (_review != null && _review.IsOpen)
            {
                if (Input.GetKeyDown(restartKey)) _review.ReplayShownSection();
                return;
            }

            // 只用舊版 Input。專案的 Input Handling 設成 Both，兩種都能用，
            // 但寫一套就好，跟 PianoKeyboardUI 的鍵盤輸入保持一致的風格。
            if (Input.GetKeyDown(restartKey))
            {
                _game.ClearRange();       // 手動重來就回到整首
                _game.Restart();
            }
            if (Input.GetKeyDown(pauseKey)) _game.TogglePause();
            if (Input.GetKeyDown(autoPlayKey)) _game.autoPlay = !_game.autoPlay;
        }

        // ---------- 建立 ----------

        private void Build()
        {
            var root = _keyboard != null ? _keyboard.KeyboardRoot : null;
            if (root == null || root.parent == null) return;   // 鍵盤還沒生好，下一幀再試

            var canvas = root.parent as RectTransform;
            var go = new GameObject("RhythmHUD", typeof(RectTransform));
            _hud = (RectTransform)go.transform;
            _hud.SetParent(canvas, false);
            _hud.anchorMin = Vector2.zero;
            _hud.anchorMax = Vector2.one;
            _hud.offsetMin = Vector2.zero;
            _hud.offsetMax = Vector2.zero;

            _title = MakeText("Title", new Vector2(0f, 1f), new Vector2(24f, -24f),
                              28, TextAnchor.UpperLeft, new Color(1f, 1f, 1f, 0.92f));
            _hint = MakeText("Hint", new Vector2(0f, 1f), new Vector2(24f, -60f),
                             15, TextAnchor.UpperLeft, new Color(1f, 1f, 1f, 0.45f));
            _score = MakeText("Score", new Vector2(1f, 1f), new Vector2(-24f, -24f),
                              34, TextAnchor.UpperRight, new Color(1f, 1f, 1f, 0.92f));
            // 連擊與判定文字會疊在落下的音符上面，加深色描邊才讀得清楚
            _combo = MakeText("Combo", new Vector2(0.5f, 0.5f), new Vector2(0f, 120f),
                              46, TextAnchor.MiddleCenter, new Color(1f, 1f, 1f, 0.85f));
            _judge = MakeText("Judge", new Vector2(0.5f, 0.5f), new Vector2(0f, 40f),
                              32, TextAnchor.MiddleCenter, Color.white);
            AddOutline(_combo);
            AddOutline(_judge);
            _status = MakeText("Status", new Vector2(0.5f, 0.5f), new Vector2(0f, 0f),
                               20, TextAnchor.MiddleCenter, new Color(1f, 0.75f, 0.5f, 1f));
            _result = MakeText("Result", new Vector2(0.5f, 0.5f), new Vector2(0f, -40f),
                               22, TextAnchor.MiddleCenter, new Color(1f, 1f, 1f, 0.9f));

            BuildProgressBar();
            _built = true;
        }

        private Text MakeText(string name, Vector2 anchor, Vector2 offset,
                              int size, TextAnchor align, Color color)
        {
            var font = UIShapes.BuiltinFont();
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_hud, false);
            rt.anchorMin = rt.anchorMax = anchor;
            rt.pivot = anchor;
            rt.sizeDelta = new Vector2(760f, 60f);
            rt.anchoredPosition = offset;

            var text = go.GetComponent<Text>();
            text.font = font;
            text.fontSize = size;
            text.alignment = align;
            text.color = color;
            text.raycastTarget = false;
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private static void AddOutline(Text text)
        {
            var outline = text.gameObject.AddComponent<Outline>();
            outline.effectColor = new Color(0.05f, 0.06f, 0.09f, 0.9f);
            outline.effectDistance = new Vector2(2f, -2f);
        }

        private void BuildProgressBar()
        {
            var back = new GameObject("Progress", typeof(RectTransform), typeof(Image));
            var rt = (RectTransform)back.transform;
            rt.SetParent(_hud, false);
            rt.anchorMin = new Vector2(0f, 1f);
            rt.anchorMax = new Vector2(1f, 1f);
            rt.pivot = new Vector2(0.5f, 1f);
            rt.offsetMin = new Vector2(0f, -4f);
            rt.offsetMax = new Vector2(0f, 0f);
            var img = back.GetComponent<Image>();
            img.color = new Color(1f, 1f, 1f, 0.12f);
            img.raycastTarget = false;

            var fill = new GameObject("Fill", typeof(RectTransform), typeof(Image));
            var frt = (RectTransform)fill.transform;
            frt.SetParent(rt, false);
            frt.anchorMin = Vector2.zero;
            frt.anchorMax = new Vector2(0f, 1f);
            frt.pivot = new Vector2(0f, 0.5f);
            frt.offsetMin = Vector2.zero;
            frt.offsetMax = Vector2.zero;

            _progressFill = fill.GetComponent<Image>();
            _progressFill.color = new Color(0.35f, 0.65f, 1f, 0.9f);
            _progressFill.raycastTarget = false;
        }

        // ---------- 每幀更新 ----------

        private void Refresh()
        {
            if (!_built) return;

            if (!string.IsNullOrEmpty(_game.StatusMessage))
            {
                _status.text = _game.StatusMessage;
                _title.text = "";
                _hint.text = "";
                return;
            }

            var chart = _game.Chart;
            if (chart == null) return;

            _status.text = "";
            _title.text = $"{chart.title}　難度 {chart.level}（{chart.level_name}）";
            string pedal = _game.Pedal != null ? $"[{_game.Pedal.pedalKey}] 延音踏板　" : "";
            _hint.text = $"{chart.note_count} 音　BPM {chart.bpm:0}　{pedal}" +
                         $"[{pauseKey}] 暫停　[{restartKey}] 重來　[{autoPlayKey}] 自動演奏" +
                         (_game.autoPlay ? "（開）" : "") +
                         (_select != null ? "　[Esc] 暫停" : "");

            _score.text = _game.Score.ToString("N0");
            _combo.text = _game.Combo >= 3 ? $"{_game.Combo} COMBO" : "";

            // 倒數階段給玩家一個準備
            if (_game.SongTime < 0f)
                _judge.text = $"{Mathf.CeilToInt(-_game.SongTime)}";
            else
                UpdateJudgeText();

            _judge.color = _game.SongTime < 0f
                ? new Color(1f, 1f, 1f, 0.7f)
                : JudgeColors[(int)_game.LastJudgement];

            float progress = chart.duration_sec > 0f
                ? Mathf.Clamp01(_game.SongTime / chart.duration_sec) : 0f;
            _progressFill.rectTransform.anchorMax = new Vector2(progress, 1f);

            _result.text = _game.IsPlaying && _game.SongTime < chart.duration_sec
                ? ""
                : BuildResult();
        }

        private void UpdateJudgeText()
        {
            bool expired = Time.time - _game.LastJudgementTime > judgementHold;
            _judge.text = expired ? "" : JudgeText[(int)_game.LastJudgement];
        }

        private string BuildResult()
        {
            if (_game.TotalJudged == 0) return "";
            return $"完成　準確率 {_game.Accuracy * 100f:0.0}%　最高連擊 {_game.MaxCombo}\n" +
                   $"Perfect {_game.Get(Judgement.Perfect)}　" +
                   $"Great {_game.Get(Judgement.Great)}　" +
                   $"Good {_game.Get(Judgement.Good)}　" +
                   $"Miss {_game.Get(Judgement.Miss)}\n" +
                   $"按 [{restartKey}] 再來一次" +
                   (_select != null ? "　按 [Esc] 換一首" : "");
        }
    }
}



