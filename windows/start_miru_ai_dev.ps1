# Start or restart Miru AI / Dev (worktree) on 18765 only. Single-instance; health-verified.
# Use this to bring up only the Dev control surface, or to restart it after a crash.
# Full worktree stack: use start_op_miru_worktree.ps1 -Native instead.
# Do NOT use port 8765.
param(
    [int]$Port = 18765,
    [string]$BindHost = "0.0.0.0",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "op_miru_common.ps1")
. (Join-Path $scriptDir "op_miru_runtime.ps1")

$logDir = Join-Path $repoRoot "data\startup-logs"
$pidFile = Join-Path $logDir "miru_ai_worktree.pid"
$stdoutLog = Join-Path $logDir "miru_ai_worktree_stdout.log"
$stderrLog = Join-Path $logDir "miru_ai_worktree_stderr.log"
$healthUrl = "http://127.0.0.1:$Port/api/health"
$devUrl = "http://127.0.0.1:$Port/dev"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null

function Get-MiruPortConnectionEntries {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        return @(Get-NetTCPConnection -LocalPort $Port -ErrorAction Stop)
    }
    catch {
        return @()
    }
}

function Get-MiruPortListenerEntries {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    return @(
        Get-MiruPortConnectionEntries -Port $Port |
            Where-Object { [string]$_.State -eq "Listen" }
    )
}

if (-not $Force) {
    if (Test-MiruAiDevHealthy -Port $Port) {
        $devOk = Test-MiruAiDevPageReachable -Port $Port
        if ($devOk) {
            Write-Host "Miru AI Dev already healthy on port $Port. No action."
            exit 0
        }
    }

    $resolve = Resolve-MiruPortBeforeStart -Port $Port -ServiceName "Miru AI Dev" -RepoRoot $repoRoot `
        -TestHealthy { Test-MiruAiDevHealthy -Port $Port } `
        -TestProcessCorrect { param($ProcessId, $RepoRoot) Test-MiruProcessIsMiruAiDev -ProcessId $ProcessId -RepoRoot $RepoRoot -ExpectedPort $Port }

    if ($resolve.Action -eq "SkipStart") {
        Write-Host $resolve.Message
        exit 0
    }
    if ($resolve.Action -eq "Error") {
        Write-Host $resolve.Message -ForegroundColor Red
        exit 1
    }
    if ($resolve.PSObject.Properties['Pid'] -and $null -ne $resolve.Pid) {
        Write-Host "Stopping existing process PID $($resolve.Pid) to restart Miru AI Dev."
        Stop-MiruProcessSafely -ProcessId $resolve.Pid -Label "Miru AI Dev"
        Start-Sleep -Seconds 2
    }
}
else {
    $entry = Get-MiruProcessOnPort -Port $Port
    if ($entry) {
        Stop-MiruProcessSafely -ProcessId $entry.Pid -Label "Miru AI Dev"
        Start-Sleep -Seconds 2
    }
}

# --- Port-level cleanup: kill anything bound to 18765 before starting ---
$portKillPids = @()
try {
    $portKillPids = @(
        Get-MiruPortConnectionEntries -Port $Port |
            ForEach-Object { [int]$_.OwningProcess } |
            Where-Object { $_ -and $_ -ne 0 } |
            Sort-Object -Unique
    )
} catch {
    $portKillPids = @()
}

foreach ($pid in $portKillPids) {
    try {
        Write-Host "Port cleanup: stopping PID $pid bound to $Port"
        Stop-Process -Id $pid -Force -ErrorAction Stop
    } catch {
        Write-Host "Port cleanup warning: failed to stop PID $pid bound to $Port via Stop-Process." -ForegroundColor Yellow
    }
}

if (@($portKillPids).Count -gt 0) {
    Start-Sleep -Seconds 2
}

$stillListening = @(Get-MiruPortListenerEntries -Port $Port)
if ($stillListening) {
    throw "Port $Port still occupied after kill attempt. Run elevated PowerShell and manually kill the PID before starting Miru AI."
}

$env:PROJECT_MIRU_PORT = "18080"
$python = Get-Command python -ErrorAction Stop
if ((Test-Path Env:PATH) -and (Test-Path Env:Path)) {
    if ([string]::IsNullOrWhiteSpace($env:Path) -and -not [string]::IsNullOrWhiteSpace($env:PATH)) {
        $env:Path = $env:PATH
    }
    Remove-Item Env:PATH -ErrorAction SilentlyContinue
}
Write-Host "Starting Miru AI Dev on port $Port."
$stillListening = @(Get-MiruPortListenerEntries -Port $Port)
if ($stillListening) {
    throw "Port $Port still occupied. Clear the port before launching Miru AI."
}
$process = Start-Process `
    -FilePath $python.Source `
    -ArgumentList @("-m", "miru_ai.server", "--host", $BindHost, "--port", "$Port") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

[pscustomobject]@{
    pid = $process.Id
    miru_ai_port = $Port
    started_at = (Get-Date).ToString("s")
    repo_root = $repoRoot
} | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8

# With --debug the Flask reloader restarts once; allow time before health checks.
Start-Sleep -Seconds 15

if (-not (Get-Command Wait-OpMiruHttp -ErrorAction SilentlyContinue)) {
    Write-Host "Started PID $($process.Id). Verify manually: $healthUrl"
    exit 0
}

$healthOk = $false
$deadline = (Get-Date).AddSeconds(120)
do {
    if (Test-MiruAiDevHealthy -Port $Port -TimeoutSeconds 5) {
        $healthOk = $true
        break
    }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if (-not $healthOk) {
    Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null
    Write-Host "Miru AI Dev did not become healthy. Check $stderrLog" -ForegroundColor Red
    exit 1
}
$dev = Wait-OpMiruHttp -Url $devUrl -TimeoutSeconds 90 -RetryDelaySeconds 2
if (-not $dev.Ok) {
    Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null
    Write-Host "WARNING: /dev page did not respond within timeout, but /api/health is confirmed healthy. Server is running. Check $stderrLog for details." -ForegroundColor Yellow
}

Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null
$pidRecord = Get-MiruAiDevPidRecord -RepoRoot $repoRoot
$reportedPid = if ($pidRecord -and $null -ne $pidRecord.pid) { [int]$pidRecord.pid } else { [int]$process.Id }

Write-Host "Miru AI Dev is ready on http://127.0.0.1:$Port/ (PID $reportedPid)."
exit 0
