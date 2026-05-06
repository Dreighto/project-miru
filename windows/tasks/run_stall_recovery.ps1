# Wrapper for MiruStallRecovery scheduled task.
# Reads python path from data/config/python_path.txt (written at task setup time)
# so it works under SYSTEM (session 0) where user PATH is unavailable.
$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$configFile = Join-Path $repoRoot "data\config\python_path.txt"

$pythonExe = $null
if (Test-Path $configFile) {
    $pythonExe = (Get-Content $configFile -ErrorAction SilentlyContinue |
                  Where-Object { $_ -and (Test-Path $_) } |
                  Select-Object -First 1)
}
if (-not $pythonExe) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
}
if (-not $pythonExe) { exit 1 }

$pythonw = Join-Path (Split-Path $pythonExe) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $pythonExe }

& $pythonw (Join-Path $repoRoot "tools\orchestrator\recovery_router.py")
