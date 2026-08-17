using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace RhythmGame
{
    /// <summary>
    /// 一個音符。欄位名稱要跟 Python 產出的 JSON 完全一致，
    /// JsonUtility 是靠名字對應的，改名就會靜靜地讀成 0。
    /// </summary>
    [Serializable]
    public class ChartNote
    {
        public float t;        // 應該被彈下的時間（秒，從曲子開頭算）
        public float d;        // 持續長度（秒）
        public int midi;       // 哪一個琴鍵
        public string hand;    // "R" 右手 / "L" 左手
        public float beat;     // 第幾拍（四分音符為單位）
        public int measure;    // 第幾小節
        public int vel;        // 譜上要求的力度 0-127（沒有強弱記號時是 80 = mf）
        public bool grace;     // 裝飾音。樂譜上時值是 0，這裡給了最短可彈長度
    }

    [Serializable]
    public class ChartMeasure
    {
        public int n;          // 小節編號
        public float t;        // 這一小節開始的秒數
        public float beats;    // 這一小節有幾拍
        // 我們自己辨識得可不可靠。拍數對不上拍號就是 false —— 那一小節的
        // 音符時值認錯了，玩家看到的不是譜上真正寫的東西。
        public bool ok;
    }

    /// <summary>一個小節畫在樂譜照片上的位置（四個角，可能因為拍歪而不是正矩形）。</summary>
    [Serializable]
    public class MeasureBox
    {
        public int n;               // 小節編號
        // 四個角攤平成 8 個數字 [x1,y1,x2,y2,x3,y3,x4,y4]，原圖像素座標。
        // 不用 float[][] 是因為 JsonUtility 反序列化不了巢狀陣列，
        // 而且失敗時不會報錯、只會靜靜給空值。
        public float[] corners;

        public bool IsValid => corners != null && corners.Length >= 8;

        public Vector2 Corner(int i) => new Vector2(corners[i * 2], corners[i * 2 + 1]);
    }

    /// <summary>一頁樂譜照片，以及上面每個小節的位置。檢討畫面圈紅用。</summary>
    [Serializable]
    public class ChartPage
    {
        public string image;        // 相對專案根目錄的路徑
        public MeasureBox[] measures;
    }

    /// <summary>data/charts/<曲名>_lv<N>.json 的內容。</summary>
    [Serializable]
    public class ChartData
    {
        public string title;
        public int level;
        public string level_name;
        public float bpm;
        public float duration_sec;
        public int note_count;
        // 辨識得可靠的小節數。選曲畫面顯示比例，讓使用者知道這份譜能不能信。
        public int reliable_measures;
        public string[] hands;
        public ChartMeasure[] measures;
        public ChartNote[] notes;
        public ChartPage[] pages;   // 沒有照片來源（文字記譜）時是空的
        // 專案根目錄的絕對路徑，由 Python 產譜面時寫進來。`pages[].image` 相對它。
        // 不能用 Application.dataPath 往上推 —— 見 SongSelectUI.ProjectRoot()。
        public string root;

        public bool IsValid => notes != null && notes.Length > 0;

        /// <summary>有沒有樂譜照片可以圈紅。</summary>
        public bool HasPages => pages != null && pages.Length > 0;

        /// <summary>這份譜用到的最低與最高音，用來檢查鍵盤音域夠不夠。</summary>
        public void GetPitchRange(out int low, out int high)
        {
            low = int.MaxValue;
            high = int.MinValue;
            if (notes == null) return;
            foreach (var n in notes)
            {
                if (n.midi < low) low = n.midi;
                if (n.midi > high) high = n.midi;
            }
        }
    }

    /// <summary>從硬碟讀譜面。</summary>
    public static class ChartLoader
    {
        /// <summary>
        /// 把設定的資料夾路徑解析成絕對路徑。
        ///
        /// 相對路徑是相對 Application.dataPath（也就是 音遊/Assets）算的，
        /// 所以預設值 "../../data/charts" 會指到專案根目錄底下的 data/charts ——
        /// 那正是 Python 那邊 run.py score build 的輸出位置，開發時不用複製檔案。
        /// 打包成執行檔之後 dataPath 會變，那時改用 StreamingAssets。
        /// </summary>
        public static string ResolveFolder(string folder)
        {
            if (string.IsNullOrWhiteSpace(folder)) folder = "../../data/charts";

            if (Path.IsPathRooted(folder) && Directory.Exists(folder)) return folder;

            string fromAssets = Path.GetFullPath(Path.Combine(Application.dataPath, folder));
            if (Directory.Exists(fromAssets)) return fromAssets;

            string streaming = Path.Combine(Application.streamingAssetsPath, "charts");
            if (Directory.Exists(streaming)) return streaming;

            return fromAssets; // 回傳期望的位置，讓錯誤訊息講得出「應該放哪」
        }

        /// <summary>列出資料夾裡所有譜面檔，依檔名排序。</summary>
        public static List<string> ListCharts(string folder)
        {
            string dir = ResolveFolder(folder);
            var result = new List<string>();
            if (!Directory.Exists(dir)) return result;

            var files = Directory.GetFiles(dir, "*.json");
            Array.Sort(files, StringComparer.OrdinalIgnoreCase);
            result.AddRange(files);
            return result;
        }

        /// <summary>讀一份譜面。讀不到或格式不對就回 null，並在 Console 說明原因。</summary>
        public static ChartData Load(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                Debug.LogError($"[音遊] 找不到譜面檔：{path}");
                return null;
            }

            try
            {
                string json = File.ReadAllText(path, System.Text.Encoding.UTF8);
                var chart = JsonUtility.FromJson<ChartData>(json);
                if (chart == null || !chart.IsValid)
                {
                    Debug.LogError($"[音遊] 譜面檔沒有任何音符：{path}");
                    return null;
                }

                // Python 那邊已經排好序了，但別人手改過的檔案不保證，
                // 判定邏輯依賴「時間遞增」，所以這裡自己再確保一次。
                Array.Sort(chart.notes, (a, b) => a.t.CompareTo(b.t));
                return chart;
            }
            catch (Exception e)
            {
                Debug.LogError($"[音遊] 讀取譜面失敗：{path}\n{e.Message}");
                return null;
            }
        }

        /// <summary>顯示用的曲名（檔名去掉 _lv1 / _lv2 之類的後綴）。</summary>
        public static string DisplayName(string path)
        {
            string stem = Path.GetFileNameWithoutExtension(path);
            int mark = stem.LastIndexOf("_lv", StringComparison.OrdinalIgnoreCase);
            return mark > 0 ? stem.Substring(0, mark) : stem;
        }
    }
}
