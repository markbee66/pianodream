using PianoUI;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace RhythmGame
{
    /// <summary>
    /// 鋼琴的右踏板（延音踏板）。
    ///
    /// 做的是真鋼琴上那件事：**踩著的時候，手指離開琴鍵音也不會停。**
    /// 所以它跟長音的判定是同一組機制 ——
    ///
    ///   沒踩踏板：長音要一直按著琴鍵，提早放開就算沒接好
    ///   踩著踏板：放開琴鍵不算中斷，音繼續延續（跟真鋼琴一樣）
    ///
    /// 這也是評分那一側早就在看的東西：src/io_utils.py 的 extract_pedal()
    /// 會從演奏 MIDI 抽出 CC64 事件，src/scoring.py 有獨立的踏板維度。
    /// 之後接上電子琴，實體踏板的訊號可以直接餵進 SetPressed()。
    /// </summary>
    [RequireComponent(typeof(RhythmGameController))]
    public class SustainPedal : MonoBehaviour
    {
        [Tooltip("踩踏板的按鍵。跟真鋼琴一樣用最大的那顆")]
        public KeyCode pedalKey = KeyCode.Space;

        [Tooltip("畫面上顯示一個可以用滑鼠踩的踏板")]
        public bool showPedal = true;

        /// <summary>踏板現在是不是踩著的。</summary>
        public bool IsDown { get; private set; }

        private PianoKeyboardUI _keyboard;
        private RhythmGameController _game;
        private Image _pedalImage;
        private Text _pedalLabel;
        private RectTransform _pedal;
        private bool _built;
        private bool _pointerDown;
        private bool _external;   // 外部驅動（實體踏板 / 測試），跟鍵盤滑鼠並存

        private static readonly Color Up = new Color(0.30f, 0.32f, 0.38f, 1f);
        private static readonly Color Down = new Color(0.95f, 0.72f, 0.25f, 1f);

        private void Awake()
        {
            _keyboard = GetComponent<PianoKeyboardUI>();
            _game = GetComponent<RhythmGameController>();
        }

        private void Update()
        {
            if (!Application.isPlaying) return;
            if (showPedal && !_built) Build();

            // 三個來源是「或」的關係，任一個踩著就算踩著。
            // 外部來源要單獨記狀態，不能只是呼叫 Apply() —— 這裡每幀都會重算，
            // 直接覆寫的話外部設的值下一幀就被鍵盤/滑鼠的「沒踩」蓋掉。
            Apply(Input.GetKey(pedalKey) || _pointerDown || _external);
        }

        /// <summary>
        /// 由外部驅動踏板：之後接電子琴的實體踏板（CC64）就呼叫這個，
        /// 自動演奏或測試也用它。跟鍵盤／滑鼠是並存的，不會互相覆寫。
        /// </summary>
        public void SetPressed(bool down)
        {
            _external = down;
            Apply(down || Input.GetKey(pedalKey) || _pointerDown);
        }

        private void Apply(bool down)
        {
            if (down == IsDown) return;
            IsDown = down;
            if (_pedalImage != null) _pedalImage.color = down ? Down : Up;
            if (_pedalLabel != null)
                _pedalLabel.color = down ? new Color(0.1f, 0.1f, 0.12f, 1f)
                                         : new Color(1f, 1f, 1f, 0.75f);
        }

        // ---------- 畫面 ----------

        private void Build()
        {
            var root = _keyboard != null ? _keyboard.KeyboardRoot : null;
            if (root == null || root.parent == null) return;

            var go = new GameObject("SustainPedal", typeof(RectTransform), typeof(Image),
                                    typeof(PedalPointer));
            _pedal = (RectTransform)go.transform;
            _pedal.SetParent(root.parent, false);
            // 放在鍵盤左下角外側，位置跟真鋼琴的踏板一樣在琴鍵下方
            _pedal.anchorMin = _pedal.anchorMax = new Vector2(0f, 0f);
            _pedal.pivot = new Vector2(0f, 0f);
            _pedal.sizeDelta = new Vector2(150f, 40f);
            _pedal.anchoredPosition = new Vector2(24f, 12f);

            _pedalImage = go.GetComponent<Image>();
            _pedalImage.sprite = UIShapes.RoundedRect(8, true, true, "Pedal");
            _pedalImage.type = Image.Type.Sliced;
            _pedalImage.color = Up;

            go.GetComponent<PedalPointer>().Bind(this);

            var textGo = new GameObject("Label", typeof(RectTransform), typeof(Text));
            var trt = (RectTransform)textGo.transform;
            trt.SetParent(_pedal, false);
            trt.anchorMin = Vector2.zero;
            trt.anchorMax = Vector2.one;
            trt.offsetMin = trt.offsetMax = Vector2.zero;

            _pedalLabel = textGo.GetComponent<Text>();
            _pedalLabel.font = UIShapes.BuiltinFont();
            _pedalLabel.fontSize = 15;
            _pedalLabel.alignment = TextAnchor.MiddleCenter;
            _pedalLabel.text = $"延音踏板 [{pedalKey}]";
            _pedalLabel.color = new Color(1f, 1f, 1f, 0.75f);
            _pedalLabel.raycastTarget = false;

            _built = true;
        }

        internal void OnPointer(bool down) => _pointerDown = down;

        /// <summary>把滑鼠的按下/放開轉給踏板。分成獨立元件才收得到 UI 事件。</summary>
        private class PedalPointer : MonoBehaviour, IPointerDownHandler, IPointerUpHandler,
                                     IPointerExitHandler
        {
            private SustainPedal _pedal;
            public void Bind(SustainPedal pedal) => _pedal = pedal;
            public void OnPointerDown(PointerEventData e) => _pedal.OnPointer(true);
            public void OnPointerUp(PointerEventData e) => _pedal.OnPointer(false);
            // 按著滑出去也要放開，否則踏板會卡在踩下狀態
            public void OnPointerExit(PointerEventData e) => _pedal.OnPointer(false);
        }
    }
}
