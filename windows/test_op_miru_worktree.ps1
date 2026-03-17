param(
    [int]$DashboardPort = 18080,
    [int]$MiruAiPort = 18765,
    [switch]$RequireWatcher,
    [switch]$SkipMiruAiRecovery
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "op_miru_common.ps1")

function Invoke-OpMiruWorktreeStartupRecovery {
    param(
        [Parameter(Mandatory = $true)]
        [int]$DashboardPort,
        [Parameter(Mandatory = $true)]
        [int]$MiruAiPort,
        [switch]$RequireWatcher
    )

    $argumentList = @(
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "start_op_miru_worktree.ps1"),
        "-DashboardPort", "$DashboardPort",
        "-MiruAiPort", "$MiruAiPort"
    )
    if ($RequireWatcher) {
        $argumentList += "-IncludeWatcher"
    }

    & powershell @argumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Worktree startup recovery failed with exit code $LASTEXITCODE."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.worktree.yml"
$dockerConfigDirectory = Join-Path $repoRoot ".docker-config"
$dashboardUrl = "http://127.0.0.1:$DashboardPort/"
$miruAiHealthUrl = "http://127.0.0.1:$MiruAiPort/api/health"
$miruAiDevUrl = "http://127.0.0.1:$MiruAiPort/dev"
$devStatusUrl = "http://127.0.0.1:$MiruAiPort/api/dev-status?view=summary"

$serviceStates = @(Get-OpMiruDockerComposeServices -RepoRoot $repoRoot -ComposeFile $composeFile -DockerConfigDirectory $dockerConfigDirectory -ProjectName "op-miru-worktree")
$dashboardMatches = @($serviceStates | Where-Object { [string]$_.Service -eq "tcg-dashboard" } | Select-Object -First 1)
$watcherMatches = @($serviceStates | Where-Object { [string]$_.Service -eq "tcg-watcher" } | Select-Object -First 1)
$dashboardService = if ($dashboardMatches.Count -gt 0) { $dashboardMatches[0] } else { $null }
$watcherService = if ($watcherMatches.Count -gt 0) { $watcherMatches[0] } else { $null }
$dashboard = Wait-OpMiruHttp -Url $dashboardUrl -TimeoutSeconds 20 -RetryDelaySeconds 2 -MustContain "Miru"
$miruAi = Test-OpMiruHttp -Url $miruAiHealthUrl -MustContain '"status":"ok"'
$miruAiDev = $null
$devStatusResponse = $null
$devStatus = $null
$projectMiruLink = ""

if ((-not $miruAi.Ok) -and (-not $SkipMiruAiRecovery)) {
    Write-Host "Miru AI on port $MiruAiPort is not healthy; relaunching the worktree startup flow."
    Invoke-OpMiruWorktreeStartupRecovery -DashboardPort $DashboardPort -MiruAiPort $MiruAiPort -RequireWatcher:$RequireWatcher
    $dashboard = Wait-OpMiruHttp -Url $dashboardUrl -TimeoutSeconds 20 -RetryDelaySeconds 2 -MustContain "Miru"
    $miruAi = Test-OpMiruHttp -Url $miruAiHealthUrl -TimeoutSeconds 10 -MustContain '"status":"ok"'
}

if ($miruAi.Ok) {
    $miruAiDev = Test-OpMiruHttp -Url $miruAiDevUrl -TimeoutSeconds 10
    $devStatusResponse = Invoke-WebRequest -UseBasicParsing -Uri $devStatusUrl -TimeoutSec 10
    $devStatus = $devStatusResponse.Content | ConvertFrom-Json
    $projectMiruLink = [string]$devStatus.links.project_miru
}

$dashboardContainerState = if ($dashboardService) { [string]$dashboardService.State } else { "missing" }
$watcherContainerState = if ($watcherService) { [string]$watcherService.State } else { "not-started" }
$miruAiListeningEntry = Get-OpMiruListeningEntry -Port $MiruAiPort
$miruAiProcessState = if ($miruAiListeningEntry) { "listening (PID $($miruAiListeningEntry.Pid))" } else { "not-listening" }

Write-Host "Dashboard container: $dashboardContainerState"
Write-Host "Dashboard HTTP 18080: $(if ($dashboard.Ok) { 'healthy' } else { 'unhealthy' })"
Write-Host "Watcher container: $watcherContainerState"
Write-Host "Miru AI process 18765: $miruAiProcessState"
Write-Host "Miru AI HTTP 18765: $(if ($miruAi.Ok) { 'healthy' } else { 'unhealthy' })"
if ($miruAiDev) {
    Write-Host "Miru AI Dev page 18765: $(if ($miruAiDev.Ok) { 'healthy' } else { 'unhealthy' })"
}
if ($projectMiruLink) {
    Write-Host "Miru AI companion dashboard link: $projectMiruLink"
}

if ($dashboardContainerState -ne "running") {
    throw "Dashboard container state is '$dashboardContainerState' instead of 'running'."
}
if (-not $dashboard.Ok) {
    throw "Dashboard check failed for $dashboardUrl. $($dashboard.Error)"
}
if ($RequireWatcher -and $watcherContainerState -ne "running") {
    throw "Watcher container state is '$watcherContainerState' instead of 'running'."
}
if (-not $miruAi.Ok) {
    throw "Miru AI health check failed for $miruAiHealthUrl. $($miruAi.Error)"
}
if (-not $miruAiDev -or -not $miruAiDev.Ok) {
    throw "Miru AI Dev page check failed for $miruAiDevUrl. $($miruAiDev.Error)"
}

Write-Host "Worktree dashboard check passed: $dashboardUrl"
Write-Host "Worktree Miru AI check passed: $miruAiHealthUrl"
Write-Host "Worktree Miru AI Dev page check passed: $miruAiDevUrl"
if ($projectMiruLink) {
    Write-Host "Miru AI summary link currently reports: $projectMiruLink"
}

return [pscustomobject]@{
    DashboardContainerState = $dashboardContainerState
    DashboardUrl = $dashboardUrl
    WatcherContainerState = $watcherContainerState
    MiruAiProcessState = $miruAiProcessState
    MiruAiHealthUrl = $miruAiHealthUrl
    MiruAiDevUrl = $miruAiDevUrl
    ProjectMiruLink = $projectMiruLink
}
