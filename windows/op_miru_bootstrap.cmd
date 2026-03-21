@echo off
setlocal
cd /d "%~dp0.."
REM First argument "worktree" = start worktree stack (18080 + 18765). Otherwise legacy main stack (8080 + 8765).
if /i "%1"=="worktree" (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_op_miru_worktree.ps1" -Native
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_op_miru.ps1" -Watchdog
)
exit /b %errorlevel%
