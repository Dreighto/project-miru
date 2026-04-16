# restart_dispatcher.ps1 — Miru Task Dispatcher restart
# Kills the process on port 19000 and starts a fresh Dispatcher.
# Does not require elevation when the Dispatcher was itself started
# non-elevated (which is guaranteed when the OP Miru Startup task
# uses RunLevel=Limited — see register_restart_tasks.ps1).
#
# Usage:  powershell -ExecutionPolicy Bypass -File windows\restart_dispatcher.ps1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = $PSScriptRoot
$repoRoot   = Split-Path -Parent $scriptDir
$port       = 19000
$targetSurface  = "TASK_DISPATCHER_19000"
$startScriptPath = Join-Path $scriptDir "start_dispatcher.ps1"
$logDirectory   = Join-Path $repoRoot "logs"
$logPath        = Join-Path $logDirectory "restart_dispatcher.log"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Set-Content -Path $logPath -Value "" -Encoding UTF8

$killedPids          = @()
$listenerDetected    = $false
$localhostVerification = $false
$tailscaleIp         = $null
$tailscaleVerificationOk = $false
$exitCode   = 1
$finalMarker = "RESTART_FAILED"

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $line = "$timestamp`t$Message"
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host "[restart-dispatcher] $Message"
}

function Get-ListeningPidsOnPort {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        return @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                ForEach-Object { [int]$_.OwningProcess } |
                Where-Object { $_ -gt 0 } |
                Sort-Object -Unique
        )
    } catch { return @() }
}

function Wait-ForPortListener {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][bool]$ShouldExist,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $entries = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        $exists  = $entries.Count -gt 0
        if ($ShouldExist -and $exists)       { return $true }
        if (-not $ShouldExist -and -not $exists) { return $true }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-UrlReachable {
    param([Parameter(Mandatory = $true)][string]$Url, [int]$TimeoutSeconds = 15)
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Proxy = $null; $req.AllowAutoRedirect = $true; $req.Method = "GET"
        $req.Timeout = [Math]::Max(1000, $TimeoutSeconds * 1000)
        $resp = $req.GetResponse()
        try { return ([int]$resp.StatusCode -ge 200 -and [int]$resp.StatusCode -lt 400) }
        finally { $resp.Close() }
    } catch { return $false }
}

function Wait-ForUrl {
    param([Parameter(Mandatory = $true)][string]$Url, [int]$TimeoutSeconds = 60, [int]$RetryDelaySeconds = 3)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-UrlReachable -Url $Url -TimeoutSeconds 12) { return $true }
        Start-Sleep -Seconds $RetryDelaySeconds
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Resolve-TailscaleIPv4 {
    $fromAdapter = @(
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -and $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                [string]$_.InterfaceAlias -match "Tailscale"
            } | Sort-Object -Property SkipAsSource, PrefixLength
    )
    if ($fromAdapter.Count -gt 0) { return [string]$fromAdapter[0].IPAddress }
    $tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($tailscaleCmd) {
        try {
            $ipLines  = @(& $tailscaleCmd.Source ip -4 2>$null) | ForEach-Object { [string]$_ }
            $candidate = $ipLines | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1
            if (-not [string]::IsNullOrWhiteSpace($candidate)) { return $candidate }
        } catch { return $null }
    }
    return $null
}

try {
    Write-LogLine "target_surface=$targetSurface"
    Write-LogLine "action=restart_begin"

    if (-not (Test-Path $startScriptPath)) {
        throw "Canonical Dispatcher start script not found at $startScriptPath"
    }

    $stalePids = @(Get-ListeningPidsOnPort -Port $port)
    if ($stalePids.Count -eq 0) {
        Write-LogLine "stale_pids_killed=none"
    } else {
        foreach ($procId in $stalePids) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                $killedPids += $procId
                Write-LogLine "killed_pid=$procId"
            } catch {
                throw "Failed to stop PID $procId on port $port. $($_.Exception.Message)"
            }
        }
        Write-LogLine "stale_pids_killed=$($killedPids -join ',')"
    }

    if (-not (Wait-ForPortListener -Port $port -ShouldExist:$false -TimeoutSeconds 20)) {
        throw "Port $port remained in LISTEN state after stop attempts."
    }

    Write-LogLine "action=start_canonical_script path=$startScriptPath args=-Force"
    & $startScriptPath -Force

    $listenerDetected = Wait-ForPortListener -Port $port -ShouldExist:$true -TimeoutSeconds 20
    if ($listenerDetected) {
        Write-LogLine "listener_observed_after_start=yes"
    } else {
        Write-LogLine "listener_observed_after_start=no"
    }

    $localhostUrl = "http://127.0.0.1:$port/"
    $localhostVerification = Wait-ForUrl -Url $localhostUrl -TimeoutSeconds 60 -RetryDelaySeconds 3
    if (-not $localhostVerification) { throw "Localhost verification failed for $localhostUrl" }
    if (-not $listenerDetected) {
        $listenerDetected = $true
        Write-LogLine "listener_ready_via_localhost_verification=yes"
    }

    $tailscaleIp = Resolve-TailscaleIPv4
    if ([string]::IsNullOrWhiteSpace($tailscaleIp)) {
        Write-LogLine "TAILSCALE_FAIL"
    } else {
        Write-LogLine "TAILSCALE_OK $tailscaleIp"
        $tailscaleUrl = "http://$tailscaleIp`:$port/"
        $tailscaleVerificationOk = Wait-ForUrl -Url $tailscaleUrl -TimeoutSeconds 30 -RetryDelaySeconds 3
        if (-not $tailscaleVerificationOk) { Write-LogLine "TAILSCALE_FAIL" }
    }

    $finalMarker = "RESTART_SUCCESS"
    $exitCode    = 0
} catch {
    Write-LogLine "error=$($_.Exception.Message)"
    if (-not $tailscaleVerificationOk -and [string]::IsNullOrWhiteSpace($tailscaleIp)) {
        Write-LogLine "TAILSCALE_FAIL"
    }
    $finalMarker = "RESTART_FAILED"
    $exitCode    = 1
} finally {
    $killedPidSummary = if ($killedPids.Count -gt 0) { $killedPids -join "," } else { "none" }
    Write-LogLine "stale_pids_killed_final=$killedPidSummary"
    Write-LogLine "listener_detected=$(if ($listenerDetected) { 'yes' } else { 'no' })"
    Write-LogLine "localhost_verification=$(if ($localhostVerification) { 'ok' } else { 'fail' })"
    Write-LogLine "tailscale_verification=$(if ($tailscaleVerificationOk) { 'ok' } else { 'fail' })"
    Add-Content -Path $logPath -Value $finalMarker -Encoding UTF8
}

exit $exitCode
