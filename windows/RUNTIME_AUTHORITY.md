# Miru runtime authority (one true way)

Single-instance, health-verified startup and verification for all three runtimes. **Do not use port 8765.**

## Always-on worktree (18765 + 18080)

The **authoritative worktree services** are:

- **18765** — Miru AI / Dev (this repo, `python -m miru_ai.server` -> `miru_ai\server.py`)
- **18080** — Project Miru worktree dashboard (this repo, `pm\app.py`)

They are intended to stay running so you can:

1. Edit in Cursor (templates, CSS, JS, UI).
2. Save; the server reloads automatically (see below).
3. Refresh on your phone and see the change — **no remote shell or manual restart** for normal UI work.

- **Start on boot:** Register the startup task once (see Reboot / crash recovery). The task runs the worktree stack (18080 + 18765) after Windows starts.
- **Recover on crash:** Re-run the same startup path (e.g. run `start_op_miru_worktree.ps1 -Native` or rely on a scheduled “keep-alive” if you add one).
- **No duplicates:** Startup scripts check the port; if the correct process is already healthy, they do nothing. If the wrong process is on the port, they stop it and start the correct one.
- **Correct repo/ports:** Scripts use fixed ports and the repo they’re run from; they never start 8765.

## Port authority

| Port  | Service                    | Role                          |
|-------|----------------------------|-------------------------------|
| **18765** | Miru AI / Dev              | Worktree Dev control surface  |
| **18080** | Project Miru worktree      | Worktree dashboard (Library)  |
| **8080**  | Main stable                | Main repo dashboard           |

## Refresh-first workflow (when is refresh enough?)

- **Templates, CSS, JS, static assets (Dev page and Project Miru UI):** Both services run with **reload-on-change** (Flask debug/reloader for 18765, `use_reloader` for dashboard on 18080). After you save a file, the process restarts itself and serves the new content. **Refresh your phone** — no manual restart needed.
- **Adding Python dependencies, changing server behavior, or changing env:** Restart the affected service (or full stack) so the new code/env is loaded. Use the fallback commands below.
- **Uncertain:** If a change doesn’t show after refresh, do a controlled restart of that service or the full worktree stack.

## Verify from phone or one command

- **From phone (one URL):** Open `http://<server>:18765/api/worktree-status`. Response: `{"18765":"ok","18080":"ok","worktree":true}` when both are healthy. If `18080` is `"unhealthy"`, the dashboard isn’t responding.
- **From machine (one command):**  
  `.\windows\verify_miru_stack.ps1`  
  Exit 0 only if 18765, 18080, and 8080 are healthy. Use `-WorktreeOnly` to check only 18765 and 18080 (ignore 8080). Use `-Quiet` to reduce output.

## Dev page runtime controls

From the Dev page (`/dev` on 18765), open **Advanced / system** and use the **Runtime control** section to:

- See live status for 18765 and 18080 (Healthy / Unhealthy) and last check time.
- **Refresh status** — re-check both ports.
- **Restart Project Miru** — runs `start_project_miru_dashboard.ps1 -Force` (18080). Works from the same machine only; shows result in the UI.
- **Restart Miru AI** — runs `start_miru_ai_dev.ps1 -Force` (18765). This replaces the current process; the tab will disconnect. Reconnect to `/dev` in a few seconds. Localhost only.
- **Restart worktree stack** — runs `start_op_miru_worktree.ps1 -Native`. Fire-and-forget; reconnect to `/dev` after a few seconds. Localhost only.

Restart actions are allowed from **localhost**, **Tailscale** (100.x or fd7a:115c:a1e0::/48), **private LAN**, or when the request includes a valid **X-Miru-Runtime-Token** header. The backend checks **X-Forwarded-For** and **X-Real-IP** first, then REMOTE_ADDR. If Restart from the phone still returns 403 (e.g. Tailscale DERP or NAT), set **MIRU_RUNTIME_RESTART_TOKEN** in the environment; the Dev page will send it with restart requests so restarts work from the phone. Status and refresh work from any client.

## Remote reachability (Tailscale / firewall)

The Miru AI server binds to **0.0.0.0:18765** and the Project Miru dashboard to **0.0.0.0:18080**, so both accept connections on all interfaces (localhost, LAN, Tailscale). Your machine’s Tailscale IP (e.g. **100.81.19.49**) is the address to use from your phone; confirm it with `ipconfig` (Tailscale adapter).

If the phone gets **ERR_CONNECTION_TIMED_OUT** or a blank page to `http://<Tailscale-IP>:18765/dev` or `http://<Tailscale-IP>:18080/` while local checks pass, first ensure the service is running (e.g. run `.\windows\start_project_miru_dashboard.ps1` for 18080). Then the cause is usually **Windows Firewall** or **Tailscale connectivity**, not the app.

- **Explicit firewall rules (one-time, run PowerShell as Administrator):**
  ```powershell
  netsh advfirewall firewall add rule name="Miru AI Dev (18765)" dir=in action=allow protocol=TCP localport=18765 profile=private,public description="Allow inbound TCP 18765 for Miru AI Dev worktree server"
  netsh advfirewall firewall add rule name="Project Miru dashboard (18080)" dir=in action=allow protocol=TCP localport=18080 profile=private,public description="Allow inbound TCP 18080 for Project Miru worktree dashboard"
  ```
- **Verify from the machine** that the Tailscale IP responds:  
  `Invoke-WebRequest -Uri "http://100.81.19.49:18765/api/health" -UseBasicParsing -TimeoutSec 5`  
  (Replace with your actual Tailscale IP if different.) StatusCode 200 and body containing `"status":"ok"` means the server is reachable on that interface; if the phone still times out, the issue is likely Tailscale NAT/discovery or the phone’s network.
- **Intermittent** timeouts can be Tailscale UDP discovery or NAT traversal; ensure both devices are on the same Tailnet and that Tailscale is connected on the phone.

## Start / restart (canonical commands — use as fallback)

### Miru AI / Dev (18765 only)

```powershell
.\windows\start_miru_ai_dev.ps1
```

- If 18765 is already healthy (`/api/health` + `/dev`), does nothing.
- If the wrong process is on 18765, stops it and starts `python -m miru_ai.server` on 18765.
- Waits for health and Dev page; exits 1 if they fail.
- Optional: `-Force` to stop whatever is on 18765 and start fresh.

### Project Miru worktree dashboard (18080 only)

```powershell
.\windows\start_project_miru_dashboard.ps1
```

- If 18080 is already healthy (root contains "Miru"), does nothing.
- If the wrong process is on 18080, stops it and starts `pm\app.py` with `PORT=18080`.
- Optional: `-Force` to stop and start fresh.

### Main stable (8080)

```powershell
.\windows\start_main_stable.ps1
```

- Verification only: exits 0 if 8080 responds with "Miru", else 1.
- With `-Start`: runs `docker compose up -d` (requires `docker-compose.yml` in repo) and waits for 8080 to become healthy.
- Use when the main dashboard is run from this repo. If 8080 is run from another repo or host, just run without `-Start` to verify.

### Full worktree stack (18080 + 18765)

```powershell
.\windows\start_op_miru_worktree.ps1 -Native
```

- Starts dashboard on 18080 and Miru AI on 18765 as native Python processes.
- Single-instance: if the correct process is already on a port, it may skip or restart only when needed (e.g. binding or health).
- Without `-Native`: dashboard via Docker compose, Miru AI still native.

## Verify the stack

```powershell
.\windows\verify_miru_stack.ps1
```

- Checks 18765 (`/api/health` + `/dev`), 18080 (root), 8080 (root).
- Exit 0 only if all three are healthy. Use `-Quiet` to reduce output.

## Stop

- **Worktree native (18080 + 18765):**  
  `.\windows\stop_op_miru_worktree.ps1 -Native`
- **Worktree Docker:**  
  `.\windows\stop_op_miru_worktree.ps1 -Docker`

## Reboot / crash recovery (scheduled task)

To run the worktree stack after Windows startup:

1. Open an **elevated** PowerShell.
2. From the repo root:
   ```powershell
   .\windows\install_op_miru_startup.ps1 -Worktree
   ```
3. The task runs `op_miru_bootstrap.cmd worktree`, which runs `start_op_miru_worktree.ps1 -Native`.

Without `-Worktree`, the task runs the legacy bootstrap (8080 + 8765). For the worktree, always use `-Worktree`. This is the **one** startup path; do not add a second competing system.

## What auto-refreshes vs what needs a restart

| Change type | After save | Your action |
|-------------|------------|-------------|
| Templates (e.g. `miru_ai/templates/*.html`, PM templates) | Reloader restarts process | **Refresh** browser |
| CSS / JS / static (e.g. `miru_ai/static/*`, PM static) | Reloader restarts process | **Refresh** browser |
| Dev page or Project Miru UI (above) | Same as above | **Refresh** browser |
| New Python dependencies (`pip install` / `requirements`) | Not picked up | **Restart** affected service or stack |
| Server logic or env (e.g. new routes, env vars) | Not picked up | **Restart** affected service or stack |

If in doubt, run `.\windows\verify_miru_stack.ps1`; if healthy, try refresh first, then restart if needed.

## Safety

- **Wrong repo/port:** Scripts use fixed ports (18765, 18080, 8080). They do not start anything on 8765.
- **Duplicates:** Before starting, scripts check the port. If the correct service is already running and healthy, they skip. If the wrong process is on the port, they stop it then start the correct one.
- **Health:** After starting, scripts wait for the appropriate HTTP check; they exit with an error if the service does not become healthy.

## Files

| File | Purpose |
|------|---------|
| `op_miru_runtime.ps1` | Port constants and helpers (single-instance resolution, health checks). |
| `start_miru_ai_dev.ps1` | Start/restart Miru AI Dev (18765) only. |
| `start_project_miru_dashboard.ps1` | Start/restart worktree dashboard (18080) only. |
| `start_main_stable.ps1` | Verify/start main stable (8080). |
| `verify_miru_stack.ps1` | Health-check all three ports. |
| `start_op_miru_worktree.ps1` | Full worktree stack (existing). |
| `stop_op_miru_worktree.ps1` | Stop worktree (existing). |
| `op_miru_bootstrap.cmd` | Entry point for scheduled task; argument `worktree` = worktree stack. |
| `install_op_miru_startup.ps1` | Register startup task; `-Worktree` for worktree recovery. |

## Everyday workflow (worktree UI work)

1. **Cursor:** Edit templates, CSS, or JS in the worktree; save.
2. **Server:** Process reloads automatically (Flask reloader).
3. **Phone:** Refresh the Dev page (18765) or Project Miru (18080); changes appear. No remote shell or manual restart.
4. **If something’s wrong:** From the machine run `.\windows\verify_miru_stack.ps1`. If 18765 or 18080 is unhealthy, run `.\windows\start_op_miru_worktree.ps1 -Native` (or the single-service script) as fallback.
