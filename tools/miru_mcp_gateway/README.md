# Miru MCP Gateway

HTTP MCP server for the Claude.ai web app: read-only filesystem / GitHub /
n8n / status tools, plus optional **Stage 3** n8n write tools and **Stage 3.5**
docs write tools (gated by env).

- **Transport:** Streamable HTTP (FastMCP 2.x). Not SSE.
- **Port:** 18766 (loopback only)
- **Public URL:** `https://room.taila28611.ts.net/mcp/<SECRET>/mcp`
- **Public health:** `https://room.taila28611.ts.net/mcp/<SECRET>/health`
- **Internal routes (behind loopback):** `/mcp` and `/health` — Tailscale strips the `/mcp/<SECRET>` prefix before forwarding, so the gateway sees bare paths.
- **Tools:** Stage 1–2 read surfaces (24 tools) plus optional `n8n_write_*`
  (21) and `docs_*` writes (3) — see below.
- **Auth:** URL-path secret enforced at the Tailscale Funnel edge. The gateway itself binds 127.0.0.1; no tailnet peer, LAN host, or internet address can reach the unsecreted routes.

## Tool categories

| Category   | Prefix              | Count | Enabled when                                  |
| ---------- | ------------------- | ----- | --------------------------------------------- |
| filesystem | `fs_*`              | 9     | always (Stage 1 baseline)                     |
| system     | `system_*`          | 3     | always                                        |
| github     | `github_*`          | 7     | `GITHUB_TOKEN_READ` set in `.env`             |
| n8n        | `n8n_*`             | 5     | `N8N_API_KEY` set in `.env`                   |
| n8n_write  | `n8n_*` (mutations) | 21    | `N8N_API_KEY` + `MIRU_N8N_WRITE_ENABLED=true` |
| docs_write | `docs_*`            | 3     | `MIRU_DOCS_WRITE_ENABLED=true`                |

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

### n8n_write (Stage 3)

Requires `N8N_API_KEY`, `MIRU_N8N_WRITE_ENABLED=true`, and the `requests`
package. Optional `MIRU_N8N_WRITE_WORKFLOW_ALLOWLIST` (comma-separated
workflow IDs); when empty, all workflow IDs are allowed for tools that take a
`workflow_id`.

**Approval contract (`n8n_create_workflow`, `n8n_update_workflow`):** the
gateway appends one JSONL intent to `data/mcp_gateway_pending_writes.jsonl`,
audits a single `pending_approval` row to `logs/mcp_gateway_writes.jsonl`, and
returns `{"status":"pending_approval", ...}` — it does **not** call the n8n
API for the mutation. The operator approves in Telegram; **W7** (callback
handler) applies the mutation via the n8n Public API. The successful mutation
is visible in **n8n execution history**; the gateway does not write a second
audit row when W7 runs.

**Telegram notify (Option A):** set `MIRU_N8N_WRITE_APPROVAL_NOTIFY_URL` to the
production URL of the small n8n workflow
`docker/n8n/workflows/w-mcp-n8n-write-notify.json` (webhook path
`mcp-n8n-write-notify`). That workflow reads the pending JSONL line, mints
HMAC buttons, appends a `pending_callbacks.jsonl` intent with
`button_set=mcp_n8n_write`, and sends Telegram. Telegram secrets stay in n8n
env only.

**W7:** import the merged workflow
`docker/n8n/workflows/w7-telegram-callback-handler.json` (or re-run
`python docker/n8n/scripts/merge_w7_mcp_n8n_branch.py` after editing
`docker/n8n/workflows/fragments/w7_mcp_branch.nodes.json`, regenerating the
fragment with `python docker/n8n/scripts/build_w7_mcp_branch_fragment.py`).
The n8n container needs `N8N_API_KEY` and `N8N_INTERNAL_API_BASE` (see
`docker/n8n/docker-compose.yml`) so W7 can call the local API from inside the
container.

Lifecycle / execution tools include activate, deactivate, execute (with
execute/run fallback), webhook trigger, stop/retry execution, settings and
tags, rename, archive/unarchive (404 → explicit “unsupported on this build”),
single and bulk execution delete (bulk refuses >100 matches without deleting),
variables, and tags.

### docs_write (Stage 3.5)

Requires `MIRU_DOCS_WRITE_ENABLED=true`. Writes Markdown and similar docs under
`MIRU_FS_ALLOW_ROOT` using a repo-relative allowlist (default globs from
config; override with comma-separated `MIRU_DOCS_WRITE_PATH_ALLOWLIST`).
Hard deny list blocks code, config, lockfiles, etc.; 256 KB max per write;
content is rejected if it contains a substring matching a known env secret
(see `redact.find_named_secret_substrings`).

Audit log: `logs/mcp_gateway_docs_writes.jsonl` (same 10 MiB rotation as
writes).

### Write audit logs

`tools/miru_mcp_gateway/audit.py` appends JSON lines and rotates when the
active file exceeds **10 MiB**: active → `.1`, shift `.1`→`.2` … `.4`→`.5`,
drop previous `.5`. Same scheme for `mcp_gateway_writes.jsonl` and
`mcp_gateway_docs_writes.jsonl` under `logs/`.

### Redaction

Every Stage 2+ tool return passes through `redact()` immediately before
returning (including write tools' JSON payloads where applicable). Two passes:

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

- Write tools exist only when their env gates are on; default is read-only
  categories only.
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

**Disable n8n_write / docs_write** — unset `MIRU_N8N_WRITE_ENABLED` /
`MIRU_DOCS_WRITE_ENABLED` (or set to a non-true value), restart the gateway.

**Disable all Stage 2 categories without code change** — set both env
vars empty, restart. Gateway falls back to fs-only behavior. System tools
have no env knob; to disable them temporarily, comment out the
`("system", system_tools)` entry in `CATEGORIES` in `server.py`.

**Full revert to Stage 1** — `git revert` the Stage 2 commit. No DB
migration, no Tailscale change, no Claude.ai connector reconfiguration.

**Full gateway uninstall** — remove the Funnel path mount, stop the
scheduled task. The repo, n8n, and other services are unchanged.

## Future

- OAuth 2.1 via FastMCP's `auth="oauth"` — the URL-secret prefix can stay or
  be retired.
- Apply the expanded deny list to `tools/miru_readonly_filesystem_mcp.py` so
  Desktop's stdio MCP gets the same protection.
