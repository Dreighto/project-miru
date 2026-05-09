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

try { Add-Type -Name MiruHide -Namespace W32 -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);' -ErrorAction SilentlyContinue } catch {}
try { [W32.MiruHide]::ShowWindow([W32.MiruHide]::GetConsoleWindow(), 0) | Out-Null } catch {}

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

# Session 0 self-check (PRO-336): at boot, before operator login, Windows
# S4U-launched scheduled tasks run in Session 0 (the non-interactive service
# session). A non-elevated worker shell (Claude Code) running in the operator's
# interactive session (Session 1+) cannot kill cross-session processes without
# SeDebugPrivilege -- so a Session 0 listener defeats the restart mechanism.
# If this guard fires, the MiruDispatchListener scheduled task launched us at
# boot via the old S4U/AtStartup path. Fix: run
#   windows\install_dispatch_listener_startup_shortcut.ps1
# then log off and back on so the shell:startup shortcut fires in Session 1+.
$_currentSessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
if ($_currentSessionId -eq 0) {
    Write-WrapperLog "WARN: session_id=0 -- running in Session 0 (non-interactive service session). A non-elevated worker shell cannot Stop-Process this PID. Primary boot path is the shell:startup shortcut. Run windows\install_dispatch_listener_startup_shortcut.ps1 then reboot. Exiting to surface this regression."
    exit 1
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

# Guard: if port 19100 is already bound, the listener is running -- exit gracefully
# so the scheduled task sees success and doesn't trigger a crash-respawn loop.
$portBound = Get-NetTCPConnection -LocalPort 19100 -State Listen -ErrorAction SilentlyContinue |
             Select-Object -First 1
if ($portBound) {
    Write-WrapperLog "port 19100 already listening (PID=$($portBound.OwningProcess)) -- dispatch_listener already running, exiting gracefully"
    exit 0
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
