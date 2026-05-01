# startup_all.ps1 — Project Miru full-stack startup script
# Starts all three services in order with delays between them.
# Called by the "OP Miru Startup" scheduled task at boot.
#
# Services started (in order):
#   1. Dispatcher   — port 19000  (dispatcher\task_dispatcher.py)
#   2. PM Dashboard — port 18080  (pm\app.py)
#   3. Miru AI      — port 18765  (python -m miru_ai.server)
#
# IMPORTANT: This script uses Start-Process without -Verb RunAs so that
# child processes inherit the caller's token. When registered under NAS\NAS
# with RunLevel=Limited, all three services run as non-elevated NAS\NAS
# processes. This ensures that subsequent restarts from a non-elevated
# Claude Code shell (also NAS\NAS, non-elevated) can Stop-Process them
# without triggering a UAC credential prompt.

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # don't abort at boot on partial errors

$repoRoot  = Split-Path -Parent $PSScriptRoot   # windows\ -> repo root
$windowsDir = $PSScriptRoot
$logDir    = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$startupLog = Join-Path $logDir "startup.log"

function Write-Log {
    param([string]$Msg)
    $line = "$((Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))`t$Msg"
    Add-Content -Path $startupLog -Value $line -Encoding UTF8
    Write-Host "[startup_all] $Msg"
}

# Separator for this boot's log block
Add-Content -Path $startupLog -Value "" -Encoding UTF8
Write-Log "========================================"
Write-Log "=== startup_all.ps1 BEGIN"
Write-Log "repo_root=$repoRoot"
Write-Log "caller_pid=$PID"

# ── Load .env so child processes inherit API keys ─────────────────────────────
$commonPath = Join-Path $windowsDir "op_miru_common.ps1"
if (Test-Path $commonPath) {
    try {
        . $commonPath
        $envResult = Import-OpMiruDotEnv -RepoRoot $repoRoot
        if ($envResult.Exists) {
            Write-Log "env=loaded ($($envResult.LoadedKeys.Count) keys)"
        } else {
            Write-Log "env=no .env file found"
        }
    } catch {
        Write-Log "WARNING: op_miru_common.ps1 failed to load: $($_.Exception.Message)"
    }
} else {
    Write-Log "WARNING: op_miru_common.ps1 not found at $commonPath"
}

# ── Resolve Python ────────────────────────────────────────────────────────────
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Log "ERROR: python not found on PATH — cannot start any service"
    exit 1
}
$pythonExe = $pythonCmd.Source
Write-Log "python=$pythonExe"

# ── Helper: kill any process listening on a port ──────────────────────────────
function Stop-PortListeners {
    param([int]$Port)
    $pids = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } | Where-Object { $_ -gt 0 } | Sort-Object -Unique
    )
    foreach ($p in $pids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-Log "port=$Port killed_pid=$p"
        } catch {
            Write-Log "port=$Port could_not_kill_pid=$p ($($_.Exception.Message))"
        }
    }
    if ($pids.Count -gt 0) { Start-Sleep -Seconds 1 }
}

# ════════════════════════════════════════════════════════════════════════════════
# 1. DISPATCHER — port 19000
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting Dispatcher (port 19000) ---"
try {
    Stop-PortListeners -Port 19000

    $dispatcherScript  = Join-Path $repoRoot "dispatcher\task_dispatcher.py"
    $dispatcherWorkDir = Join-Path $repoRoot "dispatcher"
    $dispatcherStdout  = Join-Path $logDir   "dispatcher_stdout.log"
    $dispatcherStderr  = Join-Path $logDir   "dispatcher_stderr.log"

    if (-not (Test-Path $dispatcherScript)) {
        Write-Log "ERROR: Dispatcher script not found at $dispatcherScript"
    } else {
        Set-Content -Path $dispatcherStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
        Set-Content -Path $dispatcherStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

        $proc = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($dispatcherScript) `
            -WorkingDirectory $dispatcherWorkDir `
            -RedirectStandardOutput $dispatcherStdout `
            -RedirectStandardError  $dispatcherStderr `
            -WindowStyle Hidden `
            -PassThru

        Write-Log "Dispatcher started pid=$($proc.Id)"
    }
} catch {
    Write-Log "ERROR starting Dispatcher: $($_.Exception.Message)"
}

Write-Log "Waiting 5s before PM Dashboard..."
Start-Sleep -Seconds 5

# ════════════════════════════════════════════════════════════════════════════════
# 2. PM DASHBOARD — port 18080
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting PM Dashboard (port 18080) ---"
try {
    Stop-PortListeners -Port 18080

    $pmScript  = Join-Path $repoRoot "pm\app.py"
    $pmStdout  = Join-Path $logDir   "pm_stdout.log"
    $pmStderr  = Join-Path $logDir   "pm_stderr.log"

    if (-not (Test-Path $pmScript)) {
        Write-Log "ERROR: PM script not found at $pmScript"
    } else {
        Set-Content -Path $pmStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
        Set-Content -Path $pmStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

        $env:PORT                        = "18080"
        $env:PROJECT_MIRU_DASHBOARD_PORT = "18080"
        $env:MIRU_MAIN_RUNTIME_ROOT      = $repoRoot
        $env:MIRU_DASHBOARD_NO_RELOAD    = "1"
        [Environment]::SetEnvironmentVariable("WERKZEUG_SERVER_FD",  $null, "Process")
        [Environment]::SetEnvironmentVariable("WERKZEUG_RUN_MAIN",   $null, "Process")

        $proc = Start-Process `
            -FilePath $pythonExe `
            -ArgumentList @($pmScript) `
            -WorkingDirectory $repoRoot `
            -RedirectStandardOutput $pmStdout `
            -RedirectStandardError  $pmStderr `
            -WindowStyle Hidden `
            -PassThru

        Write-Log "PM Dashboard started pid=$($proc.Id)"
    }
} catch {
    Write-Log "ERROR starting PM Dashboard: $($_.Exception.Message)"
}

Write-Log "Waiting 5s before Miru AI..."
Start-Sleep -Seconds 5

# ════════════════════════════════════════════════════════════════════════════════
# 3. MIRU AI — port 18765
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Starting Miru AI (port 18765) ---"
try {
    Stop-PortListeners -Port 18765

    $miruAiStdout = Join-Path $logDir "miru_ai_stdout.log"
    $miruAiStderr = Join-Path $logDir "miru_ai_stderr.log"

    Set-Content -Path $miruAiStdout -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
    Set-Content -Path $miruAiStderr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

    $proc = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "miru_ai.server", "--host", "0.0.0.0", "--port", "18765") `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $miruAiStdout `
        -RedirectStandardError  $miruAiStderr `
        -WindowStyle Hidden `
        -PassThru

    Write-Log "Miru AI started pid=$($proc.Id)"
} catch {
    Write-Log "ERROR starting Miru AI: $($_.Exception.Message)"
}

Start-Sleep -Seconds 3

# ════════════════════════════════════════════════════════════════════════════════
# SELF-REGISTER: MiruServiceWatchdog (PRO-238)
# Registers the service watchdog if not already present. Runs as the current
# user (NAS\NAS, S4U, RunLevel=Limited) -- no elevation required to register
# Limited-level tasks for the current account. Idempotent: skips if already
# registered and enabled. Logs but never aborts startup on failure.
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Checking MiruServiceWatchdog registration ---"
try {
    $watchdogTask = Get-ScheduledTask -TaskName "MiruServiceWatchdog" -ErrorAction SilentlyContinue
    if ($watchdogTask -and $watchdogTask.State -ne "Disabled") {
        Write-Log "MiruServiceWatchdog already registered state=$($watchdogTask.State) -- skipping"
    } else {
        $watchdogScript = Join-Path $windowsDir "tasks\run_watchdog.vbs"
        if (-not (Test-Path $watchdogScript)) {
            Write-Log "WARNING: watchdog VBS wrapper not found at $watchdogScript -- skipping"
        } else {
            $wdAction = New-ScheduledTaskAction `
                -Execute "wscript.exe" `
                -Argument "`"$watchdogScript`"" `
                -WorkingDirectory $repoRoot

            $wdTrigger = New-ScheduledTaskTrigger `
                -Once -At (Get-Date).AddMinutes(2) `
                -RepetitionInterval (New-TimeSpan -Minutes 2)

            $wdSettings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
                -MultipleInstances IgnoreNew

            $wdUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

            $wdTask = Register-ScheduledTask `
                -TaskName    "MiruServiceWatchdog" `
                -Description "Project Miru service watchdog. Polls gateway (18766) and dispatch listener (19100) every 2 min. Auto-restarts on failure, Telegram alerts on restart/recovery." `
                -Action      $wdAction `
                -Trigger     $wdTrigger `
                -Settings    $wdSettings `
                -RunLevel    Limited `
                -User        $wdUser `
                -Force

            Write-Log "MiruServiceWatchdog registered user=$wdUser state=$($wdTask.State)"
        }
    }
} catch {
    Write-Log "WARNING: MiruServiceWatchdog self-registration failed: $($_.Exception.Message)"
}

# ════════════════════════════════════════════════════════════════════════════════
# SELF-REGISTER: MiruStallRecovery (PRO-240)
# Polls heartbeat/completion logs every 3 min. Auto-re-dispatches stalled workers
# (1 retry), then Telegram-escalates. Idempotent; skips if already registered.
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Checking MiruStallRecovery registration ---"
try {
    $stallTask = Get-ScheduledTask -TaskName "MiruStallRecovery" -ErrorAction SilentlyContinue
    if ($stallTask -and $stallTask.State -ne "Disabled") {
        Write-Log "MiruStallRecovery already registered state=$($stallTask.State) -- skipping"
    } else {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) {
            Write-Log "WARNING: python not found -- cannot register MiruStallRecovery"
        } else {
            $recoveryScript = Join-Path $repoRoot "tools\orchestrator\recovery_router.py"
            if (-not (Test-Path $recoveryScript)) {
                Write-Log "WARNING: recovery_router.py not found at $recoveryScript -- skipping"
            } else {
                # Use pythonw.exe (windowless) so the task runs silently without
                # popping a console window on the logged-in user's desktop.
                $pythonwPath = Join-Path (Split-Path $pythonCmd.Source) "pythonw.exe"
                $pythonExe = if (Test-Path $pythonwPath) { $pythonwPath } else { $pythonCmd.Source }
                $srWrapperScript = Join-Path $windowsDir "tasks\run_stall_recovery.vbs"
                $srAction = New-ScheduledTaskAction `
                    -Execute "wscript.exe" `
                    -Argument "`"$srWrapperScript`"" `
                    -WorkingDirectory $repoRoot

                $srTrigger = New-ScheduledTaskTrigger `
                    -Once -At (Get-Date).AddMinutes(3) `
                    -RepetitionInterval (New-TimeSpan -Minutes 3)

                $srSettings = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
                    -MultipleInstances IgnoreNew

                $srUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

                $srTask = Register-ScheduledTask `
                    -TaskName    "MiruStallRecovery" `
                    -Description "Project Miru stall recovery. Polls worker heartbeat log every 3 min. Auto-re-dispatches stalled workers (1 retry budget), Telegram alert on escalation." `
                    -Action      $srAction `
                    -Trigger     $srTrigger `
                    -Settings    $srSettings `
                    -RunLevel    Limited `
                    -User        $srUser `
                    -Force

                Write-Log "MiruStallRecovery registered user=$srUser state=$($srTask.State)"
            }
        }
    }
} catch {
    Write-Log "WARNING: MiruStallRecovery self-registration failed: $($_.Exception.Message)"
}

# ════════════════════════════════════════════════════════════════════════════════
# SELF-REGISTER: MiruSentinel
# Runs tools/sentinel/health_check.py every 20 minutes. Checks service health,
# tails key logs, counts DLQ delta, asks AI if anything looks wrong, and sends
# a Telegram alert only when something needs attention. Silent otherwise.
# Uses pythonw.exe so no console window appears on the desktop.
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Checking MiruSentinel registration ---"
try {
    $sentinelTask = Get-ScheduledTask -TaskName "MiruSentinel" -ErrorAction SilentlyContinue
    if ($sentinelTask -and $sentinelTask.State -ne "Disabled") {
        Write-Log "MiruSentinel already registered state=$($sentinelTask.State) -- skipping"
    } else {
        $pythonCmd2 = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd2) {
            Write-Log "WARNING: python not found -- cannot register MiruSentinel"
        } else {
            $sentinelScript = Join-Path $repoRoot "tools\sentinel\health_check.py"
            if (-not (Test-Path $sentinelScript)) {
                Write-Log "WARNING: health_check.py not found at $sentinelScript -- skipping"
            } else {
                $pythonwPath2 = Join-Path (Split-Path $pythonCmd2.Source) "pythonw.exe"
                $pythonExe2 = if (Test-Path $pythonwPath2) { $pythonwPath2 } else { $pythonCmd2.Source }

                $snWrapperScript = Join-Path $windowsDir "tasks\run_sentinel.vbs"
                $snAction = New-ScheduledTaskAction `
                    -Execute "wscript.exe" `
                    -Argument "`"$snWrapperScript`"" `
                    -WorkingDirectory $repoRoot

                $snTrigger = New-ScheduledTaskTrigger `
                    -Once -At (Get-Date).AddMinutes(20) `
                    -RepetitionInterval (New-TimeSpan -Minutes 20)

                $snSettings = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
                    -MultipleInstances IgnoreNew

                $snUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

                $snTask = Register-ScheduledTask `
                    -TaskName    "MiruSentinel" `
                    -Description "Project Miru health sentinel. Runs every 20 min. Checks services, logs, DLQ, and worker activity. AI-powered anomaly detection via Ollama or OpenAI. Telegram alert on issues." `
                    -Action      $snAction `
                    -Trigger     $snTrigger `
                    -Settings    $snSettings `
                    -RunLevel    Limited `
                    -User        $snUser `
                    -Force

                Write-Log "MiruSentinel registered user=$snUser state=$($snTask.State)"
            }
        }
    }
} catch {
    Write-Log "WARNING: MiruSentinel self-registration failed: $($_.Exception.Message)"
}

Write-Log "=== startup_all.ps1 END"
exit 0
