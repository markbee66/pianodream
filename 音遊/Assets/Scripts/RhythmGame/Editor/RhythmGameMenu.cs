using System.Collections.Generic;
using System.IO;
using PianoUI;
using UnityEditor;
using UnityEngine;

namespace RhythmGame.EditorTools
{
    /// <summary>
    /// 編輯器選單：一鍵把落下式音遊裝進場景。
    /// 位置：上方選單列 → Tools → 鋼琴 UI
    /// </summary>
    public static class RhythmGameMenu
    {
        [MenuItem("Tools/鋼琴 UI/建立落下式音遊到場景", false, 20)]
        public static void CreateGame()
        {
            var keyboard = FindKeyboard();
            if (keyboard == null)
            {
                var go = new GameObject("PianoKeyboard");
                keyboard = go.AddComponent<PianoKeyboardUI>();
                Undo.RegisterCreatedObjectUndo(go, "建立鋼琴鍵盤");
                // 音遊需要完整音域，否則譜上的音會有一部分落在鍵盤外面
                keyboard.range = PianoKeyboardUI.KeyboardRange.全部88鍵;
            }

            var target = keyboard.gameObject;
            if (target.GetComponent<RhythmGameController>() == null)
                Undo.AddComponent<RhythmGameController>(target);
            if (target.GetComponent<RhythmGameHUD>() == null)
                Undo.AddComponent<RhythmGameHUD>(target);
            if (target.GetComponent<SongSelectUI>() == null)
                Undo.AddComponent<SongSelectUI>(target);
            if (target.GetComponent<SustainPedal>() == null)
                Undo.AddComponent<SustainPedal>(target);
            if (target.GetComponent<ReviewScreen>() == null)
                Undo.AddComponent<ReviewScreen>(target);
            if (target.GetComponent<PauseMenu>() == null)
                Undo.AddComponent<PauseMenu>(target);
            if (target.GetComponent<PianoAudio>() == null)
                Undo.AddComponent<PianoAudio>(target);

            Selection.activeGameObject = target;
            EditorGUIUtility.PingObject(target);

            Debug.Log(BuildReadyMessage(), target);
        }

        [MenuItem("Tools/鋼琴 UI/檢查譜面資料夾", false, 21)]
        public static void CheckCharts()
        {
            var game = FindGame();
            string folder = game != null ? game.chartFolder : "../../data/charts";
            string dir = ChartLoader.ResolveFolder(folder);
            var files = ChartLoader.ListCharts(folder);

            if (files.Count == 0)
            {
                Debug.LogWarning(
                    $"[音遊] 找不到任何譜面檔。\n" +
                    $"程式會去這裡找：{dir}\n" +
                    $"先在 Python 那邊產生：\n" +
                    $"  run.py score new <名稱> --type photo\n" +
                    $"  run.py score add <名稱> <照片或記譜檔>\n" +
                    $"  run.py score build <名稱>");
                return;
            }

            var lines = new List<string> { $"[音遊] 在 {dir} 找到 {files.Count} 份譜面：" };
            foreach (var path in files)
            {
                var chart = ChartLoader.Load(path);
                lines.Add(chart == null
                    ? $"  ✗ {Path.GetFileName(path)}（讀不進來）"
                    : $"  ✓ {Path.GetFileName(path)}　{chart.title}　難度 {chart.level}" +
                      $"（{chart.level_name}）　{chart.note_count} 音　" +
                      $"{chart.duration_sec:0.0} 秒　BPM {chart.bpm:0}");
            }
            Debug.Log(string.Join("\n", lines));
        }

        private static string BuildReadyMessage()
        {
            var files = ChartLoader.ListCharts("../../data/charts");
            string chartLine = files.Count > 0
                ? $"找到 {files.Count} 份譜面，會自動用第一份（{Path.GetFileName(files[0])}）。" +
                  "要換曲子就改 Inspector 的「Chart File」。"
                : "⚠ 還沒有任何譜面檔。先在 Python 那邊執行 run.py score build <專案名>。";

            return "[音遊] 裝好了，直接按 Play 就能玩。\n" +
                   chartLine + "\n" +
                   "按 Play 會先出現選曲畫面：↑↓ 選、Enter 開始、F5 重新掃描譜面。\n" +
                   "操作：用滑鼠點琴鍵，或用電腦鍵盤 A W S E D F T G Y H U J K O L P。\n" +
                   "不會彈也沒關係 —— 按 F1 開自動演奏，電腦會自己彈給你看。";
        }

        private static PianoKeyboardUI FindKeyboard()
        {
#if UNITY_2023_1_OR_NEWER
            return Object.FindFirstObjectByType<PianoKeyboardUI>();
#else
            return Object.FindObjectOfType<PianoKeyboardUI>();
#endif
        }

        private static RhythmGameController FindGame()
        {
#if UNITY_2023_1_OR_NEWER
            return Object.FindFirstObjectByType<RhythmGameController>();
#else
            return Object.FindObjectOfType<RhythmGameController>();
#endif
        }
    }
}


