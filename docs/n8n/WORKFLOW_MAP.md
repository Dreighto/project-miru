# Project Miru — n8n Workflow Map

> **Workers: read this before touching any n8n workflow or writing automation code.**
> Last updated: 2026-04-27. Refresh via `n8n_list_workflows` + `n8n_get_workflow_summary` MCP tools.

## n8n Instance

- URL: `http://localhost:15678`
- Workflow JSON sources: `docker/n8n/workflows/`
- Deploy script: `docker/n8n/scripts/` → `deploy-workflow.ps1`
- Credentials wired in n8n UI (not in repo). Never hardcode.

---

## Active Workflows

### W1 — Planning Intake → Task Draft Sync

| Field       | Value                                                                                                                                                                                                     |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `tFEbP14EnGQ69YZn`                                                                                                                                                                                        |
| JSON        | `docker/n8n/workflows/w1-planning-intake.json`                                                                                                                                                            |
| Trigger     | Notion database change OR incoming webhook                                                                                                                                                                |
| Node count  | 16 (notionTrigger×1, webhook×1, set×2, notion×1, code×2, httpRequest×5, if×1, telegram×3)                                                                                                                 |
| Purpose     | Ingests planning intent from Notion, normalizes it, creates a Linear issue draft, back-links the Linear URL into the source Notion page, and sends a Telegram notification. Entry point for all new work. |
| Key pattern | Dual trigger (Notion native + webhook fallback). Linear issue created via GraphQL `issueCreate`; Notion back-link via `PATCH /v1/blocks/{id}/children`. Telegram sends confirmation to operator channel.  |
| Test doc    | `docker/n8n/workflows/W1_TEST.md`                                                                                                                                                                         |

### W1 — Error Handler

| Field       | Value                                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| ID          | `l5wzFuWnJ2zSoMM2`                                                                                     |
| JSON        | `docker/n8n/workflows/w1-error-handler.json`                                                           |
| Trigger     | `errorTrigger` (catches W1 failures)                                                                   |
| Node count  | 6 (errorTrigger×1, code×2, httpRequest×2, telegram×1)                                                  |
| Purpose     | Catches unhandled errors from W1. Formats an error summary and sends a Telegram alert to the operator. |
| Key pattern | Linked error handler — must be set as W1's error workflow in n8n settings. Never fires on its own.     |

### W2 — Worker Selection Router

| Field       | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `6aCG6L5Z4VvqWogq`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| JSON        | `docker/n8n/workflows/w2_worker_selection_router.json`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Trigger     | Schedule (every 3 minutes) + manual webhook                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Node count  | 30 (scheduleTrigger×1, webhook×1, httpRequest×7, splitOut×1, set×2, code×12, if×3, telegram×3)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Purpose     | Core routing brain. Polls Linear every 3 minutes for issues whose state is `Todo` (Backlog tickets are invisible to the poll — see Known Issues). Two poll branches: an **unlabeled-poll branch** for tickets with no worker label, and a **labeled-poll branch** for tickets that already have one. The unlabeled branch runs rule-based keyword scoring (12 Code nodes against `/miru-data/config/w2_routing_rules.json`) to pick a worker + confidence + risk, then emits a Telegram approval request with inline keyboard buttons. **The labeled-poll branch is currently broken (PRO-153)** — its GraphQL filter returns zero candidates regardless of state. LLM-based routing is planned post-PRO-84 in shadow mode; current implementation is deterministic. |
| Key pattern | Deterministic scoring inside Code nodes (`w2007-score-workers` → `w2008-classify-risk` → `w2009-confidence-branch` at 0.75 threshold). SplitOut fans tasks across parallel branches. Inline keyboards sent via raw Telegram Bot API HTTP (not the n8n Telegram node — see N8N_SKILL.md §2). Approval response handled downstream by W7. **Note:** approval does NOT currently write the worker label to the issue (PRO-157), so tickets that go through unlabeled-poll → approve fail dispatch on first attempt.                                                                                                                                                                                                                                                     |

### W2 — Pending-Approval Watchdog

| Field       | Value                                                                                                                                                                                                                                                                                                                                   |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `9hRoVyMWkbi0Wba5`                                                                                                                                                                                                                                                                                                                      |
| JSON        | `docker/n8n/workflows/w2_pending_approval_watchdog.json`                                                                                                                                                                                                                                                                                |
| Trigger     | Schedule (cron `0 8,20 * * *` — twice daily at 08:00 and 20:00)                                                                                                                                                                                                                                                                         |
| Node count  | 7 (scheduleTrigger×1, httpRequest×3, splitOut×1, code×2)                                                                                                                                                                                                                                                                                |
| Purpose     | Queries Linear for issues with `pending-approval` label whose `updatedAt` is older than 24 hours. For each stale issue: mints a fresh callback token + intent row and sends a new Telegram approval nudge. Prevents silent approval rot. (Frequency increase to hourly with 6-hour staleness window is planned — see follow-up ticket.) |
| Key pattern | Linear-driven poll (no Dispatcher dependency). Each nudge mints a new token via the same `w2012a-mint-callback-token` pattern as the W2 router; W7 owns idempotency per token via the `decided` row in `pending_callbacks.jsonl`.                                                                                                       |

### W7 — Telegram Callback Handler

| Field       | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `rJiLlMFKQh8t4Y9K`                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| JSON        | `docker/n8n/workflows/w7-telegram-callback-handler.json`                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Trigger     | `telegramTrigger` (receives callback_query events)                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Node count  | 29 (telegramTrigger×1, telegram×1, code×10, if×7, noOp×3, httpRequest×7)                                                                                                                                                                                                                                                                                                                                                                                                   |
| Purpose     | Receives Telegram inline keyboard responses. Validates HMAC signature, checks replay window (10 min), deduplicates via nonce, then applies the chosen action directly to Linear via GraphQL mutation (issueUpdate + commentCreate). Approve adds the proposed worker label and drops `pending-approval` (PRO-157), Triage adds `triage`, Request Revision adds `manual-intervention-required`. Must always call `answerCallbackQuery` to clear Telegram's loading spinner. |
| Key pattern | 61-byte callback_data encoding (token + action + nonce + timestamp + HMAC). Heavy use of If nodes for multi-gate security validation. noOp nodes mark terminal rejection paths.                                                                                                                                                                                                                                                                                            |

### CC Completion Ping

| Field       | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `UCM67hqZR74Fz8US`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| JSON        | `docker/n8n/workflows/w-cc-completion-ping.json`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Trigger     | Schedule (short poll interval)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Node count  | 10 (scheduleTrigger×1, code×3, if×2, httpRequest×2, splitOut×1, noOp×1)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Purpose     | Polls `data/cc_completion_log.jsonl` for new CC completion markers. When a new CONFIRMED_WORKING / INCONCLUSIVE / FAILED row appears, sends a Telegram notification directly to the operator chat (via raw `https://api.telegram.org/.../sendMessage`) so the operator can verify without manually relaying CC's chat report.                                                                                                                                                                                                                                                                  |
| Key pattern | Stateful poll — tracks last-seen line count or timestamp to avoid re-firing on already-seen rows. **This pattern is fragile (PRO-160):** any rewrite, truncate, or atomic-rename over `cc_completion_log.jsonl` causes the watcher to re-ping every remaining row, generating false-positive completion storms. Planned fix: line-hash or stable-offset-with-checksum tracking persisted to a sidecar. **Companion guard:** an append-only invariant guard monitors row-count regressions on the same file and alerts on any non-append modification (PRO-159 fired this guard on 2026-04-27). |

---

## Inactive Workflows (do not activate without operator decision)

| Name                       | ID                 | Notes                                                       |
| -------------------------- | ------------------ | ----------------------------------------------------------- |
| My workflow                | `iTCz0gZGlBfhtg8l` | Scratch/test — no defined purpose                           |
| SPIKE ntfy approval UX     | `j7mEysTjoAoplpan` | Spike for ntfy.sh approval channel — superseded by Telegram |
| SPIKE Telegram approval UX | `vcvebHduwAKpezkL` | Earlier Telegram spike — superseded by W7                   |

---

## Workflow Interconnections

```
Notion change / webhook
        │
        ▼
   W1 Planning Intake ──(error)──► W1 Error Handler ──► Telegram alert
        │
        ▼ (Linear issueCreate + Notion back-link)
   Linear issue draft
        │
        ▼ (W2 polls Linear every 3 min)
   W2 Worker Selection Router ──► Telegram inline keyboard ──► operator
        │                                                          │
        ▼ (schedule poll, twice daily)                             │ callback_query
   W2 Watchdog (re-nudges stale approvals via Linear)              │
                                                                   ▼
                                                   W7 Telegram Callback Handler
                                                           │
                                                           ▼ (Linear issueUpdate + commentCreate)
                                                   Labels applied / removed

CC Completion Ping (independent poll) ──► Telegram (operator chat)
```

---

## State Files (written directly by n8n Code nodes via `fs.appendFileSync` to `/miru-data/*.jsonl`)

| File                           | Purpose                                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `data/pending_callbacks.jsonl` | In-flight approval tokens awaiting operator response (written by W2 router, W2 watchdog, W7)          |
| `data/routing_history.jsonl`   | Immutable log of all routing decisions (written by W2 router, W7, W2 router-failure handler)          |
| `data/cc_completion_log.jsonl` | CC task completion markers (written by Claude Code outside n8n; read by CC Completion Ping)           |
| `data/dispatch_dlq.jsonl`      | Dead-letter queue for failed dispatches (written by future dispatch listener; not yet wired into n8n) |

> **Architectural goal:** all state file writes will eventually move behind a Dispatcher API for centralized auditing and locking. This is future work — current n8n workflows write directly. See N8N_SKILL.md §5 for the documented exception class.

---

## Hard Rules for All Workers

1. **State file writes go through the existing Code-node pattern** (`fs.appendFileSync` to `/miru-data/*.jsonl`). Do not invent new state files or new write paths without operator approval. The long-term migration to a Dispatcher API is tracked separately; do not attempt the migration as a side effect of other work.
2. **W7 must always call `answerCallbackQuery`** — even on rejection paths, or Telegram shows infinite spinner.
3. **Never activate inactive workflows** without operator decision.
4. **Deploy via script** — `deploy-workflow.ps1`, never paste JSON directly in the n8n UI for production workflows.
5. **Credentials live in the n8n UI** — never commit credential values to the repo.
6. **Workflow JSON in repo is canonical** — if live n8n diverges from repo JSON, the repo is truth; resync via deploy script.

---

## Known Issues (open bugs affecting the loop)

> Workers: read this list before designing or modifying anything in the n8n loop. Several active bugs change how the loop behaves vs. how the rest of this document describes it.

### PRO-153 — W2 GraphQL filter-miss in labeled-poll branch (deferred)

W2 has two poll branches: an **unlabeled-poll branch** (picks up tickets with no worker label) and a **labeled-poll branch** (handles tickets that already have a worker label). The labeled-poll branch's GraphQL filter is broken — it returns zero candidates even when matching tickets exist. Tickets with a worker label currently fall into a black hole unless the operator manually intervenes. **Workaround:** if a ticket needs the routing loop, it must enter without a worker label.

### PRO-157 — W7 doesn't write worker label on approval (URGENT)

When the operator taps Approve on a Telegram routing proposal, W7 logs the decision and generates a dispatch token, but **does NOT write the proposed worker label to the Linear issue**. W4 dispatch then validates that the worker label is present on the issue and aborts with `worker label "X" not in issue labels [...]`. Result: every ticket that goes through the unlabeled-poll branch fails dispatch on first approval. Manual workaround: operator (or Claude Chat via Linear MCP) adds the label before retry. Once labeled, the ticket falls into PRO-153's broken branch — double bug.

### PRO-159 — cc_completion_log.jsonl append-only invariant violated (URGENT)

A guard exists that monitors `data/cc_completion_log.jsonl` for row-count regressions (it should be append-only). The guard fired during PRO-156's import on 2026-04-27 (24 → 22 rows). Root cause not yet identified — something in the PRO-156 PR's import or verification steps wrote to / truncated / rewrote the file. The guard is doing its job; the violator is unknown.

### PRO-160 — CC Completion Ping watcher not idempotent (HIGH)

The CC Completion Ping watcher (workflow `UCM67hqZR74Fz8US`) tracks "new" markers via last-seen line count or modification time. When the JSONL file is rewritten or regressed, the watcher re-pings every remaining marker — including markers from completed-days-ago tickets and historical test markers. Symptom: false-positive Telegram completion storm (~15 pings in seconds) when the JSONL is touched in any non-pure-append way. Fix is to use line-hash or stable-offset-with-checksum tracking persisted to a sidecar.

### PRO-126 — W7 mutation_body_obj earlier bug (open)

Earlier W7 bug related to how the Linear GraphQL mutation body is constructed. Diagnostic context captured in the ticket; root cause may share lineage with PRO-157.

### Backlog → Todo state filter behavior (observed 2026-04-27, may overlap with PRO-153)

W2's Linear GraphQL query filters for `state: Todo` (or stricter). Tickets in `Backlog` are invisible to the poll. Tonight's PRO-156 spent ~8 minutes invisible to W2 because it was filed in Backlog by default. **Operator workflow implication:** every loop-routable ticket must be moved from Backlog to Todo before W2's next poll.

---

## Operational notes from 2026-04-27 session

- **Append-only files in `data/`** (`cc_completion_log.jsonl`, `routing_history.jsonl`, `pending_callbacks.jsonl`, `dispatch_dlq.jsonl`) are guarded for invariant violations. Any write path that rewrites, truncates, deduplicates, or atomic-renames over these files will trigger the guard. Treat them as strictly append-only via `fs.appendFileSync`.
- **Memory layer (PRO-156) shipped 2026-04-27.** SQLite at `data/miru_memory.db`, 6 tables (`routing_decisions`, `agenda`, `decisions`, `worker_perf`, `stack_state`, `peer_review`), accessed via `mcp-server-sqlite` through the MCP gateway. JSONL writers in n8n still write to JSONL — migration to memory DB is a future ticket. Workers reading memory should query the SQLite DB; workers writing routing decisions still go through JSONL until parity is verified.
- **W4 Dispatch Listener (PRO-83)** is live on host port 19100, HMAC-gated, spawning Claude Code / Codex / Gemini CLIs as detached children. Workflow id `TwRAHqoZqNhGRHKo`. This workflow is NOT yet documented in the Active Workflows section above — drift to fix in a follow-up.
