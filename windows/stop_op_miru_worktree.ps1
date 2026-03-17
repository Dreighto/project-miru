# Stop the Project Miru worktree stack.
# -Native: stop only native Python processes (dashboard on 18080, Miru AI on 18765) using PID files from data/startup-logs/.
# -Docker: also run docker compose down for project op-miru-worktree (use when you started with the default Docker-based flow).
# You can use -Native and -Docker together to stop both native and Docker services.
param(
    [switch]$Native,
    [switch]$Docker
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $repoRoot "data\startup-logs"
$dashboardPidFile = Join-Path $logDirectory "dashboard_18080.pid"
$miruAiPidFile = Join-Path $logDirectory "miru_ai_worktree.pid"

function Stop-ByPidFile {
    param([string]$PidFilePath, [string]$Label)
    if (-not (Test-Path $PidFilePath)) {
        return $false
    }
    try {
        $content = Get-Content -Path $PidFilePath -Raw -ErrorAction Stop
        $obj = $content | ConvertFrom-Json -ErrorAction Stop
        $processId = [int]($obj.pid)
        if ($processId -le 0) { return $false }
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $proc) {
            Write-Host "$Label PID $processId not running (stale PID file)."
            return $true
        }
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Write-Host "Stopped $Label (PID $processId)."
        return $true
    }
    catch {
        Write-Warning "Could not stop from $PidFilePath : $($_.Exception.Message)"
        return $false
    }
}

$stoppedAny = $false

if ($Native) {
    if (Test-Path $dashboardPidFile) {
        if (Stop-ByPidFile -PidFilePath $dashboardPidFile -Label "worktree dashboard (18080)") { $stoppedAny = $true }
    }
    else {
        Write-Host "No native dashboard PID file at $dashboardPidFile (maybe not started with -Native)."
    }
    if (Test-Path $miruAiPidFile) {
        if (Stop-ByPidFile -PidFilePath $miruAiPidFile -Label "Miru AI (18765)") { $stoppedAny = $true }
    }
    else {
        Write-Host "No Miru AI PID file at $miruAiPidFile (maybe not started with -Native)."
    }
}

if ($Docker) {
    . (Join-Path $PSScriptRoot "op_miru_common.ps1")
    $composeFile = Join-Path $repoRoot "docker-compose.worktree.yml"
    $dockerConfigDirectory = Join-Path $repoRoot ".docker-config"
    if (-not (Test-Path $composeFile)) {
        Write-Warning "Docker compose file not found at $composeFile; skipping Docker stop."
    }
    else {
        $downResult = Invoke-OpMiruDockerCli `
            -Arguments @("compose", "-p", "op-miru-worktree", "-f", $composeFile, "down") `
            -DockerConfigDirectory $dockerConfigDirectory `
            -WorkingDirectory $repoRoot
        if ($downResult.Success) {
            Write-Host "Docker project op-miru-worktree stopped."
            $stoppedAny = $true
        }
        else {
            Write-Warning "Docker compose down failed: $(($downResult.Output) -join ' ')"
        }
    }
}

if (-not $Native -and -not $Docker) {
    Write-Host "Specify -Native (stop native Python dashboard + Miru AI) and/or -Docker (stop worktree Docker project)."
    Write-Host "Example: .\windows\stop_op_miru_worktree.ps1 -Native"
    exit 0
}

if (-not $stoppedAny) {
    Write-Host "Nothing was stopped (no matching PID files or Docker project state)."
}
