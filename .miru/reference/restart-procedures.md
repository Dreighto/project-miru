# Reference — Restart Procedures

```text
Reference: restart-procedures
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: restarting a service.
Last reviewed: 2026-05-09
```

## Restart Rules

- CC restarts services **autonomously** when needed. Do not ping the operator for routine restarts (set 2026-05-07).
- Never use `nssm restart` directly.
- Never create alternate restart scripts — extend the existing ones.

## Service restart commands

| Service           | Port  | Preferred command                                                                                                                                                                |
| ----------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dispatch Listener | 19100 | See "Dispatch Listener — boot-path caveat" below. Restart task: `Start-ScheduledTask -TaskName MiruRestartDispatcher` (only works when listener is in your interactive session). |
| PM Dashboard      | 18080 | `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1` (or `Start-ScheduledTask -TaskName MiruRestartPM`)                                                             |
| Miru AI           | 18765 | `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1` (or `Start-ScheduledTask -TaskName MiruRestartMiruAI`)                                                    |
| MCP Gateway       | 18766 | `Start-ScheduledTask -TaskName MiruRestartMcpGateway`                                                                                                                            |

## Dispatch Listener — boot-path caveat (added 2026-05-09)

**Problem:** at boot, `MiruDispatchListener` (S4U logon) launches the Node listener into Windows **Session 0** (services session). Non-elevated worker shells (CC, Cursor IDE, normal PowerShell) cannot terminate Session 0 processes — Windows requires `SeDebugPrivilege` (admin) for cross-session kills. Result: `MiruRestartDispatcher` task fails with `Access is denied` and CC cannot autonomously restart.

**Diagnosis check:**

```powershell
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
(Get-Process -Id $l.OwningProcess).SessionId
# 0 = problem; 1 or higher = healthy
```

**One-time recovery** (operator runs from elevated PowerShell — Run as Administrator):

```powershell
# Walk the tree and kill the wrapper PowerShell — that takes down cmd + node together
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
$nodepid = [int]$l.OwningProcess
$cmdpid = (Get-CimInstance Win32_Process -Filter "ProcessId=$nodepid").ParentProcessId
$pspid  = (Get-CimInstance Win32_Process -Filter "ProcessId=$cmdpid").ParentProcessId
taskkill /F /T /PID $pspid
```

**Then CC relaunches from its own (Session 1+) shell** — the new wrapper inherits CC's session, and Node lands in the operator's interactive session where CC can kill/restart it without admin:

```powershell
Start-Process powershell.exe `
    -ArgumentList '-NoLogo','-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File','D:\dev\miru\windows\start_dispatch_listener.ps1' `
    -WindowStyle Hidden
```

**Permanent fix:** tracked in **PRO-336** — boot the listener via a `shell:startup` shortcut instead of Task Scheduler so it always lands in the operator's interactive session. Until that ships, every reboot recreates the Session 0 problem and requires the one-time recovery above.

## After restart — sanity checks

```powershell
# Listener health
(Invoke-WebRequest -Uri 'http://127.0.0.1:19100/health' -UseBasicParsing -TimeoutSec 5).Content
# Should return: {"status":"ok","listener":"dispatch_listener","port":19100}

# Session check
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
"PID=$($l.OwningProcess) Session=$((Get-Process -Id $l.OwningProcess).SessionId)"
# Session must be != 0 for CC to manage autonomously
```
