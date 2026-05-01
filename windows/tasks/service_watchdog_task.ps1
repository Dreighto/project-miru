# service_watchdog_task.ps1
# Called by the "MiruServiceWatchdog" scheduled task every 2 minutes.
# Polls gateway (18766), dispatch listener (19100), and n8n (15678).
# If a service has been down for 2+ consecutive polls (>=90s), auto-restarts
# it and sends a Telegram alert.
# Ceiling: if a service restarts 3+ times within 10 minutes, stops retrying
# and sends an escalation alert until the service recovers naturally.
# Sends a recovery alert when a previously-down service comes back.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logFile   = Join-Path $logDir "service_watchdog.log"
$stateFile = Join-Path $logDir "service_watchdog_state.json"

$MAX_RESTARTS_PER_WINDOW = 3
$RESTART_WINDOW_S        = 600   # 10 minutes

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

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
    if (-not $token -or -not $chatId) { return $false }
    try {
        $body = [ordered]@{ chat_id = $chatId; text = $Msg; parse_mode = "HTML" } |
                ConvertTo-Json -Compress
        Invoke-WebRequest `
            -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method POST -ContentType "application/json" -Body $body `
            -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Log "telegram_send_failed: $($_.Exception.Message)"
        return $false
    }
}

function Send-Pushover {
    param([string]$Msg, [string]$Title = "Miru Watchdog")
    $token   = $env:PUSHOVER_API_TOKEN
    $user    = $env:PUSHOVER_USER_KEY
    $enabled = $env:PUSHOVER_ENABLED
    if (-not $token -or -not $user -or $enabled -notin @("true","1","yes")) { return }
    try {
        $body = "token=$([uri]::EscapeDataString($token))&user=$([uri]::EscapeDataString($user))&title=$([uri]::EscapeDataString($Title))&message=$([uri]::EscapeDataString($Msg))"
        Invoke-WebRequest `
            -Uri "https://api.pushover.net/1/messages.json" `
            -Method POST -ContentType "application/x-www-form-urlencoded" -Body $body `
            -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
    } catch {
        Write-Log "pushover_send_failed: $($_.Exception.Message)"
    }
}

function Send-Alert {
    param([string]$Msg, [string]$PushoverTitle = "Miru Watchdog")
    $tgOk = Send-Telegram -Msg $Msg
    if (-not $tgOk) {
        $plain = $Msg -replace '<[^>]+>',''
        Send-Pushover -Msg $plain -Title $PushoverTitle
        Write-Log "pushover_fallback_used"
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
        gateway           = @{ first_fail_utc = $null; restart_count = 0; restart_window_start_utc = $null; escalated = $false }
        dispatch_listener = @{ first_fail_utc = $null; restart_count = 0; restart_window_start_utc = $null; escalated = $false }
        n8n               = @{ first_fail_utc = $null; restart_count = 0; restart_window_start_utc = $null; escalated = $false }
    }
    if (-not (Test-Path $stateFile)) { return $empty }
    try {
        $raw = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $result = @{}
        foreach ($key in $empty.Keys) {
            $src = $raw.$key
            $result[$key] = @{
                first_fail_utc           = if ($src) { $src.first_fail_utc } else { $null }
                restart_count            = if ($src -and $src.restart_count) { [int]$src.restart_count } else { 0 }
                restart_window_start_utc = if ($src) { $src.restart_window_start_utc } else { $null }
                escalated                = if ($src -and $src.escalated) { [bool]$src.escalated } else { $false }
            }
        }
        return $result
    } catch {
        return $empty
    }
}

function Write-State {
    param([hashtable]$S)
    try {
        $S | ConvertTo-Json -Depth 4 -Compress | Set-Content -Path $stateFile -Encoding UTF8
    } catch {
        Write-Log "state_write_failed: $($_.Exception.Message)"
    }
}

function Invoke-Restart {
    param([hashtable]$Svc)
    if ($Svc.restart_type -eq "docker") {
        try {
            $result = & docker restart $Svc.container_name 2>&1
            Write-Log "$($Svc.key)_docker_restart container=$($Svc.container_name) result=$result"
            return $true
        } catch {
            Write-Log "$($Svc.key)_docker_restart_failed: $($_.Exception.Message)"
            return $false
        }
    } else {
        try {
            Start-ScheduledTask -TaskName $Svc.restart_task -ErrorAction Stop
            Write-Log "$($Svc.key)_restart_task_started task=$($Svc.restart_task)"
            return $true
        } catch {
            Write-Log "$($Svc.key)_restart_task_failed: $($_.Exception.Message)"
            return $false
        }
    }
}

$services = @(
    @{
        key          = "gateway"
        label        = "MCP Gateway (18766)"
        health_url   = "http://127.0.0.1:18766/health"
        restart_type = "task"
        restart_task = "MiruRestartMcpGateway"
    },
    @{
        key          = "dispatch_listener"
        label        = "Dispatch Listener (19100)"
        health_url   = "http://127.0.0.1:19100/health"
        restart_type = "task"
        restart_task = "MiruDispatchListener"
    },
    @{
        key            = "n8n"
        label          = "n8n (15678)"
        health_url     = "http://127.0.0.1:15678/healthz"
        restart_type   = "docker"
        container_name = "miru-n8n"
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
        if ($state[$key].first_fail_utc -or $state[$key].escalated) {
            $wasEscalated = $state[$key].escalated
            Write-Log "${key}_recovered escalated=$wasEscalated"
            $msg = "✅ <b>$($svc.label) recovered</b>`nService is responding normally."
            if ($wasEscalated) { $msg += "`n<i>Escalation cleared — watchdog resuming normal monitoring.</i>" }
            Send-Alert $msg
            $state[$key] = @{ first_fail_utc = $null; restart_count = 0; restart_window_start_utc = $null; escalated = $false }
            $changed = $true
        } else {
            Write-Log "${key}_ok"
        }
        continue
    }

    # First failure — record timestamp, wait for next poll before acting
    if (-not $state[$key].first_fail_utc) {
        $state[$key].first_fail_utc = $nowUtc.ToString("o")
        $changed = $true
        Write-Log "${key}_first_fail ts=$($state[$key].first_fail_utc)"
        continue
    }

    $firstFail = [datetime]::Parse($state[$key].first_fail_utc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
    $downSec   = ($nowUtc - $firstFail).TotalSeconds

    if ($downSec -lt 90) {
        Write-Log "${key}_still_down down_sec=$([int]$downSec)"
        continue
    }

    # Already escalated — don't restart, just log
    if ($state[$key].escalated) {
        Write-Log "${key}_escalated_skip down_sec=$([int]$downSec)"
        continue
    }

    # Check restart ceiling — reset window if it's expired
    $windowStart = $state[$key].restart_window_start_utc
    if ($windowStart) {
        $winAge = ($nowUtc - [datetime]::Parse($windowStart, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)).TotalSeconds
        if ($winAge -gt $RESTART_WINDOW_S) {
            $state[$key].restart_count            = 0
            $state[$key].restart_window_start_utc = $null
            $changed = $true
        }
    }

    if ($state[$key].restart_count -ge $MAX_RESTARTS_PER_WINDOW) {
        # Ceiling hit — escalate
        Write-Log "${key}_ceiling_hit restarts=$($state[$key].restart_count) down_sec=$([int]$downSec)"
        $state[$key].escalated = $true
        $changed = $true
        Send-Alert "🚨 <b>$($svc.label) — needs your attention</b>`nRestarted $($state[$key].restart_count) times in $($RESTART_WINDOW_S / 60) minutes with no recovery.`nWatchdog has stopped retrying. Check the service manually." -PushoverTitle "Miru — Service Down"
        continue
    }

    # Trigger restart
    Write-Log "${key}_restart_triggered down_sec=$([int]$downSec) attempt=$($state[$key].restart_count + 1)/$MAX_RESTARTS_PER_WINDOW"
    Send-Alert "🔄 <b>$($svc.label) auto-restart</b>`nDown for $([int]$downSec)s — restart attempt $($state[$key].restart_count + 1)/$MAX_RESTARTS_PER_WINDOW."

    $ok = Invoke-Restart -Svc $svc
    if (-not $ok) {
        Send-Alert "❌ <b>$($svc.label) restart FAILED</b>`nCould not execute restart command. Check the watchdog log." -PushoverTitle "Miru — Restart Failed"
    }

    if (-not $state[$key].restart_window_start_utc) {
        $state[$key].restart_window_start_utc = $nowUtc.ToString("o")
    }
    $state[$key].restart_count  += 1
    $state[$key].first_fail_utc  = $null
    $changed = $true
}

if ($changed) { Write-State -State $state }
