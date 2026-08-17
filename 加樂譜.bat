@echo off
chcp 950 >nul

rem 這個檔是用 Big5 存的，而 Big5 有些字的第二個位元組正好是 cmd 的
rem 特殊字元 —— 例如「會」是 B7 7C，7C 就是管線符號 |。
rem 主控台的字碼頁不是 950 時，cmd 會把下面那幾行從中間切成管線指令，
rem 冒出一句「'自動打開...' 不是內部或外部命令」然後整個啟動失敗。
rem 所以先把字碼頁釘回 950，不管是誰、從哪裡叫起來的都一樣。
title Piano AI - Score Input

rem 雙擊這個檔就會開啟樂譜輸入的網頁介面。
rem 不用先開命令列，也不用打任何指令。
rem
rem cd /d "%~dp0" 是切到這個 .bat 所在的資料夾——
rem 從桌面捷徑點開時工作目錄不是專案資料夾，不切會找不到 run.py。

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto noenv

echo.
echo   正在啟動樂譜輸入介面，瀏覽器會自動打開...
echo   用完直接關掉這個視窗就可以。
echo.

".venv\Scripts\python.exe" run.py web
goto done

:noenv
echo.
echo   找不到 Python 環境 .venv
echo   請先照「開始這裡.md」第六節重建環境。
echo.

:done
echo.
pause
