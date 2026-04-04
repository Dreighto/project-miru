# Start or restart the Project Miru worktree dashboard on 18080 only.
# Canonical worktree dashboard launcher: single-instance, background start, PID-tracked, and health-verified.
param(
    [int]$Port = 18080,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "op_miru_common.ps1")
. (Join-Path $scriptDir "op_miru_runtime.ps1")

$logDir = Join-Path $repoRoot "data\startup-logs"
$pidFile = Join-Path $logDir "dashboard_18080.pid"
$stdoutLog = Join-Path $logDir "dashboard_18080_stdout.log"
$stderrLog = Join-Path $logDir "dashboard_18080_stderr.log"
$dashboardScriptPath = Join-Path $repoRoot "pm\app.py"
$rootUrl = "http://127.0.0.1:$Port/"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Get-DashboardPidMetadata {
    param([Parameter(Mandatory = $true)][string]$PidFilePath)

    if (-not (Test-Path $PidFilePath)) {
        return $null
    }

    try {
        $raw = Get-Content -Path $PidFilePath -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return [pscustomobject]@{
                Exists   = $true
                IsValid  = $false
                Reason   = "empty"
                Pid      = $null
                RepoRoot = ""
            }
        }

        $payload = $raw | ConvertFrom-Json -ErrorAction Stop
        $pidValue = 0
        if ($payload.PSObject.Properties.Name -contains "pid") {
            [void][int]::TryParse([string]$payload.pid, [ref]$pidValue)
        }

        $repoRootValue = ""
        $startedAtValue = ""
        $scriptPathValue = ""
        $portValue = ""
        if ($payload.PSObject.Properties.Name -contains "repo_root") {
            $repoRootValue = [string]$payload.repo_root
        }
        if ($payload.PSObject.Properties.Name -contains "started_at") {
            $startedAtValue = [string]$payload.started_at
        }
        if ($payload.PSObject.Properties.Name -contains "script_path") {
            $scriptPathValue = [string]$payload.script_path
        }
        if ($payload.PSObject.Properties.Name -contains "port") {
            $portValue = [string]$payload.port
        }

        return [pscustomobject]@{
            Exists      = $true
            IsValid     = ($pidValue -gt 0)
            Reason      = if ($pidValue -gt 0) { "" } else { "missing_pid" }
            Pid         = if ($pidValue -gt 0) { $pidValue } else { $null }
            RepoRoot    = $repoRootValue
            StartedAt   = $startedAtValue
            ScriptPath  = $scriptPathValue
            Port        = $portValue
        }
    }
    catch {
        return [pscustomobject]@{
            Exists   = $true
            IsValid  = $false
            Reason   = $_.Exception.Message
            Pid      = $null
            RepoRoot = ""
        }
    }
}

function Get-ProcessCommandLine {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    try {
        return [string]((Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine)
    }
    catch {
        return ""
    }
}

function Test-DashboardProcessMatch {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$TrackedRepoRoot = ""
    )

    $cmd = Get-ProcessCommandLine -ProcessId $ProcessId
    if ([string]::IsNullOrWhiteSpace($cmd)) {
        return $false
    }

    $normalizedCmd = $cmd.Replace("/", "\")
    $absolutePattern = [Regex]::Escape($dashboardScriptPath)
    if ($normalizedCmd -match $absolutePattern) {
        return $true
    }

    if ($normalizedCmd -notmatch "pm\\app\.py") {
        return $false
    }

    if (-not [string]::IsNullOrWhiteSpace($TrackedRepoRoot) -and $TrackedRepoRoot -eq $repoRoot) {
        return $true
    }

    return $false
}

function Remove-StaleDashboardPidFile {
    if (Test-Path $pidFile) {
        Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    }
}

function Wait-ForDashboardPortState {
    param(
        [Parameter(Mandatory = $true)][int]$PortNumber,
        [Parameter(Mandatory = $true)][bool]$ShouldExist,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $entry = Get-MiruProcessOnPort -Port $PortNumber
        if ($ShouldExist) {
            if ($entry) { return $entry }
        }
        else {
            if (-not $entry) { return $null }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return Get-MiruProcessOnPort -Port $PortNumber
}

function Stop-DashboardProcessIfOwned {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$TrackedRepoRoot = "",
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        return
    }

    if (-not (Test-DashboardProcessMatch -ProcessId $ProcessId -TrackedRepoRoot $TrackedRepoRoot)) {
        throw "Refusing to stop PID $ProcessId for $Reason because it is not a confirmed Project Miru worktree dashboard process."
    }

    Write-Host "Stopping tracked Project Miru dashboard PID $ProcessId ($Reason)."
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop
}

$tracked = Get-DashboardPidMetadata -PidFilePath $pidFile
if ($tracked -and -not $tracked.IsValid) {
    Write-Host "Removing stale dashboard PID file ($($tracked.Reason))."
    Remove-StaleDashboardPidFile
    $tracked = $null
}

if ($tracked -and $tracked.Pid) {
    $trackedProcess = Get-Process -Id $tracked.Pid -ErrorAction SilentlyContinue
    if ($trackedProcess) {
        Stop-DashboardProcessIfOwned -ProcessId $tracked.Pid -TrackedRepoRoot $tracked.RepoRoot -Reason "tracked PID file"
        [void](Wait-ForDashboardPortState -PortNumber $Port -ShouldExist:$false -TimeoutSeconds 20)
    }
    else {
        Write-Host "Tracked dashboard PID $($tracked.Pid) is no longer running. Removing stale PID file."
    }
    Remove-StaleDashboardPidFile
}

$listeningEntry = Get-MiruProcessOnPort -Port $Port
if ($listeningEntry) {
    if (Test-DashboardProcessMatch -ProcessId $listeningEntry.Pid -TrackedRepoRoot $repoRoot) {
        Stop-DashboardProcessIfOwned -ProcessId $listeningEntry.Pid -TrackedRepoRoot $repoRoot -Reason "existing listener on port $Port"
        $remaining = Wait-ForDashboardPortState -PortNumber $Port -ShouldExist:$false -TimeoutSeconds 20
        if ($remaining) {
            throw "Port $Port is still listening after stopping the previous dashboard PID $($listeningEntry.Pid)."
        }
    }
    else {
        $cmd = Get-ProcessCommandLine -ProcessId $listeningEntry.Pid
        throw "Port $Port is in use by PID $($listeningEntry.Pid), which is not a confirmed Project Miru worktree dashboard process. Command line: $cmd"
    }
}

Set-Content -Path $stdoutLog -Value "" -Encoding UTF8
Set-Content -Path $stderrLog -Value "" -Encoding UTF8

$env:PORT = "$Port"
$env:PROJECT_MIRU_DASHBOARD_PORT = "$Port"
$env:MIRU_MAIN_RUNTIME_ROOT = $repoRoot
$env:MIRU_DASHBOARD_NO_RELOAD = "1"
[Environment]::SetEnvironmentVariable("WERKZEUG_SERVER_FD", $null, "Process")
[Environment]::SetEnvironmentVariable("WERKZEUG_RUN_MAIN", $null, "Process")

$python = Get-Command python -ErrorAction Stop
Write-Host "Starting Project Miru dashboard on port $Port in the background."
$process = Start-Process `
    -FilePath $python.Source `
    -ArgumentList @($dashboardScriptPath) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

[pscustomobject]@{
    pid         = $process.Id
    port        = $Port
    started_at  = (Get-Date).ToString("s")
    repo_root   = $repoRoot
    script_path = $dashboardScriptPath
} | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8

# Localhost probes must not go through a corporate HTTP proxy (common Invoke-WebRequest failure on 127.0.0.1).
$prevWebProxy = [System.Net.WebRequest]::DefaultWebProxy
[System.Net.WebRequest]::DefaultWebProxy = $null
# Allow time for heavy pm/app import before bind plus a slow first GET / (see Wait-OpMiruHttp HTTP timeout).
$dashboardProbeTimeoutSeconds = 240
try {
    # RetryDelaySeconds is sleep between attempts; Wait-OpMiruHttp uses a separate per-attempt HTTP timeout (min 180s, max 300s).
    $probe = Wait-OpMiruHttp -Url $rootUrl -TimeoutSeconds $dashboardProbeTimeoutSeconds -RetryDelaySeconds 10
    if (-not $probe.Ok) {
        $tail = if (Test-Path $stderrLog) {
            ((Get-Content -Path $stderrLog -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine).Trim()
        }
        else {
            ""
        }

        $running = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($running) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        Remove-StaleDashboardPidFile

        $detail = if ($tail) { " Last stderr lines:`n$tail" } else { "" }
        throw "Project Miru dashboard did not become reachable on $rootUrl within $dashboardProbeTimeoutSeconds seconds.$detail"
    }

    $stabilityProbe = Wait-OpMiruHttp -Url $rootUrl -TimeoutSeconds 20 -RetryDelaySeconds 10
}
finally {
    [System.Net.WebRequest]::DefaultWebProxy = $prevWebProxy
}

if (-not $stabilityProbe.Ok) {
    $running = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($running) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-StaleDashboardPidFile
    throw "Project Miru dashboard answered once but did not stay healthy on $rootUrl."
}

$listener = Wait-ForDashboardPortState -PortNumber $Port -ShouldExist:$true -TimeoutSeconds 5
if (-not $listener) {
    throw "Project Miru dashboard responded to HTTP but no listener was detected on port $Port."
}

if ($listener.Pid -ne $process.Id) {
    $cmd = Get-ProcessCommandLine -ProcessId $listener.Pid
    if (-not (Test-DashboardProcessMatch -ProcessId $listener.Pid -TrackedRepoRoot $repoRoot)) {
        throw "HTTP on port $Port came up on PID $($listener.Pid), not the newly started PID $($process.Id). Command line: $cmd"
    }
}

$matchingProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cmd = [string]$_.CommandLine
    $cmd -and $cmd.Replace("/", "\") -match ([Regex]::Escape($dashboardScriptPath))
})
if ($matchingProcesses.Count -gt 1) {
    $pids = ($matchingProcesses | Select-Object -ExpandProperty ProcessId) -join ", "
    throw "Multiple Project Miru dashboard processes are running for this worktree: $pids"
}

Write-Host "Project Miru dashboard is ready on $rootUrl (PID $($listener.Pid))."
