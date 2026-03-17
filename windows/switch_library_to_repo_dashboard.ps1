# Switch the Library page (port 8080) to use the dashboard image built from this repo.
# Use this when the live Library page is still showing an old UI (no "View Card", no card detail modal).
# The container serving 8080 may be named "Miru" or similar and use an older image; this script
# stops that container and starts the dashboard from this repo's docker-compose (tcg-dashboard).
# Run from repo root, or from this script's directory (windows/).

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $ScriptDir

# Container that might be holding 8080 (e.g. from another compose)
$ContainerToStop = "Miru"

$composeFile = Join-Path $RepoRoot "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Write-Error "docker-compose.yml not found at $composeFile"
}

Write-Host "Stopping container '$ContainerToStop' if it exists..."
docker stop $ContainerToStop 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (container not running or not found; continuing)"
}

Write-Host "Starting tcg-dashboard from this repo (force-recreate so port 8080 is published)..."
Push-Location $RepoRoot
try {
    docker compose up -d tcg-dashboard --force-recreate
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Dashboard started. Library page: http://127.0.0.1:8080/"
} finally {
    Pop-Location
}
