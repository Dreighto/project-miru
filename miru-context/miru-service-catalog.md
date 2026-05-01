# Miru Service Catalog

Ground-truth reference for every service in the Miru stack. Extracted directly from source
files and live logs. Workers read this before writing any code that touches a service.

Last updated: 2026-05-01

---

## How to use this document

- **Before touching a service**: read its entry so you know the real health endpoint,
  log paths, and what healthy output looks like.
- **Before writing infrastructure code**: check the protected ports and restart mechanisms.
- **Before filing a health alert**: verify the expected normal patterns — many things that
  look like errors are actually normal idle output.

---

## Service Index

| Service           | Port  | Language                   | Status                               |
| ----------------- | ----- | -------------------------- | ------------------------------------ |
| Dispatch Listener | 19100 | Node.js                    | ACTIVE                               |
| MCP Gateway       | 18766 | Python (FastMCP / uvicorn) | ACTIVE                               |
| Miru AI           | 18765 | Python (Flask)             | ACTIVE                               |
| PM Dashboard      | 18080 | Python (Flask + SvelteKit) | ACTIVE                               |
| n8n               | 15678 | Node.js                    | ACTIVE                               |
| Task Dispatcher   | 19000 | Node.js                    | DECOMMISSIONED (PRO-234, 2026-04-30) |

---

## 1. Dispatch Listener — Port 19100

**What it does:** Receives dispatch webhooks from n8n. Authenticates via HMAC. Spawns
worker processes (claude-code, codex, gemini) and streams their output back as the job runs.
Maintains an allowlist of approved worker binaries.

**Bind address:** 127.0.0.1 (loopback only — never exposed externally)

### Health endpoint

```
GET http://127.0.0.1:19100/health
Expected: 200
Body: {"status":"ok","listener":"dispatch_listener","port":19100}
```

### Log files (relative to repo root)

| File                                 | Contents                           |
| ------------------------------------ | ---------------------------------- |
| `logs/dispatch_listener_stdout.log`  | Structured JSON worker activity    |
| `logs/dispatch_listener_stderr.log`  | Node.js errors, crash stack traces |
| `logs/dispatch_listener_wrapper.log` | Wrapper respawn loop events        |

### Normal log patterns (healthy)

Dispatch Listener uses structured JSON logs — one object per line.

```json
{"ts":"2026-04-26T00:24:20.594Z","level":"info","msg":"listener_listening","host":"127.0.0.1","port":19100}
{"ts":"...","level":"info","msg":"startup_allowlist_resolved","resolved":{"claude-code":"...claude.cmd","codex":"...codex.cmd"}}
{"ts":"...","level":"info","msg":"worker_spawned","trace_id":"...","worker":"claude-code","pid":48040}
{"ts":"...","level":"info","msg":"worker_exit","trace_id":"...","exit_code":0,"status":"CONFIRMED_WORKING"}
{"ts":"...","level":"info","msg":"already_dispatched","trace_id":"..."}
```

### Failure log patterns

```json
{"ts":"...","level":"fatal","msg":"startup_missing_binaries","missing":[{"worker":"claude-code","binary":"claude.cmd"}]}
{"ts":"...","level":"warn","msg":"hmac_reject","body_bytes":184}
{"ts":"...","level":"warn","msg":"allowlist_reject","trace_id":"...","worker":"windsurf"}
{"ts":"...","level":"warn","msg":"worker_timeout_kill","trace_id":"...","pid":33820,"timeout_seconds":2}
{"ts":"...","level":"info","msg":"worker_exit","exit_code":-1,"signal":"SIGTERM","status":"FAILED","timed_out":true}
```

**Stderr EADDRINUSE note:** If you see `Error: listen EADDRINUSE :::19100` or
`Unhandled 'error' event` in `dispatch_listener_stderr.log` AND the service is currently
up (health check returns 200), this is a historical startup-race artifact — the service
started a second time while the first was already running. The entry is inert. Not an alert.

### Restart mechanism

```powershell
powershell -ExecutionPolicy Bypass -File windows\restart_dispatch_listener.ps1
```

This triggers the `MiruRestartDispatchListener` scheduled task (SYSTEM privilege).
The wrapper at `windows\start_dispatch_listener.ps1` owns the respawn loop and the
port-in-use guard.

### Config files

- `services/dispatch_listener/src/allowlist.js` — approved worker binaries
- `services/dispatch_listener/src/index.js` — main entry point

---

## 2. MCP Gateway — Port 18766

**What it does:** Exposes the local filesystem and system tools to Claude via MCP (Model
Context Protocol). Runs as a FastMCP / Starlette server. Proxies MCP calls from Claude Code,
Claude Chat, and Cursor to local tools: filesystem read/write, git, GitHub, n8n, Telegram,
Linear, Notion, Perplexity, database reads, and system health checks.

**Bind address:** 127.0.0.1 (loopback only, with Tailscale Funnel for remote access)

### Health endpoint

```
GET http://127.0.0.1:18766/health
Expected: 200
Body: {"ok":true,"version":"0.4.0","name":"miru-fs-gateway"}
```

### Log files

The gateway logs to stdout/stderr at the process level. No dedicated log files in `logs/`.
Output is captured by the startup wrapper task.

### Normal log patterns (healthy)

```
[miru-fs-gateway] starting v0.4.0
  host         : 127.0.0.1
  port         : 18766
  fs_root      : D:\dev\miru
  total        : 47 tools
```

### Failure log patterns

```
FATAL: fastmcp is not installed. Install with: pip install --user "fastmcp>=2.5,<3"
FATAL: filesystem root mismatch.
  stdio MCP module ROOT = D:\dev\miru
  gateway config root   = <wrong path>
FATAL: this FastMCP version does not expose `custom_route` for attaching /health endpoint.
```

### Restart mechanism

```powershell
powershell -ExecutionPolicy Bypass -File windows\restart_mcp_gateway.ps1
```

Triggers the `MiruRestartMCPGateway` scheduled task. Restart log: `logs/mcp_gateway_restart.log`.

---

## 3. Miru AI — Port 18765

**What it does:** Dev Review Hub backend. Handles card data queries, batch review jobs,
Ollama routing for AI analysis, Telegram webhook, and Pushover notifications. The primary
Python service workers (Claude Code, Codex) write backend code here.

**Bind address:** 0.0.0.0 (all interfaces — accessible on local network and Tailscale)

### Health endpoint

```
GET http://127.0.0.1:18765/api/health
Expected: 200
Body: {"status":"ok","app_name":"Miru AI","server_started_at":"<ISO-8601>",
       "helper_script_ready":true,"api_key_ready":true,"default_mode":"<mode>"}
```

**Important:** The path is `/api/health`, NOT `/health`. A request to `/health` returns 404.

### Log files (relative to repo root)

| File                       | Contents                                    |
| -------------------------- | ------------------------------------------- |
| `logs/miru_ai_stdout.log`  | Flask access log, startup messages          |
| `logs/miru_ai_stderr.log`  | Flask warnings, Pushover config notices     |
| `logs/miru_ai_restart.log` | Restart wrapper events (tab-delimited, UTC) |

### Normal log patterns (healthy)

Flask uses its own log format — NOT JSON.

```
[2026-04-22 21:57:12,520] WARNING in server: Pushover is enabled but missing required keys: PUSHOVER_APP_TOKEN.
 * Running on http://127.0.0.1:18765
 * Running on http://192.168.50.103:18765
127.0.0.1 - - [23/Apr/2026 02:35:40] "GET /api/health HTTP/1.1" 200 -
100.88.228.28 - - [23/Apr/2026 14:11:45] "GET /api/hub/summary HTTP/1.1" 200 -
```

**Note:** The "Pushover is enabled but missing required keys" WARNING is normal and
expected — it means Pushover was configured in .env but a required key is absent. Not
an alert condition.

### Failure log patterns

```
127.0.0.1 - - [...] "GET /health HTTP/1.1" 404 -   ← wrong health path, use /api/health
WARNING: This is a development server. Do not use it in a production deployment.
2026-04-30 20:49:09.620  WARNING: Failed to stop PID 5624: Access is denied
2026-04-30 20:49:11.776  ERROR: Port 18765 still occupied after kill attempt — aborting
```

### Restart mechanism

```powershell
powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1
```

Triggers `MiruRestartMiruAI` scheduled task. Restart log: `logs/miru_ai_restart.log`.

### Key source files

- `miru_ai/app.py` — Flask application factory, route registration
- `miru_ai/server.py` or `miru_ai/__main__.py` — entry point with `--host`/`--port` args
- `miru_ai/core/` — core business logic workers
- `miru_ai/governance/` — routing, dispatch, stall detection
- `miru_ai/workers/` — individual worker implementations

---

## 4. PM Dashboard — Port 18080

**What it does:** Project Miru storefront UI. Serves the SvelteKit-built frontend. Flask
backend provides API routes for card data, watchlist, and hub summary. The frontend is a
pre-built SPA served as static files from `pm/storefront/build/`.

**Bind address:** 0.0.0.0 (all interfaces — accessible on local network and Tailscale)

### Health endpoint

```
GET http://127.0.0.1:18080/__pm_health
Expected: 200
Body: {"storefront_built": true, "path": "<path to build/index.html>"}
```

**Important:** The health endpoint is `/__pm_health`, NOT `/health` or `/api/health`.
A request to `/health` returns 200 with the SPA `index.html` (SPA catch-all route) —
which confirms the server is running but does NOT confirm storefront health.

### Log files (relative to repo root)

| File                  | Contents                                    |
| --------------------- | ------------------------------------------- |
| `logs/pm_stdout.log`  | Flask access log, startup messages          |
| `logs/pm_stderr.log`  | Flask warnings, startup errors              |
| `logs/pm_restart.log` | Restart wrapper events (tab-delimited, UTC) |

### Normal log patterns (healthy)

```
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:18080
127.0.0.1 - - [01/May/2026 04:31:09] "GET /__pm_health HTTP/1.1" 200 -
100.88.228.28 - - [23/Apr/2026 14:11:45] "GET /api/hub/summary HTTP/1.1" 200 -
```

### Failure log patterns

```
127.0.0.1 - - [...] "GET /health HTTP/1.1" 404 -   ← this 404 is expected (wrong path)
WARNING: This is a development server. Do not use it in a production deployment.
{"storefront_built": false, ...}   ← storefront not built; run npm run build in pm/storefront/
```

### Restart mechanism

```powershell
powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1
```

Triggers `MiruRestartPM` scheduled task. Restart log: `logs/pm_restart.log`.

### Key source files

- `pm/app.py` — Flask application, API routes, SPA serving, `/__pm_health` endpoint
- `pm/storefront/` — SvelteKit frontend source
- `pm/storefront/build/` — pre-built SPA (must exist for storefront to serve)

---

## 5. n8n — Port 15678

**What it does:** Workflow automation engine. Owns the Telegram bot webhook (dispatches
operator messages to workers), routes Linear ticket events, runs the stall recovery loop,
orchestrates multi-step jobs that Claude Chat initiates.

**Bind address:** 0.0.0.0 (Docker container, exposed on host port 15678)

### Health endpoint

```
GET http://127.0.0.1:15678/healthz
Expected: 200
Body: {"status":"ok"}
```

### Log files

n8n runs in Docker. Logs are in the container:

```bash
docker logs n8n
```

Workflow execution logs are accessible via the n8n UI at http://127.0.0.1:15678.

### Normal log patterns (healthy)

```
Initializing n8n process
Editor is now accessible via: http://localhost:15678
```

### Failure log patterns

```
Error: ECONNREFUSED 127.0.0.1:5678   ← container not running
{"status":"error","message":"..."}   ← n8n internal error
```

### Critical constraint: Telegram webhook ownership

n8n OWNS the Telegram bot webhook. The n8n webhook is registered with Telegram and
processes all incoming bot messages. Any other code that calls `getUpdates` on the
same bot token will receive HTTP 409 Conflict and break message delivery.

**Do NOT** call `getUpdates` from sentinel, scripts, or any other code while n8n's
Telegram workflow is active. Route all Telegram commands through n8n instead.

### Restart mechanism

n8n runs in Docker. Restart via:

```bash
docker restart n8n
```

or via the n8n MCP tool: `service_restart` with service="n8n".

---

## Decommissioned Services

### Task Dispatcher — Port 19000 (DECOMMISSIONED)

Decommissioned 2026-04-30 (PRO-234). Code kept at `dispatcher/` for reference.
Service is stopped. Port 19000 is reserved — do not bind anything to it.

---

## Cross-Service Patterns

### Restart log format (all PS-managed services)

All PowerShell-managed restart scripts write to their service's restart log using
tab-delimited format with millisecond UTC timestamps:

```
2026-04-22 14:47:04.164	=== MiruRestartPM BEGIN ===
2026-04-22 14:47:04.181	repo_root=D:\dev\miru
2026-04-22 14:47:08.322	PM Dashboard is listening on port 18080 — restart SUCCESS
```

### "Development server" warning

Flask services (PM, Miru AI) always emit:

```
WARNING: This is a development server. Do not use it in a production deployment.
```

This is expected and not an alert condition. It appears on every start.

### Healthy idle = silence

When services are running normally, they produce minimal log output between requests.
Absence of log entries is healthy, not suspicious.
