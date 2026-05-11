# fix_terminal_popups.ps1 — apply -WindowStyle Hidden to Miru* scheduled tasks
# that pop visible PowerShell terminals onto the desktop when they fire.
#
# Run from an elevated PowerShell window:
#
#   powershell.exe -ExecutionPolicy Bypass -File windows\tasks\fix_terminal_popups.ps1
#
# Idempotent: re-running on tasks that already have the hidden flag is a no-op.
# Reports an audit of every active Miru/LogueOS scheduled task at the end.
#
# Scope:
#   - MiruWorkerUpdater   (nightly worker-update; daily 3am)
#   - OP Miru Startup     (boot-trigger; 30s after Windows startup)
#
# Source-of-truth: this script mirrors the changes that the install scripts
# (windows\register_updater_task.ps1, windows\register_restart_tasks.ps1)
# also apply. The install scripts are the canonical config; this script
# exists for the live machine where re-running the full install is overkill
# (would also reset triggers, principals, etc.).

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Error "Run this from an elevated (Administrator) PowerShell window."
    exit 1
}

$hiddenArgs = "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass"

# Tasks to update: name -> (script path)
$tasksToFix = @{
    "MiruWorkerUpdater" = "D:\dev\miru\tools\update_workers.ps1"
    "OP Miru Startup"   = "D:\dev\miru\windows\startup_all.ps1"
}

foreach ($taskName in $tasksToFix.Keys) {
    $taskScript = $tasksToFix[$taskName]
    Write-Host ""
    Write-Host "=== $taskName ===" -ForegroundColor Cyan

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "  SKIP: task not registered" -ForegroundColor Yellow
        continue
    }

    $action = $task.Actions[0]
    Write-Host "  Current args: $($action.Arguments)"

    if ($action.Arguments -match "-WindowStyle\s+Hidden") {
        Write-Host "  Already has -WindowStyle Hidden — no change" -ForegroundColor Green
        continue
    }

    # Build new args, preserving the file path and any post-script args.
    $newArgs = "$hiddenArgs -File `"$taskScript`""
    Write-Host "  New args:     $newArgs"

    $newAction = New-ScheduledTaskAction `
        -Execute $action.Execute `
        -Argument $newArgs `
        -WorkingDirectory $action.WorkingDirectory

    try {
        Set-ScheduledTask -TaskName $taskName -Action $newAction -ErrorAction Stop | Out-Null
        Write-Host "  UPDATED" -ForegroundColor Green
    } catch {
        Write-Host "  FAIL: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Final audit: all active Miru/LogueOS tasks ===" -ForegroundColor Cyan
Write-Host ""
Get-ScheduledTask | Where-Object {
    ($_.TaskName -like 'Miru*' -or $_.TaskName -like 'Logue*' -or $_.TaskName -like 'OP Miru*') `
    -and $_.State -ne 'Disabled'
} | ForEach-Object {
    $a = $_.Actions[0]
    # wscript.exe doesn't allocate a console — those tasks are silent
    # by default regardless of -WindowStyle Hidden. Label them as such
    # so the audit doesn't yield false-positive VISIBLE entries.
    $isWscript = $a.Execute -match "wscript"
    $hasHiddenFlag = $a.Arguments -match "-WindowStyle\s+Hidden"
    $windowLabel = if ($isWscript) { "hidden(wscript)" }
                   elseif ($hasHiddenFlag) { "hidden" }
                   else { "VISIBLE" }
    "    {0,-32} state={1,-8} window={2}" -f $_.TaskName, $_.State, $windowLabel
}
Write-Host ""
Write-Host "Notes:" -ForegroundColor White
Write-Host "  - hidden(wscript): wscript.exe + .vbs wrappers don't allocate a console,"
Write-Host "    so they're silent by default (MiruBackup, MiruDispatchListener)."
Write-Host "  - hidden: powershell.exe with -WindowStyle Hidden flag."
Write-Host "  - VISIBLE: still flashes a terminal. To fix the 4 periodic watchdog tasks"
Write-Host "    (MiruN8nWatchdog, MiruSentinel, MiruServiceWatchdog, MiruStallRecovery),"
Write-Host "    run from the same elevated shell:"
Write-Host "      powershell -ExecutionPolicy Bypass -File windows\fix_task_window_flash.ps1"
Write-Host "    That re-registers them with VBS wrappers (the canonical fix)."
