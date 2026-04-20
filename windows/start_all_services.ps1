# start_all_services.ps1 — Start all three Project Miru services if not already running
# Safe to run at any time — only starts services that are currently down.
# Never force-kills a running service.
#
# Services checked (in order):
#   1. PM Dashboard — port 18080
#   2. Miru AI      — port 18765
#   3. Dispatcher   — port 19000

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$windowsDir = $PSScriptRoot

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -ne $conn -and @($conn).Count -gt 0)
}

# ── PM Dashboard (18080) ─────────────────────────────────────────────────────
if (Test-PortListening -Port 18080) {
    Write-Host "[PM 18080]         [UP]"
} else {
    Write-Host "[PM 18080]         [STARTING]"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $windowsDir "restart_pm.ps1")
    Write-Host "[PM 18080]         [STARTED]"
}

# ── Miru AI (18765) ──────────────────────────────────────────────────────────
if (Test-PortListening -Port 18765) {
    Write-Host "[Miru AI 18765]    [UP]"
} else {
    Write-Host "[Miru AI 18765]    [STARTING]"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $windowsDir "restart_miru_ai.ps1")
    Write-Host "[Miru AI 18765]    [STARTED]"
}

# ── Dispatcher (19000) ───────────────────────────────────────────────────────
if (Test-PortListening -Port 19000) {
    Write-Host "[Dispatcher 19000] [UP]"
} else {
    Write-Host "[Dispatcher 19000] [STARTING]"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $windowsDir "restart_dispatcher.ps1")
    Write-Host "[Dispatcher 19000] [STARTED]"
}

Write-Host ""
Write-Host "All services checked."
