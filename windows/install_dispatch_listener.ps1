# install_dispatch_listener.ps1 -- register/refresh the Scheduled Task that
# runs the Miru W4 Dispatch Listener (PRO-83). Idempotent -- safe to re-run.
#
# Usage:  powershell -ExecutionPolicy Bypass -File windows\install_dispatch_listener.ps1
#
# Why a Scheduled Task instead of an NSSM service:
#   PRO-83 originally specified NSSM, but on this Windows install LocalSystem
#   (NSSM's default identity) cannot stat several npm-installed CLIs in the
#   operator's %APPDATA%\npm directory -- including claude.cmd and codex.cmd --
#   despite identical NTFS ACLs. The blocker is some flavor of Smart App
#   Control / AppContainer redirection that selectively shadows those binaries
#   from non-user identities. Running NSSM as the operator user would require
#   storing a password; switching to a Scheduled Task with S4U logon gives the
#   task the operator's identity (no stored password, full %APPDATA%\npm
#   visibility) and matches the existing Miru convention used by Dispatcher,
#   PM, and Miru AI (see windows\register_restart_tasks.ps1).

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$scriptDir       = $PSScriptRoot
$repoRoot        = Split-Path -Parent $scriptDir
$taskName        = "MiruDispatchListener"
$taskDescription = "Miru W4 Dispatch Listener (PRO-83). HMAC-gated webhook on 127.0.0.1:19100 that spawns claude/gemini/codex on operator dispatch. Auto-starts at boot, auto-restarts on failure."
$port            = 19100
$listenerDir     = Join-Path $repoRoot "services\dispatch_listener"
$entryScript     = Join-Path $listenerDir "src\index.js"
$wrapperScript   = Join-Path $scriptDir "start_dispatch_listener.ps1"
$logDirectory    = Join-Path $repoRoot "logs"
$installLogPath  = Join-Path $logDirectory "install_dispatch_listener.log"
$envFile         = Join-Path $repoRoot ".env"
$inboxDir        = Join-Path $repoRoot "data\n8n_inbox"
$traceLogDir     = Join-Path $logDirectory "dispatch_listener_traces"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $inboxDir | Out-Null
New-Item -ItemType Directory -Force -Path $traceLogDir | Out-Null
Set-Content -Path $installLogPath -Value "" -Encoding UTF8

$exitCode    = 1
$finalMarker = "INSTALL_FAILED"

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $line = "$timestamp`t$Message"
    Add-Content -Path $installLogPath -Value $line -Encoding UTF8
    Write-Host "[install-dispatch-listener] $Message"
}

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-Required {
    param([Parameter(Mandatory = $true)][string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $found) {
        throw "$Name not on PATH -- install it before running this script."
    }
    return $found.Source
}

function Test-UrlReachable {
    param([Parameter(Mandatory = $true)][string]$Url, [int]$TimeoutSeconds = 10)
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Proxy = $null
        $req.AllowAutoRedirect = $true
        $req.Method = "GET"
        $req.Timeout = [Math]::Max(1000, $TimeoutSeconds * 1000)
        $resp = $req.GetResponse()
        try { return ([int]$resp.StatusCode -ge 200 -and [int]$resp.StatusCode -lt 400) }
        finally { $resp.Close() }
    } catch { return $false }
}

function Wait-ForUrl {
    param([Parameter(Mandatory = $true)][string]$Url, [int]$TimeoutSeconds = 30, [int]$RetryDelaySeconds = 2)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-UrlReachable -Url $Url -TimeoutSeconds 5) { return $true }
        Start-Sleep -Seconds $RetryDelaySeconds
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-EnvSecretState {
    # Returns @{ Present = <bool>; HasValue = <bool> } so the caller can
    # surface a specific error for the line-missing case versus the
    # line-present-but-empty case. The empty case is the silent-crash-loop
    # trap that motivated this check (Bugbot Low finding on PR #22): a blank
    # `W4_LISTENER_HMAC_SECRET=` (the literal shape in .env.example) used to
    # pass the previous `^W4_LISTENER_HMAC_SECRET=` check, then the listener
    # would exit 2 on every wrapper respawn.
    if (-not (Test-Path $envFile)) {
        return @{ Present = $false; HasValue = $false }
    }
    $hasLine = (Select-String -Path $envFile -Pattern '^W4_LISTENER_HMAC_SECRET=' -SimpleMatch:$false -Quiet) -eq $true
    if (-not $hasLine) {
        return @{ Present = $false; HasValue = $false }
    }
    $hasValue = (Select-String -Path $envFile -Pattern '^W4_LISTENER_HMAC_SECRET=.+$' -SimpleMatch:$false -Quiet) -eq $true
    return @{ Present = $true; HasValue = $hasValue }
}

try {
    Write-LogLine "action=install_begin task=$taskName port=$port"

    if (-not (Test-Administrator)) {
        throw "Register-ScheduledTask requires elevation. Re-run from an elevated PowerShell (Start-Process powershell -Verb RunAs ...)."
    }
    Write-LogLine "elevated=yes"

    $node = Resolve-Required -Name "node"
    Write-LogLine "node_path=$node"

    if (-not (Test-Path $entryScript)) {
        throw "Listener entry not found at $entryScript"
    }
    Write-LogLine "entry_script=$entryScript"

    if (-not (Test-Path $wrapperScript)) {
        throw "Wrapper script not found at $wrapperScript"
    }
    Write-LogLine "wrapper_script=$wrapperScript"

    $secretState = Test-EnvSecretState
    if (-not $secretState.Present) {
        throw "W4_LISTENER_HMAC_SECRET not present in $envFile -- operator must add it before install."
    }
    if (-not $secretState.HasValue) {
        throw "W4_LISTENER_HMAC_SECRET is set but empty in $envFile. Generate a value before reinstalling: [Convert]::ToBase64String((1..32 | %{Get-Random -Maximum 256}))"
    }
    Write-LogLine "env_secret_present=yes"

    Push-Location $listenerDir
    try {
        Write-LogLine "action=npm_install_begin"
        $npmCmd = (Get-Command "npm" -ErrorAction Stop).Source
        $npmOut = & cmd /c "`"$npmCmd`" install --omit=dev --no-audit --no-fund 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE`n$npmOut" }
        Write-LogLine "action=npm_install_done"
    } finally {
        Pop-Location
    }

    # If a leftover NSSM service still exists from earlier installs, remove it
    # so its leftover state can't compete with the new Scheduled Task.
    $existingService = Get-Service -Name $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existingService) {
        Write-LogLine "leftover_nssm_service=present status=$($existingService.Status)"
        $nssm = (Get-Command "nssm" -ErrorAction SilentlyContinue).Source
        if ($nssm) {
            & $nssm stop $taskName 2>&1 | ForEach-Object { Write-LogLine "nssm stop: $_" }
            & $nssm remove $taskName confirm 2>&1 | ForEach-Object { Write-LogLine "nssm remove: $_" }
            Start-Sleep -Seconds 1
            Write-LogLine "leftover_nssm_service=removed"
        } else {
            Write-LogLine "warn=nssm_not_on_path_cannot_remove_leftover_service"
        }
    } else {
        Write-LogLine "leftover_nssm_service=absent"
    }

    # ------------------------------------------------------------------------
    # Reinstall teardown (PR #22 Bugbot finding "Install kills listener PID
    # but leaves wrapper respawning"). Naive teardown -- killing only port-19100
    # processes -- leaves the parent wrapper PowerShell alive. The wrapper
    # sleeps 30s then respawns its own node, racing the freshly-registered
    # task. Result: two listeners fight for port 19100, and the install's
    # post-start health check intermittently fails. Proper teardown order:
    #   1. Stop the Scheduled Task (kills wrapper + its child cmd/node tree
    #      via Windows job-object propagation, when it works).
    #   2. Wait for task State to leave Running.
    #   3. Defensively kill any orphan wrapper PowerShells (matched by cmdline)
    #      that survived the task stop -- this is the path that bit us during
    #      manual verification cycles.
    #   4. Kill any remaining node listening on port 19100.
    # ------------------------------------------------------------------------
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existingTask) {
        Write-LogLine "teardown_existing_task_state=$($existingTask.State)"
        try {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Write-LogLine "teardown_stop_scheduled_task=invoked"
        } catch {
            Write-LogLine "teardown_stop_scheduled_task_warn=$($_.Exception.Message)"
        }
        # Poll up to 10s for the task to leave Running state
        $deadline = (Get-Date).AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 500
            $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if ($null -eq $t -or $t.State -ne 'Running') { break }
        } while ((Get-Date) -lt $deadline)
        $finalState = if ($null -ne $t) { $t.State } else { 'absent' }
        Write-LogLine "teardown_task_state_after_stop=$finalState"
    } else {
        Write-LogLine "teardown_existing_task=absent"
    }

    # Defensive wrapper-PowerShell kill: any powershell.exe whose command line
    # contains start_dispatch_listener.ps1 must die before we register the new
    # task, otherwise the old wrapper's 30s respawn loop will race us.
    $wrapperProcs = @(
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*start_dispatch_listener.ps1*' -and $_.ProcessId -ne $PID }
    )
    if ($wrapperProcs.Count -eq 0) {
        Write-LogLine "teardown_wrapper_ps_killed=none"
    } else {
        foreach ($wp in $wrapperProcs) {
            try {
                Stop-Process -Id $wp.ProcessId -Force -ErrorAction Stop
                Write-LogLine "teardown_wrapper_ps_killed=$($wp.ProcessId)"
            } catch {
                Write-LogLine "teardown_wrapper_ps_kill_failed=$($wp.ProcessId) reason=$($_.Exception.Message)"
            }
        }
        Start-Sleep -Milliseconds 500
    }

    # Final cleanup: any orphan node still bound to port 19100.
    $stalePids = @(
        Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Where-Object { $_ -gt 0 } |
            Sort-Object -Unique
    )
    foreach ($p in $stalePids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-LogLine "teardown_orphan_listener_killed=$p"
        } catch {
            Write-LogLine "teardown_orphan_listener_kill_failed=$p reason=$($_.Exception.Message)"
        }
    }
    if ($stalePids.Count -eq 0) {
        Write-LogLine "teardown_orphan_listener_killed=none"
    }
    Write-LogLine "teardown_complete=yes"

    # Build the Scheduled Task. Conventions match windows\register_restart_tasks.ps1.
    # The wrapper script (start_dispatch_listener.ps1) hands off to cmd.exe with
    # `>> append` redirection so node's UTF-8 stdout/stderr land in the log
    # files unmangled, and owns a respawn loop that fires on non-zero exit.
    $argString = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$wrapperScript`""
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument $argString `
        -WorkingDirectory $repoRoot

    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = "PT15S"  # 15s grace after boot before launching

    # Reliability requirements (PRO-83 brief):
    #  - Auto-restart on failure: RestartCount=999, RestartInterval=PT1M
    #  - Allowed on battery (and not stopped going on battery)
    #  - Single instance only -- if the task is already running, ignore new runs
    #  - StartWhenAvailable so a missed boot trigger still fires when machine wakes
    #  - No execution time limit -- this is a long-running daemon
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)

    $userId    = "$($env:UserDomain)\$($env:UserName)"
    $principal = New-ScheduledTaskPrincipal `
        -UserId    $userId `
        -LogonType S4U `
        -RunLevel  Highest

    Write-LogLine "principal=$userId logon=S4U runlevel=Highest"
    Write-LogLine "trigger=AtStartup delay=PT15S"
    Write-LogLine "restart=count=999 interval=PT1M"

    $task = Register-ScheduledTask `
        -TaskName    $taskName `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description $taskDescription `
        -Force

    Write-LogLine "register_scheduled_task=ok task_path=$($task.TaskPath)"

    # Start it now so we can verify health immediately.
    Start-ScheduledTask -TaskName $taskName
    Write-LogLine "start_scheduled_task=invoked"

    $healthUrl = "http://127.0.0.1:$port/health"
    if (-not (Wait-ForUrl -Url $healthUrl -TimeoutSeconds 30)) {
        throw "Health check at $healthUrl did not respond within 30s after task start."
    }
    Write-LogLine "health_check=ok url=$healthUrl"

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { throw "No LISTEN sockets bound on port $port after task start." }
    foreach ($l in $listeners) {
        $addr = [string]$l.LocalAddress
        Write-LogLine "bind_observed=$addr"
        if ($addr -ne "127.0.0.1" -and $addr -ne "::1") {
            throw "Listener bound to non-loopback address $addr -- refusing to leave task running."
        }
    }
    Write-LogLine "bind_check=loopback_only"

    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-LogLine "task_state=$($info.LastTaskResult) next_run=$($info.NextRunTime) last_run=$($info.LastRunTime)"

    $finalMarker = "INSTALL_SUCCESS"
    $exitCode    = 0
} catch {
    Write-LogLine "error=$($_.Exception.Message)"
    $finalMarker = "INSTALL_FAILED"
    $exitCode    = 1
} finally {
    Add-Content -Path $installLogPath -Value $finalMarker -Encoding UTF8
    Write-Host "[install-dispatch-listener] $finalMarker"
}

exit $exitCode
