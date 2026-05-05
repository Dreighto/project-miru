# fix_dispatch_listener_task.ps1
# Run once as Administrator to update MiruDispatchListener so it uses
# the VBS wrapper instead of calling powershell.exe directly.
# This eliminates the console window flash on every start/restart.
#
# Usage: Right-click this file -> Run with PowerShell
#   OR: Open an elevated PowerShell and run it manually.

#Requires -RunAsAdministrator

$repoRoot   = "D:\dev\miru"
$vbsWrapper = "$repoRoot\windows\tasks\run_dispatch_listener.vbs"

if (-not (Test-Path $vbsWrapper)) {
    Write-Host "ERROR: VBS wrapper not found at $vbsWrapper" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$vbsWrapper`"" `
    -WorkingDirectory $repoRoot

Set-ScheduledTask -TaskName "MiruDispatchListener" -Action $action

Write-Host "MiruDispatchListener updated - now uses hidden VBS wrapper." -ForegroundColor Green
Write-Host "No restart needed; change takes effect on next task launch." -ForegroundColor DarkGray
