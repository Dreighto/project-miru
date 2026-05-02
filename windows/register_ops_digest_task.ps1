# register_ops_digest_task.ps1 — Register MiruOpsDigest scheduled task (weekly, Friday 9am).
# Usage: powershell -ExecutionPolicy Bypass -File windows\register_ops_digest_task.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TASK_NAME   = 'MiruOpsDigest'
$SCRIPT_PATH = [System.IO.Path]::GetFullPath("$PSScriptRoot\..\tools\ops_digest.ps1")

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -NonInteractive -File `"$SCRIPT_PATH`""

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At '9:00AM'

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
    Write-Host "[$TASK_NAME] Removed existing task."
}

Register-ScheduledTask `
    -TaskName    $TASK_NAME `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -RunLevel    Limited `
    -Description 'Weekly Miru ops digest posted to Telegram via /api/ops/report' | Out-Null

Write-Host "[$TASK_NAME] Registered. Next run: Friday 9:00 AM."
Write-Host "Script path: $SCRIPT_PATH"
