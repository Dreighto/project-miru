# restart_dispatcher_task.ps1
# Called by the "MiruRestartDispatcher" scheduled task.
# Runs as NAS\NAS with RunLevel=Highest (S4U) — elevated in the user's session.
# Stops whatever is on port 19000, then starts a fresh Dispatcher process.
# Logs to logs\dispatcher_restart.log with timestamps.
#
# NOTE ON ELEVATED TOKEN:
#   This task runs with a full admin token (RunLevel=Highest). The spawned
#   Dispatcher process will also be elevated. Direct Stop-Process from a
#   non-elevated interactive shell will fail — which is by design. All restarts
#   must go through Start-ScheduledTask (no UAC needed). The restart wrapper
#   restart_dispatcher.ps1 enforces this.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# Paths: tasks\ -> windows\ -> repo root
$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logPath = Join-Path $logDir "dispatcher_restart.log"
$port    = 19000

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart_dispatcher_task] $Msg"
}

Add-Content -Path $logPath -Value "" -Encoding UTF8
Write-Log "=== MiruRestartDispatcher BEGIN ==="
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

# ── Kill anything on port 19000 ───────────────────────────────────────────────
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

# Verify port is clear
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

# ── Start the Dispatcher ──────────────────────────────────────────────────────
$dispatcherScript  = Join-Path $repoRoot "dispatcher\task_dispatcher.py"
$dispatcherWorkDir = Join-Path $repoRoot "dispatcher"
$stdoutLog         = Join-Path $logDir   "dispatcher_stdout.log"
$stderrLog         = Join-Path $logDir   "dispatcher_stderr.log"

if (-not (Test-Path $dispatcherScript)) {
    Write-Log "ERROR: Dispatcher script not found at $dispatcherScript"
    exit 1
}

Set-Content -Path $stdoutLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
Set-Content -Path $stderrLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

Write-Log "Starting Dispatcher: python $dispatcherScript"
try {
    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @($dispatcherScript) `
        -WorkingDirectory $dispatcherWorkDir `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError  $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    Write-Log "Start-Process returned pid=$($proc.Id)"
} catch {
    Write-Log "ERROR: Start-Process failed: $($_.Exception.Message)"
    exit 1
}

# ── Wait for the port to come up (up to 30s) ─────────────────────────────────
Write-Log "Waiting for port $port to appear (up to 30s)..."
$deadline   = (Get-Date).AddSeconds(30)
$isListening = $false
do {
    $entries = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($entries.Count -gt 0) { $isListening = $true; break }
    Start-Sleep -Milliseconds 750
} while ((Get-Date) -lt $deadline)

if ($isListening) {
    Write-Log "Dispatcher is listening on port $port — restart SUCCESS"
    Write-Log "=== MiruRestartDispatcher END (success) ==="
    exit 0
} else {
    Write-Log "WARNING: Dispatcher did not start listening within 30s — check dispatcher_stderr.log"
    Write-Log "=== MiruRestartDispatcher END (port not detected) ==="
    exit 1
}
