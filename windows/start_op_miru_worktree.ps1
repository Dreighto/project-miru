# Canonical startup for the Project Miru worktree: dashboard on 18080, Miru AI on 18765.
# -Native: run dashboard and Miru AI as local Python processes (no Docker). Processes survive closing PowerShell.
# Without -Native: run dashboard via Docker compose and Miru AI as local process. Miru AI binds to 0.0.0.0 for Tailscale.
# PID and logs: data/startup-logs/
# Runtime authority reference: docs/RUNTIME_AUTHORITY_MATRIX.md
param(
    [switch]$Native,
    [switch]$IncludeWatcher,
    [switch]$Rebuild,
    [int]$DashboardPort = 18080,
    [int]$MiruAiPort = 18765,
    [string]$BindHost = "0.0.0.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "op_miru_common.ps1")

function Get-OpMiruWorktreeTailscaleIpv4Address {
    try {
        $candidate = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                ($_.InterfaceAlias -match "Tailscale" -or $_.InterfaceDescription -match "Tailscale") -and
                $_.IPAddress -match "^100\."
            } |
            Select-Object -First 1 -ExpandProperty IPAddress
        return $candidate
    }
    catch {
        return $null
    }
}

function Write-OpMiruWorktreeLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [string]$Color = ""
    )

    if ($Color) {
        Write-Host $Message -ForegroundColor $Color
    }
    else {
        Write-Host $Message
    }
}

function Get-OpMiruWorktreeServiceState {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Services,
        [Parameter(Mandatory = $true)]
        [string]$ServiceName
    )

    return @($Services | Where-Object { [string]$_.Service -eq $ServiceName } | Select-Object -First 1)[0]
}

function Get-OpMiruWorktreeMiruAiPidRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFilePath
    )

    if (-not (Test-Path $PidFilePath)) {
        return $null
    }

    try {
        $payload = Get-Content -Path $PidFilePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        return $payload
    }
    catch {
        return $null
    }
}

function Test-OpMiruWorktreeMiruAiOwnership {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [int]$MiruAiPort,
        [string]$PidFilePath = ""
    )

    $pidRecord = if ($PidFilePath) { Get-OpMiruWorktreeMiruAiPidRecord -PidFilePath $PidFilePath } else { $null }
    if ($pidRecord -and [int]($pidRecord.pid) -eq $ProcessId) {
        return $true
    }

    try {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $commandLine = [string]($processInfo.CommandLine)
        $pidFileMatchesRepo = $pidRecord -and ([string]$pidRecord.repo_root) -eq $RepoRoot
        return (
            $commandLine -match "tools[/\\]miru_ai_server\.py" -and (
                $commandLine -match "(^|[^\d])$MiruAiPort($|[^\d])" -or
                $pidFileMatchesRepo
            )
        )
    }
    catch {
        return $false
    }
}

function Start-OpMiruWorktreeMiruAi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$BindHost,
        [Parameter(Mandatory = $true)]
        [int]$MiruAiPort,
        [Parameter(Mandatory = $true)]
        [int]$DashboardPort,
        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,
        [Parameter(Mandatory = $true)]
        [string]$StderrLog,
        [Parameter(Mandatory = $true)]
        [string]$PidFilePath
    )

    $pythonCommand = Get-Command python -ErrorAction Stop
    $env:PROJECT_MIRU_PORT = "$DashboardPort"

    Write-OpMiruWorktreeLine "Starting worktree Miru AI with companion dashboard port $DashboardPort."
    $process = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList @("tools\miru_ai_server.py", "--host", $BindHost, "--port", "$MiruAiPort") `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru

    [pscustomobject]@{
        pid = $process.Id
        dashboard_port = $DashboardPort
        miru_ai_port = $MiruAiPort
        started_at = (Get-Date).ToString("s")
        repo_root = $RepoRoot
    } | ConvertTo-Json | Set-Content -Path $PidFilePath -Encoding UTF8

    return $process
}

function Start-OpMiruWorktreeDashboardNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [int]$DashboardPort,
        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,
        [Parameter(Mandatory = $true)]
        [string]$StderrLog,
        [Parameter(Mandatory = $true)]
        [string]$PidFilePath
    )

    $pythonCommand = Get-Command python -ErrorAction Stop
    $env:PORT = "$DashboardPort"

    Write-OpMiruWorktreeLine "Starting worktree dashboard on port $DashboardPort (native Python, no Docker)."
    $process = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList "dashboard\app.py" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru

    [pscustomobject]@{
        pid          = $process.Id
        port         = $DashboardPort
        started_at   = (Get-Date).ToString("s")
        repo_root    = $RepoRoot
    } | ConvertTo-Json | Set-Content -Path $PidFilePath -Encoding UTF8

    return $process
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.worktree.yml"
$dockerConfigDirectory = Join-Path $repoRoot ".docker-config"
$logDirectory = Join-Path $repoRoot "data\startup-logs"
$pidFilePath = Join-Path $logDirectory "miru_ai_worktree.pid"
$stdoutLog = Join-Path $logDirectory "miru_ai_worktree_stdout.log"
$stderrLog = Join-Path $logDirectory "miru_ai_worktree_stderr.log"
$dashboardPidFilePath = Join-Path $logDirectory "dashboard_18080.pid"
$dashboardStdoutLog = Join-Path $logDirectory "dashboard_18080_stdout.log"
$dashboardStderrLog = Join-Path $logDirectory "dashboard_18080_stderr.log"
$dashboardUrlLocal = "http://127.0.0.1:$DashboardPort/"
$miruAiRootUrlLocal = "http://127.0.0.1:$MiruAiPort/"
$miruAiHealthUrlLocal = "${miruAiRootUrlLocal}api/health"
$miruAiDevUrlLocal = "${miruAiRootUrlLocal}dev"
$lanIp = Get-OpMiruLanIpv4Address
$tailscaleIp = Get-OpMiruWorktreeTailscaleIpv4Address

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$envLoad = Import-OpMiruDotEnv -RepoRoot $repoRoot
$pushoverStatus = Get-OpMiruPushoverStatus
if ($envLoad.Exists) {
    Write-OpMiruWorktreeLine "Loaded local .env from $($envLoad.EnvPath)."
}
else {
    Write-OpMiruWorktreeLine "Local .env not found at $($envLoad.EnvPath)." "Yellow"
}
Write-OpMiruWorktreeLine $pushoverStatus.Summary

# Native mode: run dashboard and Miru AI as local Python processes. Both survive closing PowerShell.
if ($Native) {
    $listeningDashboard = Get-OpMiruListeningEntry -Port $DashboardPort
    if ($listeningDashboard) {
        Write-OpMiruWorktreeLine "Port $DashboardPort is already in use (PID $($listeningDashboard.Pid)). Stop it first or use a different port." "Yellow"
        throw "Dashboard port $DashboardPort is already in use."
    }
    $listeningMiru = Get-OpMiruListeningEntry -Port $MiruAiPort
    if ($listeningMiru) {
        if (Test-OpMiruWorktreeMiruAiOwnership -ProcessId $listeningMiru.Pid -RepoRoot $repoRoot -MiruAiPort $MiruAiPort -PidFilePath $pidFilePath) {
            Write-OpMiruWorktreeLine "Stopping existing Miru AI (PID $($listeningMiru.Pid)) to restart with correct binding." "Yellow"
            Stop-Process -Id $listeningMiru.Pid -Force -ErrorAction Stop
            Start-Sleep -Seconds 2
        }
        else {
            Write-OpMiruWorktreeLine "Port $MiruAiPort is in use by another process (PID $($listeningMiru.Pid)). Stop it first." "Yellow"
            throw "Miru AI port $MiruAiPort is already in use."
        }
    }

    # Ensure dashboard uses worktree-local dossier DB and images (not main runtime path).
    $env:MIRU_MAIN_RUNTIME_ROOT = $repoRoot
    $env:MIRU_RUNTIME_DOSSIER_DB_PATH = Join-Path $repoRoot "data\miru_learning_dossiers.db"
    $env:MIRU_RUNTIME_IMAGES_ROOT = Join-Path $repoRoot "data\miru_images"

    $dashboardProcess = Start-OpMiruWorktreeDashboardNative `
        -RepoRoot $repoRoot `
        -DashboardPort $DashboardPort `
        -StdoutLog $dashboardStdoutLog `
        -StderrLog $dashboardStderrLog `
        -PidFilePath $dashboardPidFilePath

    Start-Sleep -Seconds 2

    $miruAiProcess = Start-OpMiruWorktreeMiruAi `
        -RepoRoot $repoRoot `
        -BindHost $BindHost `
        -MiruAiPort $MiruAiPort `
        -DashboardPort $DashboardPort `
        -StdoutLog $stdoutLog `
        -StderrLog $stderrLog `
        -PidFilePath $pidFilePath

    $dashboardProbe = Wait-OpMiruHttp -Url $dashboardUrlLocal -TimeoutSeconds 60 -RetryDelaySeconds 3 -MustContain "Miru"
    if (-not $dashboardProbe.Ok) {
        throw "Worktree dashboard did not become reachable on $dashboardUrlLocal. Check $dashboardStderrLog for details."
    }
    Write-OpMiruWorktreeLine "Dashboard HTTP check passed on port $DashboardPort."

    $miruAiProbe = Wait-OpMiruHttp -Url $miruAiHealthUrlLocal -TimeoutSeconds 90 -RetryDelaySeconds 3 -MustContain '"status":"ok"'
    if (-not $miruAiProbe.Ok) {
        throw "Worktree Miru AI did not become healthy on $miruAiHealthUrlLocal. Check $stderrLog for details."
    }
    $miruAiDevProbe = Wait-OpMiruHttp -Url $miruAiDevUrlLocal -TimeoutSeconds 30 -RetryDelaySeconds 2
    if (-not $miruAiDevProbe.Ok) {
        throw "Worktree Miru AI Dev UI did not become reachable on $miruAiDevUrlLocal. Check $stderrLog for details."
    }
    Write-OpMiruWorktreeLine "Miru AI health and Dev UI check passed on port $MiruAiPort."

    $dashboardUrlLan = if ($lanIp) { "http://${lanIp}:$DashboardPort/" } else { $null }
    $miruAiUrlLan = if ($lanIp) { "http://${lanIp}:$MiruAiPort/" } else { $null }
    $dashboardUrlTailscale = if ($tailscaleIp) { "http://${tailscaleIp}:$DashboardPort/" } else { $null }
    $miruAiUrlTailscale = if ($tailscaleIp) { "http://${tailscaleIp}:$MiruAiPort/" } else { $null }

    Write-Host ""
    Write-OpMiruWorktreeLine "OP Miru worktree stack is ready (native mode). You can close this window; services will keep running." "Cyan"
    Write-OpMiruWorktreeLine "Dashboard: $dashboardUrlLocal (PID $($dashboardProcess.Id), log: $dashboardStdoutLog)"
    Write-OpMiruWorktreeLine "Miru AI:   $miruAiRootUrlLocal (PID $($miruAiProcess.Id), log: $stdoutLog)"
    Write-OpMiruWorktreeLine "To stop later: .\windows\stop_op_miru_worktree.ps1 -Native"
    if ($dashboardUrlLan) {
        Write-OpMiruWorktreeLine "LAN Dashboard: $dashboardUrlLan"
        Write-OpMiruWorktreeLine "LAN Miru AI:   $miruAiUrlLan"
    }
    if ($dashboardUrlTailscale) {
        Write-OpMiruWorktreeLine "Tailscale Dashboard: $dashboardUrlTailscale"
        Write-OpMiruWorktreeLine "Tailscale Miru AI:   $miruAiUrlTailscale"
    }

    return [pscustomobject]@{
        RepoRoot         = $repoRoot
        Native           = $true
        Dashboard        = [pscustomobject]@{
            LocalUrl    = $dashboardUrlLocal
            LanUrl      = $dashboardUrlLan
            TailscaleUrl = $dashboardUrlTailscale
            Port        = $DashboardPort
            ProcessId   = $dashboardProcess.Id
            PidFile     = $dashboardPidFilePath
            StdoutLog   = $dashboardStdoutLog
            StderrLog   = $dashboardStderrLog
        }
        MiruAi           = [pscustomobject]@{
            LocalUrl    = $miruAiRootUrlLocal
            HealthUrl   = $miruAiHealthUrlLocal
            LanUrl      = $miruAiUrlLan
            TailscaleUrl = $miruAiUrlTailscale
            Port        = $MiruAiPort
            ProcessId   = $miruAiProcess.Id
            PidFile     = $pidFilePath
            StdoutLog   = $stdoutLog
            StderrLog   = $stderrLog
        }
    }
}

if (-not (Test-Path $composeFile)) {
    throw "Worktree compose file was not found at $composeFile."
}

$services = [System.Collections.Generic.List[string]]::new()
$services.Add("tcg-dashboard")
if ($IncludeWatcher) {
    $services.Add("tcg-watcher")
}

$composeArgs = [System.Collections.Generic.List[string]]::new()
$composeArgs.Add("compose")
$composeArgs.Add("-p")
$composeArgs.Add("op-miru-worktree")
$composeArgs.Add("-f")
$composeArgs.Add($composeFile)
$composeArgs.Add("up")
$composeArgs.Add("-d")
if ($Rebuild) {
    $composeArgs.Add("--build")
}
foreach ($service in $services) {
    $composeArgs.Add($service)
}

Write-OpMiruWorktreeLine "Starting worktree Docker services: $($services -join ', ')."
$composeResult = Invoke-OpMiruDockerCli `
    -Arguments $composeArgs.ToArray() `
    -DockerConfigDirectory $dockerConfigDirectory `
    -WorkingDirectory $repoRoot
$composeFailed = (-not $composeResult.Success)

$serviceStates = @(Get-OpMiruDockerComposeServices -RepoRoot $repoRoot -ComposeFile $composeFile -DockerConfigDirectory $dockerConfigDirectory -ProjectName "op-miru-worktree")
if ($serviceStates.Count -gt 0) {
    $dashboardService = Get-OpMiruWorktreeServiceState -Services $serviceStates -ServiceName "tcg-dashboard"
} else {
    $dashboardService = $null
}
if (-not $dashboardService) {
    $composeDetail = $((@($composeResult.Output) -join [Environment]::NewLine).Trim())
    throw "Worktree dashboard container was not found after docker compose completed. Exit code: $($composeResult.ExitCode). $composeDetail"
}
if ([string]$dashboardService.State -ne "running") {
    $composeDetail = $((@($composeResult.Output) -join [Environment]::NewLine).Trim())
    throw "Worktree dashboard container is '$($dashboardService.State)' instead of 'running'. Exit code: $($composeResult.ExitCode). $composeDetail"
}
Write-OpMiruWorktreeLine "Dashboard container state: $($dashboardService.State)"

if ($IncludeWatcher) {
    if ($serviceStates.Count -gt 0) {
        $watcherService = Get-OpMiruWorktreeServiceState -Services $serviceStates -ServiceName "tcg-watcher"
    } else {
        $watcherService = $null
    }
    if (-not $watcherService) {
        $composeDetail = $((@($composeResult.Output) -join [Environment]::NewLine).Trim())
        throw "Worktree watcher container was not found after docker compose completed. Exit code: $($composeResult.ExitCode). $composeDetail"
    }
    if ([string]$watcherService.State -ne "running") {
        $composeDetail = $((@($composeResult.Output) -join [Environment]::NewLine).Trim())
        throw "Worktree watcher container is '$($watcherService.State)' instead of 'running'. Exit code: $($composeResult.ExitCode). $composeDetail"
    }
    Write-OpMiruWorktreeLine "Watcher container state: $($watcherService.State)"
}

if ($composeFailed) {
    Write-OpMiruWorktreeLine "docker compose returned exit code $($composeResult.ExitCode), but required worktree containers are running; continuing with health checks." "Yellow"
}

$dashboardProbe = Wait-OpMiruHttp -Url $dashboardUrlLocal -TimeoutSeconds 120 -RetryDelaySeconds 3 -MustContain "Miru"
if (-not $dashboardProbe.Ok) {
    throw "Worktree dashboard did not become reachable on $dashboardUrlLocal."
}
Write-OpMiruWorktreeLine "Dashboard HTTP check passed on port $DashboardPort."

$miruAiProbe = Test-OpMiruHttp -Url $miruAiHealthUrlLocal -MustContain '"status":"ok"'
$miruAiDevProbe = $null
$miruAiStartedThisRun = $false
$miruAiProcessId = $null
$miruAiNeedsRestart = $false
if ($miruAiProbe.Ok) {
    $listeningEntry = Get-OpMiruListeningEntry -Port $MiruAiPort
    if ($listeningEntry) {
        $miruAiProcessId = $listeningEntry.Pid
    }
    $miruAiDevProbe = Test-OpMiruHttp -Url $miruAiDevUrlLocal -TimeoutSeconds 10
    if (-not $miruAiDevProbe.Ok) {
        $miruAiNeedsRestart = $true
        Write-OpMiruWorktreeLine "Worktree Miru AI health passed, but the Dev UI did not answer on $miruAiDevUrlLocal; restarting it." "Yellow"
    }

    if (-not $miruAiNeedsRestart -and $listeningEntry -and $listeningEntry.LocalAddress -ne $BindHost) {
        $miruAiNeedsRestart = $true
        Write-OpMiruWorktreeLine "Worktree Miru AI is bound to $($listeningEntry.LocalAddress):$MiruAiPort instead of $BindHost; Tailscale access requires 0.0.0.0. Restarting with correct binding." "Yellow"
    }

    if (-not $miruAiNeedsRestart) {
        $serverFileMtime = (Get-Item (Join-Path $repoRoot "tools\miru_ai_server.py")).LastWriteTime
        $pidRecord = Get-OpMiruWorktreeMiruAiPidRecord -PidFilePath $pidFilePath
        if ($pidRecord -and [datetime]$pidRecord.started_at -lt $serverFileMtime) {
            $miruAiNeedsRestart = $true
            Write-OpMiruWorktreeLine "Miru AI source updated since last start ($($pidRecord.started_at) < mtime $($serverFileMtime.ToString('s'))); restarting to pick up new routes." "Yellow"
        }
    }

    if (-not $miruAiNeedsRestart) {
        Write-OpMiruWorktreeLine "Worktree Miru AI already responds on $miruAiRootUrlLocal."
    }
}
if ((-not $miruAiProbe.Ok) -or $miruAiNeedsRestart) {
    $listeningEntry = Get-OpMiruListeningEntry -Port $MiruAiPort
    if ($listeningEntry) {
        if (Test-OpMiruWorktreeMiruAiOwnership -ProcessId $listeningEntry.Pid -RepoRoot $repoRoot -MiruAiPort $MiruAiPort -PidFilePath $pidFilePath) {
            $reasonText = if ($miruAiNeedsRestart) { "needs a worktree-safe relaunch" } else { "is unhealthy" }
            Write-OpMiruWorktreeLine "Worktree Miru AI on PID $($listeningEntry.Pid) $reasonText; restarting it." "Yellow"
            Stop-Process -Id $listeningEntry.Pid -Force -ErrorAction Stop
            Start-Sleep -Seconds 2
        }
        else {
            throw "Port $MiruAiPort is already listening under PID $($listeningEntry.Pid), but worktree Miru AI health checks failed."
        }
    }

    $process = Start-OpMiruWorktreeMiruAi `
        -RepoRoot $repoRoot `
        -BindHost $BindHost `
        -MiruAiPort $MiruAiPort `
        -DashboardPort $DashboardPort `
        -StdoutLog $stdoutLog `
        -StderrLog $stderrLog `
        -PidFilePath $pidFilePath

    $miruAiProcessId = $process.Id
    $miruAiStartedThisRun = $true

    $miruAiProbe = Wait-OpMiruHttp -Url $miruAiHealthUrlLocal -TimeoutSeconds 90 -RetryDelaySeconds 3 -MustContain '"status":"ok"'
    if (-not $miruAiProbe.Ok) {
        throw "Worktree Miru AI did not become healthy on $miruAiHealthUrlLocal. Check $stderrLog for details."
    }
    $miruAiDevProbe = Wait-OpMiruHttp -Url $miruAiDevUrlLocal -TimeoutSeconds 90 -RetryDelaySeconds 3
    if (-not $miruAiDevProbe.Ok) {
        throw "Worktree Miru AI Dev UI did not become reachable on $miruAiDevUrlLocal. Check $stderrLog for details."
    }
}

$dashboardUrlLan = if ($lanIp) { "http://${lanIp}:$DashboardPort/" } else { $null }
$miruAiUrlLan = if ($lanIp) { "http://${lanIp}:$MiruAiPort/" } else { $null }
$dashboardUrlTailscale = if ($tailscaleIp) { "http://${tailscaleIp}:$DashboardPort/" } else { $null }
$miruAiUrlTailscale = if ($tailscaleIp) { "http://${tailscaleIp}:$MiruAiPort/" } else { $null }

Write-Host ""
Write-OpMiruWorktreeLine "OP Miru worktree services are ready." "Cyan"
Write-OpMiruWorktreeLine "Dashboard: $dashboardUrlLocal"
Write-OpMiruWorktreeLine "Miru AI:   $miruAiRootUrlLocal"
if ($dashboardUrlLan) {
    Write-OpMiruWorktreeLine "LAN Dashboard: $dashboardUrlLan"
    Write-OpMiruWorktreeLine "LAN Miru AI:   $miruAiUrlLan"
}
if ($dashboardUrlTailscale) {
    Write-OpMiruWorktreeLine "Tailscale Dashboard: $dashboardUrlTailscale"
    Write-OpMiruWorktreeLine "Tailscale Miru AI:   $miruAiUrlTailscale"
}
if ($IncludeWatcher) {
    Write-OpMiruWorktreeLine "Watcher: running in docker with isolated worktree data. Discord webhook delivery is disabled in the worktree override."
}
else {
    Write-OpMiruWorktreeLine "Watcher: not started. Re-run with -IncludeWatcher if you want the background watcher in the worktree too." "Yellow"
}
Write-OpMiruWorktreeLine "If your phone route or local proxy currently targets 8080/8765, point it to $DashboardPort/$MiruAiPort for the worktree."

return [pscustomobject]@{
    RepoRoot = $repoRoot
    Dashboard = [pscustomobject]@{
        LocalUrl = $dashboardUrlLocal
        LanUrl = $dashboardUrlLan
        TailscaleUrl = $dashboardUrlTailscale
        Port = $DashboardPort
    }
    MiruAi = [pscustomobject]@{
        LocalUrl = $miruAiRootUrlLocal
        HealthUrl = $miruAiHealthUrlLocal
        LanUrl = $miruAiUrlLan
        TailscaleUrl = $miruAiUrlTailscale
        Port = $MiruAiPort
        ProcessId = $miruAiProcessId
        StartedThisRun = $miruAiStartedThisRun
        StdoutLog = $stdoutLog
        StderrLog = $stderrLog
        PidFile = $pidFilePath
    }
    WatcherStarted = [bool]$IncludeWatcher
    DockerProject = "op-miru-worktree"
    ComposeFiles = @($composeFile)
}
