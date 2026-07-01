# register_heartbeat_miru_flask_task.ps1
# Registers (or refreshes) the MiruFlaskHeartbeat scheduled task that keeps
# the Miru Flask /api/dev-status cache warm. Pings every 60 seconds via the
# hidden-window VBS wrapper so the operator never sees a console flash.
#
# Run once as the operator (not elevated -- this is a per-user task):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\register_heartbeat_miru_flask_task.ps1
#
# Idempotent: re-running replaces the existing task.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "MiruFlaskHeartbeat"
$VbsPath  = "D:\dev\miru\windows\tasks\run_heartbeat_miru_flask.vbs"

if (-not (Test-Path $VbsPath)) {
    throw "VBS wrapper not found at $VbsPath -- check repo layout"
}

# Replace any existing registration so this script is idempotent.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing $TaskName task"
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30) `
                                    -RepetitionInterval (New-TimeSpan -Seconds 60) `
                                    -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 30) `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Description "Pings /api/dev-status every 60s to keep its in-memory cache warm. No popups." `
                       -Force | Out-Null

Write-Host "Registered $TaskName (60s heartbeat, hidden)"
