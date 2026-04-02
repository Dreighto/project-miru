Set-Location C:\Users\andre\.codex\worktrees\0814\tcg-watcher
Write-Host "Starting..." | Tee-Object -FilePath data\overlap_log.txt
python -m tools.run_worktree_worker --mode overlap --snapshot data\snapshots\community_cardlist.json --log-run 2>&1 | Tee-Object -FilePath data\overlap_log.txt -Append
Write-Host "Exit code: $LASTEXITCODE" | Tee-Object -FilePath data\overlap_log.txt -Append
pause
