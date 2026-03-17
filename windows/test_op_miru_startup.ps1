Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "op_miru_common.ps1")

$paths = Get-OpMiruPaths -ScriptDirectory $PSScriptRoot
$startupSummary = & (Join-Path $PSScriptRoot "start_op_miru.ps1") -Quiet
$lanIp = $startupSummary.LanIp

$dashboardLocal = Test-OpMiruHttp -Url $paths.DashboardUrlLocal -MustContain "Miru"
$miruAiLocal = Test-OpMiruHttp -Url $paths.MiruAiHealthUrlLocal -MustContain '"status":"ok"'

if (-not $dashboardLocal.Ok) {
    throw "Dashboard local verification failed for $($paths.DashboardUrlLocal)."
}

if (-not $miruAiLocal.Ok) {
    throw "Miru AI local verification failed for $($paths.MiruAiHealthUrlLocal)."
}

$dashboardLan = $null
$miruAiLan = $null
if ($lanIp) {
    $dashboardLan = Test-OpMiruHttp -Url "http://${lanIp}:$($paths.DashboardPort)/" -MustContain "Miru"
    $miruAiLan = Test-OpMiruHttp -Url "http://${lanIp}:$($paths.MiruAiPort)/api/health" -MustContain '"status":"ok"'

    if (-not $dashboardLan.Ok) {
        throw "Dashboard LAN verification failed for http://${lanIp}:$($paths.DashboardPort)/."
    }

    if (-not $miruAiLan.Ok) {
        throw "Miru AI LAN verification failed for http://${lanIp}:$($paths.MiruAiPort)/api/health."
    }
}

$dashboardListener = Get-OpMiruListeningEntry -Port $paths.DashboardPort
$miruAiListener = Get-OpMiruListeningEntry -Port $paths.MiruAiPort

if (-not $dashboardListener) {
    throw "Port $($paths.DashboardPort) is not listening."
}

if (-not $miruAiListener) {
    throw "Port $($paths.MiruAiPort) is not listening."
}

$dockerVerification = $null
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $composeResult = Invoke-OpMiruDockerCli -Arguments @("compose", "ps", "--services", "--status", "running") -DockerConfigDirectory $paths.DockerConfigDirectory -WorkingDirectory $paths.RepoRoot
    $dockerVerification = [pscustomobject]@{
        Accessible      = $composeResult.Success
        RunningServices = if ($composeResult.Success) { @($composeResult.Output | Where-Object { $_ }) } else { @() }
        Command         = $composeResult.Command
        Detail          = ($composeResult.Output -join [Environment]::NewLine).Trim()
    }
}

[pscustomobject]@{
    Timestamp          = (Get-Date).ToString("s")
    StartupSummary     = $startupSummary
    LocalChecks        = [pscustomobject]@{
        Dashboard = $dashboardLocal
        MiruAi    = $miruAiLocal
    }
    LanChecks          = [pscustomobject]@{
        Dashboard = $dashboardLan
        MiruAi    = $miruAiLan
    }
    ListeningPorts     = @($dashboardListener, $miruAiListener)
    DockerVerification = $dockerVerification
}

