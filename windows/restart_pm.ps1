[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
$port = 18080
$targetSurface = "PM_18080"
$startScriptPath = Join-Path $scriptDir "start_project_miru_dashboard.ps1"
$logDirectory = Join-Path $repoRoot "logs"
$logPath = Join-Path $logDirectory "restart_pm.log"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Set-Content -Path $logPath -Value "" -Encoding UTF8

$killedPids = @()
$listenerDetected = $false
$localhostVerification = $false
$tailscaleVerification = "N/A"
$exitCode = 1
$finalMarker = "RESTART_FAILED"

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $logPath -Value "$timestamp`t$Message" -Encoding UTF8
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
    }
    catch {
        return @()
    }
}

function Wait-ForPortListener {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][bool]$ShouldExist,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $entries = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        $exists = $entries.Count -gt 0
        if ($ShouldExist -and $exists) {
            return $true
        }
        if (-not $ShouldExist -and -not $exists) {
            return $true
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Test-UrlReachable {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 15
    )

    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null
        $request.AllowAutoRedirect = $true
        $request.Method = "GET"
        $request.Timeout = [Math]::Max(1000, $TimeoutSeconds * 1000)
        $response = $request.GetResponse()
        try {
            $statusCode = [int]$response.StatusCode
            return $statusCode -ge 200 -and $statusCode -lt 400
        }
        finally {
            $response.Close()
        }
    }
    catch {
        return $false
    }
}

function Wait-ForUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 180,
        [int]$RetryDelaySeconds = 4
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-UrlReachable -Url $Url -TimeoutSeconds 12) {
            return $true
        }
        Start-Sleep -Seconds $RetryDelaySeconds
    } while ((Get-Date) -lt $deadline)

    return $false
}

try {
    Write-LogLine "target_surface=$targetSurface"
    Write-LogLine "action=restart_begin"

    if (-not (Test-Path $startScriptPath)) {
        throw "Canonical PM start script not found at $startScriptPath"
    }

    $stalePids = @(Get-ListeningPidsOnPort -Port $port)
    if ($stalePids.Count -eq 0) {
        Write-LogLine "stale_pids_killed=none"
    }
    else {
        foreach ($procId in $stalePids) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                $killedPids += $procId
                Write-LogLine "killed_pid=$procId"
            }
            catch {
                throw "Failed to stop PID $procId listening on port $port. $($_.Exception.Message)"
            }
        }
        Write-LogLine "stale_pids_killed=$($killedPids -join ',')"
    }

    if (-not (Wait-ForPortListener -Port $port -ShouldExist:$false -TimeoutSeconds 20)) {
        throw "Port $port remained in LISTEN state after stop attempts."
    }

    Write-LogLine "action=start_canonical_script path=$startScriptPath"
    & $startScriptPath -Port $port -Force

    if (-not (Wait-ForPortListener -Port $port -ShouldExist:$true -TimeoutSeconds 120)) {
        throw "Listener on port $port did not appear in time."
    }
    $listenerDetected = $true

    $localhostUrl = "http://127.0.0.1:$port/"
    $localhostVerification = Wait-ForUrl -Url $localhostUrl -TimeoutSeconds 210 -RetryDelaySeconds 5
    if (-not $localhostVerification) {
        throw "Localhost verification failed for $localhostUrl"
    }

    $tailscaleUrl = "http://100.104.150.125:$port/"
    if (Test-UrlReachable -Url $tailscaleUrl -TimeoutSeconds 15) {
        $tailscaleVerification = "ok"
    }
    else {
        $tailscaleVerification = "fail"
    }

    $finalMarker = "RESTART_SUCCESS"
    $exitCode = 0
}
catch {
    Write-LogLine "error=$($_.Exception.Message)"
    $finalMarker = "RESTART_FAILED"
    $exitCode = 1
}
finally {
    $killedPidSummary = if ($killedPids.Count -gt 0) { $killedPids -join "," } else { "none" }
    Write-LogLine "stale_pids_killed_final=$killedPidSummary"
    Write-LogLine "listener_detected=$(if ($listenerDetected) { 'yes' } else { 'no' })"
    Write-LogLine "localhost_verification=$(if ($localhostVerification) { 'ok' } else { 'fail' })"
    Write-LogLine "tailscale_verification=$tailscaleVerification"
    Add-Content -Path $logPath -Value $finalMarker -Encoding UTF8
}

exit $exitCode
