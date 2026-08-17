using System.Collections.Generic;
using UnityEngine;

namespace RhythmGame
{
    /// <summary>
    /// 用程式合成鋼琴音色，不需要任何音訊檔。
    ///
    /// 跟 UIShapes 用程式畫貼圖是同一個想法：整個專案不依賴任何外部資源，
    /// 複製資料夾就能跑。音色不求好聽，求「音高聽得出來對不對」——
    /// 這是驗證辨識結果最直接的方式。
    ///
    /// 每個 MIDI 音高第一次用到時才合成，之後快取起來重複使用。
    /// </summary>
    public class PianoAudio : MonoBehaviour
    {
        [Tooltip("整體音量")]
        [UnityEngine.Range(0f, 1f)] public float volume = 0.35f;

        [Tooltip("同時最多幾個聲音。琴鍵按快一點很容易疊很多層")]
        public int voiceCount = 24;

        [Tooltip("每個音的長度（秒）。太長會讓快速樂句糊在一起")]
        public float noteLength = 1.4f;

        [Header("延音踏板踩下時")]
        [Tooltip("音長變成幾倍")]
        public float pedalLengthScale = 2.6f;
        [Tooltip("衰減速度變成幾倍（小於 1 = 衰減變慢、聲音拖得久）")]
        public float pedalDecayScale = 0.35f;

        private const int SampleRate = 44100;

        // 泛音組成：基音 + 八度 + 十二度 + 雙八度。
        // 高次泛音衰減得比較快，聽起來才有鋼琴那種「敲擊後變暗」的感覺。
        private static readonly float[] HarmonicAmp = { 1.00f, 0.42f, 0.18f, 0.09f };
        private static readonly float[] HarmonicDecay = { 1.00f, 1.55f, 2.30f, 3.10f };

        private readonly Dictionary<int, AudioClip> _clips = new Dictionary<int, AudioClip>();
        private AudioSource[] _voices;
        private int _next;

        private void Awake()
        {
            _voices = new AudioSource[Mathf.Max(1, voiceCount)];
            for (int i = 0; i < _voices.Length; i++)
            {
                var go = new GameObject($"Voice{i:00}");
                go.transform.SetParent(transform, false);
                var src = go.AddComponent<AudioSource>();
                src.playOnAwake = false;
                src.spatialBlend = 0f;   // 2D，不做空間定位
                _voices[i] = src;
            }
        }

        /// <summary>
        /// 彈一個音。velocity 0–1 控制音量。
        ///
        /// pedal=true 表示延音踏板踩著，音會延續得比較久 —— 真鋼琴踩踏板時
        /// 制音器整排抬起來，弦可以自由振動，衰減本來就慢很多。
        /// </summary>
        public void Play(int midi, float velocity = 1f, bool pedal = false)
        {
            if (_voices == null || _voices.Length == 0) return;

            var clip = GetClip(midi, pedal);
            if (clip == null) return;

            // 輪流用各個聲道。最舊的那個如果還在響就直接蓋掉 ——
            // 音遊按鍵很密，等它自然結束會讓新按的音發不出來。
            var src = _voices[_next];
            _next = (_next + 1) % _voices.Length;
            src.clip = clip;
            src.volume = Mathf.Clamp01(volume * velocity);
            src.Play();
        }

        private AudioClip GetClip(int midi, bool pedal)
        {
            // 踏板版本另外快取一份。同一個音高最多只會合成兩次。
            int key = pedal ? midi + 1000 : midi;
            if (_clips.TryGetValue(key, out var cached)) return cached;

            float freq = 440f * Mathf.Pow(2f, (midi - 69) / 12f);
            float length = pedal ? noteLength * pedalLengthScale : noteLength;
            int samples = Mathf.Max(1, Mathf.RoundToInt(SampleRate * length));
            var data = new float[samples];

            // 低音的衰減比高音慢，跟真鋼琴一樣
            float decayBase = Mathf.Lerp(4.5f, 1.6f, Mathf.InverseLerp(96f, 21f, midi));
            if (pedal) decayBase *= pedalDecayScale;
            int attack = Mathf.RoundToInt(SampleRate * 0.004f); // 4ms 起音，避免爆音

            for (int i = 0; i < samples; i++)
            {
                float t = (float)i / SampleRate;
                float value = 0f;
                for (int h = 0; h < HarmonicAmp.Length; h++)
                {
                    float amp = HarmonicAmp[h] * Mathf.Exp(-decayBase * HarmonicDecay[h] * t);
                    value += amp * Mathf.Sin(2f * Mathf.PI * freq * (h + 1) * t);
                }
                value *= 0.25f;                                   // 疊四個泛音，先降幅避免破音
                if (i < attack) value *= (float)i / attack;       // 淡入
                data[i] = value;
            }

            var clip = AudioClip.Create($"Piano{midi}{(pedal ? "P" : "")}",
                                        samples, 1, SampleRate, false);
            clip.SetData(data, 0);
            _clips[key] = clip;
            return clip;
        }
    }
}
