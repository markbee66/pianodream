@echo off
chcp 65001 >nul
title 一次設定 — 讓啟動不再跳安全性警告

rem 這個檔只負責取得系統管理員權限，真正做事的是旁邊的 一次設定.ps1。
rem 加防火牆規則一定要管理員權限，所以下面偵測到沒有時會自己再開一次。
rem
rem 只需要跑一次。跑完之後改用桌面上的「樂譜輸入」捷徑開啟。

net session >nul 2>&1
if not errorlevel 1 goto elevated

echo.
echo   這個設定需要系統管理員權限（要加防火牆規則）。
echo   接下來 Windows 會問一次「是否允許」，按「是」就好。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit

:elevated
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0一次設定.ps1"

echo.
pause
