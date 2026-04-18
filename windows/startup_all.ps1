# startup_all.ps1 — Project Miru full-stack startup script
# Starts all three services in order with delays between them.
# Called by the "OP Miru Startup" scheduled task at boot.
#
# Services started (in order):
#   1. Dispatcher   — port 19000  (dispatcher\task_dispatcher.py)
#   2. PM Dashboard — port 18080  (pm\app.py)
#   3. Miru AI      — port 18765  (python -m miru_ai.server)
#
# IMPORTANT: This script uses Start-Process without -Verb RunAs so that
# child processes inherit the caller's token. When registered under NAS\NAS
# with RunLevel=Limited, all three services run as non-elevated NAS\NAS
# processes. This ensures that subsequent restarts from a non-elevated
# Claude Code shell (also NAS\NAS, non-elevated) can Stop-Process them
# without triggering a UAC credential prompt.

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # don't abort at boot on partial errors

$repoRoot  = Split-Path -Parent $PSScriptRoot   # windows\ -> repo root
$windowsDir = $PSScriptRoot
$logDir    = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$startupLog = Join-Path $logDir "startup.log"

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $startupLog -Value $line -Encoding UTF8
    Write-Host "[startup_all] $Msg"
}

# Separator for this boot's log block
Add-Content -Path $startupLog -Value "" -Encoding UTF8
Write-Log "========================================"
Write-Log "=== startup_all.ps1 BEGIN"
Write-Log "repo_root=$repoRoot"
Write-Log "caller_pid=$PID"

# ── Load .env so child processes inherit API keys ─────────────────────────────
$commonPath = Join-Path $windowsDir "op_miru_common.ps1"
if (Test-Path $commonPath) {
    try {
        . $commonPath
        $envResult = Import-OpMiruDotEnv -RepoRoot $repoRoot
        if ($envResult.Exists) {
            Write-Log "env=loaded ($($envResult.LoadedKeys.Count) keys)"
        } else {
            Write-Log "env=no .env file found"
        }
    } catch {
        Write-Log "WARNING: op_miru_common.ps1 failed to load: $($_.Exception.Message)"
    }
} else {
    Write-Log "WARNING: op_miru_common.ps1 not found at $commonPath"
}

# ── Resolve Python ────────────────────────────────────────────────────────────
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Log "ERROR: python not found on PATH — cannot start any service"
    exit 1
}
$pythonExe = $pythonCmd.Source
Write-Log "python=$pythonExe"

# ── Helper: kill any process listening on a port ──────────────────────────────
function Stop-PortListeners {
    param([int]$Port)
    $pids = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } | Where-Object { $_ -gt 0 } | Sort-Object -Unique
    )
    foreach ($p in $pids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-Log "port=$Port killed_pid=$p"
        } catch {
            Write-Log "port=$Port could_not_kill_pid=$p ($($_.Exception.Message))"
        }
    }
    if ($pids.Count -gt 0) { Start-Sleep -Seconds 1 }
}

# ════════════════════════════════════════════════════════════════════════════════
# 1. DISPATCHER — port 19000
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting Dispatcher (port 19000) ---"
try {
    Stop-PortListeners -Port 19000

    $dispatcherScript  = Join-Path $repoRoot "dispatcher\task_dispatcher.py"
    $dispatcherWorkDir = Join-Path $repoRoot "dispatcher"
    $dispatcherStdout  = Join-Path $logDir   "dispatcher_stdout.log"
    $dispatcherStderr  = Join-Path $logDir   "dispatcher_stderr.log"

    if (-not (Test-Path $dispatcherScript)) {
        Write-Log "ERROR: Dispatcher script not found at $dispatcherScript"
    } else {
        Set-Content -Path $dispatcherStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
        Set-Content -Path $dispatcherStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

        $proc = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($dispatcherScript) `
            -WorkingDirectory $dispatcherWorkDir `
            -RedirectStandardOutput $dispatcherStdout `
            -RedirectStandardError  $dispatcherStderr `
            -WindowStyle Hidden `
            -PassThru

        Write-Log "Dispatcher started pid=$($proc.Id)"
    }
} catch {
    Write-Log "ERROR starting Dispatcher: $($_.Exception.Message)"
}

Write-Log "Waiting 5s before PM Dashboard..."
Start-Sleep -Seconds 5

# ════════════════════════════════════════════════════════════════════════════════
# 2. PM DASHBOARD — port 18080
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting PM Dashboard (port 18080) ---"
try {
    Stop-PortListeners -Port 18080

    $pmScript  = Join-Path $repoRoot "pm\app.py"
    $pmStdout  = Join-Path $logDir   "pm_stdout.log"
    $pmStderr  = Join-Path $logDir   "pm_stderr.log"

    if (-not (Test-Path $pmScript)) {
        Write-Log "ERROR: PM script not found at $pmScript"
    } else {
        Set-Content -Path $pmStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
        Set-Content -Path $pmStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

        $env:PORT                        = "18080"
        $env:PROJECT_MIRU_DASHBOARD_PORT = "18080"
        $env:MIRU_MAIN_RUNTIME_ROOT      = $repoRoot
        $env:MIRU_DASHBOARD_NO_RELOAD    = "1"
        [Environment]::SetEnvironmentVariable("WERKZEUG_SERVER_FD",  $null, "Process")
        [Environment]::SetEnvironmentVariable("WERKZEUG_RUN_MAIN",   $null, "Process")

        $proc = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($pmScript) `
            -WorkingDirectory $repoRoot `
            -RedirectStandardOutput $pmStdout `
            -RedirectStandardError  $pmStderr `
            -WindowStyle Hidden `
            -PassThru

        Write-Log "PM Dashboard started pid=$($proc.Id)"
    }
} catch {
    Write-Log "ERROR starting PM Dashboard: $($_.Exception.Message)"
}

Write-Log "Waiting 5s before Miru AI..."
Start-Sleep -Seconds 5

# ════════════════════════════════════════════════════════════════════════════════
# 3. MIRU AI — port 18765
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting Miru AI (port 18765) ---"
try {
    Stop-PortListeners -Port 18765

    $miruAiStdout = Join-Path $logDir "miru_ai_stdout.log"
    $miruAiStderr = Join-Path $logDir "miru_ai_stderr.log"

    Set-Content -Path $miruAiStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
    Set-Content -Path $miruAiStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "miru_ai.server", "--host", "0.0.0.0", "--port", "18765") `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $miruAiStdout `
        -RedirectStandardError  $miruAiStderr `
        -WindowStyle Hidden `
        -PassThru

    Write-Log "Miru AI started pid=$($proc.Id)"
} catch {
    Write-Log "ERROR starting Miru AI: $($_.Exception.Message)"
}

Write-Log "=== startup_all.ps1 END"
exit 0
