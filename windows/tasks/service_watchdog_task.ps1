# service_watchdog_task.ps1
# Called by the "MiruServiceWatchdog" scheduled task every 2 minutes.
# Polls gateway (18766) and dispatch listener (19100) health endpoints.
# If a service has been down for 2+ consecutive polls (>=90s), auto-restarts
# it via its registered scheduled task and sends a Telegram alert.
# Sends a recovery alert when a previously-down service comes back.
# Runs as the current user with RunLevel=Limited (Interactive logon).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logFile   = Join-Path $logDir "service_watchdog.log"
$stateFile = Join-Path $logDir "service_watchdog_state.json"

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

# Load .env so TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are available
$commonPath = Join-Path $windowsDir "op_miru_common.ps1"
if (Test-Path $commonPath) {
    try {
        . $commonPath
        Import-OpMiruDotEnv -RepoRoot $repoRoot | Out-Null
    } catch {
        Write-Log "WARNING: op_miru_common.ps1 load failed: $($_.Exception.Message)"
    }
}

function Send-Telegram {
    param([string]$Msg)
    $token  = $env:TELEGRAM_BOT_TOKEN
    $chatId = $env:TELEGRAM_CHAT_ID
    if (-not $token -or -not $chatId) { return }
    try {
        $body = [ordered]@{ chat_id = $chatId; text = $Msg; parse_mode = "HTML" } |
                ConvertTo-Json -Compress
        Invoke-WebRequest `
            -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method POST -ContentType "application/json" -Body $body `
            -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
    } catch {
        Write-Log "telegram_send_failed: $($_.Exception.Message)"
    }
}

function Test-Health {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Read-State {
    $empty = @{
        gateway           = @{ first_fail_utc = $null }
        dispatch_listener = @{ first_fail_utc = $null }
    }
    if (-not (Test-Path $stateFile)) { return $empty }
    try {
        $raw = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        return @{
            gateway           = @{ first_fail_utc = $raw.gateway.first_fail_utc }
            dispatch_listener = @{ first_fail_utc = $raw.dispatch_listener.first_fail_utc }
        }
    } catch {
        return $empty
    }
}

function Write-State {
    param([hashtable]$S)
    try {
        $S | ConvertTo-Json -Depth 3 -Compress | Set-Content -Path $stateFile -Encoding UTF8
    } catch {
        Write-Log "state_write_failed: $($_.Exception.Message)"
    }
}

$services = @(
    @{
        key          = "gateway"
        label        = "MCP Gateway (18766)"
        health_url   = "http://127.0.0.1:18766/health"
        restart_task = "MiruRestartMcpGateway"
    },
    @{
        key          = "dispatch_listener"
        label        = "Dispatch Listener (19100)"
        health_url   = "http://127.0.0.1:19100/health"
        restart_task = "MiruDispatchListener"
    }
)

$state   = Read-State
$nowUtc  = [datetime]::UtcNow
$changed = $false

Write-Log "=== watchdog poll ==="

foreach ($svc in $services) {
    $key     = $svc.key
    $healthy = Test-Health -Url $svc.health_url

    if ($healthy) {
        if ($state[$key].first_fail_utc) {
            Write-Log "${key}_recovered"
            Send-Telegram "✅ <b>$($svc.label) recovered</b>`nService is responding normally."
            $state[$key].first_fail_utc = $null
            $changed = $true
        } else {
            Write-Log "${key}_ok"
        }
        continue
    }

    # Unhealthy path
    if (-not $state[$key].first_fail_utc) {
        $state[$key].first_fail_utc = $nowUtc.ToString("o")
        $changed = $true
        Write-Log "${key}_first_fail ts=$($state[$key].first_fail_utc)"
        continue
    }

    $firstFail = [datetime]::Parse(
        $state[$key].first_fail_utc,
        $null,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    $downSec = ($nowUtc - $firstFail).TotalSeconds

    if ($downSec -ge 90) {
        Write-Log "${key}_restart_triggered down_sec=$([int]$downSec)"
        Send-Telegram "🔄 <b>$($svc.label) auto-restart</b>`nDown for $([int]$downSec)s — triggering restart now."
        try {
            Start-ScheduledTask -TaskName $svc.restart_task -ErrorAction Stop
            Write-Log "${key}_restart_task_started task=$($svc.restart_task)"
        } catch {
            Write-Log "${key}_restart_task_failed: $($_.Exception.Message)"
            Send-Telegram "❌ <b>$($svc.label) restart FAILED</b>`nCould not start scheduled task '$($svc.restart_task)': $($_.Exception.Message)"
        }
        $state[$key].first_fail_utc = $null
        $changed = $true
    } else {
        Write-Log "${key}_still_down down_sec=$([int]$downSec)"
    }
}

if ($changed) { Write-State -State $state }
