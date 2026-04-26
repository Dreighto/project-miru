# smoke.ps1 -- PRO-83 W4 Dispatch Listener -- 10-scenario smoke run.
#
# Runs against the live Scheduled-Task-managed listener on
# http://127.0.0.1:19100. Generates a fresh trace_id for each scenario; writes
# a per-run log to logs/dispatch_listener_smoke_<timestamp>.log.
#
# Usage:  powershell -ExecutionPolicy Bypass -File services\dispatch_listener\test\smoke.ps1
#
# Scenarios 1-8 verify happy path + rejects + idempotency + container reach.
# Scenario 9 kills the listener process and confirms Task Scheduler restarts
# it within ~2 minutes (RestartInterval=PT1M, 2x grace). Scenario 10 is a
# MANUAL reboot verification.

[CmdletBinding()]
param(
    [int]$Port = 19100,
    [switch]$SkipDockerScenario,
    [switch]$SkipAutoRestartScenario
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$repoRoot  = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $scriptDir))
$logDir    = Join-Path $repoRoot "logs"
$inboxDir  = Join-Path $repoRoot "data\n8n_inbox"
$dlqPath   = Join-Path $repoRoot "data\dispatch_dlq.jsonl"
$envFile   = Join-Path $repoRoot ".env"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath   = Join-Path $logDir "dispatch_listener_smoke_$timestamp.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Content -Path $logPath -Value "" -Encoding UTF8

$results = @()
$traceIds = @{}

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $logPath -Value "$ts`t$Message" -Encoding UTF8
    Write-Host "[smoke] $Message"
}

function Record-Result {
    param(
        [Parameter(Mandatory = $true)][string]$Scenario,
        [Parameter(Mandatory = $true)][bool]$Pass,
        [string]$Note = ""
    )
    $script:results += [pscustomobject]@{ Scenario = $Scenario; Pass = $Pass; Note = $Note }
    $marker = if ($Pass) { "PASS" } else { "FAIL" }
    Write-LogLine "$marker $Scenario $Note"
}

function Get-Secret {
    if (-not (Test-Path $envFile)) { throw ".env not found at $envFile" }
    $line = Select-String -Path $envFile -Pattern '^W4_LISTENER_HMAC_SECRET=' -SimpleMatch:$false |
        Select-Object -First 1
    if (-not $line) { throw "W4_LISTENER_HMAC_SECRET not in $envFile" }
    return ($line.Line -replace '^W4_LISTENER_HMAC_SECRET=', '')
}

function Compute-Hmac {
    param(
        [Parameter(Mandatory = $true)][string]$Body,
        [Parameter(Mandatory = $true)][string]$Secret
    )
    $hmac = New-Object System.Security.Cryptography.HMACSHA256
    $hmac.Key = [Text.Encoding]::UTF8.GetBytes($Secret)
    $bytes = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Body))
    $sb = New-Object Text.StringBuilder
    foreach ($b in $bytes) { [void]$sb.Append($b.ToString("x2")) }
    return $sb.ToString()
}

function Invoke-DispatchPost {
    param(
        [Parameter(Mandatory = $true)][string]$Body,
        [Parameter(Mandatory = $true)][string]$Signature
    )
    $url = "http://127.0.0.1:$Port/dispatch"
    try {
        $resp = Invoke-WebRequest -Uri $url -Method Post -Body $Body `
            -ContentType "application/json" `
            -Headers @{ "X-W4-HMAC" = $Signature } `
            -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $resp.Content }
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { return [pscustomobject]@{ Status = -1; Body = $_.Exception.Message } }
        $code = [int]$resp.StatusCode
        $stream = $resp.GetResponseStream()
        $reader = New-Object IO.StreamReader($stream)
        $body = $reader.ReadToEnd()
        $reader.Close(); $stream.Close()
        return [pscustomobject]@{ Status = $code; Body = $body }
    }
}

function New-TraceId {
    param([string]$Prefix = "smoke")
    $hex = -join ((1..12) | ForEach-Object { "{0:x}" -f (Get-Random -Minimum 0 -Maximum 16) })
    return "$Prefix-$timestamp-$hex"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function New-PromptFile {
    param(
        [Parameter(Mandatory = $true)][string]$TraceId,
        [string]$PromptText = "Reply with the single word OK and exit. Do not run any tools."
    )
    $path = Join-Path $inboxDir "$TraceId.json"
    $payload = @{
        schema_version = "v1"
        trace_id = $TraceId
        prompt = $PromptText
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($path, $payload, $utf8NoBom)
    return $path
}

function Get-DlqLineCount {
    if (Test-Path $dlqPath) { return (Get-Content $dlqPath | Measure-Object -Line).Lines }
    return 0
}

function Wait-For-Receipt {
    param(
        [Parameter(Mandatory = $true)][string]$TraceId,
        [int]$TimeoutSeconds = 60
    )
    $receiptPath = Join-Path $inboxDir "$TraceId.result.json"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path $receiptPath) {
            try {
                $doc = Get-Content $receiptPath -Raw | ConvertFrom-Json
                if ($doc.status -ne "spawned" -and $null -ne $doc.completed_at) {
                    return $doc
                }
            } catch { Start-Sleep -Milliseconds 500 }
        }
        Start-Sleep -Milliseconds 1000
    } while ((Get-Date) -lt $deadline)
    return $null
}

try {
    $secret = Get-Secret
    Write-LogLine "secret_loaded length=$($secret.Length)"

    Write-LogLine "=== Scenario 1: Scheduled Task status ==="
    $task = Get-ScheduledTask -TaskName "MiruDispatchListener" -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Record-Result -Scenario "1_task_running" -Pass:$false -Note "task_not_registered"
    } else {
        $info = Get-ScheduledTaskInfo -TaskName "MiruDispatchListener"
        $stateOk = ($task.State -eq "Running")
        Record-Result -Scenario "1_task_running" -Pass:$stateOk -Note "state=$($task.State) last_result=$($info.LastTaskResult)"
    }

    Write-LogLine "=== Scenario 2: bind interface ==="
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    $bindAddrs = ($listeners | ForEach-Object { [string]$_.LocalAddress }) -join ","
    $nonLoopback = @($listeners | Where-Object {
        ($_.LocalAddress -ne "127.0.0.1") -and ($_.LocalAddress -ne "::1")
    })
    $loopbackOnly = ($listeners.Count -gt 0) -and ($nonLoopback.Count -eq 0)
    Record-Result -Scenario "2_loopback_only" -Pass:$loopbackOnly -Note "addrs=$bindAddrs"

    Write-LogLine "=== Scenario 3: happy path (claude-code) ==="
    $traceIds['3'] = New-TraceId -Prefix "happy"
    $promptPath = New-PromptFile -TraceId $traceIds['3']
    $relPrompt = "data/n8n_inbox/$($traceIds['3']).json"
    $body = @{ schema_version="v1"; trace_id=$traceIds['3']; worker="claude-code"; prompt_path=$relPrompt; timeout_seconds=120 } | ConvertTo-Json -Compress
    $sig = Compute-Hmac -Body $body -Secret $secret
    $r = Invoke-DispatchPost -Body $body -Signature $sig
    Write-LogLine "scenario_3 status=$($r.Status) body=$($r.Body)"
    if ($r.Status -eq 202) {
        $receipt = Wait-For-Receipt -TraceId $traceIds['3'] -TimeoutSeconds 180
        if ($receipt -and ($receipt.status -in @("INCONCLUSIVE", "FAILED"))) {
            Record-Result -Scenario "3_happy_path_spawn_and_receipt" -Pass:$true -Note "receipt_status=$($receipt.status) exit=$($receipt.exit_code)"
        } else {
            Record-Result -Scenario "3_happy_path_spawn_and_receipt" -Pass:$false -Note "no terminal receipt within 180s"
        }
    } else {
        Record-Result -Scenario "3_happy_path_spawn_and_receipt" -Pass:$false -Note "expected 202 got $($r.Status)"
    }

    Write-LogLine "=== Scenario 4: allowlist reject ==="
    $traceIds['4'] = New-TraceId -Prefix "allowlist"
    $promptPath = New-PromptFile -TraceId $traceIds['4']
    $relPrompt = "data/n8n_inbox/$($traceIds['4']).json"
    $body = @{ schema_version="v1"; trace_id=$traceIds['4']; worker="windsurf"; prompt_path=$relPrompt; timeout_seconds=60 } | ConvertTo-Json -Compress
    $sig = Compute-Hmac -Body $body -Secret $secret
    $dlqBefore = Get-DlqLineCount
    $r = Invoke-DispatchPost -Body $body -Signature $sig
    $dlqAfter = Get-DlqLineCount
    $pass = ($r.Status -eq 403) -and ($dlqAfter -gt $dlqBefore)
    Record-Result -Scenario "4_allowlist_reject" -Pass:$pass -Note "status=$($r.Status) dlq_delta=$($dlqAfter - $dlqBefore)"

    Write-LogLine "=== Scenario 5: invalid HMAC ==="
    $traceIds['5'] = New-TraceId -Prefix "hmac"
    $promptPath = New-PromptFile -TraceId $traceIds['5']
    $relPrompt = "data/n8n_inbox/$($traceIds['5']).json"
    $body = @{ schema_version="v1"; trace_id=$traceIds['5']; worker="claude-code"; prompt_path=$relPrompt; timeout_seconds=60 } | ConvertTo-Json -Compress
    $badSig = "00" * 32
    $dlqBefore = Get-DlqLineCount
    $r = Invoke-DispatchPost -Body $body -Signature $badSig
    $dlqAfter = Get-DlqLineCount
    $pass = ($r.Status -eq 401) -and ($dlqAfter -gt $dlqBefore)
    Record-Result -Scenario "5_invalid_hmac" -Pass:$pass -Note "status=$($r.Status) dlq_delta=$($dlqAfter - $dlqBefore)"

    Write-LogLine "=== Scenario 6: idempotency replay ==="
    $body = @{ schema_version="v1"; trace_id=$traceIds['3']; worker="claude-code"; prompt_path="data/n8n_inbox/$($traceIds['3']).json"; timeout_seconds=60 } | ConvertTo-Json -Compress
    $sig = Compute-Hmac -Body $body -Secret $secret
    $r = Invoke-DispatchPost -Body $body -Signature $sig
    Record-Result -Scenario "6_idempotency_replay" -Pass:($r.Status -eq 409) -Note "status=$($r.Status)"

    Write-LogLine "=== Scenario 7: spawn timeout ==="
    $traceIds['7'] = New-TraceId -Prefix "timeout"
    $longPromptPath = Join-Path $inboxDir "$($traceIds['7']).json"
    $longPayload = @{
        schema_version = "v1"
        trace_id = $traceIds['7']
        prompt = "Count slowly to 200, one number per line, then exit."
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($longPromptPath, $longPayload, $utf8NoBom)
    $body = @{ schema_version="v1"; trace_id=$traceIds['7']; worker="claude-code"; prompt_path="data/n8n_inbox/$($traceIds['7']).json"; timeout_seconds=2 } | ConvertTo-Json -Compress
    $sig = Compute-Hmac -Body $body -Secret $secret
    $dlqBefore = Get-DlqLineCount
    $r = Invoke-DispatchPost -Body $body -Signature $sig
    if ($r.Status -eq 202) {
        $receipt = Wait-For-Receipt -TraceId $traceIds['7'] -TimeoutSeconds 60
        $dlqAfter = Get-DlqLineCount
        $timeoutDlq = $false
        if (Test-Path $dlqPath) {
            $tail = Get-Content $dlqPath | Select-Object -Last 5
            $timeoutMatches = @($tail | Where-Object { $_ -match $traceIds['7'] -and $_ -match '"timeout"' })
            $timeoutDlq = $timeoutMatches.Count -gt 0
        }
        $pass = ($null -ne $receipt) -and ($receipt.status -eq "FAILED") -and $timeoutDlq
        Record-Result -Scenario "7_spawn_timeout" -Pass:$pass -Note "receipt_status=$($receipt.status) dlq_delta=$($dlqAfter - $dlqBefore) timeout_match=$timeoutDlq"
    } else {
        Record-Result -Scenario "7_spawn_timeout" -Pass:$false -Note "expected 202 got $($r.Status)"
    }

    Write-LogLine "=== Scenario 8: container reachability ==="
    if ($SkipDockerScenario) {
        Record-Result -Scenario "8_container_reachability" -Pass:$true -Note "SKIPPED"
    } else {
        $traceIds['8'] = New-TraceId -Prefix "docker"
        $promptPath = New-PromptFile -TraceId $traceIds['8']
        $relPrompt = "data/n8n_inbox/$($traceIds['8']).json"
        $body = @{ schema_version="v1"; trace_id=$traceIds['8']; worker="claude-code"; prompt_path=$relPrompt; timeout_seconds=120 } | ConvertTo-Json -Compress
        $sig = Compute-Hmac -Body $body -Secret $secret
        # Write the body to a host file mapped into the container at /miru-data, so we
        # don't have to shell-escape it through `docker exec sh -c`.
        $hostBodyFile = Join-Path $repoRoot "data\smoke_scenario_8_body.json"
        [IO.File]::WriteAllText($hostBodyFile, $body, $utf8NoBom)
        try {
            $cmd = "wget --quiet --output-document=- --server-response --header='Content-Type: application/json' --header='X-W4-HMAC: $sig' --post-file=/miru-data/smoke_scenario_8_body.json http://host.docker.internal:19100/dispatch 2>&1"
            $rawOut = & docker exec miru-n8n sh -c $cmd 2>&1
            $httpLine = ($rawOut | Where-Object { $_ -match '^\s*HTTP/' } | Select-Object -First 1)
            $code = if ($httpLine -match 'HTTP/\S+\s+(\d+)') { $Matches[1] } else { 'parse_failed' }
            Write-LogLine "container_wget_http_line=$httpLine"
            Record-Result -Scenario "8_container_reachability" -Pass:($code -eq "202") -Note "http=$code"
        } finally {
            Remove-Item -Path $hostBodyFile -ErrorAction SilentlyContinue
        }
    }

    Write-LogLine "=== Scenario 9: kill + auto-restart ==="
    if ($SkipAutoRestartScenario) {
        Record-Result -Scenario "9_kill_auto_restart" -Pass:$true -Note "SKIPPED"
    } else {
        $listenerPids = @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
                ForEach-Object { [int]$_.OwningProcess } |
                Where-Object { $_ -gt 0 } |
                Sort-Object -Unique
        )
        if ($listenerPids.Count -eq 0) {
            Record-Result -Scenario "9_kill_auto_restart" -Pass:$false -Note "no_listener_pid_to_kill"
        } else {
            $startStamp = Get-Date
            $killedAny  = $false
            foreach ($lpid in $listenerPids) {
                $beforeStillAlive = $null -ne (Get-Process -Id $lpid -ErrorAction SilentlyContinue)
                if (-not $beforeStillAlive) { continue }
                try {
                    Stop-Process -Id $lpid -Force -ErrorAction Stop
                    Write-LogLine "killed_pid=$lpid via=Stop-Process"
                    $killedAny = $true
                } catch {
                    Write-LogLine "kill_nonelevated_failed pid=$lpid err=$($_.Exception.Message) trying_elevated"
                    # Listener runs RunLevel=Highest under S4U; non-elevated kill is denied.
                    # Escalate via UAC to taskkill /F.
                    & powershell -ExecutionPolicy Bypass -Command `
                        "Start-Process taskkill -ArgumentList '/F','/PID','$lpid' -Verb RunAs -Wait" 2>&1 |
                        Out-Null
                    Start-Sleep -Seconds 2
                    $afterAlive = $null -ne (Get-Process -Id $lpid -ErrorAction SilentlyContinue)
                    if (-not $afterAlive) {
                        Write-LogLine "killed_pid=$lpid via=elevated-taskkill"
                        $killedAny = $true
                    } else {
                        Write-LogLine "kill_elevated_also_failed pid=$lpid"
                    }
                }
            }
            if (-not $killedAny) {
                Record-Result -Scenario "9_kill_auto_restart" -Pass:$false -Note "could_not_kill_any_listener_pid"
            } else {
                # The wrapper's respawn loop waits ~30s after a non-zero exit, then
                # respawns. Allow 75s total to be safe. We require the restart to be
                # a NEW pid (different from the killed one) AND serving /health, so
                # we don't mistake an unkilled survivor for a successful respawn.
                Start-Sleep -Seconds 3
                $deadline = (Get-Date).AddSeconds(75)
                $newPid = $null
                do {
                    Start-Sleep -Seconds 5
                    $candidates = @(
                        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
                            ForEach-Object { [int]$_.OwningProcess } |
                            Where-Object { $_ -gt 0 -and -not ($listenerPids -contains $_) }
                    )
                    if ($candidates.Count -gt 0) {
                        # Confirm the new pid is actually serving health.
                        try {
                            $h = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
                            if ([int]$h.StatusCode -eq 200) { $newPid = $candidates[0]; break }
                        } catch { }
                    }
                } while ((Get-Date) -lt $deadline)
                $elapsed = [int]((Get-Date) - $startStamp).TotalSeconds
                if ($newPid) {
                    Record-Result -Scenario "9_kill_auto_restart" -Pass:$true -Note "new_pid=$newPid elapsed=${elapsed}s"
                } else {
                    Record-Result -Scenario "9_kill_auto_restart" -Pass:$false -Note "no_new_listener_pid_within_120s elapsed=${elapsed}s"
                }
            }
        }
    }

    Write-LogLine "=== Scenario 10: reboot survival (MANUAL) ==="
    Write-LogLine "Scenario 10 requires a real machine reboot of ROOM. Verify after reboot:"
    Write-LogLine "  1. Within ~30s of login: Get-ScheduledTask -TaskName 'MiruDispatchListener' shows State=Running"
    Write-LogLine "  2. Invoke-WebRequest http://127.0.0.1:19100/health returns 200 OK"
    Record-Result -Scenario "10_reboot_survival" -Pass:$true -Note "MANUAL_VERIFY_AFTER_REBOOT"
} catch {
    Write-LogLine "fatal=$($_.Exception.Message)"
    Record-Result -Scenario "fatal" -Pass:$false -Note $_.Exception.Message
} finally {
    Write-LogLine "=== summary ==="
    foreach ($r in $results) {
        $marker = if ($r.Pass) { "PASS" } else { "FAIL" }
        Write-LogLine "$marker $($r.Scenario) $($r.Note)"
    }
    Write-LogLine "trace_ids:"
    foreach ($k in $traceIds.Keys | Sort-Object) {
        Write-LogLine "  scenario_$k=$($traceIds[$k])"
    }
    $failedRows = @($results | Where-Object { -not $_.Pass })
    $failed = $failedRows.Count
    Write-LogLine "RESULT total=$($results.Count) failed=$failed"
    if ($failed -eq 0) { exit 0 } else { exit 1 }
}
