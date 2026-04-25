# Miru MCP Gateway

Read-only HTTP MCP server that lets the Claude.ai web app read filesystem,
GitHub, n8n, and Miru system status via a custom connector.

- **Transport:** Streamable HTTP (FastMCP 2.x). Not SSE.
- **Port:** 18766 (loopback only)
- **Public URL:** `https://room.taila28611.ts.net/mcp/<SECRET>/mcp`
- **Public health:** `https://room.taila28611.ts.net/mcp/<SECRET>/health`
- **Internal routes (behind loopback):** `/mcp` and `/health` — Tailscale strips the `/mcp/<SECRET>` prefix before forwarding, so the gateway sees bare paths.
- **Tools:** up to 24 read-only tools across four categories — see below.
- **Auth:** URL-path secret enforced at the Tailscale Funnel edge. The gateway itself binds 127.0.0.1; no tailnet peer, LAN host, or internet address can reach the unsecreted routes.

## Tool categories

| Category   | Prefix     | Count | Enabled when                                          |
|------------|------------|-------|-------------------------------------------------------|
| filesystem | `fs_*`     | 9     | always (Stage 1 baseline)                             |
| system     | `system_*` | 3     | always                                                |
| github     | `github_*` | 7     | `GITHUB_TOKEN_READ` set in `.env`                     |
| n8n        | `n8n_*`    | 5     | `N8N_API_KEY` set in `.env`                           |

Categories disable cleanly if their env vars are missing — the gateway still
starts, only `MIRU_MCP_URL_SECRET` is fatal. The startup banner prints the
status of each category. Run `tail logs/mcp_gateway_18766_stdout.log` to
see the line-by-line summary.

### filesystem (Stage 1, unchanged)

`fs_read_text_file`, `fs_read_media_file`, `fs_read_multiple_files`,
`fs_list_directory`, `fs_list_directory_with_sizes`, `fs_directory_tree`,
`fs_search_files`, `fs_get_file_info`, `fs_list_allowed_directories`.

Deny list (mirrored across all read paths and surfaces):
`.env`, `*.env`, `*.env.*`, `secrets.*`, `tokens.*`, `*.key`, `*.pem`,
`*.ppk`, `id_rsa*`, `id_ed25519*`, plus path segments `.git`, `logs`,
`node_modules`, `__pycache__`, `.venv`, `venv`. SQLite DB files are also
denied.

### system

- `system_check_ports()` — liveness check on the 5 approved Miru ports
  (15678 n8n, 18080 pm, 18765 miru_ai, 18766 mcp_gateway, 19000 dispatcher).
- `system_check_health_endpoints()` — probe rich health URLs first
  (`/__pm_health`, `/api/health`), fall back to `/` if the rich path 404s.
- `system_tail_safe_log(name, lines=100)` — tail an approved log file by
  name (not arbitrary path). Capped at 500 lines / 256 KB. Output passes
  through redaction. Approved names: `mcp_gateway_stdout/stderr/restart`,
  `pm_stdout/stderr/restart`, `miru_ai_stdout/stderr/restart`,
  `dispatcher_stdout/stderr`, `startup`. Call with `name=""` to list them.

### github (read-only)

Disabled cleanly if `GITHUB_TOKEN_READ` is missing. `GITHUB_TOKEN_WRITE` is
ignored on purpose — these tools never need it.

- `github_get_repo_status(owner, repo)`
- `github_list_recent_commits(owner, repo, limit=20, ref=None)`
- `github_get_pr(owner, repo, number)`
- `github_list_open_prs(owner, repo, limit=20)`
- `github_get_issue(owner, repo, number)`
- `github_search_repo_files(owner, repo, query, limit=30)`
- `github_read_file(owner, repo, path, ref=None)` — same deny patterns as
  filesystem; refuses files >256 KB.

Optional repo allowlist via `MIRU_GITHUB_REPO_ALLOWLIST=Dreighto/*,anthropics/*`
(comma-separated `owner/repo` or `owner/*` patterns). Empty = any repo the
token can see. The check happens before any HTTP call.

All HTTP calls use a 10s timeout. List limits cap at 100.

### n8n (read-only)

Disabled cleanly if `N8N_API_KEY` is missing. Base URL defaults to
`http://localhost:15678` (override with `MIRU_N8N_BASE_URL`).

- `n8n_list_workflows(active_only=False, limit=50)`
- `n8n_get_workflow_summary(workflow_id)` — counts + node-type histogram
  only. Never returns raw `nodes`/`connections` (those contain hardcoded
  webhook URLs and credential references).
- `n8n_list_recent_executions(workflow_id=None, limit=20)`
- `n8n_get_execution_summary(execution_id)`
- `n8n_read_routing_history(limit=50)` — primary source: tail of
  `data/spike_ntfy_log.jsonl`. Fallback: `/api/v1/executions` summary list.

All output passes through redaction. List limits cap at 100–500 depending
on the tool.

### Redaction

Every Stage 2 tool's return passes through `redact()` immediately before
returning. Two passes:

1. **Substring scrub** — at startup, walk `os.environ` once and collect
   the values of every variable matching `*TOKEN*`/`*KEY*`/`*SECRET*`/
   `*PASSWORD*`/`*WEBHOOK*` (length >= 12). Any output that contains one
   of those values has it replaced with `<REDACTED:NAMED:VARNAME>`.
2. **Pattern scrub** — regex replacements for `ghp_`/`github_pat_`/`gho_`/
   `ghu_`/`ghs_`/`ghr_` shapes, JWTs, `Bearer …`, n8n webhook URLs,
   Telegram bot URLs.

After rotating any secret in `.env`, restart the gateway so the substring
set rebuilds.

## Auth trade-off

The original design layered the secret at both Tailscale AND the gateway's own router. Because the installed `tailscale serve` strips the `--set-path` prefix before forwarding, the gateway cannot see the secret in the URL, so the app-level guard was removed. The secret is now enforced **only at the Tailscale layer**.

This is safe IF AND ONLY IF:
- The gateway stays bound to 127.0.0.1 (do not change `MIRU_MCP_GATEWAY_HOST`).
- No other process exposes port 18766 to the tailnet or LAN.
- The Funnel path mount is the only route that reaches the gateway.

## Boundaries

- Read-only. There are no write tools registered with FastMCP.
- Bound to 127.0.0.1. The Tailscale Funnel is the only public surface.
- Refuses to start if `MIRU_MCP_URL_SECRET` is missing or shorter than 32 hex. (Even though the gateway doesn't embed the secret in its paths anymore, a missing value signals an incomplete operator setup.)
- Refuses to start if `MIRU_FS_ALLOW_ROOT` does not exist.
- The deny list (see `fs_tools.py`) expands the stdio MCP's defaults with:
  - `.env`, `*.env`, `*.env.*`, `.env.example`
  - `secrets.*`, `tokens.*`
  - `*.key`, `*.pem`, `*.ppk`, `id_rsa*`, `id_ed25519*`
  - path segments: `.git`, `logs`, `node_modules`, `__pycache__`, `.venv`, `venv`

## First-time setup

1. Install FastMCP (no repo venv exists; install at user scope):
   ```
   pip install --user "fastmcp>=2.5,<3"
   ```
2. Generate a 64-hex secret and append to `D:\dev\miru\.env`:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   ```
   MIRU_MCP_URL_SECRET=<that_value>
   ```
3. Register the scheduled task (elevated PowerShell, one time):
   ```
   powershell -ExecutionPolicy Bypass -File windows\register_restart_tasks.ps1
   ```
4. Launch the gateway:
   ```
   Start-ScheduledTask -TaskName "MiruRestartMcpGateway"
   ```
5. Add the Tailscale Funnel path mount (elevated). See plan §4 for exact form;
   verify against `tailscale serve --help` first.
6. In Claude.ai → Settings → Connectors → Add custom connector. Name `Miru`,
   URL `https://room.taila28611.ts.net/mcp/<SECRET>/mcp`. Leave OAuth fields blank.

## Rotating the secret

1. Generate a new 64-hex value, replace `MIRU_MCP_URL_SECRET=` in `.env`.
2. `Start-ScheduledTask -TaskName "MiruRestartMcpGateway"`.
3. Update the Funnel path mount to the new prefix (remove old, add new).
4. Update the connector URL in Claude.ai settings.
5. Grep `logs\mcp_gateway_18766_stdout.log` for any requests still hitting
   the old prefix.

## Rollback

**Disable a single Stage 2 category** — unset its env var
(`GITHUB_TOKEN_READ` or `N8N_API_KEY`) in `.env`, restart the gateway via
`Start-ScheduledTask -TaskName "MiruRestartMcpGateway"`. Banner shows
the category as DISABLED; the rest of the gateway continues normally. No
code change.

**Disable all Stage 2 categories without code change** — set both env
vars empty, restart. Gateway falls back to fs-only behavior. System tools
have no env knob; to disable them temporarily, comment out the
`("system", system_tools)` entry in `CATEGORIES` in `server.py`.

**Full revert to Stage 1** — `git revert` the Stage 2 commit. No DB
migration, no Tailscale change, no Claude.ai connector reconfiguration.

**Full gateway uninstall** — remove the Funnel path mount, stop the
scheduled task. The repo, n8n, and other services are unchanged.

## Stage 3 (deferred)

- OAuth 2.1 via FastMCP's `auth="oauth"` -- the URL-secret prefix can stay
  or be retired.
- Apply the expanded deny list to `tools/miru_readonly_filesystem_mcp.py`
  so Desktop's stdio MCP gets the same protection.
