# Canonical Miru Dev runtime launcher + force-restart authority
# Replaces the old convenience launcher.
# Purpose:
# - stop the active Miru server on the target port if it is already running
# - start a fresh Miru server from the canonical repo root
# - wait for /api/health
# - prove the new PID is the active listener
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\run_miru_dev.ps1
#   powershell -ExecutionPolicy Bypass -File .\run_miru_dev.ps1 -Force
#   powershell -ExecutionPolicy Bypass -File .\run_miru_dev.ps1 -Port 18765 -BindHost 0.0.0.0

[CmdletBinding()]
param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 18765,
    [switch]$Force,
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonExe = "python"
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$StdoutLog = Join-Path $ProjectRoot "data\startup-logs\miru_ai_worktree_stdout.log"
$StderrLog = Join-Path $ProjectRoot "data\startup-logs\miru_ai_worktree_stderr.log"
$PidFile   = Join-Path $ProjectRoot "data\startup-logs\miru_ai_worktree.pid"

New-Item -ItemType Directory -Force -Path (Split-Path $StdoutLog -Parent) | Out-Null

function Write-Status {
    param(
        [string]$Message,
        [string]$Color = "Gray"
    )
    Write-Host $Message -ForegroundColor $Color
}

function Get-ListenerInfo {
    param([int]$ListenPort)

    $connections = @(Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($conn in $connections) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
        [PSCustomObject]@{
            Port        = $ListenPort
            Pid         = $conn.OwningProcess
            ProcessName = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            CommandLine = $proc.CommandLine
        }
    }
}

function Test-IsMiruProcess {
    param($Listener)

    if (-not $Listener) { return $false }
    $cmd = [string]$Listener.CommandLine
    if (-not $cmd) { return $false }

    return (
        $cmd -match 'miru_ai\.server' -and
        $cmd -match [regex]::Escape($ProjectRoot)
    )
}

function Stop-PortOwnerIfSafe {
    param(
        [int]$ListenPort,
        [switch]$AllowForce
    )

    $listeners = @(Get-ListenerInfo -ListenPort $ListenPort)
    if (-not $listeners.Count) {
        Write-Status "No existing listener on port $ListenPort." "DarkGray"
        return
    }

    foreach ($listener in $listeners) {
        $isMiru = Test-IsMiruProcess -Listener $listener

        if ($isMiru) {
            Write-Status "Stopping existing Miru listener on $ListenPort (PID $($listener.Pid))." "Yellow"
            Stop-Process -Id $listener.Pid -Force -ErrorAction Stop
            continue
        }

        if ($AllowForce) {
            Write-Status "Force-stopping non-Miru listener on $ListenPort (PID $($listener.Pid))." "Red"
            Stop-Process -Id $listener.Pid -Force -ErrorAction Stop
            continue
        }

        throw "Port $ListenPort is owned by PID $($listener.Pid) ($($listener.ProcessName)) and does not look like this repo's Miru server. Re-run with -Force only if you are sure."
    }

    Start-Sleep -Seconds 2
}

function Wait-ForPortRelease {
    param(
        [int]$ListenPort,
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $listeners = @(Get-ListenerInfo -ListenPort $ListenPort)
        if (-not $listeners.Count) {
            return $true
        }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Start-MiruServer {
    param(
        [string]$BindAddr,
        [int]$ListenPort
    )

    if (Test-Path $PidFile) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    Write-Status "Starting fresh Miru server from $ProjectRoot ..." "Cyan"

    $proc = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList @("-m", "miru_ai.server", "--host", $BindAddr, "--port", "$ListenPort") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru `
        -WindowStyle Hidden

    Set-Content -Path $PidFile -Value $proc.Id
    return $proc
}

function Wait-ForHealth {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 750
    }

    return $false
}

function Get-ActiveListenerPid {
    param([int]$ListenPort)

    $listener = @(Get-ListenerInfo -ListenPort $ListenPort) | Select-Object -First 1
    if ($listener) { return $listener.Pid }
    return $null
}

Write-Status "=== Miru Dev Restart Authority ===" "Cyan"
Write-Status "Repo root: $ProjectRoot" "Gray"
Write-Status "Target port: $Port" "Gray"
Write-Status "Health URL: $HealthUrl" "Gray"

Stop-PortOwnerIfSafe -ListenPort $Port -AllowForce:$Force

if (-not (Wait-ForPortRelease -ListenPort $Port -TimeoutSeconds 15)) {
    throw "Port $Port did not release after stop attempt."
}

$proc = Start-MiruServer -BindAddr $BindHost -ListenPort $Port

if (-not (Wait-ForHealth -Url $HealthUrl -TimeoutSeconds $StartupTimeoutSeconds)) {
    $stderrTail = ""
    if (Test-Path $StderrLog) {
        $stderrTail = (Get-Content $StderrLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "Miru server failed health check at $HealthUrl within $StartupTimeoutSeconds seconds.`n--- STDERR tail ---`n$stderrTail"
}

$activePid = Get-ActiveListenerPid -ListenPort $Port
if (-not $activePid) {
    throw "Health passed, but no active listener was found on port $Port."
}

if ($activePid -ne $proc.Id) {
    $listenerInfo = @(Get-ListenerInfo -ListenPort $Port) | Select-Object -First 1
    throw "Port $Port is not owned by the newly started process. Started PID=$($proc.Id), active PID=$activePid. CommandLine=$($listenerInfo.CommandLine)"
}

Write-Status ""
Write-Status "Miru server restarted successfully." "Green"
Write-Status "Active PID: $activePid" "Green"
Write-Status "Miru AI URL: http://127.0.0.1:$Port/" "Green"
Write-Status "Dev URL: http://127.0.0.1:$Port/dev" "Green"
Write-Status "Operator Console: http://127.0.0.1:$Port/dev/operator-console" "Green"
Write-Status "Stdout log: $StdoutLog" "DarkGray"
Write-Status "Stderr log: $StderrLog" "DarkGray"