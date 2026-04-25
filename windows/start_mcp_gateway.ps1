# start_mcp_gateway.ps1 -- Miru MCP Gateway launcher (port 18766)
# Hidden-window pattern + PID file + secret-prefixed health poll.
# Dot-sources op_miru_common.ps1 for .env loader and HTTP probe helpers.
#
# Usage:
#   .\windows\start_mcp_gateway.ps1          # start if not running
#   .\windows\start_mcp_gateway.ps1 -Force   # kill + restart

param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'op_miru_common.ps1')

# Use logs\ directly, not Get-OpMiruPaths' deprecated data\startup-logs.
$RepoRoot = Split-Path -Parent $ScriptDir
$LogDir   = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Port            = 18766
$GatewayScript   = Join-Path $RepoRoot 'tools\miru_mcp_gateway\server.py'
$PidFile         = Join-Path $LogDir   'mcp_gateway_18766.pid'
$StdoutLog       = Join-Path $LogDir   'mcp_gateway_18766_stdout.log'
$StderrLog       = Join-Path $LogDir   'mcp_gateway_18766_stderr.log'
$StartupLog      = Join-Path $LogDir   'mcp_gateway_startup.log'

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $line = "[$ts] $Message"
    Write-Host "[miru-mcp-gateway] $Message"
    Add-Content -Path $StartupLog -Value $line -ErrorAction SilentlyContinue
}

function Get-PidInfo {
    if (-not (Test-Path $PidFile)) { return $null }
    try {
        $raw = Get-Content $PidFile -Raw -ErrorAction Stop
        return ($raw | ConvertFrom-Json)
    } catch {
        Write-Log "WARNING: PID file unreadable: $_"
        return $null
    }
}

function Test-GatewayProcess {
    param([int]$ProcessId)
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        return ($null -ne $proc -and $proc.ProcessName -match 'python')
    } catch { return $false }
}

function Stop-ExistingGateway {
    $info = Get-PidInfo
    if ($null -ne $info -and $info.pid -gt 0) {
        if (Test-GatewayProcess -ProcessId $info.pid) {
            Write-Log "Stopping existing gateway PID $($info.pid)..."
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

Write-Log "=== Miru MCP Gateway launcher ==="

# Load .env so MIRU_MCP_URL_SECRET / MIRU_FS_ALLOW_ROOT are visible to the child.
$envResult = Import-OpMiruDotEnv -RepoRoot $RepoRoot
if ($envResult.Exists) {
    Write-Log "Loaded .env ($($envResult.LoadedKeys.Count) keys)"
} else {
    Write-Log "ERROR: .env not found at $RepoRoot\.env -- gateway cannot start without MIRU_MCP_URL_SECRET."
    exit 1
}

$secret = [Environment]::GetEnvironmentVariable('MIRU_MCP_URL_SECRET', 'Process')
if ([string]::IsNullOrWhiteSpace($secret)) {
    Write-Log "ERROR: MIRU_MCP_URL_SECRET is not set in .env. Refusing to start."
    Write-Log "Generate with: python -c ""import secrets; print(secrets.token_hex(32))"""
    exit 1
}
if ($secret.Length -lt 32) {
    Write-Log "ERROR: MIRU_MCP_URL_SECRET shorter than 32 chars. Refusing to start."
    exit 1
}

# Default the FS root if unset, to match the gateway's config.py default.
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('MIRU_FS_ALLOW_ROOT', 'Process'))) {
    [Environment]::SetEnvironmentVariable('MIRU_FS_ALLOW_ROOT', 'D:\dev\miru', 'Process')
}

# Path-stripped mode: Tailscale strips /mcp/<SECRET> before forwarding, so the
# gateway's internal route is bare /health. Loopback binding is what keeps this
# reachable only from the local Tailscale process.
$HealthUrl = "http://127.0.0.1:$Port/health"

# Single-instance guard
$pidInfo = Get-PidInfo
if ($null -ne $pidInfo -and $pidInfo.pid -gt 0 -and (Test-GatewayProcess -ProcessId $pidInfo.pid)) {
    if (-not $Force) {
        $probe = Test-OpMiruHttp -Url $HealthUrl -TimeoutSeconds 5 -MustContain '"ok":true'
        if ($probe.Ok) {
            Write-Log "Gateway already healthy on port $Port (PID $($pidInfo.pid)). Use -Force to restart."
            exit 0
        }
        Write-Log "Gateway PID $($pidInfo.pid) is running but NOT healthy. Restarting..."
    }
    Stop-ExistingGateway
} elseif ($Force) {
    Stop-ExistingGateway
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

if (-not (Test-Path $GatewayScript)) {
    Write-Log "ERROR: Gateway script not found at $GatewayScript"
    exit 1
}

# Clear old logs
Set-Content -Path $StdoutLog -Value "" -Encoding UTF8
Set-Content -Path $StderrLog -Value "" -Encoding UTF8

# Start hidden (no console window)
Write-Log "Starting gateway (hidden window) on port $Port..."
$process = Start-Process `
    -FilePath $python.Source `
    -ArgumentList @($GatewayScript) `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError  $StderrLog `
    -WindowStyle Hidden `
    -PassThru

[pscustomobject]@{
    pid         = $process.Id
    port        = $Port
    started_at  = (Get-Date).ToString("s")
    repo_root   = $RepoRoot
    script_path = $GatewayScript
} | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8

Write-Log "Spawned PID $($process.Id). Waiting for health check..."

# Health poll -- FastMCP + Uvicorn is light; 30s is generous.
Start-Sleep -Seconds 2
$prevWebProxy = [System.Net.WebRequest]::DefaultWebProxy
[System.Net.WebRequest]::DefaultWebProxy = $null
try {
    $probe = Wait-OpMiruHttp -Url $HealthUrl -TimeoutSeconds 30 -RetryDelaySeconds 2 -MustContain '"ok":true'
} finally {
    [System.Net.WebRequest]::DefaultWebProxy = $prevWebProxy
}

if ($probe.Ok) {
    Write-Log "Gateway is HEALTHY on port $Port (PID $($process.Id))."
    Write-Log "Health URL: $HealthUrl"
    exit 0
} else {
    Write-Log "FAILED: Gateway did not become healthy within 30s."
    Write-Log "Last probe error: $($probe.Error)"
    if (Test-Path $StderrLog) {
        $tail = Get-Content $StderrLog -Tail 30 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Log "--- stderr tail ---"
            foreach ($line in $tail) { Write-Log "  $line" }
            Write-Log "--- end tail ---"
        }
    }
    if (Test-Path $StdoutLog) {
        $tail = Get-Content $StdoutLog -Tail 30 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Log "--- stdout tail ---"
            foreach ($line in $tail) { Write-Log "  $line" }
            Write-Log "--- end tail ---"
        }
    }
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Log "Cleaned up failed process."
    exit 1
}
