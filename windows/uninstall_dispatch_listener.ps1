# uninstall_dispatch_listener.ps1 -- tear down the MiruDispatchListener
# Scheduled Task (PRO-83). Idempotent -- safe to re-run.
#
# Usage:  powershell -ExecutionPolicy Bypass -File windows\uninstall_dispatch_listener.ps1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$scriptDir         = $PSScriptRoot
$repoRoot          = Split-Path -Parent $scriptDir
$taskName          = "MiruDispatchListener"
$port              = 19100
$logDirectory      = Join-Path $repoRoot "logs"
$uninstallLogPath  = Join-Path $logDirectory "uninstall_dispatch_listener.log"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Set-Content -Path $uninstallLogPath -Value "" -Encoding UTF8

$exitCode    = 1
$finalMarker = "UNINSTALL_FAILED"

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $line = "$timestamp`t$Message"
    Add-Content -Path $uninstallLogPath -Value $line -Encoding UTF8
    Write-Host "[uninstall-dispatch-listener] $Message"
}

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Wait-ForPortFree {
    param([Parameter(Mandatory = $true)][int]$Port, [int]$TimeoutSeconds = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $entries = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        if ($entries.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    return $false
}

try {
    Write-LogLine "action=uninstall_begin task=$taskName port=$port"

    if (-not (Test-Administrator)) {
        throw "Unregister-ScheduledTask requires elevation. Re-run from an elevated PowerShell."
    }
    Write-LogLine "elevated=yes"

    # Unregister the Scheduled Task if present.
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-LogLine "scheduled_task_state=absent"
    } else {
        Write-LogLine "scheduled_task_state_before=$($existing.State)"
        try {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
            Write-LogLine "stop_scheduled_task=invoked"
        } catch {
            Write-LogLine "stop_scheduled_task_warn=$($_.Exception.Message)"
        }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-LogLine "unregister_scheduled_task=ok"
    }

    # Best-effort: kill any node process still bound to port 19100 (in case the
    # task tore down but the listener leaked a child somewhere).
    $stalePids = @(
        Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Where-Object { $_ -gt 0 } |
            Sort-Object -Unique
    )
    foreach ($p in $stalePids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-LogLine "stale_pid_killed=$p"
        } catch {
            Write-LogLine "stale_pid_kill_failed=$p reason=$($_.Exception.Message)"
        }
    }

    # Also remove any leftover NSSM service from PRO-83's earlier NSSM phase
    # so a re-install doesn't have to fight an absent-but-cached service entry.
    $existingService = Get-Service -Name $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existingService) {
        Write-LogLine "leftover_nssm_service=present status=$($existingService.Status)"
        $nssm = (Get-Command "nssm" -ErrorAction SilentlyContinue).Source
        if ($nssm) {
            & $nssm stop $taskName 2>&1 | ForEach-Object { Write-LogLine "nssm stop: $_" }
            & $nssm remove $taskName confirm 2>&1 | ForEach-Object { Write-LogLine "nssm remove: $_" }
            Write-LogLine "leftover_nssm_service=removed"
        }
    } else {
        Write-LogLine "leftover_nssm_service=absent"
    }

    if (-not (Wait-ForPortFree -Port $port -TimeoutSeconds 20)) {
        $remaining = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        $pids = ($remaining | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique) -join ","
        throw "Port $port still LISTEN after uninstall (pids=$pids)."
    }
    Write-LogLine "port_${port}_state=free"

    $finalMarker = "UNINSTALL_SUCCESS"
    $exitCode    = 0
} catch {
    Write-LogLine "error=$($_.Exception.Message)"
    $finalMarker = "UNINSTALL_FAILED"
    $exitCode    = 1
} finally {
    Add-Content -Path $uninstallLogPath -Value $finalMarker -Encoding UTF8
    Write-Host "[uninstall-dispatch-listener] $finalMarker"
}

exit $exitCode
