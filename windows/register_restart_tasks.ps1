# register_restart_tasks.ps1
# Run this ONCE from an ELEVATED (Administrator) PowerShell window.
# Registers the Project Miru scheduled tasks:
#
#   1. OP Miru Startup        -- boot trigger, NAS\NAS S4U Limited, runs startup_all.ps1
#   2. MiruRestartMcpGateway  -- on-demand,    NAS\NAS Interactive Limited, port 18766
#
# WHY Interactive + Limited for the restart task:
#   RunLevel=Highest tasks (even NAS\NAS-owned) cannot be triggered by a
#   non-elevated shell -- Windows blocks the Start-ScheduledTask call.
#   RunLevel=Limited + LogonType=Interactive tasks owned by NAS\NAS CAN be
#   triggered from a non-elevated NAS\NAS shell without any UAC prompt.
#
# WHY S4U + Limited for the startup task:
#   The startup task fires at boot before the user is logged in, so it cannot
#   use Interactive logon. S4U allows it to run without a stored password.
#   Limited means the python tasks it self-registers run non-elevated, so
#   Claude Code (also non-elevated) can manage them without UAC.
#
# After registration, non-elevated code (Claude Code, Cursor shell) triggers
# the gateway restart via:
#   Start-ScheduledTask -TaskName "MiruRestartMcpGateway"
# ...with no UAC prompt.
#
# Scope note (2026-05-11):
#   This script previously registered five tasks. The three Miru-app restart
#   tasks (MiruRestartDispatcher / MiruRestartPM / MiruRestartMiruAI for ports
#   19000 / 18080 / 18765 respectively) were removed because those services
#   were decommissioned in the same change. Their launcher scripts and
#   scheduled-task helpers were deleted along with them.
#
#   If those task registrations exist locally from a previous run, unregister:
#     Unregister-ScheduledTask -TaskName "MiruRestartDispatcher" -Confirm:$false
#     Unregister-ScheduledTask -TaskName "MiruRestartPM"         -Confirm:$false
#     Unregister-ScheduledTask -TaskName "MiruRestartMiruAI"     -Confirm:$false

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Admin check ───────────────────────────────────────────────────────────────
$currentIdentity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run from an elevated (Administrator) PowerShell window."
    exit 1
}

$repoRoot   = Split-Path -Parent $PSScriptRoot     # windows\ -> repo root
$windowsDir = $PSScriptRoot
$tasksDir   = Join-Path $windowsDir "tasks"

Write-Host ""
Write-Host "=== Project Miru Scheduled Task Registration ===" -ForegroundColor Cyan
Write-Host "repo_root : $repoRoot"
Write-Host "tasks_dir : $tasksDir"
Write-Host ""

# ── Common task settings ──────────────────────────────────────────────────────
$commonSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

$userId = "$($env:UserDomain)\$($env:UserName)"

# ════════════════════════════════════════════════════════════════════════════════
# TASK 1 — OP Miru Startup
# Boot trigger for startup_all.ps1. After the 2026-05-11 cleanup,
# startup_all.ps1 self-registers MiruServiceWatchdog / MiruStallRecovery /
# MiruSentinel / MiruBackup and does not launch any service directly. The
# task NAME is retained for operator continuity.
# ════════════════════════════════════════════════════════════════════════════════
Write-Host "Registering: OP Miru Startup..." -ForegroundColor Yellow

$startupScript = Join-Path $windowsDir "startup_all.ps1"
if (-not (Test-Path $startupScript)) {
    Write-Error "startup_all.ps1 not found at $startupScript"
}

$startupAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$startupScript`"" `
    -WorkingDirectory $repoRoot

$startupTrigger       = New-ScheduledTaskTrigger -AtStartup
$startupTrigger.Delay = "PT30S"     # 30-second delay after boot

$startupPrincipal = New-ScheduledTaskPrincipal `
    -UserId    $userId `
    -LogonType S4U `
    -RunLevel  Limited

$startupTask = Register-ScheduledTask `
    -TaskName   "OP Miru Startup" `
    -Action     $startupAction `
    -Trigger    $startupTrigger `
    -Settings   $commonSettings `
    -Principal  $startupPrincipal `
    -Description "Loads .env and self-registers watchdog/sentinel/backup tasks 30s after Windows boot. Managed by $startupScript" `
    -Force

Write-Host "  OK: '$($startupTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (S4U, RunLevel=Limited)"
Write-Host "      Trigger   : AtStartup + 30s delay"
Write-Host "      Script    : $startupScript"
Write-Host ""

# ════════════════════════════════════════════════════════════════════════════════
# TASK 2 — MiruRestartMcpGateway (port 18766)
# On-demand restart. Triggered by:
#   Start-ScheduledTask -TaskName "MiruRestartMcpGateway"
# ════════════════════════════════════════════════════════════════════════════════
Write-Host "Registering: MiruRestartMcpGateway..." -ForegroundColor Yellow

$mcpGatewayTaskScript = Join-Path $tasksDir "restart_mcp_gateway_task.ps1"
if (-not (Test-Path $mcpGatewayTaskScript)) {
    Write-Error "restart_mcp_gateway_task.ps1 not found at $mcpGatewayTaskScript"
}

$restartMcpGatewayAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$mcpGatewayTaskScript`"" `
    -WorkingDirectory $repoRoot

# No automatic trigger — on-demand only via Start-ScheduledTask
$restartTrigger = New-ScheduledTaskTrigger -Once -At "2000-01-01T00:00:00"

# Interactive + Limited: non-elevated user can trigger without UAC
$restartPrincipal = New-ScheduledTaskPrincipal `
    -UserId    $userId `
    -LogonType Interactive `
    -RunLevel  Limited

$mcpGatewayTask = Register-ScheduledTask `
    -TaskName   "MiruRestartMcpGateway" `
    -Action     $restartMcpGatewayAction `
    -Trigger    $restartTrigger `
    -Settings   $commonSettings `
    -Principal  $restartPrincipal `
    -Description "Restarts the MCP Gateway on port 18766. Trigger via: Start-ScheduledTask -TaskName 'MiruRestartMcpGateway'" `
    -Force

Write-Host "  OK: '$($mcpGatewayTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (Interactive, RunLevel=Limited)"
Write-Host "      Trigger   : on-demand only"
Write-Host "      Script    : $mcpGatewayTaskScript"
Write-Host ""

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "=== Registration complete (2 tasks) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "To restart the MCP Gateway (no elevation needed):" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName 'MiruRestartMcpGateway'" -ForegroundColor Gray
Write-Host ""
