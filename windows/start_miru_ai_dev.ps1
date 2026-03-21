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

$env:PROJECT_MIRU_PORT = "18080"
$python = Get-Command python -ErrorAction Stop
Write-Host "Starting Miru AI Dev on port $Port."
$process = Start-Process `
    -FilePath $python.Source `
    -ArgumentList @("tools\miru_ai_server.py", "--host", $BindHost, "--port", "$Port", "--debug") `
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
$dev = Wait-OpMiruHttp -Url $devUrl -TimeoutSeconds 45 -RetryDelaySeconds 2
if (-not $dev.Ok) {
    Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null
    Write-Host "Miru AI Dev page did not become reachable. Check $stderrLog" -ForegroundColor Red
    exit 1
}

Repair-MiruAiDevPidState -RepoRoot $repoRoot -Port $Port | Out-Null
$pidRecord = Get-MiruAiDevPidRecord -RepoRoot $repoRoot
$reportedPid = if ($pidRecord -and $null -ne $pidRecord.pid) { [int]$pidRecord.pid } else { [int]$process.Id }

Write-Host "Miru AI Dev is ready on http://127.0.0.1:$Port/ (PID $reportedPid)."
exit 0
