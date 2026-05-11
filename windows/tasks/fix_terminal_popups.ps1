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
    $hidden = if ($a.Arguments -match "-WindowStyle\s+Hidden") { "hidden" } else { "VISIBLE" }
    $marker = if ($hidden -eq "VISIBLE" -and $a.Execute -notmatch "wscript|vbs") { "  ⚠️" } else { "    " }
    "{0}{1} {2,-32} state={3,-8} window={4}" -f $marker, "", $_.TaskName, $_.State, $hidden
}
Write-Host ""
Write-Host "Notes:" -ForegroundColor White
Write-Host "  - wscript.exe + .vbs wrappers (MiruBackup, MiruDispatchListener) are hidden by default."
Write-Host "  - Tasks marked VISIBLE need a re-install via their respective register_*.ps1 script,"
Write-Host "    or a manual schtasks edit. This script only updates the two tracked offenders."
