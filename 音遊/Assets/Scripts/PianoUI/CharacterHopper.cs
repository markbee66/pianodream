using System.Collections;
using UnityEngine;
using UnityEngine.UI;

namespace PianoUI
{
    /// <summary>
    /// 角色本體。按下哪顆鍵就「瞬移」到那顆鍵的正上方，
    /// 位移是瞬間的（不是滑過去），只有落地的擠壓變形和光圈是漸變的。
    /// </summary>
    public class CharacterHopper : MonoBehaviour
    {
        [Header("站位")]
        [Tooltip("角色底部離鍵上緣的距離")]
        public float standGap = 4f;
        [Tooltip("站在黑鍵上時額外墊高，表現黑鍵比白鍵高的感覺")]
        public float blackKeyLift = 10f;

        [Header("落地表現")]
        [Tooltip("關掉就是完全沒有動畫的純瞬移")]
        public bool squashOnLand = true;
        public float squashDuration = 0.14f;
        [Tooltip("落地瞬間的壓扁程度，1 = 不壓扁")]
        public float squashAmount = 0.72f;
        public bool showLandingRing = true;

        private RectTransform _rect;
        private RectTransform _ringRect;
        private Image _ringImage;
        private Coroutine _squashRoutine;
        private Coroutine _ringRoutine;
        private Color _ringColor = new Color(1f, 1f, 1f, 0.9f);

        public PianoKey CurrentKey { get; private set; }

        private void Awake()
        {
            _rect = GetComponent<RectTransform>();
        }

        /// <summary>瞬移到指定琴鍵上方。</summary>
        public void TeleportTo(PianoKey key)
        {
            if (key == null) return;
            CurrentKey = key;

            // 位置是「一幀之內直接換過去」，沒有補間
            float y = key.TopY + standGap + (key.IsBlack ? blackKeyLift : 0f);
            var foot = new Vector2(key.CenterX, y);
            _rect.anchoredPosition = foot;

            // 編輯模式不能跑 Coroutine，只做瞬移、不做落地動畫
            bool canAnimate = Application.isPlaying && isActiveAndEnabled;

            if (squashOnLand && canAnimate)
            {
                if (_squashRoutine != null) StopCoroutine(_squashRoutine);
                _squashRoutine = StartCoroutine(SquashAndStretch());
            }

            if (showLandingRing && canAnimate)
            {
                EnsureRing();
                _ringRect.anchoredPosition = foot;
                _ringRect.SetAsLastSibling();
                if (_ringRoutine != null) StopCoroutine(_ringRoutine);
                _ringRoutine = StartCoroutine(RingPulse());
            }

            // 排在所有琴鍵和光圈之上，站黑鍵時才不會被白鍵蓋住
            _rect.SetAsLastSibling();
        }

        private IEnumerator SquashAndStretch()
        {
            // 落地：先橫向壓扁，再彈回原樣
            float t = 0f;
            float squashX = 2f - squashAmount; // 壓扁多少，就橫向撐開多少
            while (t < squashDuration)
            {
                t += Time.unscaledDeltaTime;
                float k = Mathf.Clamp01(t / squashDuration);
                // 用 sin 讓形變在中途最大、結束時回到 1
                float bump = Mathf.Sin(k * Mathf.PI);
                float sx = Mathf.Lerp(1f, squashX, bump);
                float sy = Mathf.Lerp(1f, squashAmount, bump);
                _rect.localScale = new Vector3(sx, sy, 1f);
                yield return null;
            }
            _rect.localScale = Vector3.one;
            _squashRoutine = null;
        }

        private void EnsureRing()
        {
            if (_ringRect != null) return;

            // 當角色的兄弟節點而不是子節點，否則會蓋在角色臉上
            var go = new GameObject("LandingRing", typeof(RectTransform), typeof(Image));
            _ringRect = (RectTransform)go.transform;
            _ringRect.SetParent(_rect.parent, false);
            _ringRect.anchorMin = Vector2.zero;
            _ringRect.anchorMax = Vector2.zero;
            _ringRect.pivot = new Vector2(0.5f, 0.5f);
            _ringRect.sizeDelta = _rect.sizeDelta;

            _ringImage = go.GetComponent<Image>();
            _ringImage.sprite = UIShapes.Circle();
            _ringImage.raycastTarget = false;
            _ringImage.color = new Color(_ringColor.r, _ringColor.g, _ringColor.b, 0f);
        }

        private IEnumerator RingPulse()
        {
            const float duration = 0.32f;
            float t = 0f;
            while (t < duration)
            {
                t += Time.unscaledDeltaTime;
                float k = Mathf.Clamp01(t / duration);
                float scale = Mathf.Lerp(0.4f, 2.2f, k);
                _ringRect.localScale = new Vector3(scale, scale * 0.45f, 1f);
                _ringImage.color = new Color(_ringColor.r, _ringColor.g, _ringColor.b, (1f - k) * _ringColor.a);
                yield return null;
            }
            _ringImage.color = new Color(_ringColor.r, _ringColor.g, _ringColor.b, 0f);
            _ringRoutine = null;
        }

        public void SetRingColor(Color c)
        {
            _ringColor = c;
            if (_ringImage != null)
                _ringImage.color = new Color(c.r, c.g, c.b, 0f);
        }
    }
}
