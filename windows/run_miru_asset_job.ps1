<#
.SYNOPSIS
  Privileged asset-job runner for Scheduled Task "RunMiruAssetJob" (typically SYSTEM).

.CONTRACT
  Pointer file (absolute path to ONE approved Python script, path only — no args):
    D:\dev\tcg-watcher-worktree\data\overlays\asset_job_pointer.txt

  Allowed script root (validated target must resolve under this directory):
    D:\dev\tcg-watcher-worktree\tools\

  Log (overwritten each run; final line is the outcome marker):
    D:\dev\tcg-watcher-worktree\logs\asset_job.log

  Final markers (exactly one, last line of the log):
    ASSET_JOB_SUCCESS
    ASSET_JOB_FAILED

  Workers: write pointer → schtasks /Run /TN "RunMiruAssetJob" → read asset_job.log for the marker.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".."))

$pointerPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "data\overlays\asset_job_pointer.txt"))
$logDirectory = Join-Path $repoRoot "logs"
$logPath = [System.IO.Path]::GetFullPath((Join-Path $logDirectory "asset_job.log"))
$toolsRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "tools"))

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$overlayDir = Split-Path -Parent $pointerPath
if (-not (Test-Path -LiteralPath $overlayDir)) {
    New-Item -ItemType Directory -Force -Path $overlayDir | Out-Null
}

if (Test-Path -LiteralPath $logPath) {
    Remove-Item -LiteralPath $logPath -Force
}

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $logPath -Value "$timestamp`t$Message" -Encoding UTF8
}

$pythonExitCode = $null
$finalMarker = "ASSET_JOB_FAILED"
$scriptExitCode = 1

try {
    Write-LogLine "event=asset_job_start repo_root=$repoRoot"
    Write-LogLine "pointer_file=$pointerPath"
    Write-LogLine "tools_root=$toolsRoot"
    Write-LogLine "log_path=$logPath"

    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        Write-LogLine "validation_outcome=POINTER_FILE_MISSING"
        throw "Pointer file not found."
    }

    $rawPointer = (Get-Content -LiteralPath $pointerPath -Raw -Encoding UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($rawPointer)) {
        Write-LogLine "validation_outcome=POINTER_FILE_MISSING"
        throw "Pointer file is empty."
    }

    # Single line only: reject newlines / command chaining in pointer content
    if ($rawPointer -match "[\r\n]") {
        Write-LogLine "validation_outcome=UNSAFE_PATH_REJECTED"
        throw "Pointer must contain a single line (path only)."
    }

    $targetJobPath = $rawPointer.Trim()

    if (-not [System.IO.Path]::IsPathRooted($targetJobPath)) {
        Write-LogLine "validation_outcome=UNSAFE_PATH_REJECTED"
        Write-LogLine "target_job_path=$targetJobPath"
        throw "Path must be absolute."
    }

    $ext = [System.IO.Path]::GetExtension($targetJobPath)
    if (-not [string]::Equals($ext, ".py", [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-LogLine "validation_outcome=INVALID_EXTENSION"
        Write-LogLine "target_job_path=$targetJobPath"
        throw "Script must end with .py"
    }

    $resolvedJob = [System.IO.Path]::GetFullPath($targetJobPath)
    $toolsTrimmed = $toolsRoot.TrimEnd([char[]]@('\', '/'))
    $resolvedTools = $toolsTrimmed + [System.IO.Path]::DirectorySeparatorChar

    if (-not $resolvedJob.StartsWith($resolvedTools, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-LogLine "validation_outcome=UNSAFE_PATH_REJECTED"
        Write-LogLine "target_job_path=$resolvedJob"
        throw "Path must be under tools directory."
    }

    if (-not (Test-Path -LiteralPath $resolvedJob -PathType Leaf)) {
        Write-LogLine "validation_outcome=POINTER_TARGET_MISSING"
        Write-LogLine "target_job_path=$resolvedJob"
        throw "Target script does not exist."
    }

    Write-LogLine "validation_outcome=OK"
    Write-LogLine "target_job_path=$resolvedJob"

    $python = Get-Command python -ErrorAction Stop
    Write-LogLine "python_executable=$($python.Source)"
    Write-LogLine "working_directory=$repoRoot"
    Write-LogLine "execution_start"

    Push-Location -LiteralPath $repoRoot
    try {
        # Native stderr via 2>&1 surfaces as ErrorRecord; Stop would abort the pipeline before exit code is read.
        $prevEap = $ErrorActionPreference
        $prevNative = $null
        if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
            $prevNative = $PSNativeCommandUseErrorActionPreference
            $PSNativeCommandUseErrorActionPreference = $false
        }
        try {
            $ErrorActionPreference = "Continue"
            & $python.Source $resolvedJob 2>&1 | ForEach-Object {
                $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { [string]$_ }
                Write-LogLine "PYTHON_OUTPUT $line"
            }
        }
        finally {
            $ErrorActionPreference = $prevEap
            if ($null -ne $prevNative) {
                $PSNativeCommandUseErrorActionPreference = $prevNative
            }
        }
        $pythonExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    }
    finally {
        Pop-Location
    }

    Write-LogLine "execution_end"
    Write-LogLine "exit_code=$pythonExitCode"

    if ($pythonExitCode -eq 0) {
        $finalMarker = "ASSET_JOB_SUCCESS"
        $scriptExitCode = 0
    }
    else {
        $finalMarker = "ASSET_JOB_FAILED"
        $scriptExitCode = [Math]::Max(1, [Math]::Abs($pythonExitCode) % 256)
        if ($scriptExitCode -eq 0) { $scriptExitCode = 1 }
    }
}
catch {
    Write-LogLine "error=$($_.Exception.Message)"
    if ($null -ne $pythonExitCode) {
        Write-LogLine "exit_code=$pythonExitCode"
    }
    $finalMarker = "ASSET_JOB_FAILED"
    $scriptExitCode = 1
}
finally {
    Add-Content -Path $logPath -Value $finalMarker -Encoding UTF8
}

exit $scriptExitCode
