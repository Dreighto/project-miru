# register_updater_task.ps1 -- Register the MiruWorkerUpdater scheduled task.
# Idempotent -- safe to re-run.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File windows\register_updater_task.ps1
#
# Must be run from an ELEVATED (Administrator) PowerShell window.
# Registers MiruWorkerUpdater to run nightly at 3am as the current user
# (LogonType=S4U, RunLevel=Limited) so it fires without an interactive session.
#
# The task runs tools\update_workers.ps1 which:
#   - updates claude-code, gemini-cli, codex npm globals
#   - updates Ollama via winget
#   - verifies each binary is callable after update
#   - sends one Telegram summary message

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = $PSScriptRoot
$repoRoot   = Split-Path -Parent $scriptDir   # windows\ -> repo root
$taskName   = "MiruWorkerUpdater"
$taskScript = Join-Path $repoRoot "tools\update_workers.ps1"
$logDir     = Join-Path $repoRoot "logs"
$logFile    = Join-Path $logDir "register_updater_task.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Content -Path $logFile -Value "" -Encoding UTF8

$exitCode    = 1
$finalMarker = "REGISTER_FAILED"

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host "[register-updater-task] $Msg"
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
        Write-Error "update_workers.ps1 not found at $taskScript"
        exit 1
    }

    # Remove existing task if present (idempotent)
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Log "existing_task_removed"
    }

    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-Log "registering_as_user=$currentUser"

    $action = New-ScheduledTaskAction `
        -Execute       "powershell.exe" `
        -Argument      "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$taskScript`"" `
        -WorkingDirectory $repoRoot

    # Nightly at 3:00am
    $trigger = New-ScheduledTaskTrigger -Daily -At "03:00"

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -MultipleInstances IgnoreNew

    # S4U: runs without an interactive session (fires at 3am even when no user is logged in).
    # RunLevel=Limited: consistent with all other Miru scheduled tasks.
    $principal = New-ScheduledTaskPrincipal `
        -UserId    $currentUser `
        -LogonType S4U `
        -RunLevel  Limited

    $task = Register-ScheduledTask `
        -TaskName   $taskName `
        -Description "Nightly (3am) update of claude-code, gemini-cli, codex npm globals and Ollama. Verifies binaries callable after update. Sends Telegram summary on completion." `
        -Action     $action `
        -Trigger    $trigger `
        -Settings   $settings `
        -Principal  $principal `
        -Force

    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Log "task_registered state=$($task.State)"
    if ($taskInfo) {
        Write-Log "next_run=$($taskInfo.NextRunTime)"
    }

    $finalMarker = "REGISTER_OK"
    $exitCode    = 0

    Write-Host ""
    Write-Host "=== MiruWorkerUpdater registered ===" -ForegroundColor Cyan
    Write-Host "  Task    : $taskName"
    Write-Host "  Trigger : daily at 03:00 (S4U, RunLevel=Limited)"
    Write-Host "  Script  : $taskScript"
    Write-Host "  User    : $currentUser"
    if ($taskInfo) {
        Write-Host "  NextRun : $($taskInfo.NextRunTime)"
    }
    Write-Host ""
    Write-Host "To run immediately (no elevation needed once registered):" -ForegroundColor White
    Write-Host "  Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Gray
    Write-Host ""
}
finally {
    Write-Log $finalMarker
    exit $exitCode
}
