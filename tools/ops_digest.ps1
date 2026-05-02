# ops_digest.ps1 — fetch /api/ops/report and post a formatted summary to Telegram.
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

# Format Telegram message (Markdown)
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('*Miru Ops Digest*')
$lines.Add("_Generated: $($report.generated_at)_")
$lines.Add('')

$lines.Add("*DLQ*: $($report.dlq_count) entries")
$lines.Add('')

if ($report.budget) {
    $budgetJson = $report.budget | ConvertTo-Json -Compress -Depth 3
    $lines.Add('*Budget*')
    $lines.Add("``$budgetJson``")
    $lines.Add('')
}

if ($report.last_completions -and $report.last_completions.Count -gt 0) {
    $lines.Add('*Last completions*')
    foreach ($c in $report.last_completions) {
        $ticket  = if ($c.ticket_id) { $c.ticket_id } else { '?' }
        $status  = if ($c.status)    { $c.status }    else { '?' }
        $summary = if ($c.summary)   { $c.summary }   else { '' }
        if ($summary.Length -gt 60) { $summary = $summary.Substring(0, 60) + '...' }
        $lines.Add("• ``$ticket`` [$status] $summary")
    }
    $lines.Add('')
}

if ($report.last_heartbeats -and $report.last_heartbeats.Count -gt 0) {
    $lines.Add('*Active workers*')
    foreach ($h in $report.last_heartbeats) {
        $wid    = if ($h.worker_id)  { $h.worker_id }  else { '?' }
        $ticket = if ($h.ticket_id)  { $h.ticket_id }  else { '?' }
        $step   = if ($h.step)       { $h.step }       else { '?' }
        $ts     = if ($h.ts)         { $h.ts }         else { '?' }
        $lines.Add("• $wid — $ticket @ $step ($ts)")
    }
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
