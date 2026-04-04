$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
python -m tools.miru_fetch_limitless --days 90 2>&1 | Tee-Object -FilePath data\limitless_fetch_output.txt
type data\limitless_fetch_output.txt
pause
