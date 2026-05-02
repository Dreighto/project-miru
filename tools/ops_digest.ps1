# ops_digest.ps1 — fetch /api/ops/report and post a formatted summary to Telegram.
# Format: operator-friendly plain English with emoji scanning.
# Usage: powershell -ExecutionPolicy Bypass -File tools\ops_digest.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Load .env from repo root
$envPath = Join-Path $PSScriptRoot '..' '.env'
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        if ($_ -match '^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($Matches[1], $val, 'Process')
        }
    }
}

$token  = $env:TELEGRAM_BOT_TOKEN
$chatId = $env:TELEGRAM_CHAT_ID

if (-not $token -or -not $chatId) {
    Write-Error 'TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env'
    exit 1
}

# Fetch ops report
try {
    $report = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/ops/report' -Method GET -TimeoutSec 10
} catch {
    Write-Error "Failed to fetch /api/ops/report: $_"
    exit 1
}

# ── Helpers ────────────────────────────────────────────────────────────────────
function Format-HumanTime {
    param([string]$Iso)
    if (-not $Iso) { return 'unknown' }
    try {
        $dt   = [datetime]::Parse($Iso, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        $now  = [datetime]::UtcNow
        $diff = $now - $dt
        if ($diff.TotalMinutes -lt 60) { return "$([int]$diff.TotalMinutes)m ago" }
        if ($diff.TotalHours   -lt 24) { return "$([int]$diff.TotalHours)h ago" }
        if ($diff.TotalDays    -lt 7)  { return "$([int]$diff.TotalDays)d ago" }
        return $dt.ToString('MMM d')
    } catch {
        return $Iso
    }
}

function Format-WorkerStep {
    param([string]$Step)
    $map = @{
        pre_flight          = 'starting up'
        opening_pr          = 'opening a PR'
        awaiting_bugbot     = 'waiting for code review bot'
        post_merge_cleanup  = 'cleaning up after merge'
        running_pre_commit  = 'running checks'
        writing_tests       = 'writing tests'
    }
    if ($map.ContainsKey($Step)) { return $map[$Step] }
    return if ($Step) { $Step } else { '?' }
}

function Format-Budget {
    param($Budget)
    if (-not $Budget) { return 'No budget data' }

    # Handle {state: ...} format (future)
    $stateVal = try { [string]$Budget.state } catch { $null }
    if ($stateVal -and $stateVal -ne '') {
        switch ($stateVal) {
            'safe'  { return 'Safe — no limits hit' }
            'watch' { return 'Watch — approaching limit ⚠️' }
            'limit' { return 'At limit — new work needs approval ❌' }
        }
    }

    # Handle legacy array-of-providers format
    $providers = @()
    if ($Budget -is [array]) { $providers = $Budget }
    if ($providers.Count -eq 0) { return 'No budget data' }

    $pcts = @($providers | Where-Object { $null -ne $_.remaining_percent } | ForEach-Object { [double]$_.remaining_percent })
    $minPct = if ($pcts.Count -gt 0) { ($pcts | Measure-Object -Minimum).Minimum } else { 100 }
    $detail = ($providers | ForEach-Object { "$($_.provider) $($_.remaining_percent)% left" }) -join ' · '
    if ($minPct -ge 50) { return "Safe — $detail" }
    if ($minPct -ge 20) { return "Watch ⚠️ — $detail" }
    return "At limit ❌ — $detail"
}

# ── Build message ──────────────────────────────────────────────────────────────
$ts    = Format-HumanTime -Iso ([string]$report.generated_at)
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("🛠 *Miru Status* — $ts")
$lines.Add('')

# Filter out TEST-* tickets — operator view only shows real work
$real = @()
if ($report.last_completions -and $report.last_completions.Count -gt 0) {
    $real = @($report.last_completions | Where-Object { ([string]$_.ticket_id) -notmatch '^TEST-' })
}

$shipped = @($real | Where-Object { ([string]$_.status) -eq 'CONFIRMED_WORKING' })
$failed  = @($real | Where-Object { ([string]$_.status) -eq 'FAILED' })
$partial = @($real | Where-Object { ([string]$_.status) -eq 'INCONCLUSIVE' })

if ($shipped.Count -gt 0) {
    $lines.Add('*What shipped*')
    foreach ($c in $shipped | Select-Object -Last 5) {
        $summary = if ($c.summary) { [string]$c.summary } else { '(no description)' }
        if ($summary.Length -gt 120) { $summary = $summary.Substring(0, 120) + '...' }
        $ticket = if ($c.ticket_id) { [string]$c.ticket_id } else { '?' }
        $lines.Add("✅ *$ticket* — $summary")
    }
    $lines.Add('')
}

if ($failed.Count -gt 0) {
    $lines.Add('*What failed*')
    foreach ($c in $failed | Select-Object -Last 3) {
        $summary = if ($c.summary) { [string]$c.summary } else { '(no description)' }
        if ($summary.Length -gt 120) { $summary = $summary.Substring(0, 120) + '...' }
        $ticket = if ($c.ticket_id) { [string]$c.ticket_id } else { '?' }
        $lines.Add("❌ *$ticket* — $summary")
    }
    $lines.Add('')
}

if ($partial.Count -gt 0) {
    $lines.Add('*Needs attention*')
    foreach ($c in $partial | Select-Object -Last 3) {
        $summary = if ($c.summary) { [string]$c.summary } else { '(no description)' }
        if ($summary.Length -gt 120) { $summary = $summary.Substring(0, 120) + '...' }
        $ticket = if ($c.ticket_id) { [string]$c.ticket_id } else { '?' }
        $lines.Add("⚠️ *$ticket* — $summary")
    }
    $lines.Add('')
}

if ($shipped.Count -eq 0 -and $failed.Count -eq 0 -and $partial.Count -eq 0) {
    $lines.Add('_No recent real activity_')
    $lines.Add('')
}

# DLQ
$dlqCount = if ($null -ne $report.dlq_count) { [int]$report.dlq_count } else { 0 }
if ($dlqCount -gt 20) {
    $lines.Add("*Failed dispatch queue:* $dlqCount attempts ⚠️ — this is high, worth reviewing")
} elseif ($dlqCount -gt 0) {
    $lines.Add("*Failed dispatch queue:* $dlqCount attempts")
} else {
    $lines.Add('*Failed dispatch queue:* Clear')
}
$lines.Add('')

# Budget
$budgetText = Format-Budget -Budget $report.budget
$lines.Add("*Budget:* $budgetText")
$lines.Add('')

# Active workers — only show heartbeats within the last 15 minutes
$activeWorkers = [System.Collections.Generic.List[object]]::new()
if ($report.last_heartbeats -and $report.last_heartbeats.Count -gt 0) {
    $cutoff = [datetime]::UtcNow.AddMinutes(-15)
    foreach ($h in $report.last_heartbeats) {
        try {
            $hTs = [datetime]::Parse([string]$h.ts, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
            if ($hTs -gt $cutoff) { $activeWorkers.Add($h) }
        } catch { }
    }
}

if ($activeWorkers.Count -gt 0) {
    $lines.Add('*Active workers*')
    foreach ($h in $activeWorkers) {
        $wid    = if ($h.worker_id) { [string]$h.worker_id } else { '?' }
        $ticket = if ($h.ticket_id) { [string]$h.ticket_id } else { '?' }
        $step   = Format-WorkerStep -Step ([string]$h.step)
        $age    = Format-HumanTime -Iso ([string]$h.ts)
        $lines.Add("🔄 $wid — working on *$ticket* ($step, last seen $age)")
    }
} else {
    $lines.Add('*Active workers:* None right now')
}

$text = $lines -join "`n"

# Post to Telegram
$body = @{
    chat_id    = $chatId
    text       = $text
    parse_mode = 'Markdown'
} | ConvertTo-Json -Compress

$url = "https://api.telegram.org/bot$token/sendMessage"
try {
    $result = Invoke-RestMethod -Uri $url -Method POST -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
    Write-Host "Sent. message_id=$($result.result.message_id)"
} catch {
    Write-Error "Failed to send Telegram message: $_"
    exit 1
}
