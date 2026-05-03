# restart_miru_ai_task.ps1
# Called by the "MiruRestartMiruAI" scheduled task.
# Runs as NAS\NAS with RunLevel=Highest (S4U) — elevated in the user's session.
# Stops whatever is on port 18765, then starts a fresh Miru AI server process.
# Logs to logs\miru_ai_restart.log with timestamps.
#
# All Miru AI restarts must go through Start-ScheduledTask, not direct Stop-Process.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

try { Add-Type -Name MiruHide -Namespace W32 -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);' -ErrorAction SilentlyContinue } catch {}
try { [W32.MiruHide]::ShowWindow([W32.MiruHide]::GetConsoleWindow(), 0) | Out-Null } catch {}

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logPath = Join-Path $logDir "miru_ai_restart.log"
$port    = 18765

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart_miru_ai_task] $Msg"
}

Add-Content -Path $logPath -Value "" -Encoding UTF8
Write-Log "=== MiruRestartMiruAI BEGIN ==="
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

# ── Kill anything on port 18765 ───────────────────────────────────────────────
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

# ── Start Miru AI ─────────────────────────────────────────────────────────────
$stdoutLog = Join-Path $logDir "miru_ai_stdout.log"
$stderrLog = Join-Path $logDir "miru_ai_stderr.log"

Set-Content -Path $stdoutLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
Set-Content -Path $stderrLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

$env:PROJECT_MIRU_PORT = "18080"   # Miru AI needs to know the companion PM port

Write-Log "Starting Miru AI: python -m miru_ai.server --host 0.0.0.0 --port $port"
try {
    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "miru_ai.server", "--host", "0.0.0.0", "--port", "$port") `
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

# ── Wait for port to appear (up to 90s — Miru AI imports are heavy) ──────────
Write-Log "Waiting for port $port to appear (up to 90s)..."
$deadline    = (Get-Date).AddSeconds(90)
$isListening = $false
do {
    $entries = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($entries.Count -gt 0) { $isListening = $true; break }
    Start-Sleep -Milliseconds 1000
} while ((Get-Date) -lt $deadline)

if ($isListening) {
    Write-Log "Miru AI is listening on port $port — restart SUCCESS"
    Write-Log "=== MiruRestartMiruAI END (success) ==="
    exit 0
} else {
    Write-Log "WARNING: Miru AI did not start listening within 90s — check miru_ai_stderr.log"
    Write-Log "=== MiruRestartMiruAI END (port not detected) ==="
    exit 1
}
