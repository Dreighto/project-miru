@echo off
:: Self-elevating launcher for fix_dispatch_listener_task.ps1
:: Double-click this file, click Yes on UAC, done.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0fix_dispatch_listener_task.ps1"
if %errorlevel% equ 0 (
    echo.
    echo Done. Press any key to close.
    pause >nul
) else (
    echo.
    echo Something went wrong. Exit code: %errorlevel%
    pause >nul
)
