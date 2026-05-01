# install_service_watchdog.ps1 -- register the MiruServiceWatchdog scheduled task.
# Idempotent -- safe to re-run.
#
# Usage:  powershell -ExecutionPolicy Bypass -File windows\install_service_watchdog.ps1
#
# Must be run from an ELEVATED (Administrator) PowerShell window.
# Registers MiruServiceWatchdog to run every 2 minutes as the current user
# (LogonType=Interactive, RunLevel=Limited) -- matches the pattern used by all
# other Miru restart tasks so it can trigger Start-ScheduledTask without UAC.
#
# The task calls windows\tasks\service_watchdog_task.ps1 which:
#   - polls gateway (18766) and dispatch listener (19100) health endpoints
#   - auto-restarts a service via its registered task if down for >= 90s
#   - sends Telegram alerts on restart and recovery

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = $PSScriptRoot
$repoRoot   = Split-Path -Parent $scriptDir
$taskName   = "MiruServiceWatchdog"
$taskScript = Join-Path $scriptDir "tasks\service_watchdog_task.ps1"
$logDir     = Join-Path $repoRoot "logs"
$logFile    = Join-Path $logDir "install_service_watchdog.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Content -Path $logFile -Value "" -Encoding UTF8

$exitCode    = 1
$finalMarker = "INSTALL_FAILED"

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host "[install-service-watchdog] $Msg"
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

try {
    if (-not (Test-IsAdmin)) {
        Write-Error "This script must be run from an elevated (Administrator) PowerShell window."
        exit 1
    }

    Write-Log "task=$taskName"
    Write-Log "script=$taskScript"
    Write-Log "repo_root=$repoRoot"

    if (-not (Test-Path $taskScript)) {
        Write-Error "Watchdog script not found: $taskScript"
        exit 1
    }

    # Remove existing task if present
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Log "existing_task_removed"
    }

    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-Log "registering_as_user=$currentUser"

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$taskScript`"" `
        -WorkingDirectory $repoRoot

    # Fire every 2 minutes starting 1 minute from now, indefinitely
    $startAt = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger `
        -Once -At $startAt `
        -RepetitionInterval (New-TimeSpan -Minutes 2)

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -MultipleInstances IgnoreNew

    $task = Register-ScheduledTask `
        -TaskName    $taskName `
        -Description "Project Miru service watchdog. Polls gateway (18766) and dispatch listener (19100) every 2 min. Auto-restarts on failure, Telegram alerts on restart/recovery." `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -RunLevel    Limited `
        -User        $currentUser `
        -Force

    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Log "task_registered state=$($task.State)"
    if ($taskInfo) {
        Write-Log "next_run=$($taskInfo.NextRunTime)"
    }

    $finalMarker = "INSTALL_OK"
    $exitCode    = 0
} finally {
    Write-Log $finalMarker
    exit $exitCode
}
