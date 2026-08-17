# 鋼琴演奏評估 AI

用**電子琴 (USB-MIDI)** 彈奏，系統對照樂譜自動分析演奏優劣，給出分維度評分、
具體問題位置與練習建議。

採用 **score-informed（有譜輔助）** 路線：因為事先知道要彈什麼，系統不需要
「盲聽判斷好壞」，只需要精準量測「實際演奏 vs 譜面」的偏差 —— 這是目前
最可靠、也最可解釋的做法。

---

## 快速開始

```powershell
# 1. 產生示範資料（一份標準譜 + 一份刻意彈壞的演奏）
.\.venv\Scripts\python.exe tools\make_demo.py

# 2. 分析
.\.venv\Scripts\python.exe run.py analyze -s data\scores\demo_score.mid -p data\performances\demo_performance.mid

# 3. 把自己的譜弄進來（拍照 / 數字記譜 / 字母記譜）
.\.venv\Scripts\python.exe run.py web
```

## 接上電子琴之後

```powershell
# 1) 查看電子琴有沒有被電腦認到
.\.venv\Scripts\python.exe run.py ports

# 2) 連線測試：彈幾個音 + 踩踏板，確認訊號真的有進來
.\.venv\Scripts\python.exe run.py test

# 錄一段演奏（開始彈就開始錄，停手 4 秒自動結束）
.\.venv\Scripts\python.exe run.py record -o data\performances\take1.mid

# 錄完直接分析（一條龍，最常用）
.\.venv\Scripts\python.exe run.py play -s data\scores\demo_score.mid --json out\take1.json
```

樂譜可以用 **MusicXML**（`.musicxml` / `.xml` / `.mxl`，MuseScore 可匯出）或 **MIDI**，
也可以**拍照**或**打數字/字母記譜**讓系統自己產生 —— 見下面的「樂譜輸入」。

`--level 1` 表示只評右手（練單手時用），不給就是整份樂譜。

---

## 系統架構

```
樂譜輸入（三條路徑，見下一節）
   照片 .jpg/.png/.pdf ─► Gate A 拍攝品質 ─► homr OMR ─┐
   數字記譜 .txt       ─► 語法檢查 ────────────────────┼─► 合併 ─► Gate B 樂理檢查
   字母記譜 .txt       ─► 語法檢查 ────────────────────┘         │
                                                                  ▼
電子琴 (USB-MIDI)                                          樂譜 MusicXML
      │  src/midi_input.py                                        │
      ▼                                                           │
  演奏 MIDI ──┐                                                   │
              ├─► src/align.py      音符級對齊 (parangonar DualDTW)◄┘
              │                      → 配對 / 漏彈 / 多彈
              │         │
              │         ▼
              │   src/features.py    速度曲線估計 + 逐音符偏差
              │         │              attack_deviation / articulation / ioi_ratio
              │         ▼
              │   src/scoring.py     7 個維度 → 0~100 分 + 問題點定位
              │         │
              │         ▼
              └─► src/report.py      終端機報告 / JSON
```

### 檔案說明

| 檔案 | 職責 |
|---|---|
| `run.py` | 命令列入口（`ports` / `record` / `analyze` / `play` / `score` / `web`） |
| `src/midi_input.py` | 電子琴 MIDI 錄製 |
| `src/io_utils.py` | 樂譜與演奏載入、踏板事件抽取 |
| `src/align.py` | 樂譜↔演奏對齊（parangonar，附內建 fallback） |
| `src/features.py` | 速度曲線 + 音符級特徵 |
| `src/scoring.py` | 評分維度與門檻（**要調鬆緊改這裡**） |
| `src/report.py` | 報告輸出與練習建議 |
| `src/score_input/` | 樂譜輸入模組，見下一節 |
| `web/` | 網頁介面（Flask，邏輯全部呼叫 `src/score_input`） |
| `tools/make_demo.py` | 產生含已知錯誤的測試資料 |

---

## 樂譜輸入

解決「每首曲子都要人工打譜」這個使用門檻。三條路徑匯流成同一份 MusicXML，
之後共用所有下游（評分、音遊譜面）。

| 檔案 | 職責 |
|---|---|
| `src/score_input/project.py` | 專案與**順序**管理（三條路徑共用同一份 manifest） |
| `src/score_input/preprocess.py` | 照片整理：找紙 → 透視矯正 → 攤平打光 → 轉正 → 裁到樂譜 |
| `src/score_input/quality.py` | **Gate A**：照片拍攝品質檢查 + 標註圖 |
| `src/score_input/notation.py` | 數字記譜 / 字母記譜的 parser 與語法檢查 |
| `src/score_input/omr_engine.py` | OMR 引擎介面 + homr adapter |
| `src/score_input/merge.py` | 多段 MusicXML 合併與正規化 |
| `src/score_input/musicxml_fix.py` | 修掉辨識引擎產出的違規寫法（會讓 partitura 掛掉） |
| `src/score_input/rules.py` | **記譜規則層**：連結線還原、連音、可彈性檢查；標明「讀到的」與「推算的」 |
| `src/score_input/ocr.py` | 整頁文字辨識一次，曲名 / 速度 / 小節號 / 連音數字共用 |
| `src/score_input/repair.py` | 用「小節拍數一定要對」修回沒標到的三連音與認錯的拍號 |
| `src/score_input/title.py` | 從譜上讀出曲名與作曲者（靠置中判斷，不是查名字） |
| `src/score_input/validate.py` | **Gate B**：辨識後的樂理合法性檢查 |
| `src/score_input/difficulty.py` | 難度分級（1=只有右手、2=雙手） |
| `src/score_input/chart.py` | 產出 Unity 音遊的落下式譜面 JSON |
| `src/score_input/pipeline.py` | 串起全流程，CLI 與網頁共用 |

### 兩道關卡

**Gate A（辨識前）** 判斷「這張照片能不能看」。所有指標都以**五線譜行距**
(interline) 為基準 —— 那才是決定 OMR 成敗的尺度，畫素數不是。行距用垂直方向
黑白 run-length 的眾數估計（OMR 的標準手法）。

| 檢查 | 量法 |
|---|---|
| 解析度 | interline < 7px 退件、< 10px 警告 |
| 模糊 | **先縮放到 interline=10px** 再算 Laplacian 變異數 |
| 傾斜 | 轉一轉找水平投影變異數最大的角度；> 8° 退件 |
| 透視 | 左右三分之一的行距比值 > 1.15 警告 |
| 反光 | 沿著偵測到的譜線走，看有沒有「該有線卻又白又空」的區段 |
| 太暗 | 亮度中位數 < 55 退件 |
| 找不到譜 / 被切到 | 譜表數與橫向覆蓋率 |

**Gate B（辨識後）** 抓「拍得清楚但認錯」，是 Gate A 完全看不到的另一半。
最強的訊號是**每小節時值總和 vs 拍號** —— 漏認一個音、把八分認成四分、
多生一個音符，都會讓某一小節的拍數對不上。這個檢查不需要原譜，
純粹從產物內部就能發現矛盾，能精準指到第幾小節。

### 設計取捨

**模糊度先縮放再量。** raw variance of Laplacian 會隨解析度與對比度浮動，
同一個門檻在不同照片上沒有可比性 —— 這是網路上大部分模糊偵測範例的通病。
以行距正規化後門檻才穩定。

**先量傾斜、轉正、再找譜表。** 反過來的話歪掉的譜永遠找不到譜線，
然後系統會回報一句沒用的「找不到五線譜」，把真正的原因蓋掉。
同理，模糊 / 太小 / 太歪被判定為根本原因時，會抑制掉連帶產生的「找不到五線譜」。

**兩種記譜法共用一套文法。** 只有音高 token 不同（`1 2 3` vs `C D E`），
時值、小節線、和弦、左右手完全一樣 —— 一套 tokenizer 配兩個 pitch resolver。
兩份寫法轉出來的 MusicXML 音符完全相同（有測試驗證）。

**難度只在取用時篩選，MusicXML 永遠是完整的。** 譜只有一份，
不會有「哪一份才是最新的」問題；要加難度 3 只改 `difficulty.py`。

左右手是看 MusicXML 的 staff。辨識引擎有時候認不出連接兩行的括弧，
把整份大譜表壓成一個 staff —— 那樣「難度 1」會直接消失。
`difficulty.py` 會偵測這種情況並用音高把兩手推回來，判準是
**「有沒有同時響、又隔超過一個八度加五度的音」**：單行旋律永遠只有一個音在響
（實測 0.0%），大譜表則是 19.7% 以上。音域寬度不能當判準 —— 實測單行旋律
29–32 半音，而 Alkan 的大譜表也才 32，分不開。

### 練習檢討（彈完後）

評分報告會說「第 12.00 拍那個 F5 沒彈到」，但練琴的人不是用拍數在找位置的。
`src/review.py` 把它翻成「第 8–11 小節沒彈好，看譜上紅框那一段」：

```
評分結果（問題點帶拍數）
    → 換算成小節（partitura 的 beat_map）
    → 依嚴重度把相鄰的弱小節併成連續段落
    → 在原始樂譜照片上圈紅（整頁壓暗、只留要練的那幾小節）
    → 給出重練那一段的指令：run.py play -s 譜 --measures 21-22
```

**小節定位靠譜上自己印的小節號。** 純用像素分不出「單行譜的小節線」與「符桿」
（符桿 3.5 個行距、譜表 4 個，幾何上幾乎一樣），但每份印刷譜都會在每一行左端印
小節號。OCR 讀出來就知道每個系統該有幾個小節，再用小節線細切、切不出來就等分。

三層：**印刷小節號 → 小節線 → 等分**。系統的歸屬永遠正確，只有系統內部的切點
在退化時是內插的。實測〈うまぴょい伝説〉（單行譜、180 小節）從 27 個框變成
**180 個**，而且畫出來的框正好落在譜上標著 50、95、162 的那幾小節。

**小節位置是 `src/score_input/layout.py` 從照片上找出來的** —— 辨識出的 MusicXML
完全沒有座標資訊，只能回頭看影像。作法是偵測小節線：

- 小節線貫穿整個大譜表（實測約 15–16 個行距），符桿只有 3.8 個 ——
  用 6 倍行距的垂直核心做開運算，符桿會被清掉
- 高度門檻**不能寫死**：密集和弦的譜（Alkan 那份）符桿疊起來也有 6–7 個行距。
  改成每個系統各自用「該系統最高那條線」的比例當門檻，
  再加一道頁面層級的下限擋掉「整群都是符桿」的假系統
- 實測 André《小奏鳴曲》第 1 頁 **47 個小節全部定位正確**（8 個系統）

驗證方式是**在已知的小節注入錯誤，看檢討會不會指到那裡** ——
在第 20–24 小節注入漏彈與抖動，檢討回報第 21–22 小節。

### 速度（BPM）偵測

音遊的音符要落在對的時間點、評分要算對節奏，都得知道曲子多快。
但 **homr 完全不輸出速度資訊**（實測三份譜的 `<sound tempo>`、`<metronome>`、
`<words>` 全是 0）。速度其實印在譜上，只是以「圖上的文字」存在，所以用 OCR 去讀
（rapidocr 已隨 homr 裝好，不用另外裝）：

| 來源 | 例子 | 實測 |
|---|---|---|
| 記譜檔指定的 | `BPM=100` | 示範_簡譜 → 100 |
| 樂譜檔內建的 | `<sound tempo>` / `<metronome>` | MuseScore 匯出的譜會有 |
| 譜上的節拍器記號 | `♩ = 96` | 示範_照片 → **96** |
| 譜上的速度術語 | `Moderato ma con moto.` | Andre → **121**；Alkan `Lentement` → **55** |

只掃頁面上方 40% —— 速度標記一定印在曲子開頭，掃全頁只會把指法數字、小節號
一起讀進來當雜訊。節拍器記號只認 `= 數字`，因為音符符頭 OCR 出來很不穩
（可能變成 `J`、`d` 或整個消失）。

**四種都找不到就明講偵測不到，並問使用者** —— 命令列直接問、網頁跳出輸入框。
悄悄套一個猜的預設值，音遊譜面會整個對不上而使用者不知道為什麼。

### 辨識正確率（實測）

`data/examples/測試用樂譜/` 放了兩首有標準答案的公有領域曲子
（[Mutopia Project](https://www.mutopiaproject.org) 的 PDF + 對應 MIDI，
出自同一份 LilyPond 原始碼，所以 MIDI 就是那份 PDF 的正確答案）。

真實的問題是「**手機拍的**譜認得準不準」，但手機照片沒有標準答案。
所以反過來做：`tools/photo_sim.py` 把有標準答案的 PDF **故意弄成照片的樣子**
（取景、透視、傾斜、單側光、失焦、感光顆粒、JPEG），標準答案照樣有效。

```powershell
.\.venv\Scripts\python.exe tools\omr_bench.py                    # 拍攝條件 x 前處理
.\.venv\Scripts\python.exe tools\omr_bench.py --ablate normal    # 前處理每一步值多少
.\.venv\Scripts\python.exe tools\omr_accuracy.py 樂譜.pdf 標準答案.mid   # 單一檔案
```

對齊後配對率（André / Alkan）：

| 拍攝條件 | 直接辨識 | 加上前處理 |
|---|---|---|
| 300 DPI 排版檔 | 94.7% / 100% | **100% / 100%** |
| 拍得很好 | 100% / 100% | **100% / 100%** |
| 隨手拍 | 99.8% / 99.2% | **100% / 100%** |
| 拍得很差 | 13.0% / 10.0% | 31.5% / 3.2% |

**只要照片還堪用，前處理後就是 100%。**「拍得很差」（紙只佔畫面 66%、失焦程度
達 0.38 個行距）兩邊都救不回來 —— 那時五條譜線在進辨識引擎之前就已經糊成一條
灰帶了。

前處理（`src/score_input/preprocess.py`）的關鍵是**透視矯正 + 裁到樂譜**：
homr 一律把輸入縮到寬 1920，所以譜佔畫面多少直接決定譜線在模型眼裡有多大。
消融實驗顯示拿掉透視矯正會掉 10.2 個百分點。

### 拍數修正（`repair.py`）

Gate B 常報「這一小節 2 拍，但拍號 4/4 要求 4 拍」。看起來像認錯了幾十個音，
但把誤差的**分佈**畫出來會發現錯的往往是**一個記號**：

    Alkan   10 個小節全部剛好多 0.5 拍   → 譜上印著三連音，homr 沒有標
    André   33 個小節全部剛好少 2.0 拍   → 第 25 小節起是 Rondo，本來就是 2/4

誤差全部落在同一個值，就不是隨機認錯。所以用「小節拍數一定要對」這條硬規則修回來：

| 曲子 | Gate B 修前 | 修後 | 小節拍數正確率 |
|---|---|---|---|
| Alkan 前奏曲 | 0.30 | **1.00**（完全沒有問題） | 0% → **100%** |
| André 小奏鳴曲 | 0.59 | **0.96** | 40% → **95%** |
| うまぴょい伝説 | 0.99 | **1.00** | 99% → **100%** |

**證明是修對音樂而不是修掉數字**：跟標準答案 MIDI 對時間，
Alkan 的節奏誤差從 RMS 0.103 秒降到 **0.000 秒**；
André 兩個段落分開擬合（曲子中途換速度）也都是 **0.000 秒**。
**兩首有標準答案的曲子現在音高與節奏都完全正確。**

> **順序不能顛倒：先修三連音，再修拍號。** 反過來的話 Alkan 的 +0.5 會被「修」成
> 9/8 —— 小節通通符合拍號、Gate B 不再抱怨，但音符時值還是錯的，
> 問題被蓋掉而不是解決。詳見 `src/score_input/repair.py` 開頭。

> 織體密集的浪漫派樂譜（`data/examples/測試用樂譜/清晰譜1.png`）誤差是散的，
> 沒有可以修的規律，信心 0.36 —— 那是 homr 真正的弱點，修不回來。

### 速度地圖

曲子中途換速度很常見。〈うまぴょい伝説〉四頁上印了 **8 個節拍器記號**，
只取第一個等於整首都用錯速度（曲長會從 4.3 分變成 6.8 分）。

`tempo.marks_on_page()` 掃全頁找出**每一個**記號，用 x 與 y 對到它正上方的那個
小節（一行裡可能有好幾個變速記號，只看 y 會全部塌在一起），
`chart.py` 的 `_Clock` 再**分段積分**算出秒數。
chart JSON 仍然存絕對秒數，所以 Unity 端完全不用改。

### 校正集（8 首，有標準答案）

`data/examples/測試用樂譜/` 收了 8 首 Mutopia 的公有領域樂譜，**刻意一半單行譜**
（單行譜的小節線只有 4 個行距高，跟大譜表的 15–16 是完全不同的狀況）。
辨識正確率 96.5–100%，小節框 6/8 完全對上 —— 對不上的兩首是多樂章長笛作品，
小節號每個樂章會重新從 1 開始，目前的遞增過濾會把重編的部分丟掉。

---

## Unity 落下式音遊

辨識出來的譜可以直接玩。音符從上往下掉、落到 88 鍵鋼琴上、按鍵判定計分。

```powershell
# 1. 先產出譜面
.\.venv\Scripts\python.exe run.py score build 小星星

# 2. Unity 開 音遊/ 專案 → Tools → 鋼琴 UI → 建立落下式音遊到場景 → 按 Play
```

不會彈鋼琴也沒關係：**按 F1 開自動演奏**，電腦會照著譜彈給你看。
這也是確認譜面對不對最快的方式 —— 自動演奏如果出現 Miss，問題就在譜面或判定，不是手殘。

| 檔案 | 職責 |
|---|---|
| `音遊/Assets/Scripts/RhythmGame/ChartData.cs` | 譜面 JSON 的資料模型與載入 |
| `.../RhythmGameController.cs` | 生成、落下、判定、計分、自動演奏 |
| `.../RhythmGameHUD.cs` | 分數、連擊、判定文字、進度條、結算 |
| `.../PianoAudio.cs` | 程式合成的鋼琴音色（不需要音訊檔） |
| `.../Editor/RhythmGameSelfTest.cs` | 不用按 Play 就能驗譜面資料 |

詳細參數看 [音遊/Assets/Scripts/RhythmGame/讀我.md](音遊/Assets/Scripts/RhythmGame/讀我.md)。

**判定線就是琴鍵的上緣** —— 音符底部碰到琴鍵頂端的那一刻就是該按的時候，
不需要另外調一個「判定線高度」的魔術數字。音符放在一個跟鍵盤根物件共用錨點的
同層物件裡，所以音符的 x 座標可以直接沿用 `PianoKey.CenterX`，不必做座標換算。

---

## 評分維度

| 維度 | 量測方式 | 權重 |
|---|---|---|
| 音符正確率 | 對齊後的 配對 ÷ (譜面音數 + 多彈音數) | 30% |
| 節奏穩定度 | 起音偏差的標準差（相對局部平滑速度曲線，容許 rubato） | 20% |
| 速度控制 | 局部每拍秒數的變異係數 | 15% |
| 和弦整齊度 | 譜面同時音的實際起音散佈 (ms) | 12% |
| 力度層次 | velocity 的 p10–p90 幅度 | 13% |
| 圜滑一致性 | 實際音長 ÷ 應有音長 的變異係數 | 5% |
| 左右手平衡 | 右手/左手 velocity 中位數比（理想 1.10–1.45） | 5% |
| 踏板 | CC64 踩放次數（目前只報告，不計分） | — |

門檻全部集中在 `src/scoring.py` 的 `THRESHOLDS`、`HAND_RATIO_IDEAL`、`WEIGHTS`，
可依程度（初學／檢定／演奏級）調整，不用改邏輯。

---

## 兩個設計重點

**1. 節奏偏差是相對「局部速度曲線」，不是相對節拍器。**
系統會先從演奏本身估出一條平滑的速度曲線，再量每個音相對這條曲線的偏差。
這樣自然的 rubato 不會被誤判成節奏不穩，真正被抓到的是「該齊卻不齊」。

**2. 圜滑度量的是手指按鍵長度，不是發聲長度。**
載入演奏時會關掉 partitura 預設的踏板延長（`sustain_pedal_threshold = 128`），
否則踩踏板的演奏會全部被誤判成完美 legato。踏板另外獨立評估。

---

## 驗證方式

`tools/make_demo.py` 會注入 7 種已知錯誤（抖動 25ms、第 5–6 小節趕拍、漏彈 1 音、
多彈 1 音、和弦散開 35ms、左手過響、力度平板且觸鍵偏斷奏），可以直接檢查系統
有沒有抓到。目前實測結果：

| 項目 | 注入值 | 系統量到 |
|---|---|---|
| 起音抖動 | 25 ms | 標準差 34.3 ms |
| 趕拍 | 快 18% | BPM 區間 96–124 |
| 漏彈 | 1 音 | 1 音（定位到第 12 拍 F5）|
| 多彈 | 1 音 | 1 音（第 5.85 秒 D#5）|
| 和弦散開 | 35 ms | 平均 52.9 ms |
| 觸鍵長度 | 0.55–0.75 | 平均比值 0.70 → 偏斷奏 |
| 左右手 | 左手較響 | 比 0.92 → 旋律被伴奏蓋過 |

**對照組**：把標準譜當成完美演奏丟進去，除了「力度層次」（量化 MIDI 力度本來就死板）
之外全部 100 分，確認沒有系統性偏差。

---

## 下一步可以做的

1. **音遊實際玩過一輪並調手感** — 落下速度與判定寬容度目前只驗證過
   「自動演奏 100% 命中」，沒有人真的用手彈過。參數在
   `RhythmGameController` 的 Approach Seconds 與三個判定視窗。
2. **即時回饋** — 換上 `parangonar.OLTWMatcher`（線上對齊），
   彈到哪判到哪，可以做逐音即時提示。
3. **音樂性評分** — 接 [PercePiano](https://github.com/JonghoKimSNU/PercePiano)
   模型輸出 19 個知覺維度（詮釋、音色、樂句感），補上規則式指標量不到的部分。
4. **第二套 OMR 引擎** — `OmrEngine` 介面已經留好，可以加
   [Audiveris](https://github.com/Audiveris/audiveris)（乾淨掃描檔更強），
   兩套結果互相比對當信心指標。
5. **長期追蹤** — 把每次 `--json` 的結果存進資料庫，畫出進步曲線。

---

## 環境

Python 3.11 + 虛擬環境 `.venv`。重建：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install python-rtmidi
.\.venv\Scripts\python.exe -m homr.main --init     # 下載 OMR 模型（約 37MB）
```

核心依賴：[partitura](https://github.com/CPJKU/partitura)（符號音樂處理）、
[parangonar](https://github.com/sildater/parangonar)（樂譜↔演奏對齊）、
[homr](https://github.com/liebharc/homr)（樂譜照片辨識）、
mido + python-rtmidi（MIDI 輸入）、flask（網頁介面）。

### 中文路徑的兩個坑

這個專案放在「桌面\專題\暑假\ai運算」底下，開發時踩到兩個只有在非 UTF-8
語系的 Windows 上才會出現的問題，兩個都已經在程式裡處理掉：

1. **homr 的相依套件 `musicxml` 讀 XSD 時沒指定編碼**，在繁體中文 Windows
   （預設 cp950）會 `UnicodeDecodeError` 直接掛掉。解法是跑子行程時帶
   `PYTHONUTF8=1`（這個環境變數必須在直譯器啟動時就存在，程式裡設來不及）。
2. **`cv2.imread` 讀不了含中文的路徑**，而且錯誤訊息是
   「file format is not supported」，完全看不出真正原因。解法是把圖檔
   先複製到純 ASCII 的暫存路徑再交給 homr，跑完搬回來。
