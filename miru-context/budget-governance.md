# Budget Governance

How the system behaves under cost pressure. Autonomous work must be cost-aware.
Spending without tracking is not autonomy — it's debt.

Budget state is tracked in `data/budget_state.json` (simple flag file, initial
implementation). The source of truth for budget state is defined in source-of-truth.md.

Last updated: 2026-05-01

---

## 1. Budget States

Three states govern system behavior. Claude Chat evaluates budget state before dispatching
any worker and adjusts behavior accordingly.

| State     | Trigger                                                  | System behavior                                                                                                 |
| --------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **Safe**  | Normal — no pressure                                     | All work allowed; model and effort selected for quality                                                         |
| **Watch** | Approaching budget limit or operator sets Watch manually | Prefer cheaper models; reduce retries on non-critical tasks; avoid dispatching speculative or low-priority work |
| **Limit** | At or over budget limit, or operator sets Limit manually | Require operator approval before any new dispatch; critical tasks only (service outages, security); no retries  |

**Default:** Safe. The system operates in Safe state unless `data/budget_state.json`
specifies otherwise or the operator says otherwise via Telegram.

---

## 2. Model and Effort Selection

Choose the cheapest model that is capable of the task. Do not default to the most
powerful model for every job.

| Task type                                              | Safe state                            | Watch state                    | Limit state                  |
| ------------------------------------------------------ | ------------------------------------- | ------------------------------ | ---------------------------- |
| Deep code audit, multi-file refactor, complex analysis | Sonnet (or Codex for static analysis) | Codex (no API cost)            | Hold — file ticket for later |
| Single-file fix, doc update, config change             | Sonnet                                | Haiku or Codex                 | Hold                         |
| Research, web search                                   | Perplexity MCP                        | Perplexity MCP (lower context) | No new research              |
| Routing decision, quick analysis                       | Haiku                                 | Haiku                          | Haiku only for critical path |
| Ollama (local)                                         | Any assigned model                    | Prefer cheaper local model     | Ollama only                  |

**Effort level:** Use extended-thinking / high reasoning only when the task requires it
(architecture decisions, complex debugging). Routine tasks do not need high effort.

---

## 3. Behavior by State

### Safe

- All tasks may be dispatched.
- Model and effort selected for task quality.
- Normal retry policy applies (see retry-backoff.md).
- Normal parallel concurrency (up to 2–3 workers, per concurrency-policy.md).

### Watch

- Non-critical low-priority tickets (backlog items with no urgency) are held — do not dispatch.
- Reduce parallel workers to 1 if budgeting is tight.
- Prefer Codex (no API cost) or Haiku over Sonnet where capable.
- No retries on non-critical tasks (save budget for first-attempt success).
- Alert operator once when Watch is entered: "Budget entering Watch state — [current spend vs. limit]."

### Limit

- No autonomous dispatch without explicit operator approval per ticket.
- Only critical work: active service outages, security findings, operator-requested tasks.
- No retries under any circumstances.
- Send one Telegram ping when Limit is entered: "Budget at Limit — autonomous dispatch paused. Operator approval required per task."
- Claude Chat remains active for routing and synthesis; it does not dispatch workers.

---

## 4. Budget State File

`data/budget_state.json` is a simple flag file. The system reads it before dispatch.

```json
{
  "state": "safe",
  "updated_at": "2026-05-01T00:00:00Z",
  "note": "operator-set" | "auto-detected" | null
}
```

**state**: `"safe"` | `"watch"` | `"limit"`

This file is NOT append-only. It is a presence/state flag — overwrite with the current
state. Do not treat it like the audit JSONL files. Do not create it during this sprint —
the contract is defined here; implementation is a separate task.

---

## 5. Cost Buckets by Worker Type

Rough cost guidance. Exact values depend on model, prompt length, and execution time.

| Worker                        | Dispatch mode | Cost bucket         | Notes                                              |
| ----------------------------- | ------------- | ------------------- | -------------------------------------------------- |
| Claude Code (claude-code CLI) | Headless CLI  | Medium              | API-billed; scales with task complexity            |
| Codex                         | Headless CLI  | Low-Medium          | API-billed but often faster/cheaper for analysis   |
| Gemini CLI                    | Headless CLI  | Low                 | Gemini Pro model; cost-effective for large context |
| Cursor                        | IDE manual    | None (subscription) | Cursor Pro subscription — no per-task API cost     |
| Ollama                        | Local         | None                | Fully local; compute cost only                     |
| Perplexity MCP                | API tool      | Low                 | Per-call cost; low per query                       |

When budget is in Watch or Limit, prefer workers in the Low cost bucket.

---

## 6. "Autonomous Work Must Be Cost-Aware"

This principle means:

- **Before dispatching**: check budget state. Do not dispatch if Limit.
- **Model selection**: choose capable-but-cheaper, not just capable.
- **Retry discipline**: blind retries waste budget. Diagnose before retrying (see retry-backoff.md).
- **Parallel work**: fewer concurrent workers = lower spend rate during Watch/Limit.
- **Speculative work**: during Watch/Limit, do not dispatch exploratory or nice-to-have tasks — only work that needs to happen now.
