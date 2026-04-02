Set-Location C:\Users\andre\.codex\worktrees\0814\tcg-watcher
python -m tools.miru_fetch_limitless --days 90 2>&1 | Tee-Object -FilePath data\limitless_fetch_output.txt
type data\limitless_fetch_output.txt
pause
