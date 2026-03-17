# LEGACY / NON-CANONICAL IN THIS WORKTREE
# This launcher follows the main-runtime model (8080 dashboard, 8765 Miru AI).
# For Project Miru worktree runtime authority, use:
#   windows/start_op_miru_worktree.ps1  (18080 dashboard, 18765 Miru AI/Dev)
# Keep this script for compatibility only; do not use it as the default worktree start path.

param(
    [switch]$SkipDocker,
    [switch]$SkipMiruAi,
    [switch]$Watchdog,
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "op_miru_common.ps1")

$paths = Get-OpMiruPaths -ScriptDirectory $PSScriptRoot
$lanIp = Get-OpMiruLanIpv4Address

New-Item -ItemType Directory -Force -Path $paths.LogDirectory | Out-Null

function Write-OpMiruStep {
    param([string]$Message)

    if (-not $Quiet) {
        Write-Host "[op-miru] $Message"
    }
}

$envLoad = Import-OpMiruDotEnv -RepoRoot $paths.RepoRoot
$pushoverStatus = Get-OpMiruPushoverStatus
if ($envLoad.Exists) {
    Write-OpMiruStep "Loaded local .env from $($envLoad.EnvPath)."
}
else {
    Write-OpMiruStep "Local .env not found at $($envLoad.EnvPath)."
}
Write-OpMiruStep $pushoverStatus.Summary

function Start-OpMiruDockerDesktop {
    $dockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{
            Started = $false
            Method  = "missing-cli"
            Detail  = "docker.exe is not available in PATH."
        }
    }

    $startResult = Invoke-OpMiruDockerCli -Arguments @("desktop", "start") -DockerConfigDirectory $paths.DockerConfigDirectory -WorkingDirectory $paths.RepoRoot
    if ($startResult.Success) {
        return [pscustomobject]@{
            Started = $true
            Method  = "docker-desktop-cli"
            Detail  = ($startResult.Output -join [Environment]::NewLine).Trim()
        }
    }

    if (Test-Path $dockerDesktopExe) {
        Start-Process -FilePath $dockerDesktopExe | Out-Null
        return [pscustomobject]@{
            Started = $true
            Method  = "docker-desktop-exe"
            Detail  = "Docker Desktop was launched directly after the CLI start attempt failed."
        }
    }

    return [pscustomobject]@{
        Started = $false
        Method  = "unavailable"
        Detail  = ($startResult.Output -join [Environment]::NewLine).Trim()
    }
}

function Ensure-OpMiruDashboard {
    $localProbe = Test-OpMiruHttp -Url $paths.DashboardUrlLocal -MustContain "Miru"
    if ($localProbe.Ok) {
        Write-OpMiruStep "Dashboard already responds on $($paths.DashboardUrlLocal)."
        return [pscustomobject]@{
            StartedThisRun = $false
            Healthy        = $true
            LocalUrl       = $paths.DashboardUrlLocal
            LanUrl         = if ($lanIp) { "http://${lanIp}:$($paths.DashboardPort)/" } else { $null }
            HealthSource   = "already-running"
            DockerCompose  = $null
            DockerDesktop  = $null
        }
    }

    if ($SkipDocker) {
        throw "Dashboard is not reachable and Docker startup was skipped."
    }

    if (-not (Test-Path $paths.DockerComposeFile)) {
        throw "docker-compose.yml was not found at $($paths.DockerComposeFile)."
    }

    Write-OpMiruStep "Dashboard is down; starting Docker Desktop and replaying docker compose."
    $desktopStart = Start-OpMiruDockerDesktop
    $composeResult = $null
    $composeErrors = New-Object System.Collections.Generic.List[string]

    $deadline = (Get-Date).AddMinutes(3)
    do {
        $probe = Test-OpMiruHttp -Url $paths.DashboardUrlLocal -MustContain "Miru"
        if ($probe.Ok) {
            return [pscustomobject]@{
                StartedThisRun = $true
                Healthy        = $true
                LocalUrl       = $paths.DashboardUrlLocal
                LanUrl         = if ($lanIp) { "http://${lanIp}:$($paths.DashboardPort)/" } else { $null }
                HealthSource   = "docker-desktop-recovered"
                DockerCompose  = $composeResult
                DockerDesktop  = $desktopStart
            }
        }

        $composeResult = Invoke-OpMiruDockerCli -Arguments @("compose", "up", "-d") -DockerConfigDirectory $paths.DockerConfigDirectory -WorkingDirectory $paths.RepoRoot
        if (-not $composeResult.Success) {
            $composeErrors.Add(($composeResult.Output -join " ").Trim())
        }

        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    $errorDetail = ($composeErrors | Where-Object { $_ } | Select-Object -Unique) -join " | "
    if ([string]::IsNullOrWhiteSpace($errorDetail)) {
        $errorDetail = "Dashboard never became reachable after Docker startup attempts."
    }

    throw "OP Miru dashboard did not recover. $errorDetail"
}

function Ensure-OpMiruMiruAi {
    $healthProbe = Test-OpMiruHttp -Url $paths.MiruAiHealthUrlLocal -MustContain '"status":"ok"'
    if ($healthProbe.Ok) {
        Write-OpMiruStep "Miru AI already responds on $($paths.MiruAiRootUrlLocal)."
        $listeningEntry = Get-OpMiruListeningEntry -Port $paths.MiruAiPort
        return [pscustomobject]@{
            StartedThisRun = $false
            Healthy        = $true
            LocalUrl       = $paths.MiruAiRootUrlLocal
            HealthUrl      = $paths.MiruAiHealthUrlLocal
            LanUrl         = if ($lanIp) { "http://${lanIp}:$($paths.MiruAiPort)/" } else { $null }
            HealthSource   = "already-running"
            PythonPath     = $null
            ProcessId      = if ($listeningEntry) { $listeningEntry.Pid } else { $null }
        }
    }

    if ($SkipMiruAi) {
        throw "Miru AI is not reachable and Miru AI startup was skipped."
    }

    $listeningEntry = Get-OpMiruListeningEntry -Port $paths.MiruAiPort
    if ($listeningEntry) {
        throw "Port $($paths.MiruAiPort) is already listening under PID $($listeningEntry.Pid), but Miru AI health checks failed."
    }

    $pythonCommand = Get-Command python -ErrorAction Stop
    $stdoutLog = Join-Path $paths.LogDirectory "miru_ai_stdout.log"
    $stderrLog = Join-Path $paths.LogDirectory "miru_ai_stderr.log"

    Write-OpMiruStep "Starting Miru AI with $($pythonCommand.Source)."
    $process = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList @($paths.MiruAiScript, "--host", "0.0.0.0", "--port", "$($paths.MiruAiPort)") `
        -WorkingDirectory $paths.RepoRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $healthProbe = Wait-OpMiruHttp -Url $paths.MiruAiHealthUrlLocal -TimeoutSeconds 90 -RetryDelaySeconds 3 -MustContain '"status":"ok"'
    if (-not $healthProbe.Ok) {
        throw "Miru AI did not become healthy on $($paths.MiruAiHealthUrlLocal). Check $stderrLog for details."
    }

    return [pscustomobject]@{
        StartedThisRun = $true
        Healthy        = $true
        LocalUrl       = $paths.MiruAiRootUrlLocal
        HealthUrl      = $paths.MiruAiHealthUrlLocal
        LanUrl         = if ($lanIp) { "http://${lanIp}:$($paths.MiruAiPort)/" } else { $null }
        HealthSource   = "started-by-script"
        PythonPath     = $pythonCommand.Source
        ProcessId      = $process.Id
    }
}

Write-OpMiruStep "Beginning OP Miru startup recovery."
$dashboard = Ensure-OpMiruDashboard
$miruAi = Ensure-OpMiruMiruAi

$dashboardLanCheck = if ($lanIp) { Test-OpMiruHttp -Url $dashboard.LanUrl -MustContain "Miru" } else { $null }
$miruAiLanCheck = if ($lanIp) { Test-OpMiruHttp -Url "$($miruAi.LanUrl)api/health" -MustContain '"status":"ok"' } else { $null }

$summary = [pscustomobject]@{
    Timestamp          = (Get-Date).ToString("s")
    LanIp              = $lanIp
    Dashboard          = $dashboard
    MiruAi             = $miruAi
    ListeningPorts     = @(
        Get-OpMiruListeningEntry -Port $paths.DashboardPort
        Get-OpMiruListeningEntry -Port $paths.MiruAiPort
    ) | Where-Object { $_ }
    LanChecks          = [pscustomobject]@{
        Dashboard = $dashboardLanCheck
        MiruAi    = $miruAiLanCheck
    }
    LogDirectory       = $paths.LogDirectory
    DockerComposeFile  = $paths.DockerComposeFile
    StartupCommandPath = Join-Path $PSScriptRoot "op_miru_bootstrap.cmd"
}

Write-OpMiruStep "Startup recovery finished."

if ($Watchdog -and $miruAi.ProcessId) {
    $maxRestartDelay = 60
    $restartDelay = 5
    while ($true) {
        $pid = $miruAi.ProcessId
        Write-OpMiruStep "Watchdog: monitoring Miru AI (PID $pid)."
        Wait-Process -Id $pid -ErrorAction SilentlyContinue
        Write-OpMiruStep "Miru AI (PID $pid) exited; restarting in $restartDelay seconds."
        Start-Sleep -Seconds $restartDelay
        try {
            $miruAi = Ensure-OpMiruMiruAi
            $restartDelay = 5
        }
        catch {
            if (-not $Quiet) {
                Write-Host "[op-miru] Watchdog: restart failed: $_" -ForegroundColor Red
            }
            $restartDelay = [Math]::Min($restartDelay * 2, $maxRestartDelay)
        }
    }
}

return $summary
