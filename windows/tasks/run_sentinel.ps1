# Wrapper for MiruSentinel scheduled task.
# Runs pythonw.exe inside a hidden PowerShell so no console window appears.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command python).Source }
& $pythonw (Join-Path $repoRoot "tools\sentinel\health_check.py")
