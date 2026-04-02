Set-Location C:\Users\andre\.codex\worktrees\0814\tcg-watcher
copy data\snapshots\onepiece_cardgame_dev.json data\snapshots\community_cardlist.json
python -m tools.run_worktree_worker --mode overlap --snapshot data\snapshots\community_cardlist.json --log-run | Tee-Object -FilePath data\overlap_output.txt
Write-Host "Done. Check data\overlap_output.txt"
pause
