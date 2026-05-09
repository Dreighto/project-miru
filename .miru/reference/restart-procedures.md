# Reference — Restart Procedures

```text
Reference: restart-procedures
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: restarting a service.
Last reviewed: 2026-05-09 (PRO-336: shell:startup primary path added)
```

## Restart Rules

- CC restarts services **autonomously** when needed. Do not ping the operator for routine restarts (set 2026-05-07).
- Never use `nssm restart` directly.
- Never create alternate restart scripts — extend the existing ones.

## Service restart commands

| Service           | Port  | Preferred command                                                                                                                                                                                                                              |
| ----------------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dispatch Listener | 19100 | **Primary:** `Start-ScheduledTask -TaskName MiruRestartDispatcher` — works when listener is in the interactive session (shell:startup path, PRO-336). **Recovery (Session 0):** see "Dispatch Listener — Session 0 recovery (FALLBACK)" below. |
| PM Dashboard      | 18080 | `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1` (or `Start-ScheduledTask -TaskName MiruRestartPM`)                                                                                                                           |
| Miru AI           | 18765 | `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1` (or `Start-ScheduledTask -TaskName MiruRestartMiruAI`)                                                                                                                  |
| MCP Gateway       | 18766 | `Start-ScheduledTask -TaskName MiruRestartMcpGateway`                                                                                                                                                                                          |

## Dispatch Listener — boot path (PRIMARY, PRO-336)

**Install once** (no elevation required):

```powershell
powershell -ExecutionPolicy Bypass -File windows\install_dispatch_listener_startup_shortcut.ps1
```

This places `MiruDispatchListener.lnk` in `shell:startup` (per-user `%APPDATA%\...\Startup`). On every subsequent logon the listener spawns in the operator's interactive session (Session 1+), where non-elevated `Stop-Process` and `Start-ScheduledTask` work without UAC.

The installer is idempotent — safe to re-run after a repo move or OS reinstall.

**Verify after logoff/logon:**

```powershell
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
"PID=$($l.OwningProcess) Session=$((Get-Process -Id $l.OwningProcess).SessionId)"
# Session must be 1 or higher
```

---

## Dispatch Listener — Session 0 recovery (FALLBACK, pre-PRO-336 or shortcut not installed)

**Problem:** `MiruDispatchListener` scheduled task (S4U / AtStartup logon) boots the listener into Windows **Session 0** (non-interactive service session). Non-elevated worker shells cannot terminate cross-session processes — `Stop-Process` and `MiruRestartDispatcher` both fail with `Access is denied`.

**Diagnosis:**

```powershell
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
(Get-Process -Id $l.OwningProcess).SessionId
# 0 = Session 0 (problem); 1 or higher = healthy
```

**One-time operator recovery** (elevated PowerShell — Run as Administrator):

```powershell
# Kill the wrapper PowerShell tree (takes down cmd + node together)
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 | Select-Object -First 1
$nodepid = [int]$l.OwningProcess
$cmdpid = (Get-CimInstance Win32_Process -Filter "ProcessId=$nodepid").ParentProcessId
$pspid  = (Get-CimInstance Win32_Process -Filter "ProcessId=$cmdpid").ParentProcessId
taskkill /F /T /PID $pspid
```

**Then CC relaunches from its interactive shell** — Node inherits Session 1+:

```powershell
Start-Process powershell.exe `
    -ArgumentList '-NoLogo','-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File','D:\dev\miru\windows\start_dispatch_listener.ps1' `
    -WindowStyle Hidden
```

**Permanent fix:** run the installer above (`install_dispatch_listener_startup_shortcut.ps1`) and reboot/logon. The Session 0 self-check in `start_dispatch_listener.ps1` will log a WARN and exit 1 if the scheduled task fires in Session 0 again, making future regressions immediately visible in the log.

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
