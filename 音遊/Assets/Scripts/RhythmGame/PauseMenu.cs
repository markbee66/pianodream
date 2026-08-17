using PianoUI;
using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 彈到一半按 Esc 的暫停選單。
    ///
    /// 之前 Esc 是直接跳回選曲，等於一按就放棄這一次的進度 —— 手滑或想休息一下
    /// 都會整輪重來。現在先暫停，再由玩家決定要繼續、重來、還是離開。
    ///
    /// 背景刻意只壓半透明，看得到後面停住的畫面，才有「暫停」而不是「換頁」的感覺。
    /// </summary>
    [RequireComponent(typeof(RhythmGameController))]
    public class PauseMenu : MonoBehaviour
    {
        public KeyCode pauseKey = KeyCode.Escape;
        public KeyCode restartKey = KeyCode.R;
        public KeyCode quitKey = KeyCode.Q;

        [Tooltip("按繼續之後倒數幾秒才真的開始。手要放回琴鍵上，直接接下去會來不及")]
        public float resumeCountdown = 3f;

        public bool IsOpen { get; private set; }

        /// <summary>正在倒數（已經按了繼續，但還沒真的開始）。</summary>
        public bool IsCountingDown => _countdown > 0f;

        private RhythmGameController _game;
        private PianoKeyboardUI _keyboard;
        private SongSelectUI _select;
        private ReviewScreen _review;

        private RectTransform _panel;
        private Text _title, _hint;
        private Sprite _sprite;
        private bool _built;

        private float _countdown;
        private readonly System.Collections.Generic.List<Button> _buttons =
            new System.Collections.Generic.List<Button>();

        private void Awake()
        {
            _game = GetComponent<RhythmGameController>();
            _keyboard = GetComponent<PianoKeyboardUI>();
            _select = GetComponent<SongSelectUI>();
            _review = GetComponent<ReviewScreen>();
        }

        private void Update()
        {
            if (!Application.isPlaying) return;
            if (!_built && !Build()) return;

            // 選曲或檢討畫面開著的時候，Esc 是它們的，不要搶
            if ((_select != null && _select.IsOpen) || (_review != null && _review.IsOpen))
                return;

            if (_countdown > 0f)
            {
                TickCountdown();
                return;      // 倒數中不吃任何按鍵
            }

            if (IsOpen)
            {
                if (Input.GetKeyDown(pauseKey)) Resume();
                else if (Input.GetKeyDown(restartKey)) RestartSong();
                else if (Input.GetKeyDown(quitKey)) BackToSelect();
                return;
            }

            if (!Input.GetKeyDown(pauseKey)) return;

            // 只有真的在彈的時候才「暫停」
            if (_game.IsPlaying) { Pause(); return; }

            // 彈完了就直接回選曲。**這一條不可省** —— 曲子結束時 IsPlaying 已經是
            // false，上面那個分支不會成立；而另外兩個會吃 Esc 的地方這時也都在棄權：
            //
            //     SongSelectUI  只要掛了暫停選單就整段 return，完全不管
            //     ReviewScreen  沒開的時候第一行就 return
            //
            // 正常情況檢討畫面會自己跳出來接手，但它有一串前置條件
            // （要有判定資料、Build() 要成功、_reported 要被重置過）。
            // 只要任何一個不成立，三個處理器就會一起棄權，Esc 變成完全沒人接，
            // 玩家卡在跑完的畫面上換不了下一首。這裡是那個情況的保底。
            if (_game.IsFinished) BackToSelect();
        }

        // ---------- 動作 ----------

        public void Pause()
        {
            if (IsOpen || !_game.IsPlaying) return;
            _game.TogglePause();
            IsOpen = true;
            _panel.gameObject.SetActive(true);
        }

        /// <summary>
        /// 按下繼續。不是立刻接下去，而是先倒數 —— 暫停時手已經離開琴鍵，
        /// 突然恢復的話最前面那幾顆一定來不及按。
        /// </summary>
        public void Resume()
        {
            if (!IsOpen || _countdown > 0f) return;
            if (resumeCountdown <= 0f) { FinishResume(); return; }

            _countdown = resumeCountdown;
            SetMenuVisible(false);        // 倒數時只留數字，按鈕收起來
            UpdateCountdownText();
        }

        private void TickCountdown()
        {
            // 用 deltaTime，跟歌曲時間同一個時鐘（RhythmGameController 也是 Time.deltaTime）。
            // 暫停是靠「不累加 SongTime」做的，沒有動 timeScale，所以這裡照樣會走。
            _countdown -= Time.deltaTime;
            if (_countdown > 0f) { UpdateCountdownText(); return; }
            FinishResume();
        }

        private void UpdateCountdownText()
        {
            _title.text = Mathf.CeilToInt(_countdown).ToString();
            _hint.text = "準備好，手放回琴鍵上";
        }

        private void FinishResume()
        {
            _countdown = 0f;
            IsOpen = false;
            SetMenuVisible(true);
            _title.text = "已暫停";
            _hint.text = $"[{pauseKey}] 繼續　[{restartKey}] 重來　[{quitKey}] 回到選曲";
            _panel.gameObject.SetActive(false);
            if (!_game.IsPlaying) _game.TogglePause();
        }

        private void SetMenuVisible(bool visible)
        {
            foreach (var button in _buttons)
                if (button != null) button.gameObject.SetActive(visible);
            _title.fontSize = visible ? 40 : 96;
        }

        public void RestartSong()
        {
            IsOpen = false;
            _panel.gameObject.SetActive(false);
            _game.ClearRange();      // 重來就是整首，不是上次練的那一段
            _game.Restart();
        }

        public void BackToSelect()
        {
            IsOpen = false;
            _panel.gameObject.SetActive(false);
            if (_select != null) _select.Open();
            else _game.Stop();
        }

        // ---------- 畫面 ----------

        private bool Build()
        {
            var root = _keyboard != null ? _keyboard.KeyboardRoot : null;
            if (root == null || root.parent == null) return false;

            _sprite = UIShapes.RoundedRect(8, true, true, "PauseBtn");

            var go = new GameObject("PauseMenu", typeof(RectTransform), typeof(Image));
            _panel = (RectTransform)go.transform;
            _panel.SetParent(root.parent, false);
            _panel.anchorMin = Vector2.zero;
            _panel.anchorMax = Vector2.one;
            _panel.offsetMin = _panel.offsetMax = Vector2.zero;
            var bg = go.GetComponent<Image>();
            // 半透明：看得到後面停住的畫面，才像「暫停」
            bg.color = new Color(0.05f, 0.06f, 0.09f, 0.78f);
            bg.raycastTarget = true;

            _title = MakeText("PauseTitle", new Vector2(0.5f, 0.5f), new Vector2(0f, 120f),
                              40, new Color(1f, 1f, 1f, 0.95f));
            _title.text = "已暫停";

            MakeButton("繼續", new Vector2(0f, 40f), new Color(0.22f, 0.45f, 0.85f, 1f), Resume);
            MakeButton("重來", new Vector2(0f, -14f), new Color(1f, 1f, 1f, 0.14f), RestartSong);
            MakeButton("回到選曲", new Vector2(0f, -68f), new Color(1f, 1f, 1f, 0.14f), BackToSelect);

            _hint = MakeText("PauseHint", new Vector2(0.5f, 0.5f), new Vector2(0f, -130f),
                             16, new Color(1f, 1f, 1f, 0.45f));
            _hint.text = $"[{pauseKey}] 繼續　[{restartKey}] 重來　[{quitKey}] 回到選曲";

            _built = true;
            _panel.gameObject.SetActive(false);
            return true;
        }

        private Text MakeText(string name, Vector2 anchor, Vector2 offset, int size, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = anchor;
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(900f, 60f);
            rt.anchoredPosition = offset;

            var text = go.GetComponent<Text>();
            text.font = UIShapes.BuiltinFont();
            text.fontSize = size;
            text.alignment = TextAnchor.MiddleCenter;
            text.color = color;
            text.raycastTarget = false;
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private void MakeButton(string label, Vector2 pos, Color color,
                                UnityEngine.Events.UnityAction onClick)
        {
            var go = new GameObject($"Btn_{label}", typeof(RectTransform), typeof(Image),
                                    typeof(Button));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_panel, false);
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(220f, 46f);
            rt.anchoredPosition = pos;

            var img = go.GetComponent<Image>();
            img.sprite = _sprite;
            img.type = Image.Type.Sliced;
            img.color = color;

            // 鍵盤與滑鼠都要能操作 —— 只做鍵盤的話，Game 視窗沒有焦點就會卡死
            var text = MakeText($"{label}Label", new Vector2(0.5f, 0.5f), Vector2.zero,
                                18, new Color(1f, 1f, 1f, 0.95f));
            text.text = label;
            var trt = (RectTransform)text.transform;
            trt.SetParent(rt, false);
            trt.anchorMin = Vector2.zero;
            trt.anchorMax = Vector2.one;
            trt.offsetMin = trt.offsetMax = Vector2.zero;

            var button = go.GetComponent<Button>();
            button.targetGraphic = img;
            button.onClick.AddListener(onClick);
            _buttons.Add(button);
        }
    }
}

