[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
$port = 18765
$targetSurface = "MIRU_AI_DEV_18765"
$startScriptPath = Join-Path $scriptDir "start_miru_ai_dev.ps1"
$logDirectory = Join-Path $repoRoot "logs"
$logPath = Join-Path $logDirectory "restart_miru_ai.log"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Set-Content -Path $logPath -Value "" -Encoding UTF8

$killedPids = @()
$listenerDetected = $false
$localhostHealthOk = $false
$localhostDevOk = $false
$tailscaleIp = $null
$tailscaleVerificationOk = $false
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
        [int]$TimeoutSeconds = 180
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
        Start-Sleep -Milliseconds 900
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
        [int]$TimeoutSeconds = 300,
        [int]$RetryDelaySeconds = 5
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-UrlReachable -Url $Url -TimeoutSeconds 15) {
            return $true
        }
        Start-Sleep -Seconds $RetryDelaySeconds
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Resolve-TailscaleIPv4 {
    $fromAdapter = @(
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -and
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                [string]$_.InterfaceAlias -match "Tailscale"
            } |
            Sort-Object -Property SkipAsSource, PrefixLength
    )
    if ($fromAdapter.Count -gt 0) {
        return [string]$fromAdapter[0].IPAddress
    }

    $tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($tailscaleCmd) {
        try {
            $ipLines = @(& $tailscaleCmd.Source ip -4 2>$null) | ForEach-Object { [string]$_ }
            $candidate = $ipLines | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1
            if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                return $candidate
            }
        }
        catch {
            return $null
        }
    }

    return $null
}

try {
    Write-LogLine "target_surface=$targetSurface"
    Write-LogLine "action=restart_begin"

    if (-not (Test-Path $startScriptPath)) {
        throw "Canonical Miru AI start script not found at $startScriptPath"
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

    if (-not (Wait-ForPortListener -Port $port -ShouldExist:$false -TimeoutSeconds 30)) {
        throw "Port $port remained in LISTEN state after stop attempts."
    }

    Write-LogLine "action=start_canonical_script path=$startScriptPath args=-Force"
    & $startScriptPath -Force

    $listenerDetected = Wait-ForPortListener -Port $port -ShouldExist:$true -TimeoutSeconds 20
    if ($listenerDetected) {
        Write-LogLine "listener_observed_after_start=yes"
    }
    else {
        Write-LogLine "listener_observed_after_start=no"
    }

    $healthUrlLocal = "http://127.0.0.1:$port/api/health"
    $devUrlLocal = "http://127.0.0.1:$port/dev"
    $localhostHealthOk = Wait-ForUrl -Url $healthUrlLocal -TimeoutSeconds 300 -RetryDelaySeconds 5
    $localhostDevOk = Wait-ForUrl -Url $devUrlLocal -TimeoutSeconds 300 -RetryDelaySeconds 5
    if (-not ($localhostHealthOk -and $localhostDevOk)) {
        throw "Localhost verification failed for Miru AI Dev on port $port."
    }
    if (-not $listenerDetected) {
        $listenerDetected = $true
        Write-LogLine "listener_ready_via_localhost_verification=yes"
    }

    $tailscaleIp = Resolve-TailscaleIPv4
    if ([string]::IsNullOrWhiteSpace($tailscaleIp)) {
        Write-LogLine "TAILSCALE_FAIL"
    }
    else {
        Write-LogLine "TAILSCALE_OK $tailscaleIp"

        $tailscaleHealthUrl = "http://$tailscaleIp`:$port/api/health"
        $tailscaleVerificationOk = Wait-ForUrl -Url $tailscaleHealthUrl -TimeoutSeconds 180 -RetryDelaySeconds 5
        if (-not $tailscaleVerificationOk) {
            Write-LogLine "TAILSCALE_FAIL"
        }
    }

    $finalMarker = "RESTART_SUCCESS"
    $exitCode = 0
}
catch {
    Write-LogLine "error=$($_.Exception.Message)"
    if (-not $tailscaleVerificationOk -and [string]::IsNullOrWhiteSpace($tailscaleIp)) {
        Write-LogLine "TAILSCALE_FAIL"
    }
    $finalMarker = "RESTART_FAILED"
    $exitCode = 1
}
finally {
    $killedPidSummary = if ($killedPids.Count -gt 0) { $killedPids -join "," } else { "none" }
    Write-LogLine "stale_pids_killed_final=$killedPidSummary"
    Write-LogLine "listener_detected=$(if ($listenerDetected) { 'yes' } else { 'no' })"
    $localhostSummary = if ($localhostHealthOk -and $localhostDevOk) { "ok" } else { "fail" }
    Write-LogLine "localhost_verification=$localhostSummary"
    Write-LogLine "tailscale_verification=$(if ($tailscaleVerificationOk) { 'ok' } else { 'fail' })"
    Add-Content -Path $logPath -Value $finalMarker -Encoding UTF8
}

exit $exitCode
