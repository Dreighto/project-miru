# update_workers.ps1 -- Update claude-code, gemini-cli, codex npm globals and Ollama.
# Verifies each binary is callable after update, then sends one Telegram summary.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\update_workers.ps1
#
# Runs automatically via the MiruWorkerUpdater scheduled task (nightly 3am).
# Register the task with: windows\register_updater_task.ps1 (requires elevation).
#
# Credentials: reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env.
# Values are never echoed; only variable names are referenced in log output.

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot = Split-Path -Parent $PSScriptRoot   # tools\ -> repo root
$logDir   = Join-Path $repoRoot "logs"
$logFile  = Join-Path $logDir "update_workers.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host "[update-workers] $Msg"
}

# ── Load .env ──────────────────────────────────────────────────────────────────
$commonPath = Join-Path $repoRoot "windows\op_miru_common.ps1"
if (-not (Test-Path $commonPath)) {
    Write-Log "ERROR: op_miru_common.ps1 not found at $commonPath"
    exit 1
}
. $commonPath
$envResult = Import-OpMiruDotEnv -RepoRoot $repoRoot
Write-Log "env_load: exists=$($envResult.Exists) keys=$($envResult.LoadedKeys.Count)"

$botToken = [Environment]::GetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "Process")
$chatId   = [Environment]::GetEnvironmentVariable("TELEGRAM_CHAT_ID",   "Process")

# ── Helper: send Telegram message ─────────────────────────────────────────────
function Send-TelegramMessage {
    param([string]$Text)
    if (-not $botToken -or -not $chatId) {
        Write-Log "telegram_skip: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured"
        return
    }
    try {
        $body = @{
            chat_id    = $chatId
            text       = $Text
            parse_mode = "Markdown"
        } | ConvertTo-Json -Compress
        $resp = Invoke-RestMethod `
            -Uri         "https://api.telegram.org/bot$botToken/sendMessage" `
            -Method      Post `
            -Body        $body `
            -ContentType "application/json" `
            -TimeoutSec  15 `
            -ErrorAction Stop
        Write-Log "telegram_sent message_id=$($resp.result.message_id)"
    }
    catch {
        Write-Log "telegram_error: $($_.Exception.Message)"
    }
}

# ── Helper: get npm global package version ─────────────────────────────────────
function Get-NpmGlobalVersion {
    param([string]$Package)
    try {
        $raw = & npm list -g $Package --depth=0 2>&1
        $line = @($raw) | Where-Object { $_ -match "@\d+\.\d+" } | Select-Object -First 1
        if ($line) {
            $m = [regex]::Match($line, "@(\d+\.\d+[\.\d]*)")
            if ($m.Success) { return $m.Groups[1].Value }
        }
    }
    catch {}
    return $null
}

# ── Helper: update npm global package ─────────────────────────────────────────
function Update-NpmGlobal {
    param([string]$Package)
    try {
        $out     = @(& npm update -g $Package 2>&1 | ForEach-Object { [string]$_ })
        $success = ($LASTEXITCODE -eq 0)
        return @{ Success = $success; Output = ($out -join "`n") }
    }
    catch {
        return @{ Success = $false; Output = $_.Exception.Message }
    }
}

# ── Helper: test binary callable ──────────────────────────────────────────────
function Test-BinaryCallable {
    param([string]$Binary, [string]$Flag)
    if (-not (Get-Command $Binary -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        $null = & $Binary $Flag 2>&1
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

# ── Tool definitions ───────────────────────────────────────────────────────────
$npmTools = @(
    @{ Name = "claude-code"; NpmPkg = "@anthropic-ai/claude-code"; Binary = "claude.cmd"; VersionFlag = "--version" },
    @{ Name = "gemini-cli";  NpmPkg = "@google/gemini-cli";        Binary = "gemini.cmd"; VersionFlag = "--version" },
    @{ Name = "codex";       NpmPkg = "@openai/codex";             Binary = "codex.cmd";  VersionFlag = "--version" }
)

$results    = [System.Collections.Generic.List[object]]::new()
$hasFailure = $false

Write-Log "=== update_workers BEGIN ==="

# ── Update npm tools ───────────────────────────────────────────────────────────
foreach ($tool in $npmTools) {
    Write-Log "checking $($tool.Name) ($($tool.NpmPkg)) ..."

    $before = Get-NpmGlobalVersion -Package $tool.NpmPkg
    Write-Log "$($tool.Name) before=$before"

    $upd = Update-NpmGlobal -Package $tool.NpmPkg
    Write-Log "$($tool.Name) update_exit=$($upd.Success)"

    $after = Get-NpmGlobalVersion -Package $tool.NpmPkg
    Write-Log "$($tool.Name) after=$after"

    $ok      = Test-BinaryCallable -Binary $tool.Binary -Flag $tool.VersionFlag
    $changed = ($null -ne $before) -and ($null -ne $after) -and ($before -ne $after)
    $status  = if (-not $upd.Success) {
        "FAIL_UPDATE"
    }
    elseif (-not $ok) {
        "FAIL_BINARY"
    }
    else {
        "ok"
    }

    if ($status -ne "ok") { $hasFailure = $true }

    $detail = if ($status -ne "ok") {
        $raw = $upd.Output
        if ($raw.Length -gt 300) { $raw.Substring(0, 300) + "..." } else { $raw }
    }
    else { $null }

    $results.Add([pscustomobject]@{
        Name    = $tool.Name
        Before  = $before
        After   = $after
        Changed = $changed
        Status  = $status
        Detail  = $detail
    })
    Write-Log "$($tool.Name) status=$status changed=$changed"
}

# ── Update Ollama ─────────────────────────────────────────────────────────────
Write-Log "checking ollama ..."

function Get-OllamaVersion {
    try {
        $raw = @(& ollama --version 2>&1 | ForEach-Object { [string]$_ })
        $line = $raw -join " "
        $m = [regex]::Match($line, "(\d+\.\d+[\.\d]*)")
        if ($m.Success) { return $m.Groups[1].Value }
    }
    catch {}
    return $null
}

$ollamaBefore = Get-OllamaVersion
Write-Log "ollama before=$ollamaBefore"

$ollamaStatus = "ok"
$ollamaDetail = $null

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Log "ollama_skip: winget not found in PATH"
    $ollamaStatus = "SKIP_NO_WINGET"
}
else {
    try {
        $wgOut  = @(& winget upgrade --id Ollama.Ollama --silent `
            --accept-package-agreements --accept-source-agreements 2>&1 `
            | ForEach-Object { [string]$_ })
        $wgExit = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        Write-Log "ollama_winget exit=$wgExit"

        # 0 = upgraded; -1978335189 (0x8A150013) = no applicable upgrade found (already current)
        if ($wgExit -ne 0 -and $wgExit -ne -1978335189) {
            $raw = $wgOut -join "`n"
            # Also treat "No applicable upgrade found" in output as success
            if ($raw -match "No applicable upgrade") {
                Write-Log "ollama_winget: no upgrade available (output match)"
            }
            else {
                $ollamaStatus = "FAIL_UPDATE"
                $ollamaDetail = if ($raw.Length -gt 300) { $raw.Substring(0, 300) + "..." } else { $raw }
                $hasFailure   = $true
                Write-Log "ollama_winget FAILED: $ollamaDetail"
            }
        }
    }
    catch {
        $ollamaStatus = "FAIL_UPDATE"
        $ollamaDetail = $_.Exception.Message
        $hasFailure   = $true
        Write-Log "ollama_winget exception: $ollamaDetail"
    }
}

$ollamaAfter   = Get-OllamaVersion
$ollamaChanged = ($null -ne $ollamaBefore) -and ($null -ne $ollamaAfter) -and ($ollamaBefore -ne $ollamaAfter)
Write-Log "ollama after=$ollamaAfter status=$ollamaStatus changed=$ollamaChanged"

$results.Add([pscustomobject]@{
    Name    = "ollama"
    Before  = $ollamaBefore
    After   = $ollamaAfter
    Changed = $ollamaChanged
    Status  = $ollamaStatus
    Detail  = $ollamaDetail
})

# ── Build Telegram summary ────────────────────────────────────────────────────
$timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm") + " UTC"
$lines     = [System.Collections.Generic.List[string]]::new()
$lines.Add("*MiruWorkerUpdater* — $timestamp")
$lines.Add("")

foreach ($r in $results) {
    if ($r.Status -eq "SKIP_NO_WINGET") {
        $lines.Add("⬛ *$($r.Name)*: skipped (winget not available)")
        continue
    }

    $v = if ($r.After) { $r.After } elseif ($r.Before) { $r.Before } else { "unknown" }
    if ($r.Status -ne "ok") {
        $lines.Add("❌ *$($r.Name)*: $v (update failed)")
        if ($r.Detail) {
            $snippet = $r.Detail
            if ($snippet.Length -gt 120) { $snippet = $snippet.Substring(0, 120) + "..." }
            $lines.Add("   ``$snippet``")
        }
    }
    elseif ($r.Changed) {
        $lines.Add("✅ *$($r.Name)*: $($r.Before) → $($r.After)")
    }
    else {
        $lines.Add("⬜ *$($r.Name)*: $v (current)")
    }
}

$lines.Add("")
if (-not $hasFailure) {
    $lines.Add("All workers up to date.")
}
else {
    $lines.Add("⚠️ One or more updates failed. Check ``logs/update_workers.log``.")
}

$message = $lines -join "`n"
Write-Log "sending_telegram length=$($message.Length)"
Send-TelegramMessage -Text $message

Write-Log "=== update_workers DONE hasFailure=$hasFailure ==="
exit $(if ($hasFailure) { 1 } else { 0 })
