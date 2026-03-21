param(
    [Parameter(Mandatory = $true)]
    [string]$CheckpointName,
    [Parameter(Mandatory = $true)]
    [ValidateSet("snapshot", "primary", "cloud")]
    [string]$SourceType,
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir

$sources = [ordered]@{
    snapshot = Join-Path $repoRoot "data\snapshots\$CheckpointName"
    primary  = Join-Path "E:\MiruBackups" $CheckpointName
    cloud    = Join-Path "G:\My Drive\MiruBackups" $CheckpointName
}

$sourceRoot = $sources[$SourceType]
if (-not (Test-Path -LiteralPath $sourceRoot)) {
    throw "Checkpoint source path not found: $sourceRoot"
}

$manifestPath = Join-Path $sourceRoot "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Manifest not found in checkpoint source: $manifestPath"
}

function Ensure-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Invoke-RestoreCopy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [switch]$PlanOnlyMode
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return [pscustomobject]@{
            source      = $Source
            destination = $Destination
            copied      = $false
            skipped     = $true
            reason      = "Source missing"
        }
    }

    if ($PlanOnlyMode) {
        return [pscustomobject]@{
            source      = $Source
            destination = $Destination
            copied      = $false
            skipped     = $false
            reason      = "PlanOnly"
        }
    }

    Ensure-Directory -Path $Destination
    $log = robocopy $Source $Destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /COPY:DAT /DCOPY:DAT
    $exitCode = $LASTEXITCODE
    if ($exitCode -ge 8) {
        throw "Robocopy failed for '$Source' -> '$Destination' with exit code $exitCode."
    }
    return [pscustomobject]@{
        source      = $Source
        destination = $Destination
        copied      = $true
        skipped     = $false
        reason      = "Restored"
        exit_code   = $exitCode
    }
}

$restoreTargets = @(
    @{ Name = "data";      Source = (Join-Path $sourceRoot "data");      Destination = (Join-Path $repoRoot "data") },
    @{ Name = "tools";     Source = (Join-Path $sourceRoot "tools");     Destination = (Join-Path $repoRoot "tools") },
    @{ Name = "windows";   Source = (Join-Path $sourceRoot "windows");   Destination = (Join-Path $repoRoot "windows") },
    @{ Name = "dashboard"; Source = (Join-Path $sourceRoot "dashboard"); Destination = (Join-Path $repoRoot "dashboard") },
    @{ Name = "docs";      Source = (Join-Path $sourceRoot "docs");      Destination = (Join-Path $repoRoot "docs") }
)

$results = foreach ($target in $restoreTargets) {
    Invoke-RestoreCopy -Source $target.Source -Destination $target.Destination -PlanOnlyMode:$PlanOnly
}

[pscustomobject]@{
    checkpoint_name = $CheckpointName
    source_type     = $SourceType
    source_root     = $sourceRoot
    manifest_path   = $manifestPath
    plan_only       = [bool]$PlanOnly
    restore_results = $results
} | ConvertTo-Json -Depth 6
