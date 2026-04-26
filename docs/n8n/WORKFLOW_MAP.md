# Project Miru — n8n Workflow Map

> **Workers: read this before touching any n8n workflow or writing automation code.**
> Last updated: 2026-04-26. Refresh via `n8n_list_workflows` + `n8n_get_workflow_summary` MCP tools.

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

| Field       | Value                                                                                                                                                                                                                                                                                                                                                                                |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ID          | `6aCG6L5Z4VvqWogq`                                                                                                                                                                                                                                                                                                                                                                   |
| JSON        | `docker/n8n/workflows/w2_worker_selection_router.json`                                                                                                                                                                                                                                                                                                                               |
| Trigger     | Schedule (every 3 minutes) + manual webhook                                                                                                                                                                                                                                                                                                                                          |
| Node count  | 30 (scheduleTrigger×1, webhook×1, httpRequest×7, splitOut×1, set×2, code×12, if×3, telegram×3)                                                                                                                                                                                                                                                                                       |
| Purpose     | Core routing brain. Polls Linear every 3 minutes for unassigned issues, runs rule-based keyword scoring (12 Code nodes against `/miru-data/config/w2_routing_rules.json`) to pick a worker + confidence + risk, and emits Telegram approval requests with inline keyboard buttons. LLM-based routing is planned post-PRO-84 in shadow mode; current implementation is deterministic. |
| Key pattern | Deterministic scoring inside Code nodes (`w2007-score-workers` → `w2008-classify-risk` → `w2009-confidence-branch` at 0.75 threshold). SplitOut fans tasks across parallel branches. Inline keyboards sent via raw Telegram Bot API HTTP (not the n8n Telegram node — see N8N_SKILL.md §2). Approval response handled downstream by W7.                                              |

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

| Field       | Value                                                                                                                                                                                                                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `rJiLlMFKQh8t4Y9K`                                                                                                                                                                                                                                                                                                                    |
| JSON        | `docker/n8n/workflows/w7-telegram-callback-handler.json`                                                                                                                                                                                                                                                                              |
| Trigger     | `telegramTrigger` (receives callback_query events)                                                                                                                                                                                                                                                                                    |
| Node count  | 29 (telegramTrigger×1, telegram×1, code×10, if×7, noOp×3, httpRequest×7)                                                                                                                                                                                                                                                              |
| Purpose     | Receives Telegram inline keyboard responses. Validates HMAC signature, checks replay window (10 min), deduplicates via nonce, then applies the approved / rejected / triage action directly to Linear via GraphQL mutation (issueUpdate + commentCreate). Must always call `answerCallbackQuery` to clear Telegram's loading spinner. |
| Key pattern | 61-byte callback_data encoding (token + action + nonce + timestamp + HMAC). Heavy use of If nodes for multi-gate security validation. noOp nodes mark terminal rejection paths.                                                                                                                                                       |

### CC Completion Ping

| Field       | Value                                                                                                                                                                                                                                                                                                                         |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ID          | `UCM67hqZR74Fz8US`                                                                                                                                                                                                                                                                                                            |
| JSON        | `docker/n8n/workflows/w-cc-completion-ping.json`                                                                                                                                                                                                                                                                              |
| Trigger     | Schedule (short poll interval)                                                                                                                                                                                                                                                                                                |
| Node count  | 10 (scheduleTrigger×1, code×3, if×2, httpRequest×2, splitOut×1, noOp×1)                                                                                                                                                                                                                                                       |
| Purpose     | Polls `data/cc_completion_log.jsonl` for new CC completion markers. When a new CONFIRMED_WORKING / INCONCLUSIVE / FAILED row appears, sends a Telegram notification directly to the operator chat (via raw `https://api.telegram.org/.../sendMessage`) so the operator can verify without manually relaying CC's chat report. |
| Key pattern | Stateful poll — tracks last-seen line count or timestamp to avoid re-firing on already-seen rows.                                                                                                                                                                                                                             |

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
