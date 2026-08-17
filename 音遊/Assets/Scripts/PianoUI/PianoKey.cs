using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace PianoUI
{
    /// <summary>
    /// 單一顆琴鍵。負責自己的外觀、按下/放開狀態，並回報事件給鍵盤。
    /// RectTransform 一律用左下角 anchor + 左下角 pivot，方便算「鍵的正上方」。
    /// </summary>
    [RequireComponent(typeof(Image))]
    public class PianoKey : MonoBehaviour,
        IPointerDownHandler, IPointerUpHandler, IPointerEnterHandler, IPointerExitHandler
    {
        private static readonly string[] NoteNames =
            { "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B" };

        public int Midi { get; private set; }
        public bool IsBlack { get; private set; }
        public bool IsPressed { get; private set; }

        /// <summary>按下事件（滑鼠、電腦鍵盤、外部 MIDI 都會走這裡）。</summary>
        public event Action<PianoKey> Pressed;
        public event Action<PianoKey> Released;

        private RectTransform _rect;
        private Image _image;
        private PianoKeyboardUI _keyboard;
        private Color _normalColor;
        private Color _pressedColor;
        private Text _label;

        public RectTransform Rect => _rect;

        /// <summary>鍵的水平中心（相對於鍵盤根物件的座標）。</summary>
        public float CenterX => _rect.anchoredPosition.x + _rect.sizeDelta.x * 0.5f;

        /// <summary>鍵的上緣高度（相對於鍵盤根物件的座標）。角色就站這裡。</summary>
        public float TopY => _rect.anchoredPosition.y + _rect.sizeDelta.y;

        public string NoteName => NoteNames[((Midi % 12) + 12) % 12] + (Midi / 12 - 1);

        public void Init(PianoKeyboardUI keyboard, int midi, bool isBlack,
                         Vector2 position, Vector2 size,
                         Color normalColor, Color pressedColor,
                         Sprite sprite, bool showLabel)
        {
            _keyboard = keyboard;
            Midi = midi;
            IsBlack = isBlack;
            _normalColor = normalColor;
            _pressedColor = pressedColor;

            _rect = GetComponent<RectTransform>();
            _rect.anchorMin = Vector2.zero;
            _rect.anchorMax = Vector2.zero;
            _rect.pivot = Vector2.zero;
            _rect.sizeDelta = size;
            _rect.anchoredPosition = position;

            _image = GetComponent<Image>();
            _image.sprite = sprite;
            _image.type = Image.Type.Sliced;
            _image.color = _normalColor;
            _image.raycastTarget = true;

            name = (isBlack ? "BlackKey_" : "WhiteKey_") + NoteName;

            if (showLabel) CreateLabel();
        }

        private void CreateLabel()
        {
            var font = UIShapes.BuiltinFont();
            if (font == null) return; // 取不到字型就不畫標籤，其他功能照常

            var go = new GameObject("Label", typeof(RectTransform), typeof(Text));
            var rt = (RectTransform)go.transform;
            rt.SetParent(transform, false);
            rt.anchorMin = new Vector2(0f, 0f);
            rt.anchorMax = new Vector2(1f, 0f);
            rt.pivot = new Vector2(0.5f, 0f);
            rt.offsetMin = new Vector2(0f, 8f);
            rt.offsetMax = new Vector2(0f, 30f);

            _label = go.GetComponent<Text>();
            _label.font = font;
            _label.fontSize = IsBlack ? 12 : 15;
            _label.alignment = TextAnchor.LowerCenter;
            _label.text = NoteName;
            _label.color = IsBlack ? new Color(1f, 1f, 1f, 0.55f) : new Color(0f, 0f, 0f, 0.35f);
            _label.raycastTarget = false;
            _label.horizontalOverflow = HorizontalWrapMode.Overflow;
            _label.verticalOverflow = VerticalWrapMode.Overflow;
        }

        /// <summary>顯示按下狀態並發出事件。重複呼叫不會重複觸發。</summary>
        public void Press()
        {
            if (IsPressed) return;
            IsPressed = true;
            _image.color = _pressedColor;
            // 往下沉一點點，像真的被按下去
            _rect.anchoredPosition += new Vector2(0f, -3f);
            Pressed?.Invoke(this);
        }

        public void Release()
        {
            if (!IsPressed) return;
            IsPressed = false;
            // 場景關閉時 OnDisable 也會走到這裡，那時元件可能已經被銷毀
            if (_image == null || _rect == null) return;
            _image.color = _normalColor;
            _rect.anchoredPosition += new Vector2(0f, 3f);
            Released?.Invoke(this);
        }

        public void OnPointerDown(PointerEventData eventData)
        {
            _keyboard.NotifyPointerDown();
            Press();
        }

        public void OnPointerUp(PointerEventData eventData)
        {
            _keyboard.NotifyPointerUp();
            Release();
        }

        public void OnPointerEnter(PointerEventData eventData)
        {
            // 按著滑過去 = 滑奏（glissando）
            if (_keyboard.PointerHeld) Press();
        }

        public void OnPointerExit(PointerEventData eventData)
        {
            // 滑出去就放開，避免鍵卡在按下狀態
            Release();
        }

        private void OnDisable()
        {
            if (IsPressed) Release();
        }
    }
}
