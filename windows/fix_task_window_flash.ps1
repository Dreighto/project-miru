# fix_task_window_flash.ps1
# Re-registers the four periodic SYSTEM tasks to use VBS wrappers instead of
# bare powershell.exe. Fixes the console window flash on Windows 11 24H2.
#
# MUST be run elevated (Run as Administrator).
# Safe to re-run — idempotent.

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

$tasks = @(
    @{
        Name        = "MiruServiceWatchdog"
        Wrapper     = Join-Path $repoRoot "windows\tasks\run_watchdog.vbs"
        Interval    = (New-TimeSpan -Minutes 2)
        TimeLimit   = (New-TimeSpan -Minutes 2)
        Description = "Project Miru service watchdog. Polls gateway, dispatch listener, and n8n every 2 min. Auto-restarts on failure, Telegram alerts on restart/recovery."
    },
    @{
        Name        = "MiruStallRecovery"
        Wrapper     = Join-Path $repoRoot "windows\tasks\run_stall_recovery.vbs"
        Interval    = (New-TimeSpan -Minutes 3)
        TimeLimit   = (New-TimeSpan -Minutes 3)
        Description = "Project Miru stall recovery. Polls worker heartbeat log every 3 min. Auto-re-dispatches stalled workers, Telegram alert on escalation."
    },
    @{
        Name        = "MiruSentinel"
        Wrapper     = Join-Path $repoRoot "windows\tasks\run_sentinel.vbs"
        Interval    = (New-TimeSpan -Minutes 20)
        TimeLimit   = (New-TimeSpan -Minutes 5)
        Description = "Project Miru health sentinel. Runs every 20 min. Checks services, logs, DLQ, and worker activity. AI-powered anomaly detection. Telegram alert on issues."
    },
    @{
        Name        = "MiruN8nWatchdog"
        Wrapper     = Join-Path $repoRoot "windows\tasks\run_n8n_watchdog.vbs"
        Interval    = (New-TimeSpan -Minutes 15)
        TimeLimit   = (New-TimeSpan -Minutes 2)
        Description = "Project Miru n8n watchdog. Polls n8n health every 15 min. Docker restart on failure."
    }
)

foreach ($t in $tasks) {
    Write-Host "`n--- $($t.Name) ---"

    if (-not (Test-Path $t.Wrapper)) {
        Write-Host "  SKIP: wrapper not found at $($t.Wrapper)"
        continue
    }

    $action = New-ScheduledTaskAction `
        -Execute "wscript.exe" `
        -Argument "`"$($t.Wrapper)`"" `
        -WorkingDirectory $repoRoot

    $trigger = New-ScheduledTaskTrigger `
        -Once -At (Get-Date).AddMinutes(5) `
        -RepetitionInterval $t.Interval

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit $t.TimeLimit `
        -MultipleInstances IgnoreNew

    $principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Limited

    try {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask `
            -TaskName    $t.Name `
            -Description $t.Description `
            -Action      $action `
            -Trigger     $trigger `
            -Settings    $settings `
            -Principal   $principal `
            -Force | Out-Null

        Write-Host "  OK: re-registered as SYSTEM + VBS wrapper"
    } catch {
        Write-Host "  FAIL: $($_.Exception.Message)"
    }
}

Write-Host "`nDone. All four tasks now use VBS wrappers (SW_HIDE) under SYSTEM."
Write-Host "The window flash should stop within the next cycle."
