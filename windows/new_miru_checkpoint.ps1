param(
    [string]$CheckpointName = "20260319-040101-publication-staging-ready",
    [string]$MilestoneLabel = "publication-staging-ready",
    [string[]]$AdditionalNotes = @(),
    [switch]$SkipHealthCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir

. (Join-Path $scriptDir "op_miru_common.ps1")

$snapshotRoot = Join-Path $repoRoot "data\snapshots"
$snapshotPath = Join-Path $snapshotRoot $CheckpointName
$primaryRoot = "E:\MiruBackups"
$primaryPath = Join-Path $primaryRoot $CheckpointName
$cloudRoot = "G:\My Drive\MiruBackups"
$cloudPath = Join-Path $cloudRoot $CheckpointName
$catalogDbPath = Join-Path $repoRoot "data\card_catalog.db"
$docsPath = Join-Path $repoRoot "docs"

function Ensure-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Reset-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        & cmd /c "rmdir /s /q `"$Path`"" 2>$null | Out-Null
        if (Test-Path -LiteralPath $Path) {
            $pythonCleanup = @'
import os
import shutil
import sys

target = os.path.abspath(sys.argv[1])
if os.path.exists(target):
    shutil.rmtree("\\\\?\\" + target)
'@
            @'
'@ + $pythonCleanup + @'
'@ | python - $Path | Out-Null
        }
        if (Test-Path -LiteralPath $Path) {
            throw "Failed to clear existing checkpoint directory: $Path"
        }
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Invoke-RobocopySafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [string[]]$ExcludeDirectories = @()
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source path not found: $Source"
    }

    Ensure-Directory -Path $Destination
    $arguments = @($Source, $Destination, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/COPY:DAT", "/DCOPY:DAT")
    foreach ($excludedDirectory in $ExcludeDirectories) {
        if (-not [string]::IsNullOrWhiteSpace($excludedDirectory)) {
            $arguments += "/XD"
            $arguments += $excludedDirectory
        }
    }
    $log = & robocopy @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ge 8) {
        throw "Robocopy failed for '$Source' -> '$Destination' with exit code $exitCode."
    }
    return [pscustomobject]@{
        Source      = $Source
        Destination = $Destination
        ExitCode    = $exitCode
        Output      = @($log)
    }
}

function Get-CheckpointDbCounts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabasePath
    )

    $defaultCounts = [ordered]@{
        card_intelligence             = $null
        miru_card_insights           = $null
        miru_review_queue            = $null
        miru_action_history          = $null
        miru_publication_stage       = $null
        miru_publication_batches     = $null
        miru_publication_batch_items = $null
    }

    if (-not (Test-Path -LiteralPath $DatabasePath)) {
        return $defaultCounts
    }

    $python = @'
import json
import sqlite3
import sys

db_path = sys.argv[1]
tables = [
    "card_intelligence",
    "miru_card_insights",
    "miru_review_queue",
    "miru_action_history",
    "miru_publication_stage",
    "miru_publication_batches",
    "miru_publication_batch_items",
]
out = {}
conn = sqlite3.connect(db_path)
cur = conn.cursor()
for table in tables:
    exists = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        out[table] = None
        continue
    value = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out[table] = int(value)
print(json.dumps(out, sort_keys=True))
'@
    $jsonText = @'
'@ + $python + @'
'@ | python - $DatabasePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read SQLite counts from $DatabasePath"
    }
    $countsObject = $jsonText | ConvertFrom-Json
    foreach ($key in @($defaultCounts.Keys)) {
        if ($null -ne $countsObject.PSObject.Properties[$key]) {
            $defaultCounts[$key] = $countsObject.$key
        }
    }
    return $defaultCounts
}

function Get-RuntimeExpectation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [string]$MustContain = ""
    )

    $result = Test-OpMiruHttp -Url $Url -TimeoutSeconds 10 -MustContain $MustContain
    [pscustomobject]@{
        healthy = [bool]$result.Ok
        url     = $Url
        detail  = if ($result.Ok) {
            "HTTP $($result.StatusCode)"
        }
        elseif ($result.Error) {
            $result.Error
        }
        else {
            "Probe failed."
        }
    }
}

Ensure-Directory -Path $snapshotRoot
Ensure-Directory -Path $primaryRoot
Ensure-Directory -Path $cloudRoot
Reset-Directory -Path $snapshotPath
Reset-Directory -Path $primaryPath
Reset-Directory -Path $cloudPath

$health18765 = $null
$health18080 = $null
if (-not $SkipHealthCheck) {
    $health18765 = Get-RuntimeExpectation -Url "http://127.0.0.1:18765/api/health"
    $health18080 = Get-RuntimeExpectation -Url "http://127.0.0.1:18080/"
}

$copyOperations = New-Object System.Collections.Generic.List[object]

$snapshotSourceData = Join-Path $repoRoot "data"
$repoSnapshotsPath = Join-Path $repoRoot "data\snapshots"
$copyOperations.Add((Invoke-RobocopySafe -Source $snapshotSourceData -Destination (Join-Path $snapshotPath "data") -ExcludeDirectories @($repoSnapshotsPath)))

foreach ($folder in @("data", "tools", "windows", "dashboard")) {
    $source = Join-Path $repoRoot $folder
    $destination = Join-Path $primaryPath $folder
    if ($folder -eq "data") {
        $copyOperations.Add((Invoke-RobocopySafe -Source $source -Destination $destination -ExcludeDirectories @($repoSnapshotsPath)))
    }
    else {
        $copyOperations.Add((Invoke-RobocopySafe -Source $source -Destination $destination))
    }
}
if (Test-Path -LiteralPath $docsPath) {
    $copyOperations.Add((Invoke-RobocopySafe -Source $docsPath -Destination (Join-Path $primaryPath "docs")))
}

$copyOperations.Add((Invoke-RobocopySafe -Source $primaryPath -Destination $cloudPath))

$dbCounts = Get-CheckpointDbCounts -DatabasePath $catalogDbPath
$createdAt = (Get-Date).ToUniversalTime().ToString("o")
$manifestNotes = @(
    "Milestone checkpoint for $MilestoneLabel.",
    "Snapshot contains repo data only for fast local recovery.",
    "Primary and cloud backups contain data, tools, windows, dashboard, and docs when present.",
    "The live repo data\\snapshots tree is excluded from copied data to avoid recursive checkpoint nesting.",
    "This workflow does not mutate storefront code."
)
foreach ($note in $AdditionalNotes) {
    $noteText = [string]$note
    if (-not [string]::IsNullOrWhiteSpace($noteText)) {
        $manifestNotes += $noteText.Trim()
    }
}
if ($MilestoneLabel -eq "governed-insights-live-locals-voice") {
    $manifestNotes += @(
        "Miru now has governed backend intelligence.",
        "Publication gate is active in the backend.",
        "Safe storefront bridge is live.",
        "Live approved insights are available on the site.",
        "Miru voice is polished for locals-fluent storefront copy.",
        "Storefront mutation remains blocked except safe read-only insight access.",
        "No governance internals are exposed on 18080."
    )
}

$manifest = [ordered]@{
    checkpoint_name        = $CheckpointName
    created_at             = $createdAt
    milestone_label        = $MilestoneLabel
    source_worktree_path   = $repoRoot
    snapshot_path          = $snapshotPath
    primary_backup_path    = $primaryPath
    cloud_backup_path      = $cloudPath
    runtime_expectations   = [ordered]@{
        miru_dev_18765            = if ($health18765) { $health18765 } else { [pscustomobject]@{ healthy = $null; detail = "Not verified during checkpoint creation."; url = "http://127.0.0.1:18765/api/health" } }
        project_miru_18080        = if ($health18080) { $health18080 } else { [pscustomobject]@{ healthy = $null; detail = "Not verified during checkpoint creation."; url = "http://127.0.0.1:18080/" } }
        storefront_mutation_blocked = $true
    }
    db_counts              = $dbCounts
    included_paths         = [ordered]@{
        snapshot = @("data")
        primary  = if (Test-Path -LiteralPath $docsPath) { @("data", "tools", "windows", "dashboard", "docs") } else { @("data", "tools", "windows", "dashboard") }
        cloud    = if (Test-Path -LiteralPath $docsPath) { @("data", "tools", "windows", "dashboard", "docs") } else { @("data", "tools", "windows", "dashboard") }
    }
    notes                  = $manifestNotes
}

$manifestJson = $manifest | ConvertTo-Json -Depth 8
foreach ($manifestDir in @($snapshotPath, $primaryPath, $cloudPath)) {
    Set-Content -LiteralPath (Join-Path $manifestDir "manifest.json") -Value $manifestJson -Encoding UTF8
}

[pscustomobject]@{
    checkpoint_name       = $CheckpointName
    snapshot_path         = $snapshotPath
    primary_backup_path   = $primaryPath
    cloud_backup_path     = $cloudPath
    manifest_paths        = @(
        (Join-Path $snapshotPath "manifest.json"),
        (Join-Path $primaryPath "manifest.json"),
        (Join-Path $cloudPath "manifest.json")
    )
    copy_operations_count = $copyOperations.Count
    db_counts             = $dbCounts
    runtime_checks        = [ordered]@{
        miru_dev_18765     = $health18765
        project_miru_18080 = $health18080
    }
} | ConvertTo-Json -Depth 6
