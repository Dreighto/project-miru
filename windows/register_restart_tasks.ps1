# register_restart_tasks.ps1
# Run this ONCE from an ELEVATED (Administrator) PowerShell window.
# It registers all five scheduled tasks for Project Miru:
#
#   1. OP Miru Startup        -- boot trigger, NAS\NAS S4U Limited, starts all 3 services
#   2. MiruRestartDispatcher  -- on-demand, NAS\NAS Interactive Limited, port 19000
#   3. MiruRestartPM          -- on-demand, NAS\NAS Interactive Limited, port 18080
#   4. MiruRestartMiruAI      -- on-demand, NAS\NAS Interactive Limited, port 18765
#   5. MiruRestartMcpGateway  -- on-demand, NAS\NAS Interactive Limited, port 18766
#
# WHY Interactive + Limited for restart tasks:
#   RunLevel=Highest tasks (even NAS\NAS-owned) cannot be triggered by a
#   non-elevated shell -- Windows blocks the Start-ScheduledTask call.
#   RunLevel=Limited + LogonType=Interactive tasks owned by NAS\NAS CAN be
#   triggered from a non-elevated NAS\NAS shell without any UAC prompt.
#   This is the same pattern used by the existing RestartMiruAIRelay and
#   RestartMiruDashboardRelay tasks already in Task Scheduler.
#
# WHY S4U + Limited for the startup task:
#   The startup task fires at boot before the user is logged in, so it cannot
#   use Interactive logon. S4U allows it to run without a stored password.
#   Limited means the Python processes it spawns are non-elevated, so that
#   Claude Code (also non-elevated) can Stop-Process them without UAC.
#
# After registration, non-elevated code (Claude Code, Cursor shell) triggers
# restarts via:
#   Start-ScheduledTask -TaskName "MiruRestartDispatcher"
# ...with no UAC prompt.

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

# ════════════════════════════════════════════════════════════════════════════════
# TASK 1 — OP Miru Startup (FIX 1)
# Replaces the broken task that pointed to a deleted Codex worktree path.
# Runs as NAS\NAS with S4U (no password prompt, survives reboots without login).
# RunLevel = Limited so spawned Python processes are non-elevated.
# Non-elevated processes can be killed by Stop-Process from a non-elevated shell
# (Claude Code), which eliminates the UAC prompt on restarts.
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

$userId           = "$($env:UserDomain)\$($env:UserName)"
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
    -Description "Starts Dispatcher (19000), PM Dashboard (18080), and Miru AI (18765) 30s after Windows boot. Managed by D:\dev\miru\windows\startup_all.ps1" `
    -Force

Write-Host "  OK: '$($startupTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (S4U, RunLevel=Limited)"
Write-Host "      Trigger   : AtStartup + 30s delay"
Write-Host "      Script    : $startupScript"
Write-Host ""

# ════════════════════════════════════════════════════════════════════════════════
# TASK 2 — MiruRestartDispatcher (FIX 2)
# RunLevel=Limited + LogonType=Interactive: the task runs as NAS\NAS with a
# standard (non-elevated) token in the user's interactive session.
# A non-elevated NAS\NAS shell CAN trigger Interactive/Limited tasks via
# Start-ScheduledTask without any UAC prompt — this is the same pattern used
# by the existing RestartMiruAIRelay and RestartMiruDashboardRelay tasks.
# ════════════════════════════════════════════════════════════════════════════════
Write-Host "Registering: MiruRestartDispatcher..." -ForegroundColor Yellow

$dispatcherTaskScript = Join-Path $tasksDir "restart_dispatcher_task.ps1"
if (-not (Test-Path $dispatcherTaskScript)) {
    Write-Error "restart_dispatcher_task.ps1 not found at $dispatcherTaskScript"
}

$restartDispatcherAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$dispatcherTaskScript`"" `
    -WorkingDirectory $repoRoot

# No automatic trigger — on-demand only via: Start-ScheduledTask -TaskName "MiruRestartDispatcher"
$restartTrigger = New-ScheduledTaskTrigger -Once -At "2000-01-01T00:00:00"

# Interactive + Limited: non-elevated user can trigger without UAC
$restartPrincipal = New-ScheduledTaskPrincipal `
    -UserId    $userId `
    -LogonType Interactive `
    -RunLevel  Limited

$dispatcherTask = Register-ScheduledTask `
    -TaskName   "MiruRestartDispatcher" `
    -Action     $restartDispatcherAction `
    -Trigger    $restartTrigger `
    -Settings   $commonSettings `
    -Principal  $restartPrincipal `
    -Description "Restarts Dispatcher on port 19000. Trigger via: Start-ScheduledTask -TaskName 'MiruRestartDispatcher'" `
    -Force

Write-Host "  OK: '$($dispatcherTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (Interactive, RunLevel=Limited)"
Write-Host "      Trigger   : on-demand only"
Write-Host "      Script    : $dispatcherTaskScript"
Write-Host ""

# ════════════════════════════════════════════════════════════════════════════════
# TASK 3 — MiruRestartPM (FIX 2)
# ════════════════════════════════════════════════════════════════════════════════
Write-Host "Registering: MiruRestartPM..." -ForegroundColor Yellow

$pmTaskScript = Join-Path $tasksDir "restart_pm_task.ps1"
if (-not (Test-Path $pmTaskScript)) {
    Write-Error "restart_pm_task.ps1 not found at $pmTaskScript"
}

$restartPmAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$pmTaskScript`"" `
    -WorkingDirectory $repoRoot

$pmTask = Register-ScheduledTask `
    -TaskName   "MiruRestartPM" `
    -Action     $restartPmAction `
    -Trigger    $restartTrigger `
    -Settings   $commonSettings `
    -Principal  $restartPrincipal `
    -Description "Restarts PM Dashboard on port 18080. Trigger via: Start-ScheduledTask -TaskName 'MiruRestartPM'" `
    -Force

Write-Host "  OK: '$($pmTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (Interactive, RunLevel=Limited)"
Write-Host "      Trigger   : on-demand only"
Write-Host "      Script    : $pmTaskScript"
Write-Host ""

# ════════════════════════════════════════════════════════════════════════════════
# TASK 4 — MiruRestartMiruAI (FIX 2)
# ════════════════════════════════════════════════════════════════════════════════
Write-Host "Registering: MiruRestartMiruAI..." -ForegroundColor Yellow

$miruAiTaskScript = Join-Path $tasksDir "restart_miru_ai_task.ps1"
if (-not (Test-Path $miruAiTaskScript)) {
    Write-Error "restart_miru_ai_task.ps1 not found at $miruAiTaskScript"
}

$restartMiruAiAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$miruAiTaskScript`"" `
    -WorkingDirectory $repoRoot

$miruAiTask = Register-ScheduledTask `
    -TaskName   "MiruRestartMiruAI" `
    -Action     $restartMiruAiAction `
    -Trigger    $restartTrigger `
    -Settings   $commonSettings `
    -Principal  $restartPrincipal `
    -Description "Restarts Miru AI on port 18765. Trigger via: Start-ScheduledTask -TaskName 'MiruRestartMiruAI'" `
    -Force

Write-Host "  OK: '$($miruAiTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (Interactive, RunLevel=Limited)"
Write-Host "      Trigger   : on-demand only"
Write-Host "      Script    : $miruAiTaskScript"
Write-Host ""

# ════════════════════════════════════════════════════════════════════════════════
# TASK 5 — MiruRestartMcpGateway (Stage 1 remote MCP gateway, port 18766)
# Same Interactive + Limited principal as Tasks 2-4. Triggered by:
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

$mcpGatewayTask = Register-ScheduledTask `
    -TaskName   "MiruRestartMcpGateway" `
    -Action     $restartMcpGatewayAction `
    -Trigger    $restartTrigger `
    -Settings   $commonSettings `
    -Principal  $restartPrincipal `
    -Description "Restarts MCP Gateway on port 18766. Trigger via: Start-ScheduledTask -TaskName 'MiruRestartMcpGateway'" `
    -Force

Write-Host "  OK: '$($mcpGatewayTask.TaskName)'" -ForegroundColor Green
Write-Host "      Principal : $userId (Interactive, RunLevel=Limited)"
Write-Host "      Trigger   : on-demand only"
Write-Host "      Script    : $mcpGatewayTaskScript"
Write-Host ""

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "=== All 5 tasks registered successfully (Interactive/Limited) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "To restart the Dispatcher (no elevation needed):" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName 'MiruRestartDispatcher'" -ForegroundColor Gray
Write-Host ""
Write-Host "To restart PM Dashboard (no elevation needed):" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName 'MiruRestartPM'" -ForegroundColor Gray
Write-Host ""
Write-Host "To restart Miru AI (no elevation needed):" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName 'MiruRestartMiruAI'" -ForegroundColor Gray
Write-Host ""
Write-Host "IMPORTANT: Reply 'tasks registered' in the Claude Code chat to continue." -ForegroundColor Yellow
Write-Host ""
