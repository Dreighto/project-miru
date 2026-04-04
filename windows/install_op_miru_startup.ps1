[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "OP Miru Startup",
    [switch]$AsSystem,
    [switch]$SkipAdminCheck,
    [switch]$Worktree
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-OpMiruAdministrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $SkipAdminCheck -and -not (Test-OpMiruAdministrator)) {
    throw "Run this installer from an elevated PowerShell window."
}

$bootstrapPath = Join-Path $PSScriptRoot "op_miru_bootstrap.cmd"
if (-not (Test-Path $bootstrapPath)) {
    throw "Bootstrap wrapper was not found at $bootstrapPath."
}

if ($Worktree) {
    $action = New-ScheduledTaskAction -Execute $bootstrapPath -Argument "worktree"
    $description = "Starts OP Miru worktree stack (dashboard 18080 + Miru AI Dev 18765) after Windows startup."
}
else {
    $action = New-ScheduledTaskAction -Execute $bootstrapPath
    $description = "Starts Miru AI Dev on 18765 after Windows startup."
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

if ($AsSystem) {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
}
else {
    $userId = if ($env:UserDomain) { "$($env:UserDomain)\$($env:UserName)" } else { $env:UserName }
    # Docker Desktop is user-scoped, so the user S4U principal is the safest default for boot recovery.
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Highest
}

if ($PSCmdlet.ShouldProcess($TaskName, "Register OP Miru startup task")) {
    $task = Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force

    [pscustomobject]@{
        TaskName      = $task.TaskName
        TaskPath      = $task.TaskPath
        Principal     = $principal.UserId
        BootstrapPath = $bootstrapPath
        Trigger       = "AtStartup"
    }
}
