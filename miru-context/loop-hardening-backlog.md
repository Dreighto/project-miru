# Loop-Hardening Backlog — Awaiting Linear Filing

> Source: 2026-05-02 loop-hardening campaign (CAMPAIGN_REPORT.md, PR #75).
> These four ticket designs were drafted for Linear but filing was blocked by the free-tier issue limit.
> When Linear is unblocked, copy each section into a new ticket. Each design is locked per the PRO-180 lesson — "Lock design in the Linear ticket description, not in the prompt wrapper."

---

## Ticket A — Clean up CLAUDE.md references to non-existent orchestrator modules

**Severity:** LOW · **Type:** chore · **Source:** B6
**Status as of 2026-05-03:** likely landed in PR-XX (work done in this session)

### Symptom

`CLAUDE.md` "Orchestrator-side modules (PRO-187 follow-on, 2026-04-28)" section claims `task_store.py` and `worktree_manager.py` exist under `tools/orchestrator/`. They do not. Only `stall_detector.py`, `recovery_router.py`, and `__init__.py` exist there.

### Why this matters

Workers reading CLAUDE.md believe these modules exist and may try to import or extend them. The reference also implies a "prompt-hash idempotency" capability the system does not have today.

### Scope

Edit `CLAUDE.md` only. Remove the two missing-module bullet lines from the "Orchestrator-side modules" subsection. Leave `stall_detector.py` and `recovery_router.py` lines intact.

### Don't touch

- The "Heartbeat emission" section above it.
- The terminal-state schema (CONFIRMED_WORKING / INCONCLUSIVE / FAILED).
- The stall taxonomy table.

### Done when

1. The four-bullet list becomes a two-bullet list.
2. `CLAUDE.md` still reads coherently.
3. Pre-commit clean.
4. Direct commit to main (canon-only edit per merge policy).
5. Append completion marker.

---

## Ticket B — Persistent worktree leases (listener-restart safety)

**Severity:** MEDIUM · **Type:** Improvement · **Source:** B4

### Symptom

`services/dispatch_listener/src/worktree.js` keeps slot leases in an in-memory `Map`. Listener restart (deploy, crash, watchdog-triggered) wipes them, after which the listener can re-lease a slot still in use by a pre-restart worker.

### Why this matters

Two workers in the same worktree simultaneously will collide on branch checkout, file edits, and commits. The 1-hour orphan sweep doesn't catch newer placeholders.

### Design (locked)

Persistent state file `data/worktree_leases.json` (NOT append-only — small dict, read-modify-write fine):

```json
{
  "D:\\dev\\miru-w1": {
    "trace_id": "abc-123",
    "worker": "claude-code",
    "leased_at": "2026-05-03T01:00:00Z",
    "pid": 12345
  },
  "D:\\dev\\miru-w2": null
}
```

**Behavior:**

1. `leaseSlot(traceId, worker)`: read file, find first slot whose value is `null` OR whose `pid` is no longer alive, claim with current pid, write atomically.
2. `releaseSlot(slotPath)`: set value to `null`, write atomically.
3. Listener startup: load file, clear dead-pid leases.
4. In-memory `Map` becomes a write-through cache backed by the file.

**Atomicity:** write to `<file>.tmp`, then rename. On parse error (corrupt file), log + treat as empty.

### Don't touch

- `WORKTREE_SLOTS` array.
- Don't add `data/worktree_leases.json` to the append-only list (it's a small state dict).
- Existing 1-hour orphan sweep window.

### Done when

1. `data/worktree_leases.json` exists and is gitignored.
2. `worktree.js` reads/writes file on every lease/release.
3. Startup clears dead-pid leases.
4. Test in `tests/`: simulate dead-pid lease, verify reclaim.
5. Pre-commit clean. PR opened (operator-merge: infrastructure).
6. Append completion marker.

---

## Ticket C — Prompt-hash idempotency (catch duplicate work with different trace_ids)

**Severity:** HIGH · **Type:** Improvement · **Source:** B3

### Symptom

The dispatch listener dedupes by `trace_id` only. Same prompt POSTed with two different trace_ids (e.g., recovery_router fires while original worker still running) → both dispatches succeed.

### Why this matters

Recovery flow already mints a fresh `recovery-X-Y-Z` trace_id, so collision protection at trace_id alone is insufficient.

### Design (locked)

Add SHA-256(promptText).slice(0,16) prompt-hash idempotency in addition to trace_id idempotency.

**Storage:** placeholder receipt gains a `prompt_hash` field.

**Check:** new function `findInFlightByPromptHash(inboxDir, promptHash, windowSeconds=600)`:

- Scans `*.result.json` files in inbox.
- Returns first receipt with `status=spawned` AND matching `prompt_hash` AND `started_at` within window.

**Listener flow:**

1. After existing `tryReadReceipt(traceId)` check.
2. Before `leaseSlot`, compute promptHash, call `findInFlightByPromptHash`.
3. Match → `409 already_dispatched` body `{error: "duplicate_prompt_in_flight", existing_trace_id: <found>}`. Write DLQ row tagged `error_class=duplicate_prompt`.
4. No match → proceed; pass promptHash to `writePlaceholderReceipt`.

**Window:** 10 min.

### Don't touch

- The 5 append-only data files.
- `tryReadReceipt` (unchanged).
- W4 dispatch payload schema (listener computes hash itself).
- `recovery_router.py`.

### Done when

1. `receipt.js` schema accepts `prompt_hash`.
2. `index.js` computes hash, calls `findInFlightByPromptHash`, returns 409 with new error reason.
3. New error_class `duplicate_prompt` added to `dlq.js` ERROR_CLASSES.
4. Test covers: same prompt + different trace_id → 409.
5. Pre-commit clean. PR opened (operator-merge: infrastructure).
6. Manual verification with synthetic placeholder receipt.
7. Append completion marker.

---

## Ticket D — Daily Linear↔completion-marker drift scanner workflow

**Severity:** MEDIUM · **Type:** Improvement · **Source:** B7

### Symptom

`vp_ops_verify.py` does per-ticket reconciliation. No scheduled scan walks all open Linear tickets and flags ones whose state contradicts `cc_completion_log.jsonl`.

### Why this matters

- Linear marked Done with no marker → silent.
- Linear stuck "In Progress" while worker crashed weeks ago → silent.
- Marker exists but Linear bridge missed it → silent.

### Design (locked)

New n8n workflow `w-drift-scanner.json`. Schedule: daily 09:00 local.

**Steps:**

1. Linear GraphQL: list open Project Miru issues (Todo, In Progress, In Review). Cap 200.
2. Read `cc_completion_log.jsonl` last 1000 rows → `Set<ticket_id>` of CONFIRMED_WORKING markers.
3. Classify per issue:
   - state ∈ {Done, In Review} AND ticket_id NOT in markers → MISSING_MARKER
   - state ∈ {Todo, In Progress} AND ticket_id IN markers → STALE_LINEAR_STATE
   - else: clean
4. Drift rows exist → one Telegram message listing them.
5. Append row to `data/drift_scanner_log.jsonl` (NEW append-only file — must be added to the protected list, pytest invariant, and pre-commit excludes).
6. Zero drift → no Telegram, still append log row.

### Don't touch

- 5 existing append-only files.
- `vp_ops_verify.py`.
- Linear Completion Bridge.

### Done when

1. `docker/n8n/workflows/w-drift-scanner.json` exists, schema-valid.
2. Workflow imported, runs daily 09:00.
3. `data/drift_scanner_log.jsonl` in gitignored protected list + pytest invariant.
4. CLAUDE.md "Append-only data files" updated to list 6 files.
5. First run produces Telegram alert OR clean-no-message + log row.
6. PR opened (operator-merge: new workflow + infra).
7. Append completion marker.

### Investigation steps

Read these for n8n patterns before authoring:

- `w-cc-completion-ping.json` (Telegram-on-new-row)
- `dlq_watcher.json` (schedule + Telegram alert)
- `w-linear-completion-bridge.json` (Linear GraphQL list-issues)

---

## Filing instructions (when Linear is unblocked)

For each section above:

1. Copy title (first line `## Ticket X — ...`) into Linear issue title.
2. Copy everything from `### Symptom` through the last `### Done when` block as the description.
3. Apply labels: `claude-code` + `Improvement` (or `chore` for Ticket A).
4. Set priority: 4 (low) for A; 3 (normal) for B/C/D.
5. Update CAMPAIGN_REPORT.md "Recommended Follow-up Tickets" section with the assigned PRO-XXX numbers.
