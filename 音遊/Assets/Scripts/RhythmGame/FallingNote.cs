using UnityEngine;
using UnityEngine.UI;

namespace RhythmGame
{
    public enum Judgement
    {
        None = 0,
        Perfect,
        Great,
        Good,
        Miss,
    }

    /// <summary>音符目前的狀態。</summary>
    public enum NoteState
    {
        /// <summary>還沒被按到。</summary>
        Waiting = 0,
        /// <summary>已經按下、正在按住中（長音專用）。成績要等放開才算。</summary>
        Holding,
        /// <summary>成績已經定了。</summary>
        Done,
    }

    /// <summary>
    /// 畫面上一個落下的音符。
    ///
    /// 只負責「自己長什麼樣、現在在哪」；什麼時候該判定、判得如何，
    /// 全部由 RhythmGameController 決定 —— 判定邏輯散在每個音符身上會很難改。
    /// </summary>
    public class FallingNote : MonoBehaviour
    {
        public ChartNote Data { get; private set; }
        public Judgement Result { get; private set; } = Judgement.None;
        public NoteState State { get; private set; } = NoteState.Waiting;

        /// <summary>成績已經定了（不管是打到還是漏掉）。</summary>
        public bool Judged => State == NoteState.Done;

        /// <summary>長音：按下之後還要按住才算完整。</summary>
        public bool IsHold { get; private set; }

        private RectTransform _rect;
        private Image _image;
        private Color _baseColor;

        public void Init(ChartNote data, Vector2 size, Color color, Sprite sprite, bool isHold)
        {
            Data = data;
            _baseColor = color;
            IsHold = isHold;

            _rect = GetComponent<RectTransform>();
            _rect.anchorMin = Vector2.zero;
            _rect.anchorMax = Vector2.zero;
            _rect.pivot = new Vector2(0.5f, 0f);   // 底部中心 = 音符要對齊判定線的那一點
            _rect.sizeDelta = size;

            _image = GetComponent<Image>();
            _image.sprite = sprite;
            _image.type = Image.Type.Sliced;
            _image.color = color;
            _image.raycastTarget = false;          // 別擋到底下琴鍵的滑鼠事件

            name = $"Note_{data.midi}_{data.t:0.00}";
        }

        /// <summary>更新位置。x 是琴鍵中心、y 是音符底部該在的高度。</summary>
        public void SetPosition(float x, float y)
        {
            _rect.anchoredPosition = new Vector2(x, y);
        }

        public void SetHeight(float height)
        {
            _rect.sizeDelta = new Vector2(_rect.sizeDelta.x, Mathf.Max(6f, height));
        }

        /// <summary>開始按住（長音按下的那一刻）。成績還沒定。</summary>
        public void BeginHold()
        {
            State = NoteState.Holding;
            if (_image != null)
                _image.color = new Color(1f, 1f, 1f, 0.9f);   // 亮起來表示正在按住
        }

        public void MarkJudged(Judgement result)
        {
            Result = result;
            State = NoteState.Done;
            if (_image == null) return;

            // 判定完就淡掉，讓玩家看得出「這顆已經處理過了」。
            // 保留左右手的顏色只降透明度，不要換成白色 —— 長音符換成白色會
            // 變成一大片亮柱，反而比還沒打到的音符更搶眼。
            float alpha = result == Judgement.Miss ? 0.18f : 0.30f;
            _image.color = new Color(_baseColor.r, _baseColor.g, _baseColor.b, alpha);
        }

        public void ResetState()
        {
            Result = Judgement.None;
            State = NoteState.Waiting;
            if (_image != null) _image.color = _baseColor;
        }
    }
}
