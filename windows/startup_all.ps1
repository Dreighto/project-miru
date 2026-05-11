# startup_all.ps1 — Project Miru boot-time startup hooks
# Called by the "OP Miru Startup" scheduled task at boot.
#
# What this script does today (2026-05-11):
#   - Loads .env so child task processes inherit API keys.
#   - Self-registers four scheduled tasks (idempotent — skips if already
#     present and enabled): MiruServiceWatchdog, MiruStallRecovery,
#     MiruSentinel, MiruBackup.
#
# What it NO LONGER does:
#   The three legacy services (Dispatcher 19000, PM Dashboard 18080,
#   Miru AI 18765) were decommissioned in this repo on 2026-05-11. Their
#   launcher blocks and the associated launcher / scheduled-task helper
#   scripts were removed in the same change. The orchestrator (MCP Gateway
#   on 18766, dispatch_listener on 19100) is the active service set; the
#   watchdog/stall-recovery/sentinel tasks below monitor those.
#
# Replacement strategy for the legacy services is intentionally deferred
# (see operator note 2026-05-11): a new restart UX will be tied to the
# LogueOS Console rather than re-built as PowerShell scripts.

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

# ── Resolve Python (required by self-register blocks below) ───────────────────
# CR R3 (PR #193): the top-level `$pythonExe = $pythonCmd.Source` assignment
# was a leftover from the deleted legacy service launchers. The self-register
# blocks (MiruStallRecovery, MiruSentinel) compute their own python path
# locally — see the `$pythonExe = if (Test-Path $pythonwPath) { ... }` lines
# inside each block. Log directly via $pythonCmd.Source to keep the diagnostic
# breadcrumb without binding an unused outer variable.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Log "WARNING: python not found on PATH — self-register blocks may skip"
} else {
    Write-Log "python=$($pythonCmd.Source)"
}

# ════════════════════════════════════════════════════════════════════════════════
# LEGACY SERVICES (Dispatcher 19000 / PM 18080 / Miru AI 18765) — REMOVED
#
# These three services were decommissioned in this repo on 2026-05-11. The
# launcher blocks that previously lived here, along with their helper scripts
# (windows/start_dispatcher.ps1, windows/restart_dispatcher.ps1,
# windows/start_all_services.ps1, windows/restart_pm.ps1,
# windows/restart_miru_ai.ps1, windows/start_miru_ai_dev.ps1,
# windows/start_project_miru_dashboard.ps1, windows/install_dispatcher_startup.ps1,
# windows/op_dispatcher_bootstrap.cmd, and the
# windows/tasks/restart_{dispatcher,pm,miru_ai}_task.ps1 scheduled-task helpers),
# were removed in the same change. Active services (MCP Gateway 18766,
# dispatch_listener 19100) live in the LogueOS-Orchestrator repo and boot via
# its own startup mechanisms.
# ════════════════════════════════════════════════════════════════════════════════

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

# ════════════════════════════════════════════════════════════════════════════════
# SELF-REGISTER: MiruBackup
# Backs up miru_memory.db, .env, and gitignored data files to D:\backups\miru\
# and G:\My Drive\Miru Backups\ twice daily. Keeps 7 rolling days.
# Telegram alert on failure only. Idempotent; skips if already registered.
# ════════════════════════════════════════════════════════════════════════════════
Write-Log "--- Checking MiruBackup registration ---"
try {
    $backupTask = Get-ScheduledTask -TaskName "MiruBackup" -ErrorAction SilentlyContinue
    if ($backupTask -and $backupTask.State -ne "Disabled") {
        Write-Log "MiruBackup already registered state=$($backupTask.State) -- skipping"
    } else {
        $backupWrapperScript = Join-Path $windowsDir "tasks\run_backup.vbs"
        if (-not (Test-Path $backupWrapperScript)) {
            Write-Log "WARNING: backup VBS wrapper not found at $backupWrapperScript -- skipping"
        } else {
            $buAction = New-ScheduledTaskAction `
                -Execute "wscript.exe" `
                -Argument "`"$backupWrapperScript`"" `
                -WorkingDirectory $repoRoot

            $buTrigger = New-ScheduledTaskTrigger `
                -Once -At (Get-Date).AddMinutes(10) `
                -RepetitionInterval (New-TimeSpan -Hours 12)

            $buSettings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
                -MultipleInstances IgnoreNew

            $buUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

            $buTask = Register-ScheduledTask `
                -TaskName    "MiruBackup" `
                -Description "Project Miru data backup. Runs twice daily. Copies miru_memory.db, .env, and gitignored data files to D:\backups\miru\ and G:\My Drive\Miru Backups\. Keeps 7 rolling days. Telegram alert on failure." `
                -Action      $buAction `
                -Trigger     $buTrigger `
                -Settings    $buSettings `
                -RunLevel    Limited `
                -User        $buUser `
                -Force

            Write-Log "MiruBackup registered user=$buUser state=$($buTask.State)"
        }
    }
} catch {
    Write-Log "WARNING: MiruBackup self-registration failed: $($_.Exception.Message)"
}

Write-Log "=== startup_all.ps1 END"
exit 0
