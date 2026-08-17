using System.Collections.Generic;
using System.IO;
using System.Text;
using PianoUI;
using UnityEditor;
using UnityEngine;

namespace RhythmGame.EditorTools
{
    /// <summary>
    /// 自我檢查：不用按 Play 就能確認譜面讀得進來、資料合理、鍵盤涵蓋得到。
    ///
    /// 最容易出問題的是 JsonUtility ——它是靠**欄位名稱**對應 JSON 的，
    /// 名字打錯不會報錯，只會靜靜地全部讀成 0。所以這裡除了「有沒有讀到」，
    /// 還會檢查數值是否合理（時間遞增、音高在鋼琴範圍內、總長對得上）。
    ///
    /// 選單：Tools → 鋼琴 UI → 自我檢查
    /// </summary>
    public static class RhythmGameSelfTest
    {
        [MenuItem("Tools/鋼琴 UI/自我檢查（譜面與資料）", false, 22)]
        public static void RunMenu()
        {
            var report = Run(out bool ok);
            if (ok) Debug.Log(report);
            else Debug.LogError(report);
        }

        /// <summary>batch mode 用的進入點：Unity.exe -executeMethod 會呼叫這個。</summary>
        public static void RunBatch()
        {
            var report = Run(out bool ok);
            Debug.Log(report);
            EditorApplication.Exit(ok ? 0 : 1);
        }

        public static string Run(out bool ok)
        {
            ok = true;
            var log = new StringBuilder();
            log.AppendLine("===== 音遊自我檢查 =====");

            string folder = ChartLoader.ResolveFolder("../../data/charts");
            log.AppendLine($"譜面資料夾：{folder}");

            var files = ChartLoader.ListCharts("../../data/charts");
            if (files.Count == 0)
            {
                log.AppendLine("✗ 找不到任何譜面檔。先執行 run.py score build <專案名>");
                ok = false;
                log.AppendLine("===== 檢查結束 =====");
                return log.ToString();
            }

            log.AppendLine($"找到 {files.Count} 份譜面\n");

            var byTitle = new Dictionary<string, List<ChartData>>();

            foreach (var path in files)
            {
                string file = Path.GetFileName(path);
                var chart = ChartLoader.Load(path);
                if (chart == null)
                {
                    log.AppendLine($"✗ {file}：讀不進來");
                    ok = false;
                    continue;
                }

                bool good = CheckOne(chart, file, log);
                ok &= good;

                if (!byTitle.TryGetValue(chart.title, out var list))
                    byTitle[chart.title] = list = new List<ChartData>();
                list.Add(chart);
            }

            ok &= CheckLevels(byTitle, log);
            ok &= CheckKeyboardCoverage(files, log);

            log.AppendLine();
            log.AppendLine(ok ? "全部通過。" : "有項目不通過，看上面的 ✗。");
            log.AppendLine("===== 檢查結束 =====");
            return log.ToString();
        }

        private static bool CheckOne(ChartData chart, string file, StringBuilder log)
        {
            bool ok = true;
            var problems = new List<string>();

            // JsonUtility 讀不到欄位時不會報錯，只會給 0 —— 所以要主動抓「全都是 0」
            if (chart.notes.Length == 0) problems.Add("沒有音符");
            if (chart.bpm <= 0f) problems.Add($"BPM 是 {chart.bpm}（欄位可能沒對到）");
            if (chart.duration_sec <= 0f) problems.Add($"總長 {chart.duration_sec} 秒");
            if (chart.note_count != chart.notes.Length)
                problems.Add($"note_count {chart.note_count} 與實際音符數 {chart.notes.Length} 不符");

            int zeroDur = 0, badPitch = 0, noHand = 0, noMeasure = 0;
            float prev = float.NegativeInfinity, last = 0f;
            bool monotonic = true;

            foreach (var n in chart.notes)
            {
                if (n.d <= 0f) zeroDur++;
                if (n.midi < 21 || n.midi > 108) badPitch++;
                if (string.IsNullOrEmpty(n.hand)) noHand++;
                if (n.measure <= 0) noMeasure++;
                if (n.t < prev) monotonic = false;
                prev = n.t;
                last = Mathf.Max(last, n.t + n.d);
            }

            if (zeroDur > 0) problems.Add($"{zeroDur} 個音符長度是 0");
            if (badPitch > 0) problems.Add($"{badPitch} 個音高超出鋼琴範圍 21–108");
            if (noHand > 0) problems.Add($"{noHand} 個音符沒有左右手資訊");
            if (noMeasure > 0) problems.Add($"{noMeasure} 個音符沒有小節編號");
            if (!monotonic) problems.Add("音符時間沒有遞增");
            if (Mathf.Abs(last - chart.duration_sec) > 0.05f)
                problems.Add($"最後一個音結束在 {last:0.00} 秒，但總長寫 {chart.duration_sec:0.00} 秒");
            if (chart.measures == null || chart.measures.Length == 0)
                problems.Add("沒有小節資料");

            chart.GetPitchRange(out int low, out int high);
            log.AppendLine(problems.Count == 0
                ? $"✓ {file}　{chart.title}　難度 {chart.level}（{chart.level_name}）" +
                  $"　{chart.notes.Length} 音　{chart.duration_sec:0.0} 秒" +
                  $"　BPM {chart.bpm:0}　音高 {low}–{high}"
                : $"✗ {file}：{string.Join("、", problems)}");

            if (problems.Count > 0) ok = false;
            return ok;
        }

        /// <summary>難度 1 必須是難度 2 的子集，而且只有右手 —— 這是分級的定義。</summary>
        private static bool CheckLevels(Dictionary<string, List<ChartData>> byTitle, StringBuilder log)
        {
            bool ok = true;
            foreach (var pair in byTitle)
            {
                ChartData lv1 = null, lv2 = null;
                foreach (var c in pair.Value)
                {
                    if (c.level == 1) lv1 = c;
                    if (c.level == 2) lv2 = c;
                }
                if (lv1 == null || lv2 == null) continue;

                var problems = new List<string>();
                foreach (var n in lv1.notes)
                    if (n.hand != "R") { problems.Add("難度 1 出現非右手的音"); break; }

                var set = new HashSet<string>();
                foreach (var n in lv2.notes) set.Add($"{n.t:0.0000}|{n.midi}");
                int missing = 0;
                foreach (var n in lv1.notes)
                    if (!set.Contains($"{n.t:0.0000}|{n.midi}")) missing++;
                if (missing > 0) problems.Add($"難度 1 有 {missing} 個音不在難度 2 裡（應該是子集）");
                if (lv1.notes.Length >= lv2.notes.Length)
                    problems.Add($"難度 1 的音數 {lv1.notes.Length} 沒有比難度 2 的 {lv2.notes.Length} 少");

                log.AppendLine(problems.Count == 0
                    ? $"✓ {pair.Key} 難度分級：{lv1.notes.Length} 音（右手）⊂ {lv2.notes.Length} 音（雙手）"
                    : $"✗ {pair.Key} 難度分級：{string.Join("、", problems)}");
                if (problems.Count > 0) ok = false;
            }
            return ok;
        }

        /// <summary>88 鍵鍵盤（MIDI 21–108）能不能涵蓋所有譜面的音。</summary>
        private static bool CheckKeyboardCoverage(List<string> files, StringBuilder log)
        {
            int outside = 0, total = 0;
            foreach (var path in files)
            {
                var chart = ChartLoader.Load(path);
                if (chart == null) continue;
                foreach (var n in chart.notes)
                {
                    total++;
                    if (n.midi < 21 || n.midi > 108) outside++;
                }
            }
            bool ok = outside == 0;
            log.AppendLine(ok
                ? $"✓ 鍵盤涵蓋：{total} 個音全部落在 88 鍵範圍內"
                : $"✗ 鍵盤涵蓋：{outside}/{total} 個音超出 88 鍵，會被略過");
            return ok;
        }
    }
}
