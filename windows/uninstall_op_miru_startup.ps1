[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "OP Miru Startup",
    [switch]$SkipAdminCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-OpMiruAdministrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $SkipAdminCheck -and -not (Test-OpMiruAdministrator)) {
    throw "Run this uninstaller from an elevated PowerShell window."
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Scheduled task '$TaskName' was not found. Nothing to uninstall." -ForegroundColor Yellow
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, "Unregister OP Miru startup task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Uninstalled scheduled task '$TaskName'." -ForegroundColor Green
}
