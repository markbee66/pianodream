# 一次性設定：把「每次啟動都要按一次信任 / 允許」的來源一次處理掉。
#
# 由同資料夾的「一次設定.bat」呼叫（那個 .bat 會先要求系統管理員權限）。
# 直接執行也可以，只是沒有管理員權限時防火牆那一段會被跳過。
#
# 三件事：
#   1. 解除封鎖    啟動用的檔案如果帶著「從網路下載」的標記，執行時會被攔下來問
#   2. 防火牆      先幫 Python 與 Audiveris 加好放行規則，就不會再跳「允許存取」
#   3. 本機啟動器  在本機磁碟做一個啟動器 + 桌面捷徑，繞開雲端磁碟的所有檢查

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot     # 這個檔跟 加樂譜.bat 放在同一層

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Head([string]$text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }

$isAdmin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host ""
Say "樂譜輸入 —— 一次性設定" "White"
Say "專案位置：$root"
Say "系統管理員權限：$(if ($isAdmin) { '有' } else { '沒有（防火牆那一段會跳過）' })"

# ---------------------------------------------------------------------------
Head "1 / 3　解除封鎖啟動用的檔案"

# 「從網路下載」的標記存在 NTFS 的交替資料流裡。Google Drive 的磁碟是 FAT32，
# 根本存不了這種標記，所以在 G: 上跑這一段一定是零筆 —— 那不是失敗，
# 是這台機器上本來就不可能有這個問題。專案搬到 C: 之後這段才有意義。
#
# **只檢查會被雙擊的那幾個檔，不要整個資料夾遞迴掃。** .venv 與 data\projects
# 加起來是好幾萬個檔案又放在雲端磁碟上，遞迴掃要跑好幾分鐘，
# 而那些檔案本來就不是被 ShellExecute 的對象，掃了也沒有意義。
$candidates = @(
    (Join-Path $root "加樂譜.bat"),
    (Join-Path $root "一次設定.bat"),
    (Join-Path $root "一次設定.ps1"),
    (Join-Path $root "run.py")
)

# **先看檔案系統，不是直接去讀資料流。** 在 FAT32 / exFAT 上
# `Get-Item -Stream` 會丟 Win32Exception「參數錯誤」，而且 -ErrorAction
# SilentlyContinue 壓不住它（那是 provider 層丟出來的），畫面上會噴一整排
# 紅字 —— 使用者會以為設定失敗了，其實只是這個磁碟不支援而已。
$drive = $root.Substring(0, 2)
$fs = (Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'" `
        -ErrorAction SilentlyContinue).FileSystem

if ($fs -and $fs -ne "NTFS") {
    Say "  $drive 是 $fs，存不了「從網路下載」的標記，這一項不適用。"
} else {
    $unblocked = 0
    foreach ($f in $candidates) {
        if (-not (Test-Path $f)) { continue }
        try {
            if (Get-Item $f -Stream Zone.Identifier -ErrorAction Stop) {
                Unblock-File -Path $f -ErrorAction SilentlyContinue
                $unblocked++
                Say "  已解除封鎖：$f" "Green"
            }
        } catch { }   # 沒有那個資料流就是沒被封鎖，正常情況
    }
    if ($unblocked -eq 0) { Say "  沒有任何檔案帶著封鎖標記。" }
}

# ---------------------------------------------------------------------------
Head "2 / 3　防火牆放行規則"

# 會跳「Windows 安全性警訊 —— 允許存取」是因為程式開了 socket 而防火牆
# 還沒有對應的規則。把視窗按 X 關掉不會產生任何規則，所以下次還是會問 ——
# 這就是「每次都要按一次」的成因。先建好規則就不會再問。
$targets = @()

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $targets += @{ Name = "Piano AI - Python (venv)"; Path = $venvPy }
    # .venv\Scripts\python.exe 只是個轉接器，真正在監聽的是底層那支直譯器，
    # 而防火牆規則比對的是實際行程的映像路徑，所以兩個都要加。
    $base = & $venvPy -c "import sys; print(getattr(sys, '_base_executable', sys.executable))" 2>$null
    if ($base -and (Test-Path $base) -and $base -ne $venvPy) {
        $targets += @{ Name = "Piano AI - Python (base)"; Path = $base }
    }

    # **上面那個還不夠。** 這台機器的 Python 是從 Microsoft Store 裝的，
    # sys._base_executable 回傳的是 WindowsApps 底下的「執行別名」：
    #   …\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_…\python.exe
    # 但防火牆比對的是行程真正的映像路徑：
    #   C:\Program Files\WindowsApps\…_3.11.2544.0_x64__…\python3.11.exe
    # 只加別名等於沒加 —— 現有那兩條 Query User 殘規則指的正是後者。
    # 問行程自己（GetModuleFileNameW）拿到的才是真的。
    $probe = 'import ctypes; b = ctypes.create_unicode_buffer(32768); ' +
             'ctypes.windll.kernel32.GetModuleFileNameW(None, b, 32768); print(b.value)'
    $image = & $venvPy -c $probe 2>$null
    if ($image -and (Test-Path $image) -and $image -ne $base -and $image -ne $venvPy) {
        $targets += @{ Name = "Piano AI - Python (image)"; Path = $image }
        # 這個路徑含版本號，Store 更新 Python 後會換資料夾、舊規則就對不上 ——
        # 哪天又開始跳，重跑一次這個設定即可。
    }
} else {
    Say "  找不到 $venvPy —— 先照「開始這裡.md」建好 .venv 再跑這個。" "Yellow"
}

# Audiveris 是第二個辨識引擎，每次辨識照片都會被叫起來，而且沒有簽章。
#
# **安裝位置直接問專案自己的 `audiveris._default_home()`，不要在這裡再寫一份清單。**
# 那個函式認 AUDIVERIS_HOME 環境變數，也認三個常見的安裝位置。各寫各的話，
# 換一台機器就會出現「OMR 找得到 Audiveris、但設定腳本沒幫它開防火牆」——
# 那正是「這個設定只在原作者的電腦上有效」的典型成因。
if (Test-Path $venvPy) {
    # python 的字串一律用單引號。PowerShell 把參數交給原生執行檔時，
    # 字串裡的雙引號會被吃掉 —— 結果 python 收到的是一段壞掉的程式碼，
    # 靜靜地什麼都不回傳，看起來就像「這台機器沒裝 Audiveris」。
    $findHome = 'import sys; sys.path.insert(0, r' + "'" + $root + "'" + '); ' +
                'from src.score_input.audiveris import _default_home; ' +
                'h = _default_home(); print(h if h else str())'
    $audHome = & $venvPy -c $findHome 2>$null
    if ($audHome -and (Test-Path $audHome)) {
        foreach ($exe in @("runtime\bin\java.exe", "runtime\bin\javaw.exe")) {
            $full = Join-Path $audHome $exe
            if (Test-Path $full) { $targets += @{ Name = "Piano AI - Audiveris ($exe)"; Path = $full } }
        }
        Say "  Audiveris 安裝在：$audHome"
    } else {
        Say "  這台機器沒裝 Audiveris（或還沒設 AUDIVERIS_HOME），跳過它的規則。"
    }
}

if (-not $isAdmin) {
    Say "  沒有管理員權限，跳過這一段。" "Yellow"
    Say "  要做這一段，請改用「一次設定.bat」執行（它會自己要權限）。" "Yellow"
    if ($targets.Count) {
        Say "  找到了這些需要放行的程式："
        foreach ($t in $targets) { Say "    $($t.Path)" }
    }
} elseif ($targets.Count -eq 0) {
    Say "  沒有找到需要放行的程式。" "Yellow"
} else {
    foreach ($t in $targets) {
        # 先刪掉同名舊規則，避免重複執行時越堆越多
        netsh advfirewall firewall delete rule name="$($t.Name)" 2>&1 | Out-Null
        foreach ($proto in @("TCP", "UDP")) {
            netsh advfirewall firewall add rule name="$($t.Name)" dir=in action=allow `
                program="$($t.Path)" protocol=$proto profile=any enable=yes 2>&1 | Out-Null
        }
        Say "  已放行：$($t.Path)" "Green"
    }

    # 以前把對話框按掉時留下的 Query User 規則會蓋過上面新加的放行規則，
    # 留著會讓問題看起來沒解決。
    $removed = 0
    foreach ($rule in (Get-NetFirewallRule -ErrorAction SilentlyContinue |
                       Where-Object { $_.DisplayName -like "*Query User*" })) {
        $app = $rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        if ($app.Program -and ($app.Program -match "python" -or $app.Program -match "audiveris")) {
            Remove-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
            $removed++
        }
    }
    if ($removed) { Say "  清掉 $removed 條以前按掉對話框留下的殘規則。" "Green" }
}

# ---------------------------------------------------------------------------
Head "3 / 3　本機啟動器與桌面捷徑"

# Windows 的「開啟檔案 - 安全性警告」與 SmartScreen 檢查的是「被雙擊的那個檔案」。
# 專案放在雲端磁碟上時那個檔案就是檢查對象；改成雙擊一個放在本機磁碟的小啟動器，
# 由它去呼叫專案裡的 加樂譜.bat（cmd 內部呼叫不經過 ShellExecute），
# 整條路徑就都不會再被檢查。
$projBat = Join-Path $root "加樂譜.bat"

if (-not (Test-Path $projBat)) {
    Say "  找不到 $projBat，跳過。" "Yellow"
} else {
    # 捷徑指向 cmd.exe，把 加樂譜.bat 當參數傳進去 —— **不要直接指向那個 .bat**。
    #
    # 兩個理由：
    #   1. 被檢查的是捷徑的目標。cmd.exe 在 System32、有微軟簽章，永遠不會被攔；
    #      直接指向雲端磁碟上的 .bat 就會變成被檢查的對象。
    #   2. 不用另外產生一個中間的 .bat。中間檔要把中文路徑寫進批次檔內容裡，
    #      而批次檔是由 cmd 用系統的 OEM 字碼頁逐位元組讀的 ——
    #      寫錯編碼路徑就整條變成問號，檔案直接不能用（這裡踩過一次）。
    #      .lnk 的參數欄位存的是 UTF-16，沒有這個問題。
    $desktop = [Environment]::GetFolderPath("Desktop")
    $link = Join-Path $desktop "樂譜輸入.lnk"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($link)
        $sc.TargetPath = Join-Path $env:SystemRoot "System32\cmd.exe"
        $sc.Arguments = '/c "' + $projBat + '"'
        $sc.WorkingDirectory = $root
        $sc.Description = "開啟樂譜輸入的網頁介面"
        $sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,41"
        $sc.Save()
        Say "  桌面捷徑：$link" "Green"
        Say "  （指向 cmd.exe，由它去跑 $projBat）"
    } catch {
        Say "  桌面捷徑建立失敗：$($_.Exception.Message)" "Yellow"
    }

    # 早期版本會在 %LOCALAPPDATA%\PianoAI 產一個中間啟動器，而且那個檔的
    # 中文路徑被寫壞了。留著只會讓人點到壞的那個，清掉。
    $stale = Join-Path $env:LOCALAPPDATA "PianoAI\start_score_input.bat"
    if (Test-Path $stale) {
        Remove-Item $stale -Force -ErrorAction SilentlyContinue
        Say "  清掉了舊版留下的中間啟動器。"
    }
}

Write-Host ""
Say "完成。以後請從桌面的「樂譜輸入」開始。" "White"
Say "如果之後還是有視窗跳出來，把它的<標題列文字>記下來 —— 不同的視窗解法不一樣。"
Write-Host ""
