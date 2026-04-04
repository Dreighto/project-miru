# OP Miru Worktree Startup

This startup path is for the `OP Miru` worktree only. It keeps the main repo runtime untouched and uses separate local ports for the worktree UI surfaces.

Canonical authority note: this is the primary startup doc for worktree runtime. The sibling `windows/README.md` is legacy/main-style context and is non-canonical for worktree startup.

## Services

1. **Dashboard** on host port **18080** (worktree Project Miru site from `pm/app.py`)
2. **Miru AI** from `miru_ai/server.py` via `python -m miru_ai.server` on host port **18765** (Dev control surface, binds 0.0.0.0 for Tailscale)
3. Optionally **tcg-watcher** from `app/` with isolated worktree `data/` (Docker flow only)

## Files

- `docker-compose.worktree.yml` — worktree Docker overrides (used when starting without `-Native`)
- `windows/start_op_miru_worktree.ps1` — starts the worktree stack (Docker or native)
- `windows/stop_op_miru_worktree.ps1` — stops the worktree stack (`-Native` and/or `-Docker`)
- `windows/test_op_miru_worktree.ps1` — verifies the worktree wiring
- PID and log files: `data/startup-logs/` (dashboard_18080.pid, miru_ai_worktree.pid, *.log)

## Start

**Native (recommended for “start and close PowerShell”):** dashboard and Miru AI run as local Python processes. They survive closing the launching window. Uses worktree `data/prices.json` and `data/card_catalog.db`.

```powershell
.\windows\start_op_miru_worktree.ps1 -Native
```

**Docker-based:** dashboard and optional watcher run in containers; Miru AI runs as a local process.

```powershell
.\windows\start_op_miru_worktree.ps1
```

All three services (dashboard + Miru AI + watcher in Docker):

```powershell
.\windows\start_op_miru_worktree.ps1 -IncludeWatcher
```

## Stop

Stop **native** dashboard and Miru AI (use when you started with `-Native`):

```powershell
.\windows\stop_op_miru_worktree.ps1 -Native
```

Stop the **Docker** worktree project (use when you started without `-Native`):

```powershell
.\windows\stop_op_miru_worktree.ps1 -Docker
```

You can pass both `-Native` and `-Docker` to stop everything.

## Verify

Local verification:

```powershell
.\windows\test_op_miru_worktree.ps1
```

Phone / Tailscale verification:

1. Open the printed Tailscale URLs from `start_op_miru_worktree.ps1`.
2. Confirm you are using ports `18080` and `18765`, not `8080` and `8765`.
3. Open the Miru AI worktree URL and confirm its `/api/dev-status` payload points `links.project_miru` at the worktree dashboard port.

## Proxy / Tunnel Note

If your existing phone route forwards directly to local services, use the worktree ports instead:

- dashboard upstream: `18080`
- Miru AI upstream: `18765`

If you already have a local reverse proxy outside this repo, the minimal change is to repoint its upstream targets from `8080` to `18080` and from `8765` to `18765`.
