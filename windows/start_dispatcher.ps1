# Miru Task Dispatcher launcher (port 19000)
# Hidden-window pattern + PID file + health poll.
# Dot-sources op_miru_common.ps1 for shared helpers.
#
# Usage:
#   .\windows\start_dispatcher.ps1          # start if not running
#   .\windows\start_dispatcher.ps1 -Force   # kill + restart

param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths (via shared helpers)
# ---------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'op_miru_common.ps1')

$paths = Get-OpMiruPaths -ScriptDirectory $ScriptDir
$RepoRoot = $paths.RepoRoot
$LogDir = $paths.LogDirectory

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Port = 19000
$DispatcherScript = Join-Path $RepoRoot 'dispatcher\task_dispatcher.py'
$PidFile = Join-Path $LogDir 'dispatcher_19000.pid'
$StdoutLog = Join-Path $LogDir 'dispatcher_19000_stdout.log'
$StderrLog = Join-Path $LogDir 'dispatcher_19000_stderr.log'
$StartupLog = Join-Path $ScriptDir 'dispatcher_startup.log'
$HealthUrl = "http://127.0.0.1:${Port}/"

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $line = "[$ts] $Message"
    Write-Host "[miru-dispatcher] $Message"
    Add-Content -Path $StartupLog -Value $line -ErrorAction SilentlyContinue
}

function Get-DispatcherPidInfo {
    if (-not (Test-Path $PidFile)) { return $null }
    try {
        $raw = Get-Content $PidFile -Raw -ErrorAction Stop
        return ($raw | ConvertFrom-Json)
    } catch {
        Write-Log "WARNING: PID file unreadable: $_"
        return $null
    }
}

function Test-DispatcherProcess {
    param([int]$ProcessId)
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        return ($null -ne $proc -and $proc.ProcessName -match 'python')
    } catch { return $false }
}

function Stop-ExistingDispatcher {
    $info = Get-DispatcherPidInfo
    if ($null -ne $info -and $info.pid -gt 0) {
        if (Test-DispatcherProcess -ProcessId $info.pid) {
            Write-Log "Stopping existing dispatcher PID $($info.pid)..."
            try {
                Stop-Process -Id $info.pid -Force -ErrorAction Stop
                Start-Sleep -Seconds 1
                Write-Log "Stopped PID $($info.pid)."
            } catch {
                Write-Log "WARNING: Failed to stop PID $($info.pid): $_"
            }
        } else {
            Write-Log "PID file references dead process $($info.pid); cleaning up."
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # Check if something else holds the port
    $listener = Get-OpMiruListeningEntry -Port $Port
    if ($null -ne $listener) {
        Write-Log "WARNING: Port $Port still occupied by PID $($listener.Pid). Stopping..."
        try {
            Stop-Process -Id $listener.Pid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        } catch {
            Write-Log "WARNING: Could not stop PID $($listener.Pid): $_"
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Log "=== Miru Task Dispatcher launcher ==="

# Load .env so child process inherits Pushover keys
$envResult = Import-OpMiruDotEnv -RepoRoot $RepoRoot
if ($envResult.Exists) {
    Write-Log "Loaded .env ($($envResult.LoadedKeys.Count) keys)"
} else {
    Write-Log "No .env found; relying on process environment."
}

# Single-instance guard
$pidInfo = Get-DispatcherPidInfo
if ($null -ne $pidInfo -and $pidInfo.pid -gt 0 -and (Test-DispatcherProcess -ProcessId $pidInfo.pid)) {
    if (-not $Force) {
        $probe = Test-OpMiruHttp -Url $HealthUrl -TimeoutSeconds 5 -MustContain 'Miru Task Dispatcher'
        if ($probe.Ok) {
            Write-Log "Dispatcher already healthy on port $Port (PID $($pidInfo.pid)). Use -Force to restart."
            exit 0
        }
        Write-Log "Dispatcher PID $($pidInfo.pid) is running but NOT healthy. Restarting..."
    }
    Stop-ExistingDispatcher
} elseif ($Force) {
    Stop-ExistingDispatcher
} else {
    $listener = Get-OpMiruListeningEntry -Port $Port
    if ($null -ne $listener) {
        Write-Log "Port $Port occupied by unknown PID $($listener.Pid). Use -Force to take over."
        exit 1
    }
}

# Resolve Python (venv preferred, system fallback)
$venvActivate = Join-Path $RepoRoot 'venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $venvActivate) {
    Write-Log "Activating venv: $venvActivate"
    . $venvActivate
} else {
    Write-Log "No venv found, using system Python."
}

$python = Get-Command python -ErrorAction Stop
Write-Log "Python: $($python.Source)"

# Clear old logs
Set-Content -Path $StdoutLog -Value "" -Encoding UTF8
Set-Content -Path $StderrLog -Value "" -Encoding UTF8

# Start hidden (no console window)
Write-Log "Starting dispatcher (hidden window)..."
$process = Start-Process `
    -FilePath $python.Source `
    -ArgumentList @($DispatcherScript) `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -WindowStyle Hidden `
    -PassThru

# Write PID file (same schema as PM dashboard)
[pscustomobject]@{
    pid         = $process.Id
    port        = $Port
    started_at  = (Get-Date).ToString("s")
    repo_root   = $RepoRoot
    script_path = $DispatcherScript
} | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8

Write-Log "Spawned PID $($process.Id). Waiting for health check..."

# Health poll — dispatcher is lightweight, 15s is generous
Start-Sleep -Seconds 2
$prevWebProxy = [System.Net.WebRequest]::DefaultWebProxy
[System.Net.WebRequest]::DefaultWebProxy = $null
try {
    $probe = Wait-OpMiruHttp -Url $HealthUrl -TimeoutSeconds 15 -RetryDelaySeconds 2 -MustContain 'Miru Task Dispatcher'
} finally {
    [System.Net.WebRequest]::DefaultWebProxy = $prevWebProxy
}

if ($probe.Ok) {
    Write-Log "Dispatcher is HEALTHY on port $Port (PID $($process.Id))."
    exit 0
} else {
    Write-Log "FAILED: Dispatcher did not become healthy within 15s."
    Write-Log "Last probe error: $($probe.Error)"
    if (Test-Path $StderrLog) {
        $tail = Get-Content $StderrLog -Tail 20 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Log "--- stderr tail ---"
            foreach ($line in $tail) { Write-Log "  $line" }
            Write-Log "--- end tail ---"
        }
    }
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Log "Cleaned up failed process."
    exit 1
}
