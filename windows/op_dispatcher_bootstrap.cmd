@echo off
setlocal
cd /d "%~dp0.."
REM Miru Task Dispatcher bootstrap — called by Windows Task Scheduler.
REM Invokes start_dispatcher.ps1; the single-instance guard handles the rest.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_dispatcher.ps1"
exit /b %errorlevel%
