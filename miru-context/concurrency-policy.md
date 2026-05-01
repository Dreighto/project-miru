# Concurrency Policy — Parallel Execution Rules

> For behavioral coordination rules (task ownership, status reporting, worker-to-worker
> handoffs, help requests), see **coordination-contract.md**. This document covers
> parallelism limits and file conflict rules.

This is a secondary file. Claude reads it when 2+ workers are active simultaneously or when a routing decision involves parallel execution. It does not need to be read at the start of every thread.

## Governing Rule

Keep parallel work small unless the system is proven stable and the operator says to expand.

Claude should never raise concurrency on its own just to be helpful or to move faster. More workers running at once means more chances for conflicts, drift, and wasted work. The operator decides when to scale up — Claude's job is to keep things safe and controlled at whatever level is currently approved.

---

## Current Limit: 2–3 Concurrent Workers (Hard Cap)

The system is approved for a maximum of 2–3 active workers at the same time. This is a hard limit, not a suggestion.

**Why this limit:** The routing loop, drift detection, and merge safety have been proven with single-worker dispatch. Running more workers in parallel multiplies the chance of conflicts, and we don't yet have the tooling to catch those conflicts automatically. The limit stays until we do.

**When in doubt, keep it to 1.** If Claude isn't sure whether two tasks can safely run at the same time, run them one at a time. Sequential is always safer than parallel.

---

## Future Expansion: 6–7 Workers (Behind Decision Gate)

Expanding to 6–7 concurrent workers is a future goal, not a current capability. It requires all three of these conditions:

1. Proven stability at 2–3 for at least 2 weeks with no merge conflicts or drift incidents
2. Automated shared-state checks (not just Claude watching manually)
3. Explicit operator decision to advance — Claude does not advocate for expansion

Until the operator opens this gate, treat 6–7 as a design target that influences how we build things, not as something we actually do. Claude should never suggest raising the limit or frame it as a bottleneck. The limit is there because the system hasn't earned more yet.

---

## What Can Run in Parallel

| Work type                                                                 | Parallel OK? | Why                                                               |
| ------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------- |
| Two workers on different files in different parts of the repo             | ✅           | No overlap, low conflict risk                                     |
| Research tasks (Perplexity, web search) alongside code work               | ✅           | Read-only, doesn't touch the repo                                 |
| Codex analysis while Claude Code is executing a different ticket          | ✅           | Different roles, different scope                                  |
| Two workers editing the same file                                         | ❌           | Merge conflict guaranteed                                         |
| Two workers editing different files that import from each other           | ⚠️ Ask first | Interface changes can break the other worker's assumptions        |
| Canon updates (Notion, repo docs) while a worker is relying on that canon | ❌           | Worker reads stale state, produces wrong output                   |
| Two n8n workflow changes at the same time                                 | ❌           | Workflow JSON is a single file per workflow — conflicts are messy |

**Default when uncertain:** Run it sequentially. Ask the operator if you think parallelism would help — don't just do it.

---

## Shared State Protection

Only one worker should touch a given file or shared surface at a time. When multiple workers are active, these resources are single-writer:

- **Any single file in the repo** — only one worker touches it at a time
- **Linear ticket state** — only Claude Chat or the loop moves states. Workers don't move their own tickets.
- **Notion canon pages** — only one editor at a time (Claude Chat for small edits, Claude Code for big ones)
- **Project Memory** — Claude Chat is the only writer. Workers don't write to miru_memory.db.
- **Append-only files** (routing_history.jsonl, cc_completion_log.jsonl, dispatch_dlq.jsonl, pending_callbacks.jsonl) — safe for concurrent appends in theory, but only one process should append at a time in practice.

---

## Queueing and Backpressure

When the system is busy, Claude queues work instead of spawning more workers:

- If 2–3 workers are already active, new tasks wait in Linear (Backlog or Todo) until a slot opens.
- Claude does not promote tickets to Todo faster than workers can pick them up.
- If the loop has a dispatch in flight (W7 callback pending), Claude does not send another dispatch to the same worker type until the first one resolves or times out.

**Backpressure signal:** If 2+ DLQ entries appear within an hour, something systemic is wrong. Claude stops dispatching and reports to the operator.

---

## Merge Conflict Rules

When two workers have been active on related areas:

1. The first PR to merge wins. The second worker must rebase or re-check before merging.
2. If a merge conflict is detected, Claude does not auto-resolve. Surface to the operator.
3. Workers should always pull latest main before starting and before pushing.
4. If a worker's branch is more than 24 hours old, it should rebase before opening a PR.

---

## When Claude Pulls This File

Claude reads this file when:

- About to promote a second ticket to Todo while one is already In Progress
- Evaluating whether two tasks can run at the same time
- A merge conflict or shared-state issue has come up
- The operator asks about parallel execution capacity

Claude does not read this file at routine thread start when only single-worker dispatch is expected.
