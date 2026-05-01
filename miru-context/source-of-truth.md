# Source-of-Truth Matrix

Which system is authoritative for each type of state. When two systems disagree, this
table tells you which one wins — and what to do about the loser.

For the narrative hierarchy and drift detection rules, see canon-and-drift.md.
For how knowledge flows into Notion (promotion, deduplication), see canon-contract.md.
This document provides the quick-reference matrix.

Last updated: 2026-05-01

---

## Authority Table

| State / Truth type                                              | Source of Truth                     | Tiebreaker rule                                                                                             |
| --------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Task status (what is being worked on)                           | **Linear**                          | If memory or chat says different, update memory to match Linear                                             |
| Execution trail (what was tried, commented, decided per-ticket) | **Linear**                          | Not promoted to Notion unless it passes the promotion test                                                  |
| Durable system canon (architecture, patterns, hard-won lessons) | **Notion**                          | If Linear comment contradicts Notion, promote only after validation                                         |
| Runtime service status                                          | **Health endpoints / Sentinel**     | Log output is supporting evidence; the health endpoint is the verdict                                       |
| Raw logs                                                        | **Filesystem (logs/ via gateway)**  | Logs are evidence, not decisions — read them, don't promote them                                            |
| Worker role and capability                                      | **worker-roster.md**                | If CLAUDE.md and worker-roster.md disagree, CLAUDE.md wins (it's higher in the repo doc hierarchy)          |
| Budget state                                                    | **data/budget_state.json** (future) | Until implemented: operator's last stated budget direction                                                  |
| Memory recall (decisions, agenda, routing history)              | **miru_memory.db**                  | If memory contradicts Notion, Notion wins — then update memory                                              |
| Code truth (what the service actually does)                     | **repo main branch**                | A PR branch is a proposal; main is what's deployed                                                          |
| Active task ownership                                           | **Linear (In Progress state)**      | If worktree_registry disagrees, Linear is the human-readable truth; worktree_registry is the technical lock |

---

## Conflict Resolution Rules

### Linear says Done, but completion marker is missing

Linear is wrong. A ticket moved to Done without a completion marker means someone closed
the ticket manually without going through the proper completion flow. Actions:

1. Check the PR — was it merged? If yes, write the missing completion marker retroactively.
2. If no PR and no merge: reopen the ticket, re-confirm the work is actually done.
3. If work was done but undocumented: write the marker, note the retroactive fill in the `notes` field.

### Notion says X, but code says Y

Code wins for what the service currently does. Notion may be stale. Actions:

1. Read the code to confirm the current behavior.
2. If the code is intentionally different from Notion (a change was made and not documented): update Notion to match code.
3. If the code is wrong (Notion describes the intended behavior, but someone broke it): that's a bug — file a ticket.

### Memory says a ticket is stuck, but Linear shows it moved

Linear wins. Memory is stale. Actions:

1. Update `stack_state` in miru_memory.db to reflect Linear's current state.
2. If the memory entry was load-bearing (other decisions referenced it): note the correction in Project Memory's decisions log.

### Two workers claim ownership of the same file

Linear wins. Whichever ticket is In Progress for that file owns the lock. Actions:

1. Check both tickets' In Progress state.
2. The one that reached In Progress first has priority.
3. The second worker pauses, emits `BLOCKED_ON: <first ticket>`, and waits.

### Health endpoint says service is up, but logs show errors

Health endpoint wins for routing decisions (is the service usable?). Logs win for
diagnosis (what is wrong and why). Actions:

1. If health endpoint returns expected 200 + correct body: service is up. Log errors may be inert (check against miru-service-catalog.md for normal vs. failure patterns).
2. If log errors match a failure pattern AND health endpoint is degraded: service is down. Escalate.

---

## Who Writes What

| Surface                      | Who writes                                                     | Who reads                     |
| ---------------------------- | -------------------------------------------------------------- | ----------------------------- |
| Linear ticket state          | Claude Chat (state transitions) + workers (stall signals)      | Everyone                      |
| Linear ticket description    | Ticket creator (operator or Claude Chat)                       | Workers                       |
| Notion canon                 | Claude Chat (default) + Claude Code (when operator-authorized) | Everyone                      |
| repo main                    | Workers via PR + merge                                         | Everyone                      |
| miru_memory.db               | Claude Chat                                                    | Claude Chat, Claude Code      |
| data/cc_completion_log.jsonl | Workers (append only)                                          | Claude Chat, Claude Code      |
| data/cc_heartbeat_log.jsonl  | Workers (append only)                                          | Claude Code (stall detection) |
| Health endpoints             | Services (self-reported)                                       | Sentinel, Claude Code         |
| worker-roster.md             | Claude Chat (when operator-authorized)                         | All workers                   |
| CLAUDE.md                    | Claude Chat (when operator-authorized)                         | All workers                   |
