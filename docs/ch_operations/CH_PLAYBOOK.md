# CH Operations Playbook — Pattern Reference

> Load on demand. Each section is self-contained — jump to the pattern you need.
> For single-tool lookups, use `miru-context/ch-tool-operations.md` (boot context).

---

## Pattern Index

| Pattern                      | Section                               | Entry gate                                           |
| ---------------------------- | ------------------------------------- | ---------------------------------------------------- |
| Ticket verification          | [§1](#1--ticket-verification)         | "Is this ticket actually done?"                      |
| System health check          | [§2](#2--system-health-check)         | "Is the system healthy before I dispatch?"           |
| Dispatch orchestration       | [§3](#3--dispatch-orchestration)      | "Route this ticket to a worker"                      |
| PR lifecycle                 | [§4](#4--pr-lifecycle)                | "A PR was opened / needs review / is ready to merge" |
| Drift detection + correction | [§5](#5--drift-detection--correction) | "Something is out of sync across surfaces"           |
| Sprint standup               | [§6](#6--sprint-standup)              | "What happened since last session?"                  |
| Stall recovery               | [§7](#7--stall-recovery)              | "A worker isn't responding"                          |
| Canon maintenance            | [§8](#8--canon-maintenance)           | "A truth changed and docs need updating"             |

---

## §1 — Ticket Verification

**Entry gate:** Checking whether a completed task actually shipped.

**Building blocks:**

| Step | Tool                     | Input                          | Output                                                | Notes                          |
| ---- | ------------------------ | ------------------------------ | ----------------------------------------------------- | ------------------------------ |
| A    | `get_issue` (Linear MCP) | ticket_id                      | Ticket state, PR number, assignee                     | —                              |
| B    | `github_get_pr`          | pr_number from A               | PR status, merge state, merge SHA                     | Needs output from A            |
| C    | `activity_since`         | minutes (time window)          | Cross-system timeline — scan for ticket_id in results | Independent of A/B             |
| D    | `fs_read_text_file`      | `data/cc_completion_log.jsonl` | Completion markers — grep for ticket_id               | Independent — direct file read |

Steps A→B have a data dependency (B needs A's PR number). Steps C and D are independent — run alongside A.

**Composition example:**

Read the ticket (A) to get its state and PR number. Check the PR (B) for merge status. Read the completion log (D) for a terminal marker. If PR merged + completion marker shows `CONFIRMED_WORKING` + Linear state is Done → verified. If any surface disagrees → use §5 (Drift Detection) to correct.

**Error recovery:**

| Failure                   | Likely cause                              | Recovery                                                                                                                              |
| ------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| No PR number on ticket    | Worker didn't link PR to ticket           | List open PRs via `github_list_open_prs`, then `github_get_pr` on candidates to match `head` branch ref against ticket branch pattern |
| Completion marker missing | Worker forgot to emit                     | Check `system_tail_safe_log` for worker's stdout log                                                                                  |
| Linear state stale        | Worker completed but didn't update Linear | Correct via `save_issue` per drift correction rules                                                                                   |

---

## §2 — System Health Check

**Entry gate:** Confirming the system is ready before dispatch or after an incident.

**Building blocks:**

| Step | Tool                            | Input | Output                         | Notes                     |
| ---- | ------------------------------- | ----- | ------------------------------ | ------------------------- |
| A    | `system_check_ports`            | —     | List of open ports             | —                         |
| B    | `system_check_health_endpoints` | —     | JSON health status per service | Run alongside A           |
| C    | `worker_availability`           | —     | idle / busy / stalled per slot | Independent               |
| D    | `n8n_list_recent_executions`    | —     | Recent execution status list   | Check for failures        |
| E    | `gateway_audit_tail`            | —     | Recent gateway tool calls      | Check for profile denials |

All steps are independent — run A+B+C in parallel, then D+E.

**Composition example:**

Check ports (A) and health endpoints (B) to confirm services are running. Check worker availability (C) to confirm dispatch slots are free. Scan recent n8n executions (D) for failures. Check gateway audit (E) for profile denial patterns. If all green → safe to dispatch. If any service is down → use `service_restart` before proceeding.

**Error recovery:**

| Failure                       | Likely cause         | Recovery                                      |
| ----------------------------- | -------------------- | --------------------------------------------- |
| Port not listening            | Service crashed      | Run `service_restart` with the service name   |
| Health endpoint returns error | Service partially up | Check `system_tail_safe_log` for service logs |
| Worker shows "stalled"        | Heartbeat timeout    | See §7 (Stall Recovery)                       |
| n8n execution failed          | Workflow error       | `n8n_get_execution` for full error details    |

---

## §3 — Dispatch Orchestration

**Entry gate:** Routing a ticket to a worker for execution.

**Building blocks:**

| Step | Tool                  | Input                     | Output                                                                                                          | Notes                   |
| ---- | --------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------------------- |
| A    | `worker_availability` | —                         | Idle status per slot                                                                                            | Must be idle before B   |
| B    | `dispatch_worker`     | worker, prompt, ticket_id | trace_id                                                                                                        | Needs A to confirm idle |
| C    | `activity_since`      | minutes (time window)     | Cross-system timeline — scan for dispatch-related activity (ticket state changes, PR events, log modifications) | Poll every 3–5 min      |

Steps A→B→C form a strict data dependency chain.

**Cross-references:**

- `docs/dispatch_contract.md` — prompt template, authority tiers, HMAC signing
- Phase 4: W2 classifier assigns tool profile automatically; operator can override via Telegram Profile button
- `miru-context/worker-roster.md` — worker selection criteria

**Composition example:**

Check worker availability (A). If idle, dispatch the task (B) with the prompt built from the Linear ticket description. Poll activity_since (C) every 3–5 minutes until a terminal status appears. On `CONFIRMED_WORKING` → verify via §1. On `INCONCLUSIVE` → read the question, answer via Linear comment, consider re-dispatch. On `FAILED` or stall → use §7.

**Error recovery:**

| Failure                 | Likely cause                   | Recovery                                  |
| ----------------------- | ------------------------------ | ----------------------------------------- |
| Worker busy             | Prior task still running       | Wait or check for stall via §7            |
| Dispatch returns error  | Listener down or HMAC mismatch | Check `system_check_ports` for port 19100 |
| No activity after 5 min | Worker stalled or crashed      | Check `worker_status`, then §7            |
| Rate limit hit          | >5 dispatches/minute           | Wait 60s, retry                           |

---

## §4 — PR Lifecycle

**Entry gate:** A PR was opened, needs review, or is ready to merge.

**Building blocks:**

| Step | Tool                            | Input     | Output                       | Notes                |
| ---- | ------------------------------- | --------- | ---------------------------- | -------------------- |
| A    | `github_list_open_prs`          | —         | All open PRs                 | Quick scan           |
| B    | `github_get_pr`                 | pr_number | Full PR details              | For specific PR      |
| C    | `github_get_pr_check_runs`      | pr_number | CI status, review bot status | —                    |
| D    | `github_get_pr_review_comments` | pr_number | Inline review comments       | —                    |
| E    | `github_list_pr_reviews`        | pr_number | Review decisions             | —                    |
| F    | `github_get_pr_diff`            | pr_number | Changed files                | For scope assessment |

Steps B through F all need a PR number but are independent of each other.

**Merge decision:** Apply CLAUDE.md merge policy table. Check: does every file in the diff fall in the CC-merge column? If any file falls in operator-merge → do not self-merge.

**Post-merge:** `github_delete_branch` only after confirming the PR shows as merged.

**Error recovery:**

| Failure                        | Likely cause                    | Recovery                                          |
| ------------------------------ | ------------------------------- | ------------------------------------------------- |
| Check run still pending        | CI not finished                 | Poll `github_get_pr_check_runs` again after 2 min |
| Review shows CHANGES_REQUESTED | Automated reviewer found issues | Read comments, push fix commit, re-poll           |
| Merge conflict                 | Branch diverged from main       | Worker must rebase; if not active, re-dispatch    |

---

## §5 — Drift Detection + Correction

**Entry gate:** A surface disagrees with another about what's true.

**Primary input source:** The automated Drift Scanner (`w-drift-scanner.json`, node `dsw003-classify-drift`) runs daily at 09:00 and writes results to `data/drift_scanner_log.jsonl`. Read the latest scan FIRST — the scanner may have already identified the drift. Do not duplicate dsw003's work by manually re-scanning what it already covers (`missing_marker`, `stale_linear`, `orphan_markers`).

**Building blocks:**

| Step | Tool                     | Input                          | Output                                                | Notes                           |
| ---- | ------------------------ | ------------------------------ | ----------------------------------------------------- | ------------------------------- |
| 0    | `fs_read_text_file`      | `data/drift_scanner_log.jsonl` | Latest scan results                                   | Check before manual query       |
| A    | `get_issue` (Linear MCP) | ticket_id                      | Linear state                                          | Only if scanner didn't cover it |
| B    | `github_get_pr`          | pr_number                      | PR/merge state                                        | —                               |
| C    | `activity_since`         | minutes (time window)          | Cross-system timeline — scan for ticket_id in results | —                               |
| D    | `save_issue`             | Correction payload             | State update                                          | Final corrective act            |

Step 0 runs first. Steps A–C are parallel (no dependency on each other). Step D depends on the comparison result.

**Authority:** Per `claude-operating-model.md` drift correction rules — reversible corrections (state moves, comments, memory writes, doc patches) are autonomous. Irreversible or ambiguous corrections → escalate to operator.

**Composition example:**

Read the drift scanner log (0). If the scanner flagged ticket PRO-XXX as `stale_linear` (Linear shows In Progress but completion marker exists), confirm by reading the ticket (A) and checking recent activity via `activity_since` (C). If confirmed stale → move Linear to Done (D) and add a comment explaining the correction. No operator approval needed — this is reversible.

**Error recovery:**

| Failure                    | Likely cause             | Recovery                                      |
| -------------------------- | ------------------------ | --------------------------------------------- |
| Scanner log empty          | Scanner hasn't run today | Run manual comparison (A+B+C)                 |
| Multiple surfaces disagree | Complex drift            | Escalate to operator with the comparison data |
| State correction rejected  | Wrong state name         | Check `list_issue_statuses` for exact names   |

---

## §6 — Sprint Standup

**Entry gate:** Understanding what happened since the last operator session.

**Building blocks:**

| Step | Tool                         | Input                 | Output                    | Notes |
| ---- | ---------------------------- | --------------------- | ------------------------- | ----- |
| A    | `n8n_list_recent_executions` | —                     | Recent execution list     | —     |
| B    | `n8n_read_routing_history`   | —                     | Recent routing decisions  | —     |
| C    | `activity_since`             | minutes (time window) | Recent completion markers | —     |
| D    | `list_issues` (Linear MCP)   | filter: recent        | Recently updated tickets  | —     |

All steps are independent — run in parallel.

**Composition:** Gather all four data sources. Synthesize into an operator-readable summary: what shipped, what's in progress, what stalled, what's next. Use plain English per `claude-operating-model.md` translation rules.

---

## §7 — Stall Recovery

**Entry gate:** A dispatched worker has gone silent.

**Building blocks:**

| Step | Tool                   | Input                    | Output                                               | Notes                |
| ---- | ---------------------- | ------------------------ | ---------------------------------------------------- | -------------------- |
| A    | `worker_availability`  | —                        | Idle / busy / stalled per slot (reads heartbeat log) | —                    |
| B    | `activity_since`       | minutes (time window)    | Cross-system timeline — check heartbeat staleness    | Needs ticket context |
| C    | `system_tail_safe_log` | Worker stdout/stderr log | Error messages or crash info                         | —                    |

Steps A→B have context dependency. Step C is independent.

**Decision tree:**

- Heartbeat < 5 min old → worker is alive, wait
- Heartbeat > 5 min, no terminal marker → stalled
- Stall + no error in logs → transient stall, re-dispatch once
- Stall + error in logs → read error, decide: fix and re-dispatch, or escalate
- Stall + repeated failure (same ticket >2 times) → escalate to operator

**Cross-references:** `tools/orchestrator/stall_detector.py`, `tools/orchestrator/recovery_router.py`

---

## §8 — Canon Maintenance

**Entry gate:** A truth changed (merged PR, new rule, decommissioned service) and docs need updating.

**Building blocks:**

| Step | Tool                  | Input                      | Output            | Notes                                 |
| ---- | --------------------- | -------------------------- | ----------------- | ------------------------------------- |
| A    | `fs_read_text_file`   | File path                  | Current content   | Identify what needs changing          |
| B    | `docs_patch_file`     | File path + surgical edits | Patched file      | Use for small targeted edits          |
| C    | `docs_write_file`     | File path + full content   | New/replaced file | Use for new sections or full rewrites |
| D    | `git_commit_and_push` | File list + message        | Committed update  | Allowlisted canon files only          |

Steps A→B/C→D form a chain. B and C are alternatives (pick one based on edit scope).

**Authority:** Standing VP Ops authority for factual/maintenance updates. This covers: port changes, service status changes, rule additions from operator directives, tool list updates, worker roster changes. Does NOT cover: architectural decisions, new page structure, scope expansion.

**Error recovery:**

| Failure                      | Likely cause                     | Recovery                                  |
| ---------------------------- | -------------------------------- | ----------------------------------------- |
| `docs_patch_file` fails      | Target text not found            | Read file first (A), verify exact match   |
| `git_commit_and_push` denied | File not in allowlist            | Check CLAUDE.md for allowlisted paths     |
| Conflicting edits            | Another worker touched same file | Check `git_local_status`, resolve or wait |

---

## Error Recovery Index

Aggregated failure modes across all patterns. When something breaks mid-composition, check here first.

| Failure                 | Patterns affected | Likely cause                         | Recovery                                              |
| ----------------------- | ----------------- | ------------------------------------ | ----------------------------------------------------- |
| Tool returns empty/null | All               | Wrong ID, resource doesn't exist     | Verify input with a read/query tool first             |
| Tool returns error      | All               | Service down, auth issue, rate limit | Check `system_check_health_endpoints`                 |
| Timeout                 | §3, §7            | Worker slot busy or crashed          | Check `worker_availability`, then §7                  |
| State name mismatch     | §1, §5            | Linear state names are exact-match   | Use `list_issue_statuses` to get valid names          |
| Profile denial          | §3                | Worker has wrong tool profile        | Check `gateway_audit_tail` for denial log             |
| Append-only violation   | §8                | Tried to edit a JSONL file           | Use `>>` / `appendFileSync` — never read-modify-write |
| File not in allowlist   | §8                | `git_commit_and_push` blocked        | Check CLAUDE.md allowlisted paths                     |
| Rate limit hit          | §3                | >5 dispatches/minute                 | Wait 60s, retry                                       |
| Stale data              | §2, §6            | Cached/old execution results         | Use `n8n_list_recent_executions` with fresh query     |

---

## Tool Combination Quick Reference

| "I need to..."                             | Use this pattern            |
| ------------------------------------------ | --------------------------- |
| Verify a ticket is done                    | §1 — Ticket Verification    |
| Check system before dispatching            | §2 → §3                     |
| Dispatch and monitor a worker              | §3 — Dispatch Orchestration |
| Review a PR and decide on merge            | §4 — PR Lifecycle           |
| Fix a mismatch between Linear and GitHub   | §5 — Drift Detection        |
| Brief the operator on recent activity      | §6 — Sprint Standup         |
| Recover from a silent worker               | §7 — Stall Recovery         |
| Update docs after a truth changed          | §8 — Canon Maintenance      |
| Dispatch → verify → clean up               | §3 → §1 → §4 (post-merge)   |
| Health check → dispatch → monitor → verify | §2 → §3 → §1                |
| Detect drift → correct → update canon      | §5 → §8                     |
