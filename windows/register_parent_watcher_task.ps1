# register_parent_watcher_task.ps1 -- Register MiruParentWatcher scheduled task.
# Runs parent_watcher.py every 5 minutes to auto-close parent tickets when
# all sub-tickets are done. Appends results to parent_watcher_runs.jsonl.
#
# Usage (elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File windows\register_parent_watcher_task.ps1
#
# To run manually:
#   schtasks /Run /TN "MiruParentWatcher"

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

# Read python path from config (written by FIX_TASK_SESSIONS setup script)
$pythonPathFile = Join-Path $repoRoot "data\config\python_path.txt"
if (Test-Path $pythonPathFile) {
    $python = (Get-Content $pythonPathFile -Raw).Trim()
} else {
    $python = "python"
}

$script   = Join-Path $repoRoot "tools\parent_watcher.py"
$logDir   = Join-Path $repoRoot "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$taskName = "MiruParentWatcher"
$taskDesc = "Auto-closes parent Linear tickets when all sub-tickets are done. Runs every 5 min."

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName"
}

$logFile = Join-Path $logDir "parent_watcher_sched.log"

$action = New-ScheduledTaskAction `
    -Execute  "powershell.exe" `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command `"& $python '$script' --execute --json --team-id f9d6193c-4572-40a9-b834-c408439f1aa1 *>> '$logFile'`""

$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At "00:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit    (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable    `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances     IgnoreNew

# Run as SYSTEM to avoid focus stealing (CLAUDE.md mandatory approach #1)
$principal = New-ScheduledTaskPrincipal `
    -UserId    "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel  Limited

Register-ScheduledTask `
    -TaskName    $taskName `
    -Description $taskDesc `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Principal   $principal `
    -Force | Out-Null

Write-Host "Registered: $taskName (every 5 min, runs as SYSTEM)"
Write-Host "To run now: schtasks /Run /TN '$taskName'"
Write-Host "To check:   Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
