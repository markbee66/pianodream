#if ENABLE_INPUT_SYSTEM && !ENABLE_LEGACY_INPUT_MANAGER
#define PIANOUI_NEW_INPUT
#endif

using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

#if PIANOUI_NEW_INPUT
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
#endif

namespace PianoUI
{
    /// <summary>
    /// 音遊的鋼琴鍵盤 UI。掛在一個空物件上按 Play 就會自動生出：
    /// Canvas → 鍵盤底座 → 白鍵 / 黑鍵 → 角色。
    /// 按下任何一鍵，角色會「瞬移」到那顆鍵的正上方。
    ///
    /// 外部（例如之後接電子琴 MIDI）只要呼叫 PressNote(midi) / ReleaseNote(midi) 即可。
    /// </summary>
    [DisallowMultipleComponent]
    [ExecuteAlways] // 編輯模式也會建，不用按 Play 就看得到
    public class PianoKeyboardUI : MonoBehaviour
    {
        public enum KeyboardRange
        {
            全部88鍵 = 0,   // A0 – C8
            鍵76 = 1,       // E1 – G7
            鍵61 = 2,       // C2 – C7
            鍵49 = 3,       // C2 – C6
            自訂 = 99,
        }

        public enum LabelMode
        {
            只標C = 0,   // 鍵多的時候全部標會糊成一片
            全部標 = 1,
            不標 = 2,
        }

        public enum ChordPolicy
        {
            最高音 = 0,  // 和弦時角色站在最高的那個音（旋律線）
            最後按的 = 1,
        }

        [Header("音域")]
        public KeyboardRange range = KeyboardRange.全部88鍵;
        [Tooltip("range 選「自訂」時才有作用。最左邊那顆鍵的 MIDI 音高，60 = 中央 C")]
        public int startMidi = 60;
        [Tooltip("range 選「自訂」時才有作用。白鍵數量，7 = 一個八度")]
        // 這裡要寫完整名稱：本檔有 using System，[Range] 會被當成 System.Range 而編譯失敗
        [UnityEngine.Range(3, 60)] public int whiteKeyCount = 14;

        [Header("尺寸")]
        [Tooltip("自動把鍵盤撐滿畫面寬度。88 鍵一定要開，否則會超出畫面")]
        public bool autoFitWidth = true;
        [Tooltip("鍵盤左右各留多少空白")]
        public float sideMargin = 24f;
        [Tooltip("autoFitWidth 關掉時才用這個寬度")]
        public Vector2 whiteKeySize = new Vector2(78f, 280f);
        [Tooltip("黑鍵高度佔白鍵的比例。真鋼琴大約 0.63")]
        public float blackKeyHeightRatio = 0.63f;
        [Tooltip("黑鍵寬度佔白鍵的比例。真鋼琴大約 0.58")]
        public float blackKeyWidthRatio = 0.58f;
        [Tooltip("鍵盤離畫面底部的高度")]
        public float keyboardBottomMargin = 60f;

        // autoFitWidth 算出來的實際尺寸，Build 時填
        private Vector2 _whiteSize;
        private Vector2 _blackSize;

        [Header("顏色")]
        [Tooltip("關掉就變成透明的，可以疊在遊戲畫面上")]
        public bool drawBackground = true;
        public Color backgroundColor = new Color(0.086f, 0.09f, 0.129f, 1f);
        public Color whiteKeyColor = new Color(0.97f, 0.97f, 0.98f, 1f);
        public Color whiteKeyPressedColor = new Color32(120, 180, 255, 255);
        public Color blackKeyColor = new Color(0.13f, 0.14f, 0.18f, 1f);
        // 黑鍵按下去要比白鍵按下去暗，不然兩種按下狀態顏色太接近，
        // 快速樂句時分不出剛才按到的是白鍵還是黑鍵
        public Color blackKeyPressedColor = new Color(0.16f, 0.35f, 0.72f, 1f);

        [Header("角色")]
        [Tooltip("留空就用程式畫的圓形角色")]
        public Sprite characterSprite;
        [Tooltip("角色大小自動跟著鍵寬縮放。88 鍵時要開，否則角色會比琴鍵大好幾倍")]
        public bool autoScaleCharacter = true;
        [Tooltip("角色寬度 = 白鍵寬 × 這個倍數")]
        public float characterWidthRatio = 1.5f;
        [Tooltip("autoScaleCharacter 關掉時才用這個大小")]
        public Vector2 characterSize = new Vector2(64f, 64f);
        public Color characterColor = new Color(1f, 0.78f, 0.29f, 1f);
        [Tooltip("一開始角色站在第幾顆白鍵上（0 = 最左邊）")]
        public int startWhiteKeyIndex = 0;

        [Header("和弦")]
        [Tooltip("同時按多個音時，角色要站哪一顆")]
        public ChordPolicy chordPolicy = ChordPolicy.最高音;

        [Header("輸入")]
        [Tooltip("可以用電腦鍵盤彈：A W S E D F T G Y H U J K O L P")]
        public bool enableComputerKeyboard = true;
        [Tooltip("電腦鍵盤的 A 對應到哪個音。60 = 中央 C（和音域起點分開，88 鍵時才不會落在最低音區）")]
        public int computerKeyboardStartMidi = 60;
        [Tooltip("音名標籤。鍵一多就建議只標 C")]
        public LabelMode labelMode = LabelMode.只標C;
        [Tooltip("自動建立 Canvas / EventSystem。已經有自己的 Canvas 就關掉並指定 targetCanvas")]
        public bool autoCreateCanvas = true;
        public Canvas targetCanvas;

        /// <summary>任何一顆鍵被按下時觸發，參數是 MIDI 音高。</summary>
        public event Action<int> NotePressed;
        public event Action<int> NoteReleased;

        /// <summary>鍵盤重建完成時觸發。音遊要等這個才拿得到鍵的位置。</summary>
        public event Action Built;

        public CharacterHopper Character { get; private set; }
        public bool PointerHeld { get; private set; }

        /// <summary>
        /// 鍵盤的根物件。所有琴鍵的座標都是相對它算的，
        /// 落下的音符要跟琴鍵對齊就必須放進同一個座標系。
        /// </summary>
        public RectTransform KeyboardRoot => _keyboardRoot;

        /// <summary>單顆白鍵的實際寬高（autoFitWidth 算完之後的值）。</summary>
        public Vector2 WhiteKeySize => _whiteSize;
        public Vector2 BlackKeySize => _blackSize;

        private readonly Dictionary<int, PianoKey> _keysByMidi = new Dictionary<int, PianoKey>();
        private readonly List<PianoKey> _whiteKeys = new List<PianoKey>();
        private RectTransform _keyboardRoot;
        private Canvas _createdCanvas; // 只記「我自己建的」Canvas，清除時才知道該不該刪
        private readonly HashSet<int> _heldNotes = new HashSet<int>(); // 目前按著的音，和弦規則要用

        // 半音 0 起算，哪些是黑鍵
        private static readonly bool[] IsBlackPitchClass =
            { false, true, false, true, false, false, true, false, true, false, true, false };

        // 電腦鍵盤對應：索引 = 距離 startMidi 的半音數
        private static readonly KeyCode[] LegacyKeyMap =
        {
            KeyCode.A, KeyCode.W, KeyCode.S, KeyCode.E, KeyCode.D, KeyCode.F, KeyCode.T,
            KeyCode.G, KeyCode.Y, KeyCode.H, KeyCode.U, KeyCode.J, KeyCode.K, KeyCode.O,
            KeyCode.L, KeyCode.P, KeyCode.Semicolon, KeyCode.Quote
        };

#if PIANOUI_NEW_INPUT
        private static readonly Key[] NewKeyMap =
        {
            Key.A, Key.W, Key.S, Key.E, Key.D, Key.F, Key.T,
            Key.G, Key.Y, Key.H, Key.U, Key.J, Key.K, Key.O,
            Key.L, Key.P, Key.Semicolon, Key.Quote
        };
#endif

        private const string GeneratedRootName = "PianoUI (自動產生)";

        private void OnEnable()
        {
            if (Application.isPlaying)
            {
                Build();
                return;
            }
#if UNITY_EDITOR
            // 編輯模式下不能在 OnEnable 當場建物件（可能正在載入場景 / 重新編譯），
            // 延到下一個編輯器迴圈再建。
            UnityEditor.EditorApplication.delayCall += EditorDeferredBuild;
#endif
        }

#if UNITY_EDITOR
        private void EditorDeferredBuild()
        {
            if (this == null) return;          // 期間被刪掉了
            if (Application.isPlaying) return; // 期間進了 Play
            Build();
        }
#endif

        private void OnDisable()
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.delayCall -= EditorDeferredBuild;
#endif
            // 編輯模式產生的東西不留在場景裡，避免存檔後變成一堆垃圾物件
            if (!Application.isPlaying) Clear();
        }

        /// <summary>建立整個鍵盤。重複呼叫會先清掉舊的（改參數後想重生可以呼叫這個）。</summary>
        public void Build()
        {
            Clear();

            var canvas = ResolveCanvas();
            if (canvas == null)
            {
                Debug.LogError("[PianoKeyboardUI] 找不到 Canvas，也沒有開 autoCreateCanvas。", this);
                return;
            }

            ResolveRange();
            ResolveSizes(canvas);

            var background = CreateBackground(canvas);
            _keyboardRoot = CreateKeyboardRoot(background);
            CreateKeys();
            CreateCharacter();

            // 開場先讓角色站在指定的白鍵上
            int idx = Mathf.Clamp(startWhiteKeyIndex, 0, _whiteKeys.Count - 1);
            if (_whiteKeys.Count > 0) Character.TeleportTo(_whiteKeys[idx]);

            // 整棵樹都標成不存檔（只影響編輯模式）
            if (!Application.isPlaying)
            {
                foreach (var t in background.GetComponentsInChildren<Transform>(true))
                    t.gameObject.hideFlags = HideFlags.DontSave;
            }

            Built?.Invoke();
        }

        public void Clear()
        {
            _keysByMidi.Clear();
            _whiteKeys.Clear();
            _heldNotes.Clear();
            _keyboardRoot = null;
            Character = null;

            // 重新編譯後私有欄位會被清空，靠名字把殘留的舊鍵盤找出來，
            // 否則每次改程式都會多疊一套上去。
            foreach (var root in FindGeneratedRoots()) DestroySafe(root);

            if (_createdCanvas != null)
            {
                DestroySafe(_createdCanvas.gameObject);
                _createdCanvas = null;
            }
        }

        private static List<GameObject> FindGeneratedRoots()
        {
            var result = new List<GameObject>();
#if UNITY_2023_1_OR_NEWER
            var canvases = UnityEngine.Object.FindObjectsByType<Canvas>(
                FindObjectsInactive.Include, FindObjectsSortMode.None);
#else
            var canvases = UnityEngine.Object.FindObjectsOfType<Canvas>(true);
#endif
            foreach (var c in canvases)
            {
                var t = c.transform;
                for (int i = 0; i < t.childCount; i++)
                {
                    var child = t.GetChild(i);
                    if (child.name == GeneratedRootName) result.Add(child.gameObject);
                }
            }
            return result;
        }

        private static void DestroySafe(GameObject go)
        {
            if (go == null) return;
            if (Application.isPlaying) Destroy(go);
            else DestroyImmediate(go);
        }

#if UNITY_EDITOR
        private void OnValidate()
        {
            // 在 Inspector 改數值就即時重生。OnValidate 裡不能直接建/刪物件，要延一拍。
            if (Application.isPlaying || this == null) return;
            UnityEditor.EditorApplication.delayCall += EditorDeferredBuild;
        }
#endif

        // ---------- 音域與尺寸 ----------

        /// <summary>實際使用的音域起點（自訂以外由 range 決定）。</summary>
        public int ResolvedStartMidi { get; private set; }
        /// <summary>實際使用的白鍵數。</summary>
        public int ResolvedWhiteKeyCount { get; private set; }

        private void ResolveRange()
        {
            switch (range)
            {
                // 起點 MIDI 與白鍵數都是實體琴的規格
                case KeyboardRange.全部88鍵: ResolvedStartMidi = 21; ResolvedWhiteKeyCount = 52; break; // A0–C8
                case KeyboardRange.鍵76:    ResolvedStartMidi = 28; ResolvedWhiteKeyCount = 45; break; // E1–G7
                case KeyboardRange.鍵61:    ResolvedStartMidi = 36; ResolvedWhiteKeyCount = 36; break; // C2–C7
                case KeyboardRange.鍵49:    ResolvedStartMidi = 36; ResolvedWhiteKeyCount = 29; break; // C2–C6
                default:
                    ResolvedStartMidi = startMidi;
                    ResolvedWhiteKeyCount = Mathf.Max(1, whiteKeyCount);
                    break;
            }
        }

        private void ResolveSizes(Canvas canvas)
        {
            _whiteSize = whiteKeySize;

            if (autoFitWidth)
            {
                float available = GetCanvasWidth(canvas) - sideMargin * 2f;
                if (available > 0f)
                    _whiteSize.x = available / ResolvedWhiteKeyCount;
            }

            _blackSize = new Vector2(
                _whiteSize.x * blackKeyWidthRatio,
                _whiteSize.y * blackKeyHeightRatio);
        }

        private static float GetCanvasWidth(Canvas canvas)
        {
            // 剛建好的 Canvas 可能還沒排版，先強制更新一次再量
            Canvas.ForceUpdateCanvases();
            var rt = canvas.transform as RectTransform;
            float w = rt != null ? rt.rect.width : 0f;
            return w > 1f ? w : 1920f; // 量不到就用參考解析度
        }

        // ---------- 場景組裝 ----------

        private Canvas ResolveCanvas()
        {
            if (targetCanvas != null) return targetCanvas;

            var existing = GetComponentInParent<Canvas>();
            if (existing != null) return existing.rootCanvas;

            if (!autoCreateCanvas) return null;

            var go = new GameObject("PianoCanvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            var canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;

            var scaler = go.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;

            EnsureEventSystem();
            MarkGenerated(go);
            _createdCanvas = canvas;
            return canvas;
        }

        private static void EnsureEventSystem()
        {
#if UNITY_2023_1_OR_NEWER
            if (UnityEngine.Object.FindFirstObjectByType<EventSystem>() != null) return;
#else
            if (UnityEngine.Object.FindObjectOfType<EventSystem>() != null) return;
#endif
            var go = new GameObject("EventSystem", typeof(EventSystem));
#if PIANOUI_NEW_INPUT
            go.AddComponent<InputSystemUIInputModule>();
#else
            go.AddComponent<StandaloneInputModule>();
#endif
            MarkGenerated(go);
        }

        /// <summary>
        /// 產生出來的東西不寫進場景檔（HideFlags.DontSave），
        /// 這樣編輯模式看得到、存檔卻不會被塞進 .unity 變成垃圾。
        /// </summary>
        private static void MarkGenerated(GameObject go)
        {
            if (!Application.isPlaying) go.hideFlags = HideFlags.DontSave;
        }

        private RectTransform CreateBackground(Canvas canvas)
        {
            var go = new GameObject(GeneratedRootName, typeof(RectTransform), typeof(Image));
            MarkGenerated(go);
            var rt = (RectTransform)go.transform;
            rt.SetParent(canvas.transform, false);
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;

            var img = go.GetComponent<Image>();
            img.color = drawBackground ? backgroundColor : new Color(0f, 0f, 0f, 0f);
            img.raycastTarget = false;
            return rt;
        }

        private RectTransform CreateKeyboardRoot(RectTransform parent)
        {
            float width = ResolvedWhiteKeyCount * _whiteSize.x;

            var go = new GameObject("KeyboardRoot", typeof(RectTransform));
            var rt = (RectTransform)go.transform;
            rt.SetParent(parent, false);
            // 貼在畫面底部中央，pivot 在左下角 → 子物件座標從 (0,0) 往右上長
            rt.anchorMin = new Vector2(0.5f, 0f);
            rt.anchorMax = new Vector2(0.5f, 0f);
            rt.pivot = new Vector2(0f, 0f);
            rt.sizeDelta = new Vector2(width, _whiteSize.y);
            rt.anchoredPosition = new Vector2(-width * 0.5f, keyboardBottomMargin);
            return rt;
        }

        private void CreateKeys()
        {
            var whiteSprite = UIShapes.WhiteKey();
            var blackSprite = UIShapes.BlackKey();

            // 先掃一遍決定每顆鍵的位置，白鍵先生成、黑鍵後生成，
            // 這樣黑鍵的 sibling index 比較大，才會畫在白鍵上面。
            var whitePlan = new List<(int midi, Vector2 pos)>();
            var blackPlan = new List<(int midi, Vector2 pos)>();

            int whiteIndex = 0;
            int midi = ResolvedStartMidi;
            while (whiteIndex < ResolvedWhiteKeyCount)
            {
                bool isBlack = IsBlackPitchClass[((midi % 12) + 12) % 12];
                if (isBlack)
                {
                    // 黑鍵騎在前一顆白鍵的右邊界上
                    if (whiteIndex > 0)
                    {
                        float x = whiteIndex * _whiteSize.x - _blackSize.x * 0.5f;
                        blackPlan.Add((midi, new Vector2(x, _whiteSize.y - _blackSize.y)));
                    }
                }
                else
                {
                    whitePlan.Add((midi, new Vector2(whiteIndex * _whiteSize.x, 0f)));
                    whiteIndex++;
                }
                midi++;
            }

            foreach (var (m, pos) in whitePlan)
            {
                var key = SpawnKey(m, false, pos, _whiteSize, whiteKeyColor, whiteKeyPressedColor, whiteSprite);
                _whiteKeys.Add(key);
            }
            foreach (var (m, pos) in blackPlan)
            {
                SpawnKey(m, true, pos, _blackSize, blackKeyColor, blackKeyPressedColor, blackSprite);
            }
        }

        private PianoKey SpawnKey(int midi, bool isBlack, Vector2 pos, Vector2 size,
                                  Color normal, Color pressed, Sprite sprite)
        {
            var go = new GameObject("Key", typeof(RectTransform), typeof(Image), typeof(PianoKey));
            go.transform.SetParent(_keyboardRoot, false);

            var key = go.GetComponent<PianoKey>();
            key.Init(this, midi, isBlack, pos, size, normal, pressed, sprite, ShouldLabel(midi, isBlack));
            key.Pressed += OnKeyPressed;
            key.Released += OnKeyReleased;

            _keysByMidi[midi] = key;
            return key;
        }

        private bool ShouldLabel(int midi, bool isBlack)
        {
            switch (labelMode)
            {
                case LabelMode.全部標: return true;
                case LabelMode.不標:   return false;
                // 只標 C：和實體鋼琴的定位方式一樣，靠 C 找位置
                default: return !isBlack && ((midi % 12) + 12) % 12 == 0;
            }
        }

        private void CreateCharacter()
        {
            var size = autoScaleCharacter
                ? Vector2.one * (_whiteSize.x * characterWidthRatio)
                : characterSize;

            var go = new GameObject("Character", typeof(RectTransform), typeof(Image), typeof(CharacterHopper));
            var rt = (RectTransform)go.transform;
            rt.SetParent(_keyboardRoot, false);
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.zero;
            rt.pivot = new Vector2(0.5f, 0f); // 底部中心 = 腳底，方便「站在鍵上」
            rt.sizeDelta = size;

            var img = go.GetComponent<Image>();
            img.sprite = characterSprite != null ? characterSprite : UIShapes.Circle();
            img.color = characterColor;
            img.raycastTarget = false;

            if (characterSprite == null) AddFace(rt);

            Character = go.GetComponent<CharacterHopper>();
            Character.SetRingColor(characterColor);
        }

        /// <summary>沒有指定角色圖時，幫預設的圓形加兩顆眼睛，看起來像個角色。</summary>
        private static void AddFace(RectTransform body)
        {
            for (int i = 0; i < 2; i++)
            {
                var eye = new GameObject(i == 0 ? "Eye_L" : "Eye_R", typeof(RectTransform), typeof(Image));
                var rt = (RectTransform)eye.transform;
                rt.SetParent(body, false);
                rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
                rt.pivot = new Vector2(0.5f, 0.5f);
                // 依身體大小等比，角色縮小時眼睛才不會爆掉
                float w = body.sizeDelta.x;
                rt.sizeDelta = new Vector2(w * 0.14f, w * 0.17f);
                rt.anchoredPosition = new Vector2((i == 0 ? -0.17f : 0.17f) * w, w * 0.094f);

                var img = eye.GetComponent<Image>();
                img.sprite = UIShapes.Circle();
                img.color = new Color(0.1f, 0.11f, 0.16f, 1f);
                img.raycastTarget = false;
            }
        }

        // ---------- 輸入 ----------

        private void Update()
        {
            if (!Application.isPlaying) return; // 編輯模式只做預覽，不吃鍵盤輸入
            if (!enableComputerKeyboard || _keysByMidi.Count == 0) return;

            for (int i = 0; i < LegacyKeyMap.Length; i++)
            {
                int midi = computerKeyboardStartMidi + i;
                if (!_keysByMidi.TryGetValue(midi, out var key)) continue;

                if (WasKeyPressedThisFrame(i)) key.Press();
                else if (WasKeyReleasedThisFrame(i)) key.Release();
            }
        }

        private static bool WasKeyPressedThisFrame(int index)
        {
#if PIANOUI_NEW_INPUT
            var kb = Keyboard.current;
            return kb != null && kb[NewKeyMap[index]].wasPressedThisFrame;
#else
            return Input.GetKeyDown(LegacyKeyMap[index]);
#endif
        }

        private static bool WasKeyReleasedThisFrame(int index)
        {
#if PIANOUI_NEW_INPUT
            var kb = Keyboard.current;
            return kb != null && kb[NewKeyMap[index]].wasReleasedThisFrame;
#else
            return Input.GetKeyUp(LegacyKeyMap[index]);
#endif
        }

        internal void NotifyPointerDown() => PointerHeld = true;
        internal void NotifyPointerUp() => PointerHeld = false;

        // ---------- 對外 API（之後接電子琴 MIDI 就呼叫這兩個）----------

        /// <summary>用 MIDI 音高按下某顆鍵。找不到對應的鍵會回 false。</summary>
        public bool PressNote(int midi)
        {
            if (!_keysByMidi.TryGetValue(midi, out var key)) return false;
            key.Press();
            return true;
        }

        public bool ReleaseNote(int midi)
        {
            if (!_keysByMidi.TryGetValue(midi, out var key)) return false;
            key.Release();
            return true;
        }

        public PianoKey GetKey(int midi) => _keysByMidi.TryGetValue(midi, out var k) ? k : null;

        // ---------- 事件轉發 ----------

        private void OnKeyPressed(PianoKey key)
        {
            _heldNotes.Add(key.Midi);
            UpdateCharacterTarget(key);
            NotePressed?.Invoke(key.Midi);
        }

        private void OnKeyReleased(PianoKey key)
        {
            _heldNotes.Remove(key.Midi);
            // 放開最高音之後，角色退回剩下還按著的最高音；全放開就留在原地
            UpdateCharacterTarget(null);
            NoteReleased?.Invoke(key.Midi);
        }

        /// <summary>
        /// 依和弦規則決定角色該站哪。
        /// 最高音：和弦時站在最高的那個音，也就是旋律線。
        /// </summary>
        private void UpdateCharacterTarget(PianoKey justPressed)
        {
            if (Character == null) return;

            PianoKey target;
            if (chordPolicy == ChordPolicy.最後按的)
            {
                target = justPressed; // 放開時不移動
            }
            else
            {
                int highest = int.MinValue;
                foreach (var midi in _heldNotes)
                    if (midi > highest) highest = midi;

                if (highest == int.MinValue) return; // 全部放開了，留在原地
                target = GetKey(highest);
            }

            if (target == null || target == Character.CurrentKey) return;
            Character.TeleportTo(target); // ← 瞬移就發生在這一行
        }
    }
}
