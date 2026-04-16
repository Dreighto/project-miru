# restart_pm.ps1 — Project Miru PM Dashboard restart wrapper
# Triggers the MiruRestartPM scheduled task (SYSTEM, highest privilege).
# Does NOT require elevation — Start-ScheduledTask works from any user session.
#
# The actual restart logic lives in:
#   windows\tasks\restart_pm_task.ps1
#
# Progress can be monitored via:
#   Get-Content logs\pm_restart.log -Wait
#   (Get-ScheduledTaskInfo -TaskName "MiruRestartPM").LastTaskResult

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logDir   = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath  = Join-Path $logDir "restart_pm.log"

function Write-LogLine {
    param([string]$Message)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Message"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart-pm] $Message"
}

Set-Content -Path $logPath -Value "" -Encoding UTF8

Write-LogLine "target_surface=PM_18080"
Write-LogLine "action=trigger_scheduled_task"

$task = Get-ScheduledTask -TaskName "MiruRestartPM" -ErrorAction SilentlyContinue
if (-not $task) {
    Write-LogLine "ERROR: Scheduled task 'MiruRestartPM' does not exist."
    Write-LogLine "Run this from an elevated shell to register it:"
    Write-LogLine "  powershell -ExecutionPolicy Bypass -File windows\register_restart_tasks.ps1"
    exit 1
}

Write-LogLine "Starting scheduled task: MiruRestartPM"
Start-ScheduledTask -TaskName "MiruRestartPM"
Write-LogLine "Restart triggered via scheduled task — no UAC required"
Write-LogLine "Monitor: Get-Content logs\pm_restart.log -Wait"

Start-Sleep -Seconds 2
$taskState = (Get-ScheduledTask -TaskName "MiruRestartPM").State
Write-LogLine "task_state_after_trigger=$taskState"
Write-LogLine "RESTART_TRIGGERED"

exit 0
