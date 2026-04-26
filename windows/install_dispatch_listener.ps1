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

function Test-EnvSecretPresent {
    if (-not (Test-Path $envFile)) { return $false }
    return ((Select-String -Path $envFile -Pattern '^W4_LISTENER_HMAC_SECRET=' -SimpleMatch:$false -Quiet) -eq $true)
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

    if (-not (Test-EnvSecretPresent)) {
        throw "W4_LISTENER_HMAC_SECRET not present in $envFile -- operator must add it before install."
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

    # Stop any prior listener that may still be holding port 19100. Best-effort.
    $stalePids = @(
        Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Where-Object { $_ -gt 0 } |
            Sort-Object -Unique
    )
    foreach ($p in $stalePids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-LogLine "stale_pid_killed=$p"
        } catch {
            Write-LogLine "stale_pid_kill_failed=$p reason=$($_.Exception.Message)"
        }
    }

    # Build the Scheduled Task. Conventions match windows\register_restart_tasks.ps1.
    # The wrapper script handles stdout/stderr redirection cleanly via
    # Start-Process -RedirectStandardOutput/-RedirectStandardError and propagates
    # the listener's exit code so Task Scheduler's RestartOnFailure can fire.
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
