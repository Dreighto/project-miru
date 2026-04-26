# start_dispatch_listener.ps1 -- entry point for the MiruDispatchListener
# Scheduled Task. Launches the Node listener with stdout/stderr redirected to
# logs/dispatch_listener_*.log and respawns the listener if it crashes.
#
# Why an internal respawn loop instead of trusting Task Scheduler's
# RestartOnFailure setting:
#
# Empirically on the deployment machine (ROOM, Windows 11 26200), the task's
# `<RestartOnFailure><Count>999</Count><Interval>PT1M</Interval></RestartOnFailure>`
# does not fire when the action exits with code 1 (verified after `taskkill /F`
# of the listener pid: LastTaskResult=1, task transitions to Ready, but no new
# instance starts within several minutes). Rather than chase this Windows
# quirk, we own the respawn loop here. Task Scheduler still owns the AtStartup
# trigger and the high-level lifecycle (Stop-ScheduledTask kills the whole job
# tree); this wrapper handles "the listener crashed, bring it back" within
# seconds.
#
# Graceful exits (exit code 0) are NOT respawned -- this lets
# `Stop-ScheduledTask` shut things down cleanly via the listener's SIGTERM
# handler. Non-zero exits are respawned with a short backoff. Up to
# $MAX_RESPAWNS attempts before the wrapper itself bails out (Task Scheduler's
# RestartOnFailure is then the final-fallback mechanism).

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$scriptDir  = $PSScriptRoot
$repoRoot   = Split-Path -Parent $scriptDir
$logDir     = Join-Path $repoRoot "logs"
$entry      = Join-Path $repoRoot "services\dispatch_listener\src\index.js"
$stdoutLog  = Join-Path $logDir "dispatch_listener_stdout.log"
$stderrLog  = Join-Path $logDir "dispatch_listener_stderr.log"
$wrapperLog = Join-Path $logDir "dispatch_listener_wrapper.log"

$MAX_RESPAWNS    = 50      # ~25 minutes of crash-loop tolerance at $RESPAWN_BACKOFF
$RESPAWN_BACKOFF = 30      # seconds between respawns after a non-zero exit

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-WrapperLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[$(Get-Date -Format o)] start_dispatch_listener: $Message"
    Add-Content -Path $wrapperLog -Value $line -Encoding UTF8
}

if (-not (Test-Path $entry)) {
    Write-WrapperLog "fatal: entry not found at $entry"
    exit 2
}

$nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-WrapperLog "fatal: node not on PATH"
    exit 3
}

$respawns = 0
$lastExit = -1
while ($respawns -lt $MAX_RESPAWNS) {
    Write-WrapperLog "spawn attempt=$($respawns + 1) node=$($nodeCmd.Source) entry=$entry"

    Push-Location -Path $repoRoot
    try {
        $cmdLine = ('"{0}" "{1}" >> "{2}" 2>> "{3}"' -f $nodeCmd.Source, $entry, $stdoutLog, $stderrLog)
        & $env:ComSpec /d /c $cmdLine
        $lastExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-WrapperLog "exit code=$lastExit attempts=$($respawns + 1)"

    if ($lastExit -eq 0) {
        Write-WrapperLog "graceful exit -- not respawning"
        break
    }

    $respawns++
    if ($respawns -ge $MAX_RESPAWNS) {
        Write-WrapperLog "respawn budget exhausted ($MAX_RESPAWNS) -- giving up; Task Scheduler RestartOnFailure is the final fallback"
        break
    }

    Write-WrapperLog "respawning in ${RESPAWN_BACKOFF}s"
    Start-Sleep -Seconds $RESPAWN_BACKOFF
}

exit $lastExit
