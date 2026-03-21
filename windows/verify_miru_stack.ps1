# Verify health of authoritative Miru runtimes: 18765 (Miru AI Dev), 18080 (Project Miru worktree), optionally 8080 (main stable).
# Exit 0 only if checked ports are healthy. Use -WorktreeOnly to check only 18765 and 18080. Use -Quiet to suppress per-port output.
param(
    [switch]$Quiet,
    [switch]$WorktreeOnly,
    [int]$TimeoutSeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
. (Join-Path $scriptDir "op_miru_common.ps1")
. (Join-Path $scriptDir "op_miru_runtime.ps1")

$ports = Get-MiruRuntimePorts
$allOk = $true

# 18765 - Miru AI Dev
$health18765 = Test-OpMiruHttp -Url "http://127.0.0.1:$($ports.MiruAiDevPort)/api/health" -TimeoutSeconds $TimeoutSeconds -MustContain '"status":"ok"'
$dev18765 = Test-OpMiruHttp -Url "http://127.0.0.1:$($ports.MiruAiDevPort)/dev" -TimeoutSeconds $TimeoutSeconds
$ok18765 = $health18765.Ok -and $dev18765.Ok
if (-not $Quiet) {
    $s = if ($ok18765) { "healthy" } else { "unhealthy" }
    Write-Host "18765 (Miru AI Dev): $s"
}
if (-not $ok18765) { $allOk = $false }

# 18080 - Project Miru worktree
$ok18080 = Test-DashboardHealthy -Port $ports.ProjectMiruWorktreePort -TimeoutSeconds $TimeoutSeconds
if (-not $Quiet) {
    $s = if ($ok18080) { "healthy" } else { "unhealthy" }
    Write-Host "18080 (Project Miru worktree): $s"
}
if (-not $ok18080) { $allOk = $false }

# 8080 - Main stable (skipped when -WorktreeOnly)
if (-not $WorktreeOnly) {
    $ok8080 = Test-DashboardHealthy -Port $ports.MainStablePort -TimeoutSeconds $TimeoutSeconds
    if (-not $Quiet) {
        $s = if ($ok8080) { "healthy" } else { "unhealthy" }
        Write-Host "8080 (main stable): $s"
    }
    if (-not $ok8080) { $allOk = $false }
}

if ($allOk) {
    if (-not $Quiet) {
        if ($WorktreeOnly) { Write-Host "Worktree runtimes (18765, 18080) are healthy." }
        else { Write-Host "All three runtimes are healthy." }
    }
    exit 0
}
exit 1
