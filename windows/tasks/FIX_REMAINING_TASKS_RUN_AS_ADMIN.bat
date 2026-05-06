@echo off
:: Self-elevating. Fixes remaining Miru tasks that still run in the interactive session.
:: - MiruN8nWatchdog      -> SYSTEM (session 0, no windows)
:: - MiruDispatchListener -> VBS wrapper (hidden window, stays as Dreighto for node.js access)
:: - MiruRestartMcpGateway -> adds -WindowStyle Hidden
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo Ensuring python_path.txt exists...
for /f "delims=" %%i in ('where python 2^>nul') do (
    set PYPATH=%%i
    goto :found_python
)
:found_python
if defined PYPATH (
    if not exist "D:\dev\miru\data\config" mkdir "D:\dev\miru\data\config"
    echo %PYPATH%> "D:\dev\miru\data\config\python_path.txt"
    echo   Python path saved: %PYPATH%
) else (
    echo   WARNING: Python not found in PATH. SYSTEM tasks will exit 1 if python_path.txt is missing.
)

echo.
echo Switching MiruN8nWatchdog to SYSTEM...
schtasks /Change /TN "MiruN8nWatchdog" /RU "SYSTEM" /RP "" /TR "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"D:\dev\miru\windows\tasks\run_n8n_watchdog.ps1\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Updating MiruDispatchListener to use VBS wrapper...
schtasks /Change /TN "MiruDispatchListener" /TR "wscript.exe \"D:\dev\miru\windows\tasks\run_dispatch_listener.vbs\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Adding -WindowStyle Hidden to MiruRestartMcpGateway...
schtasks /Change /TN "MiruRestartMcpGateway" /TR "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"D:\dev\miru\windows\tasks\restart_mcp_gateway_task.ps1\""
if %errorlevel% equ 0 (echo   OK) else (echo   FAILED exit=%errorlevel%)

echo.
echo Done.
echo   MiruN8nWatchdog  -> SYSTEM (session 0 - no focus stealing possible)
echo   MiruDispatchListener -> VBS wrapper (hidden window)
echo   MiruRestartMcpGateway -> WindowStyle Hidden added
echo.
echo Press any key to close.
pause >nul
