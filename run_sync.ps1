Set-Location C:\Users\andre\.codex\worktrees\0814\tcg-watcher
python -m tools.run_worktree_worker --mode sync_only --log-run 2>&1 | Tee-Object -FilePath data\sync_result.txt
type data\sync_result.txt
pause