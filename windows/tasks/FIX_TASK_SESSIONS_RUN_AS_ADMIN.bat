@echo off
:: Self-elevating. Converts MiruServiceWatchdog, MiruStallRecovery, and MiruSentinel
:: to run as SYSTEM (session 0 -- completely outside your desktop).
:: This permanently stops focus-stealing from periodic Miru tasks.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo Detecting Python path...
for /f "delims=" %%i in ('where python 2^>nul') do (
    set PYPATH=%%i
    goto :found_python
)
:found_python

if defined PYPATH (
    echo Python found: %PYPATH%
    if not exist "D:\dev\miru\data\config" mkdir "D:\dev\miru\data\config"
    echo %PYPATH%> "D:\dev\miru\data\config\python_path.txt"
    echo Python path saved to data\config\python_path.txt
) else (
    echo WARNING: Python not found in PATH. Stall recovery and sentinel will skip if Python unavailable.
)

echo.
echo Switching MiruServiceWatchdog to SYSTEM...
schtasks /Change /TN "MiruServiceWatchdog" /RU "SYSTEM" /RP "" /TR "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"D:\dev\miru\windows\tasks\service_watchdog_task.ps1\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Switching MiruStallRecovery to SYSTEM...
schtasks /Change /TN "MiruStallRecovery" /RU "SYSTEM" /RP "" /TR "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"D:\dev\miru\windows\tasks\run_stall_recovery.ps1\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Switching MiruSentinel to SYSTEM...
schtasks /Change /TN "MiruSentinel" /RU "SYSTEM" /RP "" /TR "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"D:\dev\miru\windows\tasks\run_sentinel.ps1\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Done. Tasks now run in session 0 - no more focus stealing.
echo Press any key to close.
pause >nul
