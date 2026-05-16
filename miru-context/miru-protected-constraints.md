# Miru Protected Constraints

Non-negotiable architectural constraints every coding worker must know before touching
any Miru service. These are not preferences — violating them causes production failures.

Last updated: 2026-05-01

---

## What "protected" means

A constraint is protected when breaking it has caused or would cause a production failure
that cannot be rolled back quickly. These constraints are locked at the architecture level.
Workers must not alter them without an explicit operator decision logged in CLAUDE.md.

Adaptable things (implementation details, log formats, variable names, algorithm choices)
are NOT in this document. Use your judgment on those.

---

## 1. Ports — DO NOT CHANGE OR REUSE

| Port  | Service           | Rule                                                             |
| ----- | ----------------- | ---------------------------------------------------------------- |
| 18080 | PM Dashboard      | Active — do not bind anything else here                          |
| 18765 | Miru AI           | Active — do not bind anything else here                          |
| 18766 | MCP Gateway       | Active — do not bind anything else here                          |
| 19100 | Dispatch Listener | Active — do not bind anything else here                          |
| 15678 | n8n               | Active (Docker) — do not bind anything else here                 |
| 8080  | (Reserved)        | RESERVED — do not touch                                          |
| 8765  | (Reserved)        | **NEVER TOUCH under any circumstances**                          |
| 11434 | Ollama            | Local dependency — not Miru-owned; do not restart or reconfigure |

**Why:** Port conflicts cause EADDRINUSE crash loops that are hard to diagnose.

---

## 2. Append-Only Files — NEVER Edit, Truncate, or Read-Modify-Write

These five files in `data/` are strictly append-only. The only allowed operation is
appending a new line. Never overwrite, sort, deduplicate, or truncate them.

| File                           | Purpose                                         |
| ------------------------------ | ----------------------------------------------- |
| `data/cc_completion_log.jsonl` | Task completion markers (tracked in git)        |
| `data/routing_history.jsonl`   | n8n routing decisions (gitignored)              |
| `data/pending_callbacks.jsonl` | Telegram callback ledger (gitignored)           |
| `data/dispatch_dlq.jsonl`      | Dispatch dead-letter queue (gitignored)         |
| `data/cc_heartbeat_log.jsonl`  | Worker heartbeat / liveness signal (gitignored) |

Use `fs.appendFileSync` (Node) or `>>` (shell) or `open(path, "a")` (Python) only.

**Why:** These files are the audit trail. Workers, orchestrator, and sentinel all read
them to understand system state. A truncation or sort destroys history irreversibly.
Pre-commit hooks are configured to exclude them from formatting.

The append-only invariant is enforced by `tests/test_jsonl_append_only_invariant.py`.
If that test starts failing, STOP and escalate — something is breaking the contract.

---

## 3. Telegram Bot Token — One Owner Only

The Telegram bot token has exactly one webhook owner at any time: **n8n**.

- n8n registers a webhook with Telegram and receives all messages via HTTP push.
- Any other code that calls `getUpdates` on the same token will get HTTP 409 Conflict.
- This WILL silently break Telegram message delivery for the entire system.

**Rule:** Never call `getUpdates` or `setWebhook` from sentinel, scripts, worker code,
or any service other than n8n. Route all Telegram command handling through n8n workflows.

This was discovered when sentinel snooze polling was added (PRO-248). The 409 conflict
broke message routing. Snooze state is set via `logs/sentinel_state.json` instead.

---

## 4. Database — Read-Only for Workers

`card_catalog.db` is the live production database. Workers interact with it through
the sqlite-ro-snapshot MCP only (read-only). Direct writes from worker sessions are
**prohibited**.

| What you can do       | How                                  |
| --------------------- | ------------------------------------ |
| Read card data        | Use `sqlite-ro-snapshot` MCP tool    |
| Read schema           | Use `sqlite-ro-snapshot` MCP tool    |
| Propose schema change | Write a proposal for operator review |

**Why:** An accidental write to the live DB corrupts the card catalog with no undo.
Schema changes require a migration, not an in-session write.

`sqlite3` is available at `C:\tools\sqlite3\sqlite3.exe` if you need to inspect the DB
outside of MCP — but still read-only.

---

## 5. Service Code Lives in Service Directories

Code belongs to its service. The file placement rules are non-negotiable.

| Service                        | Directory            |
| ------------------------------ | -------------------- |
| Miru AI backend                | `miru_ai/`           |
| PM Dashboard backend           | `pm/`                |
| Windows operational scripts    | `windows/`           |
| Shared utilities (2+ services) | `shared/`            |
| Tools and standalone scripts   | `tools/`             |
| Tests                          | `tests/`             |
| Documentation                  | `docs/`              |
| Config JSON                    | `config/`            |
| Runtime logs                   | `logs/` (gitignored) |

**Never create service code at the repo root.** Never create temp or debug files at
the repo root. See CLAUDE.md "File Placement — Hard Rules" for the full list.

---

## 6. Health Endpoint Contracts

Health endpoints return specific bodies. Code that checks health must use the correct
path — not a fallback path that happens to return 200.

| Service           | Correct health path | Body shape                                                    |
| ----------------- | ------------------- | ------------------------------------------------------------- |
| Dispatch Listener | `/health`           | `{"status":"ok","listener":"dispatch_listener","port":19100}` |
| MCP Gateway       | `/health`           | `{"ok":true,"version":"...","name":"miru-fs-gateway"}`        |
| Miru AI           | `/api/health`       | `{"status":"ok","app_name":"Miru AI",...}`                    |
| PM Dashboard      | `/__pm_health`      | `{"storefront_built":bool,"path":"..."}`                      |
| n8n               | `/healthz`          | `{"status":"ok"}`                                             |

Miru AI's health path is `/api/health` (not `/health`). PM's path is `/__pm_health`
(not `/health`, not `/api/health`). Using the wrong path gives a 404 or a 200 with
wrong content — both mislead health monitors.

---

## 7. Worker Code Change Ownership

| Worker      | What it owns                                                              |
| ----------- | ------------------------------------------------------------------------- |
| Claude Code | Python backend, tests, verification scripts                               |
| Claude Chat | CLAUDE.md, GEMINI.md, CURSOR.md, CODEX.md, worker prompts, Notion pages   |
| Codex       | Assigned work only — never autonomously edits CLAUDE.md or worker prompts |
| Cursor      | IDE-guided manual edits as directed by operator                           |

Claude Code **must not** touch HTML/CSS/JS templates, `.mcp.json` files, or
`card_catalog.db`. Claude Chat **must not** execute code directly on the server.

When a worker needs to edit another worker's owned files, the operator must explicitly
authorize it for that specific task. The authorization is per-task, not standing.

---

## 8. Git Working Tree — Ends on Main

Every task session ends on `main` with a clean working tree. No exceptions.

- After PR merge: checkout main, pull, delete the task branch (`git branch -d`, not `-D`).
- After an interrupted task: stash or WIP-commit in-progress work, then checkout main.
- Never end a session on a feature branch — the next session starts blind.

Force-push and `git branch -D` (force delete) require explicit operator authorization.

---

## 9. Restart Scripts — Use Canonical Paths Only

| Service           | Restart command                                                                  |
| ----------------- | -------------------------------------------------------------------------------- |
| PM Dashboard      | `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1`                |
| Miru AI           | `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1`           |
| Dispatch Listener | `powershell -ExecutionPolicy Bypass -File windows\restart_dispatch_listener.ps1` |
| MCP Gateway       | `powershell -ExecutionPolicy Bypass -File windows\restart_mcp_gateway.ps1`       |

**Never** use `nssm restart` directly. **Never** create alternate restart scripts.
The canonical scripts trigger SYSTEM-privilege scheduled tasks that handle port cleanup
and process management correctly.

---

## 10. Completion Markers — Required on Every CONFIRMED WORKING Task

When CC completes a task with `STATUS: CONFIRMED WORKING`, it must append one row to
`data/cc_completion_log.jsonl` before reporting completion. Claude Chat reads this file
to verify task completion without relying on chat context alone.

Schema is defined in CLAUDE.md "Completion-marker convention". Use `json.dumps` or
equivalent to ensure valid JSON — never hand-format the line.

This is how the orchestration loop closes. Missing markers break Claude Chat's ability
to verify work is done and transition Linear tickets correctly.
