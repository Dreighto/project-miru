# Miru MCP Gateway

HTTP MCP server for the Claude.ai web app: read-only filesystem / GitHub /
n8n / status tools, plus optional **Stage 3** n8n write tools and **Stage 3.5**
docs write tools (gated by env).

- **Transport:** Streamable HTTP (FastMCP 2.x). Not SSE.
- **Port:** 18766 (loopback only)
- **Public URL:** `https://room.taila28611.ts.net/mcp/<SECRET>/mcp`
- **Public health:** `https://room.taila28611.ts.net/mcp/<SECRET>/health`
- **Internal routes (behind loopback):** `/mcp` and `/health` — Tailscale strips the `/mcp/<SECRET>` prefix before forwarding, so the gateway sees bare paths.
- **Tools:** filesystem, system, optional GitHub / n8n reads (gated), n8n writes,
  docs writes, optional aggregator / audit read / worker status — see below.
- **Auth:** URL-path secret enforced at the Tailscale Funnel edge. The gateway itself binds 127.0.0.1; no tailnet peer, LAN host, or internet address can reach the unsecreted routes.

## Tool categories

| Category      | Prefix               | Enabled when                                          |
| ------------- | -------------------- | ----------------------------------------------------- |
| filesystem    | `fs_*`               | always                                                |
| system        | `system_*`           | always                                                |
| github        | `github_*`           | `GITHUB_TOKEN_READ` + `MIRU_GITHUB_READ_ENABLED=true` |
| n8n           | `n8n_*`              | `N8N_API_KEY` + `MIRU_N8N_READ_ENABLED=true`          |
| n8n_write     | `n8n_*` (mutations)  | `N8N_API_KEY` + `MIRU_N8N_WRITE_ENABLED=true`         |
| docs_write    | `docs_*`             | `MIRU_DOCS_WRITE_ENABLED=true`                        |
| activity      | `activity_since`     | `MIRU_AGGREGATOR_ENABLED=true`                        |
| audit_read    | `gateway_audit_tail` | `MIRU_AUDIT_READ_ENABLED=true`                        |
| worker_status | `worker_status`      | `MIRU_WORKER_STATUS_ENABLED=true`                     |

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

When `MIRU_SYSTEM_LOGS_ENABLED=true`, docker-backed keys are added:
`n8n_stdout`, `n8n_stderr`, `n8n_combined` (uses `docker logs` with
`--since 1h` / fallback `--since 4h` and `--tail`, container
`MIRU_N8N_CONTAINER_NAME` default `miru-n8n`). Requires Docker CLI on PATH.

### github (read-only, PRO-131)

Requires **`GITHUB_TOKEN_READ`** and **`MIRU_GITHUB_READ_ENABLED=true`**.
`GITHUB_TOKEN_WRITE` is ignored on purpose.

- `github_get_repo_status`, `github_list_recent_commits`, `github_get_pr`,
  `github_list_open_prs`, `github_get_issue`, `github_search_repo_files`,
  `github_read_file` — same semantics as before.
- **`github_get_pr_diff(owner, repo, number, max_lines=2000)`** — per-file
  patches from `GET /pulls/{n}/files`; `max_lines` hard-capped at 8000;
  generated lockfiles listed under `skipped_paths`.
- **`github_list_pr_reviews`**, **`github_get_pr_review_comments`** (REST +
  GraphQL thread `thread_resolved` when available),
  **`github_get_pr_check_runs`** (check runs + `app_slug`).

Optional repo allowlist via `MIRU_GITHUB_REPO_ALLOWLIST`. All HTTP calls use a
10s timeout.

### n8n (read-only, PRO-132)

Requires **`N8N_API_KEY`** and **`MIRU_N8N_READ_ENABLED=true`**. Base URL
`MIRU_N8N_BASE_URL` (default `http://localhost:15678`).

- `n8n_list_workflows`, `n8n_get_workflow_summary`, `n8n_list_recent_executions`,
  `n8n_get_execution_summary`, `n8n_read_routing_history`
- **`n8n_list_executions(status?, workflow_id?, since?, limit=20)`** — richer
  list with `workflow_name` and `error_message` when available.
- **`n8n_get_execution(execution_id, include_node_data=False, approval_request_id?)`**
  — per-node diagnosis (shapes by default). `include_node_data=True` queues
  Telegram approval (`operation: read_execution_include_data` on the pending
  JSONL); after W7/operator drops
  `data/mcp_gateway_execution_cache/<request_id>.json`, call again with
  `approval_request_id`. Dev bypass: `MIRU_N8N_EXECUTION_DATA_SKIP_APPROVAL=true`.

All output passes through `redact()` (extra patterns for DB URLs, webhook
paths, etc.).

### activity (PRO-134)

- **`activity_since(minutes=30, sources?)`** — merges Linear (needs
  `LINEAR_API_KEY` + `MIRU_LINEAR_TEAM_ID`), GitHub commits (repos from
  `MIRU_GITHUB_REPO_ALLOWLIST` or optional `MIRU_ACTIVITY_GITHUB_REPOS=owner/repo`),
  n8n executions, and filesystem `mtime` scans under `MIRU_FS_ALLOW_ROOT`.
  Parallel per-source 5s budget; max 200 events; `partial` / `truncated` flags.

### audit_read (PRO-135)

- **`gateway_audit_tail(log_kind='writes', category?, since?, limit=50, summary=True)`**
  — tail `mcp_gateway_writes.jsonl`, `mcp_gateway_reads.jsonl`, or
  `mcp_gateway_docs_writes.jsonl` under `logs/` (`log_kind` = `writes` |
  `reads` | `docs`), optional filters, `chain_intact` via hash verification on
  the returned slice.

### worker_status (PRO-136)

- **`worker_status(worker_name?)`** — git snapshot per worker from
  `MIRU_WORKERS_CONFIG=name:path,name:path` and/or `MIRU_WORKERS_YAML` (repo-relative
  or absolute path to YAML with `workers: [{name, worktree_path}]`). Paths must
  sit under `MIRU_FS_ALLOW_ROOT` or `MIRU_WORKER_PATH_ALLOWLIST` prefixes.

### Security primitives (PRO-137)

Always-on per-tool **rate limits** (in-memory sliding 60s window) and **regex
parameter validation** for common identifiers (`owner`, `repo`, `path`,
`workflow_id`, `execution_id`, …). Tune with `MIRU_RATE_LIMIT_GITHUB_READ`,
`MIRU_RATE_LIMIT_N8N_READ`, `MIRU_RATE_LIMIT_DEFAULT`, etc. (see `config.py`).
Optional filesystem read-audit rows: `MIRU_READ_AUDIT_FS=true`.

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

### Audit logs (writes, reads, hash chain PRO-135)

`tools/miru_mcp_gateway/audit.py` appends JSON lines and rotates when the
active file exceeds **10 MiB**: active → `.1`, shift `.1`→`.2` … `.4`→`.5`,
drop previous `.5`. Files under `logs/`:

- `mcp_gateway_writes.jsonl` — n8n write tool audits (**hash-chained**).
- `mcp_gateway_docs_writes.jsonl` — docs writes (**hash-chained**).
- `mcp_gateway_reads.jsonl` — per-tool read invocations (PRO-131/132/137;
  **hash-chained**).

Legacy rows without `row_hash` remain readable; chain verification treats them
as `unverified` where noted in `gateway_audit_tail`.

### Redaction

Every Stage 2+ tool return passes through `redact()` immediately before
returning (including write tools' JSON payloads where applicable). Two passes:

1. **Substring scrub** — at startup, walk `os.environ` once and collect
   the values of every variable matching `*TOKEN*`/`*KEY*`/`*SECRET*`/
   `*PASSWORD*`/`*WEBHOOK*` (length >= 12). Any output that contains one
   of those values has it replaced with `<REDACTED:NAMED:VARNAME>`.
2. **Pattern scrub** — regex replacements for `ghp_`/`github_pat_`/`gho_`/
   `ghu_`/`ghs_`/`ghr_` shapes, JWTs, `Bearer …`, n8n webhook URLs,
   Telegram bot URLs, `postgresql://…`, `/webhook/…`, raw `X-N8N-API-KEY:` lines.

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
