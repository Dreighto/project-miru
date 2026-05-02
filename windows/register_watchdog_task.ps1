# register_watchdog_task.ps1 -- Register MiruN8nWatchdog scheduled task.
# Runs n8n_loop_watchdog.py every 15 minutes to detect n8n failures,
# silence, and recurring errors. Sends Telegram alerts on state changes.
#
# Usage (elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File windows\register_watchdog_task.ps1
#
# To run manually at any time:
#   powershell -ExecutionPolicy Bypass -Command "& { schtasks /Run /TN 'MiruN8nWatchdog' }"

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python   = "python"
$script   = Join-Path $repoRoot "tools\n8n_loop_watchdog.py"
$logDir   = Join-Path $repoRoot "logs"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$taskName   = "MiruN8nWatchdog"
$taskDesc   = "Monitors n8n workflows for failures, silence, and recurring errors. Sends Telegram alerts."

# Unregister existing task if present
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName"
}

$logFile = Join-Path $logDir "n8n_loop_watchdog_sched.log"

$action = New-ScheduledTaskAction `
    -Execute  "powershell.exe" `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& $python '$script' *>> '$logFile'`""

# Trigger: every 15 minutes, starting at midnight, indefinitely
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At "00:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit    (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable    `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances     IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Limited

Register-ScheduledTask `
    -TaskName   $taskName `
    -Description $taskDesc `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Principal  $principal `
    -Force | Out-Null

Write-Host "Registered: $taskName (every 15 min)"
Write-Host "To run now: schtasks /Run /TN '$taskName'"
Write-Host "To check:   Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
