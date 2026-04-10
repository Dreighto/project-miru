# Registers the "MiruTaskDispatcher" scheduled task.
# Must be run from an elevated (Administrator) PowerShell session.
#
# Usage:
#   .\windows\install_dispatcher_startup.ps1
#   .\windows\install_dispatcher_startup.ps1 -UserName andre

param(
    [string]$UserName = $env:USERNAME
)

$ErrorActionPreference = 'Stop'

# Verify elevation
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

$TaskName = "MiruTaskDispatcher"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BootstrapPath = Join-Path $ScriptDir "op_dispatcher_bootstrap.cmd"

if (-not (Test-Path $BootstrapPath)) {
    Write-Error "Bootstrap script not found: $BootstrapPath"
    exit 1
}

Write-Host "Registering scheduled task '$TaskName'..."
Write-Host "  User:      $UserName"
Write-Host "  Bootstrap: $BootstrapPath"
Write-Host "  Trigger:   At logon (30s delay for Tailscale)"

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Removing existing task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $BootstrapPath
$trigger = New-ScheduledTaskTrigger -AtLogon -User $UserName
$trigger.Delay = 'PT30S'
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $taskPrincipal `
    -Settings $settings `
    -Description "Starts Miru Task Dispatcher on port 19000 after user logon (30s delay for Tailscale)." `
    | Out-Null

Write-Host ""
Write-Host "Task '$TaskName' registered successfully."
Write-Host ""
Write-Host "To verify:  Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
