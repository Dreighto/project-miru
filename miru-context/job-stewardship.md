# Job Stewardship

Claude Code owns every job from dispatch to terminal state — not just at completion.
This document defines what ownership means, what "done" means, and how Claude Code
verifies that work has actually landed.

See CLAUDE.md for the terminal state schema (CONFIRMED_WORKING / INCONCLUSIVE / FAILED)
and stall classification taxonomy. This document defines the supervisory responsibilities
that surround those terminal states.

Last updated: 2026-05-01

---

## 1. What Stewardship Means

Stewardship is not the same as doing the work. Workers do the work. Claude Code stewards
the job — which means:

- Confirming the worker started clean (pre-flight: right branch, clean working tree, no overlap).
- Monitoring for stalls (heartbeat gap > 5 minutes with no terminal state = stalled).
- Responding when a worker emits a stall signal (INCONCLUSIVE, BLOCKED_ON, ESCALATE).
- Verifying the outcome once the worker reports done.
- Closing the ticket in Linear and writing the completion marker.
- Flagging canon-worthy findings for Notion promotion.

**Worker says done is not enough. Claude Code verifies done.**

---

## 2. Terminal States and Their Meaning

Four terminal states cover the full outcome space:

| State                       | Meaning                                                                      | Claude Code action                                                  |
| --------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **Done**                    | Work completed, verified, merged or committed                                | Close Linear ticket, write completion marker, flag learnings        |
| **Blocked**                 | Waiting on an external dependency (ticket, operator decision, service state) | Record block reason in Linear, park the ticket, monitor for unblock |
| **Cancelled**               | Work deliberately stopped before completion                                  | Close ticket with cancellation note, no completion marker needed    |
| **Needs Operator Decision** | Claude Code cannot proceed without human judgment                            | Send one Telegram ping with one specific decision needed            |

---

## 3. Stewardship Checklist

Before closing any job as Done, Claude Code confirms all seven items:

1. **Intended change happened.** The files that should have changed, changed. The behavior that should be different, is different.
2. **Checks passed or failures are explained.** Pre-commit hooks green, tests pass (or known pre-existing failures are documented).
3. **Linear reflects true state.** Ticket is in the correct state (In Review if PR open, Done if merged).
4. **PR state is resolved if applicable.** PR is merged, or reason for not merging is documented.
5. **No hidden blocker remains.** Nothing is silently broken that the next task will trip over.
6. **Claude Code verified the outcome.** Not just "the worker said it works" — Claude Code ran the check.
7. **Canon-worthy learning flagged.** If the work produced a finding worth carrying forward, it's been flagged for Notion promotion (not necessarily promoted yet, but noted).

If any item fails: the job is not Done. Return to the appropriate state and address the gap.

---

## 4. Verification Methods by Change Type

Claude Code uses the appropriate check for the type of change — not a generic "looks good."

| Change type                 | Verification method                                                            |
| --------------------------- | ------------------------------------------------------------------------------ |
| Python code change          | Pre-commit hooks pass + relevant tests pass (or known failures documented)     |
| Service config change       | Service restart + health endpoint returns expected response                    |
| n8n workflow JSON change    | Deploy + test execution in n8n + check execution log for no errors             |
| Documentation / context doc | Read the file, confirm cross-references are correct and content matches intent |
| Append-only JSONL write     | Confirm new line is valid JSON, confirm file was not truncated                 |
| Linear ticket update        | Re-read the ticket to confirm state and description reflect reality            |

**If Claude Code cannot run the verification** (e.g. service is not accessible, test
environment not available): state this explicitly. Do not declare Done with unrun checks.
The completion marker's `test_evidence` field must reflect what was actually verified,
not what was intended to be verified.

---

## 5. Stall Response Protocol

When a worker's heartbeat goes stale (> 5 minutes since last heartbeat, no terminal state):

1. Check `data/cc_heartbeat_log.jsonl` for the last known step.
2. Check `data/cc_completion_log.jsonl` — the worker may have completed without a heartbeat gap being detected.
3. If genuinely stalled: classify the stall using the taxonomy in CLAUDE.md (Transient / Ambiguous spec / Dependency starvation / Human-required).
4. Apply the matching recovery action from `tools/orchestrator/recovery_router.py`.
5. If recovery fails: emit one Telegram ping to the operator with exact blocker + recommendation.

**Do not re-dispatch blindly.** A retry without understanding the root cause is noise
that masks the real problem.

---

## 6. Follow-Up Ticket Discipline

When a worker discovers an out-of-scope finding during a task, Claude Code files a
follow-up Linear ticket. Rules:

- File the ticket before the current task closes — not "later" or "next session."
- The ticket description must stand alone. It should not reference "what we were just doing" — it should be readable cold.
- Add the ticket ID to the current task's completion marker `follow_up_tickets_filed` field.
- Do NOT expand the current task's scope to cover the finding. Complete in-scope work first.
