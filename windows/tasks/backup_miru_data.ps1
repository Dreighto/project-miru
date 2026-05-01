# backup_miru_data.ps1
# Backs up critical non-git files to D:\backups\miru\ and G:\My Drive\Miru Backups\
# Runs twice daily via MiruBackup scheduled task (registered in startup_all.ps1).
# Keeps 7 rolling days on both destinations.
# Sends a Telegram alert only on failure -- silent on success.
#
# Files backed up (gitignored or secret -- not covered by GitHub):
#   data/miru_memory.db         -- project memory (decisions, routing, worker profiles)
#   .env                        -- API keys and secrets
#   data/routing_history.jsonl  -- gitignored operational history
#   data/dispatch_dlq.jsonl     -- gitignored failure log
#   data/pending_callbacks.jsonl
#   data/cc_heartbeat_log.jsonl
#   logs/service_watchdog_state.json
#   logs/stall_recovery_state.json

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$windowsDir = Split-Path -Parent $PSScriptRoot
$logDir     = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$logFile = Join-Path $logDir "backup.log"

$KEEP_DAYS        = 7
$LOCAL_BACKUP_ROOT = "D:\backups\miru"
$GDRIVE_BACKUP_ROOT = "G:\My Drive\Miru Backups"

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

$filesToBackup = @(
    "data\miru_memory.db",
    ".env",
    "data\routing_history.jsonl",
    "data\dispatch_dlq.jsonl",
    "data\pending_callbacks.jsonl",
    "data\cc_heartbeat_log.jsonl",
    "logs\service_watchdog_state.json",
    "logs\stall_recovery_state.json"
)

Write-Log "=== backup run ==="

$dateStamp  = Get-Date -Format "yyyy-MM-dd_HH-mm"
$localDest  = Join-Path $LOCAL_BACKUP_ROOT $dateStamp
$gdriveDest = Join-Path $GDRIVE_BACKUP_ROOT $dateStamp

$errors      = @()
$copiedLocal = 0
$copiedDrive = 0

# Local backup
try {
    New-Item -ItemType Directory -Force -Path $localDest | Out-Null
} catch {
    $errors += "create_local_dir"
    Write-Log "ERROR: could not create local backup dir $localDest : $($_.Exception.Message)"
}

# Google Drive backup
$gdriveAvailable = Test-Path "G:\My Drive"
if (-not $gdriveAvailable) {
    Write-Log "WARNING: G:\My Drive not reachable -- skipping Google Drive backup this run"
} else {
    try {
        New-Item -ItemType Directory -Force -Path $gdriveDest | Out-Null
    } catch {
        $gdriveAvailable = $false
        Write-Log "ERROR: could not create Google Drive backup dir: $($_.Exception.Message)"
    }
}

foreach ($rel in $filesToBackup) {
    $src = Join-Path $repoRoot $rel
    if (-not (Test-Path $src)) {
        Write-Log "skip_not_found: $rel"
        continue
    }

    # Local copy
    if (Test-Path $localDest) {
        try {
            Copy-Item $src -Destination $localDest -Force -ErrorAction Stop
            $copiedLocal++
        } catch {
            $errors += "local:$rel"
            Write-Log "local_copy_failed: $rel error=$($_.Exception.Message)"
        }
    }

    # Google Drive copy
    if ($gdriveAvailable) {
        try {
            Copy-Item $src -Destination $gdriveDest -Force -ErrorAction Stop
            $copiedDrive++
        } catch {
            $errors += "gdrive:$rel"
            Write-Log "gdrive_copy_failed: $rel error=$($_.Exception.Message)"
        }
    }
}

Write-Log "copied local=$copiedLocal gdrive=$copiedDrive errors=$($errors.Count)"

# Prune old backups (keep last KEEP_DAYS days)
foreach ($root in @($LOCAL_BACKUP_ROOT, $GDRIVE_BACKUP_ROOT)) {
    if (-not (Test-Path $root)) { continue }
    try {
        $cutoff = (Get-Date).AddDays(-$KEEP_DAYS)
        Get-ChildItem -Path $root -Directory |
            Where-Object { $_.CreationTime -lt $cutoff } |
            ForEach-Object {
                Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
                Write-Log "pruned: $($_.FullName)"
            }
    } catch {
        Write-Log "prune_failed root=$root : $($_.Exception.Message)"
    }
}

if ($errors.Count -gt 0) {
    $errList = $errors -join ", "
    Write-Log "backup_completed_with_errors: $errList"
    Send-Telegram "❌ <b>Miru backup failed</b>`nFailed items: <code>$errList</code>`nCheck logs\backup.log for details."
} else {
    Write-Log "backup_ok local=$localDest"
}
