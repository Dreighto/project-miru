# Authoritative runtime constants and helpers for the three Miru runtimes.
# Port authority: 18765 = Miru AI/Dev (worktree), 18080 = Project Miru worktree, 8080 = main stable.
# Do NOT use 8765.
# Dot-source op_miru_common.ps1 first (callers must do so, or source this after common).
Set-StrictMode -Version Latest

# Canonical ports (single source of truth)
$script:MiruAiDevPort           = 18765
$script:ProjectMiruWorktreePort = 18080
$script:MainStablePort          = 8080

function Get-MiruRuntimePorts {
    return [pscustomobject]@{
        MiruAiDevPort           = $script:MiruAiDevPort
        ProjectMiruWorktreePort = $script:ProjectMiruWorktreePort
        MainStablePort          = $script:MainStablePort
    }
}

function Get-MiruProcessOnPort {
    param([Parameter(Mandatory = $true)][int]$Port)
    if (Get-Command Get-OpMiruListeningEntry -ErrorAction SilentlyContinue) {
        return Get-OpMiruListeningEntry -Port $Port
    }
    foreach ($line in (netstat -ano -p tcp)) {
        if ($line -match "^\s*TCP\s+(.+):$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [pscustomobject]@{ LocalAddress = $matches[1]; Port = $Port; Pid = [int]$matches[2] }
        }
    }
    return $null
}

function Test-MiruAiDevHealthy {
    param(
        [int]$Port = $script:MiruAiDevPort,
        [int]$TimeoutSeconds = 8
    )
    $url = "http://127.0.0.1:$Port/api/health"
    if (-not (Get-Command Test-OpMiruHttp -ErrorAction SilentlyContinue)) { return $false }
    $r = Test-OpMiruHttp -Url $url -TimeoutSeconds $TimeoutSeconds
    if (-not $r.Ok) { return $false }
    $content = [string]$r.Content
    if ([string]::IsNullOrWhiteSpace($content)) { return $false }
    try {
        $payload = $content | ConvertFrom-Json
        return ([string]$payload.status).Trim().ToLowerInvariant() -eq "ok"
    }
    catch {
        return $content -match '"status"\s*:\s*"ok"'
    }
}

function Test-MiruAiDevPageReachable {
    param(
        [int]$Port = $script:MiruAiDevPort,
        [int]$TimeoutSeconds = 5
    )
    $url = "http://127.0.0.1:$Port/dev"
    if (-not (Get-Command Test-OpMiruHttp -ErrorAction SilentlyContinue)) { return $false }
    $r = Test-OpMiruHttp -Url $url -TimeoutSeconds $TimeoutSeconds
    return $r.Ok
}

function Test-DashboardHealthy {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 8
    )
    $url = "http://127.0.0.1:$Port/"
    if (-not (Get-Command Test-OpMiruHttp -ErrorAction SilentlyContinue)) { return $false }
    $r = Test-OpMiruHttp -Url $url -TimeoutSeconds $TimeoutSeconds -MustContain "Miru"
    return $r.Ok
}

function Test-MiruProcessIsMiruAiDev {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [int]$ExpectedPort = $script:MiruAiDevPort
    )
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $cmd = [string]$proc.CommandLine
        $portMatch = $cmd -match "(^|[^\d])$ExpectedPort($|[^\d])"
        $scriptMatch = $cmd -match "tools[/\\]miru_ai_server\.py" -or $cmd -match "(^|\\s)-m\\s+miru_ai\.server(\\s|$)"
        return $scriptMatch -and $portMatch
    }
    catch { return $false }
}

function Get-MiruAiDevPidFilePath {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    return Join-Path $RepoRoot "data\startup-logs\miru_ai_worktree.pid"
}

function Get-MiruAiDevPidRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    $path = Get-MiruAiDevPidFilePath -RepoRoot $RepoRoot
    if (-not (Test-Path $path)) { return $null }
    try {
        $raw = Get-Content -Path $path -Raw -Encoding UTF8
        $record = $raw | ConvertFrom-Json
        if ($null -eq $record -or $null -eq $record.pid) { return $null }
        return $record
    }
    catch {
        return $null
    }
}

function Set-MiruAiDevPidRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][int]$MiruPid,
        [int]$Port = $script:MiruAiDevPort
    )
    $path = Get-MiruAiDevPidFilePath -RepoRoot $RepoRoot
    $dir = Split-Path -Parent $path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [pscustomobject]@{
        pid = $MiruPid
        miru_ai_port = $Port
        started_at = (Get-Date).ToString("s")
        repo_root = $RepoRoot
    } | ConvertTo-Json | Set-Content -Path $path -Encoding UTF8
}

function Clear-MiruAiDevPidRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    $path = Get-MiruAiDevPidFilePath -RepoRoot $RepoRoot
    if (Test-Path $path) {
        Remove-Item -Force -Path $path -ErrorAction SilentlyContinue
    }
}

function Repair-MiruAiDevPidState {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [int]$Port = $script:MiruAiDevPort
    )
    $record = Get-MiruAiDevPidRecord -RepoRoot $RepoRoot
    $entry = Get-MiruProcessOnPort -Port $Port
    if ($entry -and (Test-MiruProcessIsMiruAiDev -ProcessId $entry.Pid -RepoRoot $RepoRoot -ExpectedPort $Port)) {
        if ($null -eq $record -or [int]$record.pid -ne [int]$entry.Pid) {
            Set-MiruAiDevPidRecord -RepoRoot $RepoRoot -MiruPid ([int]$entry.Pid) -Port $Port
            return [pscustomobject]@{ State = "Refreshed"; Pid = [int]$entry.Pid }
        }
        return [pscustomobject]@{ State = "Current"; Pid = [int]$entry.Pid }
    }

    if ($null -ne $record) {
        $recordPid = 0
        try { $recordPid = [int]$record.pid } catch { $recordPid = 0 }
        $alive = $false
        if ($recordPid -gt 0) {
            try {
                $alive = $null -ne (Get-Process -Id $recordPid -ErrorAction SilentlyContinue)
            }
            catch {
                $alive = $false
            }
        }
        $matches = $alive -and (Test-MiruProcessIsMiruAiDev -ProcessId $recordPid -RepoRoot $RepoRoot -ExpectedPort $Port)
        if (-not $matches) {
            Clear-MiruAiDevPidRecord -RepoRoot $RepoRoot
            return [pscustomobject]@{ State = "ClearedStale"; Pid = $recordPid }
        }
    }

    return [pscustomobject]@{ State = "Missing"; Pid = $null }
}

function Test-MiruProcessIsDashboard {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $cmd = [string]$proc.CommandLine
        return $cmd -match "dashboard[/\\]app\.py" -or $cmd -match "dashboard\\app\.py"
    }
    catch { return $false }
}

function Stop-MiruProcessSafely {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$Label = "Process"
    )
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return $true }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
        return $true
    }
    catch { return $false }
}

# Returns: SkipStart (already correct), ShouldStart (port free or wrong process stopped), or Error (wrong process and could not stop)
function Resolve-MiruPortBeforeStart {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [scriptblock]$TestHealthy,
        [scriptblock]$TestProcessCorrect
    )
    $entry = Get-MiruProcessOnPort -Port $Port
    if (-not $entry) {
        return [pscustomobject]@{ Action = "ShouldStart"; Message = "Port $Port is free." }
    }
    $correct = & $TestProcessCorrect -ProcessId $entry.Pid -RepoRoot $RepoRoot
    if ($correct) {
        if ($TestHealthy -and ((& $TestHealthy))) {
            return [pscustomobject]@{ Action = "SkipStart"; Message = "$ServiceName already running and healthy on port $Port (PID $($entry.Pid))."; Pid = $entry.Pid }
        }
        return [pscustomobject]@{ Action = "ShouldStart"; Message = "$ServiceName process on $Port is not healthy; will restart."; Pid = $entry.Pid }
    }
    $stopped = Stop-MiruProcessSafely -ProcessId $entry.Pid -Label $ServiceName
    if (-not $stopped) {
        return [pscustomobject]@{ Action = "Error"; Message = "Port $Port is in use by PID $($entry.Pid) (not $ServiceName). Stop it manually." }
    }
    return [pscustomobject]@{ Action = "ShouldStart"; Message = "Stopped wrong process on $Port; starting $ServiceName." }
}
