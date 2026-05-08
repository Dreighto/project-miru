# restart_dispatch_listener_task.ps1
# Called by the "MiruRestartDispatcher" scheduled task (or directly by
# restart_tools.py via service_restart).
# Runs as NAS\NAS Interactive/Limited (not SYSTEM) so it can be triggered
# from non-elevated shells without UAC. See register_restart_tasks.ps1.
# Kills the Node process on port 19100, then re-triggers the
# MiruDispatchListener task which owns the wrapper + respawn loop.
# Logs to logs\dispatch_listener_restart.log.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

try { Add-Type -Name MiruHide -Namespace W32 -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);' -ErrorAction SilentlyContinue } catch {}
try { [W32.MiruHide]::ShowWindow([W32.MiruHide]::GetConsoleWindow(), 0) | Out-Null } catch {}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$logDir   = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logPath = Join-Path $logDir "dispatch_listener_restart.log"
$port    = 19100

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Add-Content -Path $logPath -Value "" -Encoding UTF8
Write-Log "=== MiruRestartDispatcher BEGIN ==="
Write-Log "repo_root=$repoRoot"

# -- Stop the wrapper task first so it doesn't interfere --
Write-Log "Stopping MiruDispatchListener task..."
try {
    Stop-ScheduledTask -TaskName "MiruDispatchListener" -ErrorAction SilentlyContinue
    Write-Log "Task stopped (or was not running)"
} catch {
    Write-Log "WARNING: Stop-ScheduledTask failed: $($_.Exception.Message)"
}
Start-Sleep -Seconds 1

# -- Kill anything on port 19100 (the orphaned Node child) --
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
    Write-Log "ERROR: Port $port still occupied after kill attempt -- aborting"
    Write-Log "=== MiruRestartDispatcher END (failed) ==="
    exit 1
}
Write-Log "Port $port is clear"

# -- Re-trigger the listener task --
Write-Log "Starting MiruDispatchListener task..."
try {
    Start-ScheduledTask -TaskName "MiruDispatchListener"
    Write-Log "Task triggered"
} catch {
    Write-Log "ERROR: Start-ScheduledTask failed: $($_.Exception.Message)"
    Write-Log "=== MiruRestartDispatcher END (failed) ==="
    exit 1
}

# -- Wait for port to appear (up to 30s) --
Write-Log "Waiting for port $port to appear (up to 30s)..."
$deadline    = (Get-Date).AddSeconds(30)
$isListening = $false
do {
    $entries = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($entries.Count -gt 0) { $isListening = $true; break }
    Start-Sleep -Milliseconds 1000
} while ((Get-Date) -lt $deadline)

if ($isListening) {
    $newPid = (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -First 1).OwningProcess
    Write-Log "Dispatch listener is listening on port $port (PID=$newPid) -- restart SUCCESS"
    Write-Log "=== MiruRestartDispatcher END (success) ==="
    exit 0
} else {
    Write-Log "WARNING: Dispatch listener did not start listening within 30s"
    Write-Log "=== MiruRestartDispatcher END (port not detected) ==="
    exit 1
}
