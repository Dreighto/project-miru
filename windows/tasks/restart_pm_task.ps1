# restart_pm_task.ps1
# Called by the "MiruRestartPM" scheduled task.
# Runs as NAS\NAS with RunLevel=Highest (S4U) — elevated in the user's session.
# Stops whatever is on port 18080, then starts a fresh PM Dashboard process.
# Logs to logs\pm_restart.log with timestamps.
#
# All PM restarts must go through Start-ScheduledTask, not direct Stop-Process.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logPath = Join-Path $logDir "pm_restart.log"
$port    = 18080

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart_pm_task] $Msg"
}

Add-Content -Path $logPath -Value "" -Encoding UTF8
Write-Log "=== MiruRestartPM BEGIN ==="
Write-Log "repo_root=$repoRoot"
Write-Log "caller_pid=$PID"

# ── Load .env ─────────────────────────────────────────────────────────────────
$commonPath = Join-Path $windowsDir "op_miru_common.ps1"
if (Test-Path $commonPath) {
    try {
        . $commonPath
        $envResult = Import-OpMiruDotEnv -RepoRoot $repoRoot
        Write-Log "env=loaded ($($envResult.LoadedKeys.Count) keys)"
    } catch {
        Write-Log "WARNING: op_miru_common.ps1 load failed: $($_.Exception.Message)"
    }
}

# ── Kill anything on port 18080 ───────────────────────────────────────────────
Write-Log "Checking for existing listener on port $port..."
$listenerPids = @(
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object { [int]$_.OwningProcess } |
        Where-Object { $_ -gt 0 } |
        Sort-Object -Unique
)

if ($listenerPids.Count -eq 0) {
    Write-Log "No process found on port $port"
} else {
    foreach ($p in $listenerPids) {
        Write-Log "Stopping PID $p on port $port"
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-Log "Stopped PID $p"
        } catch {
            Write-Log "WARNING: Failed to stop PID $p : $($_.Exception.Message)"
        }
    }
    Write-Log "Waiting 2s for port to clear..."
    Start-Sleep -Seconds 2
}

$remaining = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
if ($remaining.Count -gt 0) {
    Write-Log "ERROR: Port $port still occupied after kill attempt — aborting"
    exit 1
}
Write-Log "Port $port is clear"

# ── Resolve Python ────────────────────────────────────────────────────────────
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Log "ERROR: python not found on PATH"
    exit 1
}
$pythonExe = $pythonCmd.Source
Write-Log "python=$pythonExe"

# ── Start PM Dashboard ────────────────────────────────────────────────────────
$pmScript  = Join-Path $repoRoot "pm\app.py"
$stdoutLog = Join-Path $logDir   "pm_stdout.log"
$stderrLog = Join-Path $logDir   "pm_stderr.log"

if (-not (Test-Path $pmScript)) {
    Write-Log "ERROR: PM script not found at $pmScript"
    exit 1
}

Set-Content -Path $stdoutLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
Set-Content -Path $stderrLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

# Set PM-specific environment variables (inherited by child process via Start-Process)
$env:PORT                        = "$port"
$env:PROJECT_MIRU_DASHBOARD_PORT = "$port"
$env:MIRU_MAIN_RUNTIME_ROOT      = $repoRoot
$env:MIRU_DASHBOARD_NO_RELOAD    = "1"
[Environment]::SetEnvironmentVariable("WERKZEUG_SERVER_FD",  $null, "Process")
[Environment]::SetEnvironmentVariable("WERKZEUG_RUN_MAIN",   $null, "Process")

Write-Log "Starting PM Dashboard: python $pmScript"
try {
    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @($pmScript) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError  $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    Write-Log "Start-Process returned pid=$($proc.Id)"
} catch {
    Write-Log "ERROR: Start-Process failed: $($_.Exception.Message)"
    exit 1
}

# ── Wait for port to appear (up to 60s — PM is slower to start) ──────────────
Write-Log "Waiting for port $port to appear (up to 60s)..."
$deadline    = (Get-Date).AddSeconds(60)
$isListening = $false
do {
    $entries = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($entries.Count -gt 0) { $isListening = $true; break }
    Start-Sleep -Milliseconds 1000
} while ((Get-Date) -lt $deadline)

if ($isListening) {
    Write-Log "PM Dashboard is listening on port $port — restart SUCCESS"
    Write-Log "=== MiruRestartPM END (success) ==="
    exit 0
} else {
    Write-Log "WARNING: PM Dashboard did not start listening within 60s — check pm_stderr.log"
    Write-Log "=== MiruRestartPM END (port not detected) ==="
    exit 1
}
