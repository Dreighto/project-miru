Write-Host "=== PORT 18080 ==="
$l18 = Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction SilentlyContinue
if ($l18) {
    foreach ($entry in $l18) {
        $ownerPid = [int]$entry.OwningProcess
        Write-Host "  Listener: $($entry.LocalAddress):18080  PID=$ownerPid"
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Process: $($proc.ProcessName)  SessionId=$($proc.SessionId)  Path=$($proc.Path)"
        }
        try {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction Stop
            Write-Host "  CommandLine: $($cim.CommandLine)"
        } catch {
            Write-Host "  CommandLine: <unreadable - likely SYSTEM owned>"
        }
    }
} else {
    Write-Host "  No listener"
}

Write-Host ""
Write-Host "=== PORT 28080 ==="
$l28 = Get-NetTCPConnection -State Listen -LocalPort 28080 -ErrorAction SilentlyContinue
if ($l28) {
    foreach ($entry in $l28) {
        $ownerPid = [int]$entry.OwningProcess
        Write-Host "  Listener: $($entry.LocalAddress):28080  PID=$ownerPid"
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Process: $($proc.ProcessName)  SessionId=$($proc.SessionId)  Path=$($proc.Path)"
        }
        try {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction Stop
            Write-Host "  CommandLine: $($cim.CommandLine)"
        } catch {
            Write-Host "  CommandLine: <unreadable - likely SYSTEM owned>"
        }
    }
} else {
    Write-Host "  No listener"
}

Write-Host ""
Write-Host "=== PID FILE ==="
$pidFile = "D:\dev\tcg-watcher-worktree\data\startup-logs\dashboard_18080.pid"
if (Test-Path $pidFile) {
    Get-Content $pidFile
} else {
    Write-Host "  No PID file"
}

Write-Host ""
Write-Host "=== SCHTASK PROBE ==="
$taskFile = Join-Path $env:SystemRoot "System32\Tasks\RestartMiruDashboard"
Write-Host "  Task file exists: $(Test-Path -LiteralPath $taskFile)"
$queryOut = schtasks.exe /Query /TN RestartMiruDashboard 2>&1 | Out-String
Write-Host "  Query exit: $LASTEXITCODE"

Write-Host ""
Write-Host "=== USER CONTEXT ==="
Write-Host "  User: $(C:\Windows\System32\whoami.exe)"
$isElevated = ([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "  Elevated: $isElevated"

Write-Host ""
Write-Host "=== HTTP PROBES ==="
try {
    $req18 = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:18080/")
    $req18.Proxy = $null
    $req18.Timeout = 5000
    $resp18 = $req18.GetResponse()
    Write-Host "  18080: HTTP $([int]$resp18.StatusCode)"
    $resp18.Close()
} catch {
    Write-Host "  18080: UNREACHABLE ($($_.Exception.InnerException.Message))"
}
try {
    $req28 = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:28080/")
    $req28.Proxy = $null
    $req28.Timeout = 5000
    $resp28 = $req28.GetResponse()
    Write-Host "  28080: HTTP $([int]$resp28.StatusCode)"
    $resp28.Close()
} catch {
    Write-Host "  28080: UNREACHABLE ($($_.Exception.InnerException.Message))"
}
