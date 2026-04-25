# restart_mcp_gateway.ps1 -- Project Miru MCP Gateway restart wrapper
# Triggers the MiruRestartMcpGateway scheduled task.
# Does NOT require elevation -- Start-ScheduledTask works from any user session
# because the registered task uses Interactive + Limited.
#
# The actual restart logic lives in:
#   windows\tasks\restart_mcp_gateway_task.ps1
#
# Progress can be monitored via:
#   Get-Content logs\mcp_gateway_restart.log -Wait
#   (Get-ScheduledTaskInfo -TaskName "MiruRestartMcpGateway").LastTaskResult

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logDir   = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath  = Join-Path $logDir "restart_mcp_gateway.log"

function Write-LogLine {
    param([string]$Message)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Message"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart-mcp-gateway] $Message"
}

Set-Content -Path $logPath -Value "" -Encoding UTF8

Write-LogLine "target_surface=MCP_GATEWAY_18766"
Write-LogLine "action=trigger_scheduled_task"

$task = Get-ScheduledTask -TaskName "MiruRestartMcpGateway" -ErrorAction SilentlyContinue
if (-not $task) {
    Write-LogLine "ERROR: Scheduled task 'MiruRestartMcpGateway' does not exist."
    Write-LogLine "Run this from an elevated shell to register it:"
    Write-LogLine "  powershell -ExecutionPolicy Bypass -File windows\register_restart_tasks.ps1"
    exit 1
}

Write-LogLine "Starting scheduled task: MiruRestartMcpGateway"
Start-ScheduledTask -TaskName "MiruRestartMcpGateway"
Write-LogLine "Restart triggered via scheduled task -- no UAC required"
Write-LogLine "Monitor: Get-Content logs\mcp_gateway_restart.log -Wait"

Start-Sleep -Seconds 2
$taskState = (Get-ScheduledTask -TaskName "MiruRestartMcpGateway").State
Write-LogLine "task_state_after_trigger=$taskState"
Write-LogLine "RESTART_TRIGGERED"

exit 0
