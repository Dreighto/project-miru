$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
python -m tools.run_worktree_worker --mode bulk --snapshot data/snapshots/onepiece_cardgame_dev.json --log-run | Tee-Object -FilePath data\bulk_growth_output.txt
Write-Host "Done. Output saved to data\bulk_growth_output.txt"
pause
