# Ensure the main stable site (dashboard) on 8080 is running. Verification-only by default; -Start tries docker compose up.
# Main stable (8080) is typically run from the main repo or Docker; this script verifies health and optionally starts
# the dashboard via docker-compose.yml in the current repo (if present). Do NOT use 8765.
param(
    [int]$Port = 8080,
    [switch]$Start,
    [switch]$DockerOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "op_miru_common.ps1")
. (Join-Path $scriptDir "op_miru_runtime.ps1")

$rootUrl = "http://127.0.0.1:$Port/"

if (Test-DashboardHealthy -Port $Port) {
    Write-Host "Main stable (port $Port) already healthy."
    exit 0
}

if (-not $Start) {
    Write-Host "Main stable (port $Port) is not responding. Run with -Start to attempt startup (e.g. docker compose up), or start the main dashboard elsewhere." -ForegroundColor Yellow
    exit 1
}

$composeFile = Join-Path $repoRoot "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Write-Host "docker-compose.yml not found at $composeFile. Start the main dashboard manually on port $Port." -ForegroundColor Yellow
    exit 1
}

$dockerConfigDir = Join-Path $repoRoot ".docker-config"
Initialize-OpMiruDockerEnvironment -DockerConfigDirectory $dockerConfigDir | Out-Null
Write-Host "Starting main stable dashboard via docker compose (port $Port)."
$result = Invoke-OpMiruDockerCli -Arguments @("compose", "up", "-d") -DockerConfigDirectory $dockerConfigDir -WorkingDirectory $repoRoot
if (-not $result.Success) {
    Write-Host "Docker compose failed: $(($result.Output) -join ' ')" -ForegroundColor Red
    exit 1
}

$probe = Wait-OpMiruHttp -Url $rootUrl -TimeoutSeconds 120 -RetryDelaySeconds 5 -MustContain "Miru"
if (-not $probe.Ok) {
    Write-Host "Main stable did not become reachable on $rootUrl after compose up." -ForegroundColor Red
    exit 1
}

Write-Host "Main stable is ready on $rootUrl."
exit 0
