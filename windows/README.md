# OP Miru / Miru AI – Windows Startup
 
> Legacy note for this worktree: this README documents the legacy/main-style startup path (`8080`/`8765`).
> For canonical Project Miru worktree runtime authority (`18080`/`18765`), use:
> - `windows/start_op_miru_worktree.ps1`
> - `windows/README.worktree.md`
> - `docs/RUNTIME_AUTHORITY_MATRIX.md`
>
> Keep this file for compatibility and historical startup context only.

# Legacy watchdog path removed. start_op_miru.ps1 is retired. Do not invoke.

Brief documentation for the automatic startup flow that runs at Windows boot.

## Overview

The startup flow described below was the **historical** `8080` / `8765` stack. The scheduled task should invoke `op_miru_bootstrap.cmd` with **no arguments** (Miru AI Dev on `18765` only) or `worktree` for the full worktree stack—not `start_op_miru.ps1 -Watchdog` (removed).

1. **Dashboard** – Docker-backed OP Miru services on port 8080 *(legacy stack; retired path)*  
2. **Miru AI** – Flask app (Dev Monitor, Ask, etc.) on port 8765 *(legacy stack; retired path)*  

~~A **watchdog** via `start_op_miru.ps1 -Watchdog` monitored the Miru AI process.~~ **Removed—do not use.**

## How it works

1. A Windows Scheduled Task runs at startup.
2. The task executes `op_miru_bootstrap.cmd` (no arg → `start_miru_ai_dev.ps1`; `worktree` → `start_op_miru_worktree.ps1`).
3. ~~`start_op_miru.ps1 -Watchdog`~~ **Retired.** Do not invoke.

## Install

From an **elevated** PowerShell window:

```powershell
cd path\to\tcg-watcher
.\windows\install_op_miru_startup.ps1
```

The default task name is `OP Miru Startup`. Use `-TaskName "Custom Name"` to override.

## Test

~~`.\windows\start_op_miru.ps1`~~ and ~~`.\windows\start_op_miru.ps1 -Watchdog`~~ **Retired—do not invoke.**

Validate that both dashboard and Miru AI are reachable (legacy test script, if still applicable):

```powershell
.\windows\test_op_miru_startup.ps1
```

## Uninstall

From an **elevated** PowerShell window:

```powershell
.\windows\uninstall_op_miru_startup.ps1
```

This removes the scheduled task only. It does not stop running processes.

## Files

| File | Purpose |
|------|---------|
| `install_op_miru_startup.ps1` | Registers the scheduled task |
| `uninstall_op_miru_startup.ps1` | Removes the scheduled task |
| `op_miru_bootstrap.cmd` | Wrapper invoked by the task |
| `start_op_miru.ps1` | ~~Startup and watchdog logic~~ **RETIRED—do not invoke** |
| `op_miru_common.ps1` | Paths and helpers |
| `test_op_miru_startup.ps1` | Health checks |

## Logs

Startup and Miru AI output are written to `data\startup-logs\`:

- `miru_ai_stdout.log`
- `miru_ai_stderr.log`

## Pushover Env

Store local notification credentials in the repo-root `.env` file. Use `.env.example` as the template and keep `.env` local only:

```dotenv
PUSHOVER_USER_KEY=
PUSHOVER_APP_TOKEN=
PUSHOVER_ENABLED=true
PUSHOVER_DEFAULT_PRIORITY=0
```

Both `run_miru_dev.ps1` and the Windows startup flow load this `.env` file before launching Miru AI. The repo ignores `.env`, so real secrets stay out of tracked files. If the file is missing or `PUSHOVER_ENABLED=true` is set without both required keys, the startup logs will report that clearly.
