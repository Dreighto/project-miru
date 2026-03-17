@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_op_miru.ps1" -Watchdog
exit /b %errorlevel%
