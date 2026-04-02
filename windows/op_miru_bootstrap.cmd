@echo off
setlocal
cd /d "%~dp0.."
REM worktree  = full worktree stack (18080 dashboard + 18765 Miru AI) via start_op_miru_worktree.ps1
REM (no arg)  = scheduled startup: Miru AI Dev on 18765 only (worktree intelligence layer)
if /i "%1"=="worktree" (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_op_miru_worktree.ps1" -Native
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_miru_ai_dev.ps1" -Force
)
exit /b %errorlevel%
