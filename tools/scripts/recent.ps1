#!/usr/bin/env pwsh
# recent.ps1 — print last N entries from data/cc_completion_log.jsonl readably.
#
# Usage:
#   pwsh tools/scripts/recent.ps1            # last 5
#   pwsh tools/scripts/recent.ps1 -Count 10  # last 10
#
# Resolves the canonical repo path via `git rev-parse --git-common-dir` so it
# works correctly from any worktree. Read-only — never writes to the log.

[CmdletBinding()]
param(
    [int]$Count = 5,
    [string]$LogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Force UTF-8 stdout so non-ASCII characters (em-dash, middle-dot separator,
# repaired mojibake) survive piping into other tools, tests, or n8n
# Execute Command nodes. Default PowerShell on Windows is cp1252, which
# silently lossy-converts Unicode and breaks downstream consumers.
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if ($Count -lt 1) { $Count = 1 }

if (-not $LogPath) {
    # Resolution order — both anchored to the SCRIPT'S location, not CWD,
    # so the operator gets the same canonical log no matter where they run from.
    #
    #   1. git rev-parse --git-common-dir (run with -C $PSScriptRoot to handle
    #      the case of being invoked from outside any git tree). Resolves to
    #      the canonical repo even when the script lives in a worktree.
    #   2. $PSScriptRoot fallback (e.g. git binary missing).
    $repoRoot = $null
    $scriptDir = $PSScriptRoot

    if ($scriptDir) {
        $gitOutput = & git -C $scriptDir rev-parse --git-common-dir 2>$null
        if ($LASTEXITCODE -eq 0 -and $gitOutput) {
            $gitCommonDir = ([string]$gitOutput).Trim()
            if ($gitCommonDir) {
                # git-common-dir may be relative when run with -C; resolve
                # against the script directory, not the user's CWD.
                if (-not [IO.Path]::IsPathRooted($gitCommonDir)) {
                    $gitCommonDir = Join-Path $scriptDir $gitCommonDir
                }
                try {
                    $repoRoot = (Resolve-Path (Join-Path $gitCommonDir '..') -ErrorAction Stop).Path
                } catch {
                    $repoRoot = $null
                }
            }
        }

        if (-not $repoRoot) {
            # Script lives at <repo>\tools\scripts\recent.ps1 — climb two levels.
            $candidate = (Resolve-Path (Join-Path $scriptDir '..\..') -ErrorAction SilentlyContinue).Path
            if ($candidate -and (Test-Path (Join-Path $candidate 'data'))) {
                $repoRoot = $candidate
            }
        }
    }

    if (-not $repoRoot) {
        Write-Error "Could not resolve repo root. Run from inside the repo or pass -LogPath."
        exit 1
    }

    $LogPath = [IO.Path]::Combine($repoRoot, 'data', 'cc_completion_log.jsonl')
}

$logPath = $LogPath

if (-not (Test-Path $logPath)) {
    Write-Output 'No completed tasks yet.'
    exit 0
}

$lines = @(Get-Content -LiteralPath $logPath -Encoding UTF8 | Where-Object { $_.Trim().Length -gt 0 })
if ($lines.Count -eq 0) {
    Write-Output 'No completed tasks yet.'
    exit 0
}

# Mojibake repair: UTF-8 bytes were mis-decoded as Windows-1252 when the row
# was written. Reverse via cp1252 -> UTF-8 round-trip. Heuristic guard: only
# attempt when telltale chars present, and only accept the result if it
# round-trips cleanly (no replacement chars, no encode failure).
$script:MojibakeProbe = "[$([char]0x00c2)$([char]0x00c3)$([char]0x00e2)]"

function Repair-Mojibake {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    if ($Text -notmatch $script:MojibakeProbe) { return $Text }
    try {
        $cp1252 = [System.Text.Encoding]::GetEncoding(
            1252,
            [System.Text.EncoderFallback]::ExceptionFallback,
            [System.Text.DecoderFallback]::ExceptionFallback
        )
        $utf8 = New-Object System.Text.UTF8Encoding $false, $true
        $bytes = $cp1252.GetBytes($Text)
        $decoded = $utf8.GetString($bytes)
        if ($decoded.Contains([char]0xFFFD)) { return $Text }
        return $decoded
    } catch {
        return $Text
    }
}

function Format-Timestamp {
    param([string]$Iso)
    if ([string]::IsNullOrEmpty($Iso)) { return '' }
    try {
        $dt = [datetime]::Parse($Iso, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        return $dt.ToUniversalTime().ToString('yyyy-MM-dd HH:mm UTC')
    } catch {
        return $Iso
    }
}

function Format-FilesList {
    param([array]$Files)
    if (-not $Files -or $Files.Count -eq 0) { return '' }
    $count = $Files.Count
    $abbreviated = @()
    foreach ($f in $Files | Select-Object -First 3) {
        # Show last two path segments only (parent/file)
        $parts = $f -split '[\\/]'
        if ($parts.Count -ge 2) {
            $abbreviated += "$($parts[-2])/$($parts[-1])"
        } else {
            $abbreviated += $f
        }
    }
    $tail = if ($count -gt 3) { ", +$($count - 3) more" } else { '' }
    return "files ($count): $($abbreviated -join ', ')$tail"
}

function Format-OneLine {
    param([string]$Text, [int]$MaxLen = 200)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    $clean = ($Text -replace '\s+', ' ').Trim()
    if ($clean.Length -gt $MaxLen) {
        return $clean.Substring(0, $MaxLen - 3) + '...'
    }
    return $clean
}

function Format-Entry {
    param(
        [Parameter(Mandatory)]$Record,
        [int]$Index,
        [int]$Total
    )

    $lines = @()
    $tsStr = Format-Timestamp -Iso $Record.timestamp

    $headerParts = @()
    $headerParts += "[$Index/$Total]"
    if ($tsStr) { $headerParts += $tsStr }
    if ($Record.PSObject.Properties.Match('status').Count -and $Record.status) {
        $headerParts += $Record.status
    }
    if ($Record.PSObject.Properties.Match('ticket_id').Count -and $Record.ticket_id) {
        $headerParts += $Record.ticket_id
    }
    $lines += ($headerParts -join '  |  ')

    if ($Record.PSObject.Properties.Match('phase').Count -and $Record.phase) {
        $lines += "  phase: $($Record.phase)"
    }

    if ($Record.PSObject.Properties.Match('summary').Count -and $Record.summary) {
        $lines += "  $(Repair-Mojibake (Format-OneLine -Text $Record.summary -MaxLen 280))"
    }

    $brPrParts = @()
    if ($Record.PSObject.Properties.Match('branch').Count -and $Record.branch) {
        $brPrParts += "branch: $($Record.branch)"
    }
    if ($Record.PSObject.Properties.Match('pr_number').Count -and $null -ne $Record.pr_number) {
        $brPrParts += "PR #$($Record.pr_number)"
    }
    if ($Record.PSObject.Properties.Match('merge_commit_sha').Count -and $Record.merge_commit_sha) {
        $sha = ([string]$Record.merge_commit_sha).Substring(0, [math]::Min(7, ([string]$Record.merge_commit_sha).Length))
        $brPrParts += $sha
    }
    $sep = "  $([char]0x00b7)  "
    if ($brPrParts.Count -gt 0) { $lines += "  $($brPrParts -join $sep)" }

    if ($Record.PSObject.Properties.Match('files_touched').Count) {
        $filesLine = Format-FilesList -Files @($Record.files_touched)
        if ($filesLine) { $lines += "  $filesLine" }
    }

    if ($Record.PSObject.Properties.Match('test_evidence').Count -and $Record.test_evidence) {
        $lines += "  tests: $(Repair-Mojibake (Format-OneLine -Text $Record.test_evidence -MaxLen 180))"
    }

    if ($Record.PSObject.Properties.Match('notes').Count -and $Record.notes) {
        $lines += "  notes: $(Repair-Mojibake (Format-OneLine -Text $Record.notes -MaxLen 180))"
    }

    return ($lines -join "`n")
}

# Take the last N parseable entries. Skip malformed lines with a warning.
$selected = @()
$skipped = 0
for ($i = $lines.Count - 1; $i -ge 0 -and $selected.Count -lt $Count; $i--) {
    try {
        $obj = $lines[$i] | ConvertFrom-Json -ErrorAction Stop
        $selected = @($obj) + $selected
    } catch {
        $skipped++
        Write-Warning "Skipped malformed JSON at line $($i + 1)"
    }
}

if ($selected.Count -eq 0) {
    Write-Output 'No completed tasks yet.'
    exit 0
}

$total = $selected.Count
for ($i = 0; $i -lt $total; $i++) {
    if ($i -gt 0) { Write-Output '' }
    Write-Output (Format-Entry -Record $selected[$i] -Index ($i + 1) -Total $total)
}

if ($skipped -gt 0) {
    Write-Output ''
    Write-Output "($skipped malformed line$(if ($skipped -ne 1) { 's' }) skipped.)"
}
