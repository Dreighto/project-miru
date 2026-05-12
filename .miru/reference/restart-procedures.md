# Reference — Restart Procedures

```text
Reference: restart-procedures
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: restarting a service.
Last reviewed: 2026-05-11 (DelegationTerminal Win11 24H2 mitigation added)
```

## Restart Rules

- CC restarts services **autonomously** when needed. Do not ping the operator for routine restarts (set 2026-05-07).
- Never use `nssm restart` directly.
- Never create alternate restart scripts — extend the existing ones.

## Service restart commands

| Service           | Port  | Preferred command                                                                                                                                                                                                                                                                     |
| ----------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dispatch Listener | 19100 | **Primary:** `Start-ScheduledTask -TaskName MiruRestartDispatcher` — works when listener is in the interactive session (shell:startup path, PRO-336). **Recovery (Session 0):** see "Dispatch Listener — Session 0 recovery (FALLBACK, pre-PRO-336 or shortcut not installed)" below. |
| PM Dashboard      | 18080 | `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1` (or `Start-ScheduledTask -TaskName MiruRestartPM`)                                                                                                                                                                  |
| Miru AI           | 18765 | `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1` (or `Start-ScheduledTask -TaskName MiruRestartMiruAI`)                                                                                                                                                         |
| MCP Gateway       | 18766 | `Start-ScheduledTask -TaskName MiruRestartMcpGateway`                                                                                                                                                                                                                                 |

## Dispatch Listener — boot path (PRIMARY, PRO-336)

**Install once** (no elevation required):

```powershell
powershell -ExecutionPolicy Bypass -File windows\install_dispatch_listener_startup_shortcut.ps1
```

This places `MiruDispatchListener.lnk` in `shell:startup` (per-user `%APPDATA%\...\Startup`). On every subsequent logon the listener spawns in the operator's interactive session (Session 1+), where non-elevated `Stop-Process` and `Start-ScheduledTask` work without UAC.

The installer is idempotent — safe to re-run after a repo move or OS reinstall.

**Verify after logoff/logon:**

```powershell
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $l) { Write-Host "ERROR: No listener on port 19100 — service may not be running"; exit 1 }
"PID=$($l.OwningProcess) Session=$((Get-Process -Id $l.OwningProcess).SessionId)"
# Session must be 1 or higher
```

---

## Dispatch Listener — Session 0 recovery (FALLBACK, pre-PRO-336 or shortcut not installed)

**Problem:** `MiruDispatchListener` scheduled task (S4U / AtStartup logon) boots the listener into Windows **Session 0** (non-interactive service session). Non-elevated worker shells cannot terminate cross-session processes — `Stop-Process` and `MiruRestartDispatcher` both fail with `Access is denied`.

**Diagnosis:**

```powershell
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $l) { Write-Host "ERROR: No listener on port 19100 — service may not be running"; exit 1 }
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
    -ArgumentList '-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File','D:\dev\miru\windows\start_dispatch_listener.ps1' `
    -WindowStyle Hidden
```

**Permanent fix:** run the installer above (`install_dispatch_listener_startup_shortcut.ps1`) and reboot or log on. The Session 0 self-check in `start_dispatch_listener.ps1` will log a WARN and exit 1 if the scheduled task fires in Session 0 again, making future regressions immediately visible in the log.

## After restart — sanity checks

```powershell
# Listener health
try {
    (Invoke-WebRequest -Uri 'http://127.0.0.1:19100/health' -UseBasicParsing -TimeoutSec 5).Content
    # Should return: {"status":"ok","listener":"dispatch_listener","port":19100}
} catch {
    Write-Host "ERROR: Health check failed — $($_.Exception.Message)"
}

# Session check
$l = Get-NetTCPConnection -State Listen -LocalPort 19100 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $l) { Write-Host "ERROR: No listener on port 19100"; exit 1 }
"PID=$($l.OwningProcess) Session=$((Get-Process -Id $l.OwningProcess).SessionId)"
# Session must be != 0 for CC to manage autonomously
```

---

## Windows 11 24H2 — console-flash mitigation (DelegationTerminal, set 2026-05-11)

**Symptom:** brief console windows flash on screen every few seconds during normal worker activity, interrupting operator keystrokes. Two sources:

1. Worker `Bash --run_in_background` loops spawning short-lived `sleep.exe` / `cmd.exe` children (each one allocates a new console).
2. Scheduled tasks whose action launches `powershell.exe` directly with `-WindowStyle Hidden` — the hide flag races the window creation on Win11 24H2 and a sub-second flash leaks through on every fire and every restart-on-failure.

**Operator-machine fix (registry, one-time, no reboot):**

Set the Windows Terminal GUIDs as the default `DelegationConsole` and `DelegationTerminal` so every console allocation goes through Windows Terminal silently (Terminal handles `CreateNoWindow` correctly even when older code paths don't).

```powershell
# Run from any PowerShell on the operator's machine
$wtGuid = '{2EACA947-7F5F-4CFA-BA87-8F7FBEEFBE69}'  # Windows Terminal
New-ItemProperty -Path 'HKCU:\Console\%%Startup' -Name 'DelegationConsole'  -Value $wtGuid -PropertyType String -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Console\%%Startup' -Name 'DelegationTerminal' -Value $wtGuid -PropertyType String -Force | Out-Null
```

This is defense-in-depth and is **not a substitute** for fixing the two root causes:

- **Worker side:** kill Monitor / background Bash loops before declaring a terminal state. See the "Kill all background Monitor loops" lesson in `.miru/overlays/adopted-lessons.md` (2026-05-11).
- **Scheduled-task side:** wrap every task action in a `wscript.exe` + 9-line VBS (`WshShell.Run "...", 0, False` for SW_HIDE at CreateProcess). Canonical example: `D:\dev\LogueOS-Orchestrator\windows\tasks\run_dispatch_listener.vbs`. Followup ticket LOS-33 tracks the systematic wrap of the 5 remaining flash-risk installers.

**Verify the registry fix is live:**

```powershell
Get-ItemProperty -Path 'HKCU:\Console\%%Startup' | Select-Object DelegationConsole, DelegationTerminal
# Both should equal {2EACA947-7F5F-4CFA-BA87-8F7FBEEFBE69}
```

If the operator ever sees flashes after this is set, the source is almost certainly worker-side (Monitor loops) — go to the lesson above.
