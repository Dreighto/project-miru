@echo off
REM Scheduler-friendly wrapper: overlap mode + run logging (worktree-only).
REM Run from Task Scheduler with "Start in" = this script's directory (worktree root),
REM or run manually from worktree root.
cd /d "%~dp0"
python -m tools.run_worktree_worker --mode overlap --log-run
exit /b %ERRORLEVEL%
