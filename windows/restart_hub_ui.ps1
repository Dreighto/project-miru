# restart_hub_ui.ps1
# Rebuild + restart the Miru dev page (hub_ui SvelteKit, port 18768) in
# production mode. Default daily-use launcher -- ~10-100x faster initial
# page load than `vite dev` because the bundle is precompiled.
#
# Usage:
#   .\restart_hub_ui.ps1            # rebuild + restart (default)
#   .\restart_hub_ui.ps1 -NoBuild   # skip rebuild, just restart from existing build/
#   .\restart_hub_ui.ps1 -Dev       # restart in vite dev mode (live-edit mode)
#
# Notes:
#   - The production node bundle reads PORT from the environment.
#   - Process is launched detached, window hidden -- no terminal popups.
#   - Old processes on 18768 are killed first regardless of mode.

[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$Dev
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HubUiRoot = "D:\dev\miru\miru_ai\hub_ui"
$Port      = 18768
$LogDir    = "D:\dev\miru\logs"
$LogPath   = Join-Path $LogDir "hub_ui_restart.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log([string]$msg) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$stamp $msg" | Out-File -FilePath $LogPath -Append -Encoding utf8
    Write-Host $msg
}

Write-Log "restart_hub_ui begin (NoBuild=$NoBuild Dev=$Dev)"

# 1. Stop anything currently bound to 18768.
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $existing) {
    $pidToKill = $conn.OwningProcess
    try {
        Stop-Process -Id $pidToKill -Force -ErrorAction Stop
        Write-Log "killed PID $pidToKill on port $Port"
    } catch {
        Write-Log "could not kill PID $pidToKill -- $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 1

# 2. Optionally rebuild.
if (-not $NoBuild -and -not $Dev) {
    Write-Log "running npm run build"
    Push-Location $HubUiRoot
    try {
        $buildOutput = & npm run build 2>&1
        $buildOutput | Out-File -FilePath $LogPath -Append -Encoding utf8
        if ($LASTEXITCODE -ne 0) {
            throw "npm run build failed with exit code $LASTEXITCODE"
        }
        Write-Log "build complete"
    } finally {
        Pop-Location
    }
}

# 3. Relaunch in the chosen mode, detached + hidden.
if ($Dev) {
    Write-Log "starting in DEV mode (vite dev, hot-reload)"
    $args = @("run", "dev", "--", "--port", $Port)
    $proc = Start-Process -FilePath "npm.cmd" `
                          -ArgumentList $args `
                          -WorkingDirectory $HubUiRoot `
                          -WindowStyle Hidden `
                          -PassThru
} else {
    Write-Log "starting in PROD mode (node build/index.js)"
    $env:PORT = "$Port"
    $env:HOST = "0.0.0.0"
    $proc = Start-Process -FilePath "node.exe" `
                          -ArgumentList @("build/index.js") `
                          -WorkingDirectory $HubUiRoot `
                          -WindowStyle Hidden `
                          -PassThru
}

Write-Log "launched PID $($proc.Id)"

# 4. Wait briefly + verify the port is bound.
Start-Sleep -Seconds 3
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Log "port $Port is listening (PID $($listening[0].OwningProcess))"
    exit 0
} else {
    Write-Log "WARNING port $Port not bound after 3s -- check log"
    exit 1
}
