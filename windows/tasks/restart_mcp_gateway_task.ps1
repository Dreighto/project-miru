# restart_mcp_gateway_task.ps1
# Called by the "MiruRestartMcpGateway" scheduled task.
# Runs as the current user with RunLevel=Limited (Interactive logon).
# Stops whatever is on port 18766, then starts a fresh MCP Gateway process.
# Logs to logs\mcp_gateway_restart.log with timestamps.
#
# All MCP Gateway restarts must go through Start-ScheduledTask, not direct Stop-Process.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

try { Add-Type -Name MiruHide -Namespace W32 -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);' -ErrorAction SilentlyContinue } catch {}
try { [W32.MiruHide]::ShowWindow([W32.MiruHide]::GetConsoleWindow(), 0) | Out-Null } catch {}

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logPath = Join-Path $logDir "mcp_gateway_restart.log"
$port    = 18766

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart_mcp_gateway_task] $Msg"
}

Add-Content -Path $logPath -Value "" -Encoding UTF8
Write-Log "=== MiruRestartMcpGateway BEGIN ==="
Write-Log "repo_root=$repoRoot"
Write-Log "caller_pid=$PID"

# -- Load .env so MIRU_MCP_URL_SECRET is available to the child gateway process --
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

$secret = [Environment]::GetEnvironmentVariable('MIRU_MCP_URL_SECRET', 'Process')
if ([string]::IsNullOrWhiteSpace($secret) -or $secret.Length -lt 32) {
    Write-Log "ERROR: MIRU_MCP_URL_SECRET missing or too short. Cannot start gateway."
    Write-Log "Add a 64-hex secret to .env: python -c ""import secrets; print(secrets.token_hex(32))"""
    exit 1
}

# -- Kill anything on port 18766 --
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
    exit 1
}
Write-Log "Port $port is clear"

# -- Resolve Python --
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Log "ERROR: python not found on PATH"
    exit 1
}
$pythonExe = $pythonCmd.Source
Write-Log "python=$pythonExe"

# -- Default the FS root if unset --
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('MIRU_FS_ALLOW_ROOT', 'Process'))) {
    [Environment]::SetEnvironmentVariable('MIRU_FS_ALLOW_ROOT', 'D:\dev\miru', 'Process')
}

# -- Start MCP Gateway --
$gatewayScript = Join-Path $repoRoot "tools\miru_mcp_gateway\server.py"
$stdoutLog     = Join-Path $logDir   "mcp_gateway_18766_stdout.log"
$stderrLog     = Join-Path $logDir   "mcp_gateway_18766_stderr.log"
$pidFile       = Join-Path $logDir   "mcp_gateway_18766.pid"

if (-not (Test-Path $gatewayScript)) {
    Write-Log "ERROR: Gateway script not found at $gatewayScript"
    exit 1
}

Set-Content -Path $stdoutLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
Set-Content -Path $stderrLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

Write-Log "Starting MCP Gateway: python $gatewayScript"
try {
    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @($gatewayScript) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError  $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    Write-Log "Start-Process returned pid=$($proc.Id)"

    [pscustomobject]@{
        pid         = $proc.Id
        port        = $port
        started_at  = (Get-Date).ToString("s")
        repo_root   = $repoRoot
        script_path = $gatewayScript
    } | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8
} catch {
    Write-Log "ERROR: Start-Process failed: $($_.Exception.Message)"
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
    Write-Log "MCP Gateway is listening on port $port -- restart SUCCESS"
    Write-Log "Loopback health URL: http://127.0.0.1:$port/health (no secret; loopback-only)"
    Write-Log "Public URL: https://room.taila28611.ts.net/mcp/<SECRET>/health (Tailscale injects prefix)"
    Write-Log "=== MiruRestartMcpGateway END (success) ==="
    exit 0
} else {
    Write-Log "WARNING: Gateway did not start listening within 30s -- check mcp_gateway_18766_stderr.log"
    if (Test-Path $stderrLog) {
        $tail = Get-Content $stderrLog -Tail 20 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Log "--- stderr tail ---"
            foreach ($line in $tail) { Write-Log "  $line" }
            Write-Log "--- end tail ---"
        }
    }
    Write-Log "=== MiruRestartMcpGateway END (port not detected) ==="
    exit 1
}
