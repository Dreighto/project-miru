# Miru Protected Constraints

Non-negotiable architectural constraints every coding worker must know before touching
any Miru service. These are not preferences — violating them causes production failures.

Last updated: 2026-05-17

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

The orchestration append-only chains (12 files: `cc_completion_log.jsonl`,
`routing_history.jsonl`, `pending_callbacks.jsonl`, `dispatch_dlq.jsonl`,
`cc_heartbeat_log.jsonl`, `vp_ops_supervision.jsonl`, `drift_scanner_log.jsonl`,
`agent_decisions.jsonl`, `github_resource_ledger.jsonl`, `usage_anomalies.jsonl`,
`salvage_reports.jsonl`, `hermes_predictions.jsonl`) **live in the orchestrator**
at `D:\dev\LogueOS-Orchestrator\data\` since Migration Phase 3 (LOS-55, 2026-05-14).
They are NOT stored in this repo. The kernel canon at
`D:\dev\LogueOS-Orchestrator\CLAUDE.md` ("Append-Only Data Files") is authoritative.

The only append-only file that remains in this repo's `data/` directory:

| File                          | Purpose                                                                  |
| ----------------------------- | ------------------------------------------------------------------------ |
| `data/miru_worker_runs.jsonl` | Miru-product worker run records (tracked in git, governed by miru tests) |

**Why this matters:** workers dispatched into a miru worktree call the local
`tools/emit_completion.py` / `tools/emit_heartbeat.py` helpers — the listener
sets `LOGUEOS_DATA_DIR` so every helper resolves the canonical orchestrator
path automatically. Use the helpers; never hand-roll the append. Truncation
or sort destroys history irreversibly. Pre-commit hooks exclude all `*.jsonl`
files from formatting.

The append-only invariant for `miru_worker_runs.jsonl` is enforced by
`tests/test_jsonl_append_only_invariant.py` in this repo. The orchestrator's
12 chains are enforced by its own equivalent test. If either test starts
failing, STOP and escalate — something is breaking the contract.

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

## 4. Database — Write Discipline for Workers

`card_catalog.db` is the live production database. Direct writes from worker
sessions ARE in scope (operator-set 2026-05-17) when the work requires them
— set population (OP01–OP15), provenance backfills, meta-relevancy / insight
columns, image-asset linkage. The earlier never-touch rule was situational to
the schema-setup-and-initial-population phase and is no longer in force.

| What you can do                             | How                                                       |
| ------------------------------------------- | --------------------------------------------------------- |
| Read card data (any worker, any time)       | `sqlite-ro-snapshot` MCP tool                             |
| Read schema                                 | `sqlite-ro-snapshot` MCP tool                             |
| Write rows (CC, for in-scope work)          | Direct `sqlite3` after backup + change log (see below)    |
| ALTER / CREATE / DROP TABLE (schema change) | STOP. Operator approval required. Write a proposal first. |

### Required discipline before any write

Every UPDATE / INSERT / DELETE batch MUST:

1. **Backup first:**
   `cp data/card_catalog.db data/card_catalog.db.bak.<YYYYMMDD_HHMMSS>`
2. **Log the change** to a `data/*.log` file (what rows, what columns, what
   query, why).
3. **Surface the diff in commit messages** — say what changed and how to
   verify it.
4. **Verify after** — read the rows back via `sqlite-ro-snapshot` to
   confirm the write landed as intended.

Schema changes (ALTER TABLE, CREATE TABLE, DROP TABLE, new indexes affecting
query plans) remain operator-approval-only. Propose, don't execute.

**Why:** Data writes have real impact on production — but blocking them
entirely blocks set-population and provenance work that the operator has
prioritized. The backup + log + verify pattern gives the same audit trail
ALTER would, without gating every row update on operator approval.

`sqlite3` is available at `C:\tools\sqlite3\sqlite3.exe` for direct CLI work.
See `D:\dev\LogueOS-Orchestrator\.logueos\reference\database-rules.md` for
the kernel-level rules that apply to all SQLite DBs in this stack.

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
