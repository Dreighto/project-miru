# Retry and Backoff

When to retry, when to stop, and how to avoid spending budget on blind repetition.
This document extends the "1 retry max" rule in CLAUDE.md with the full context: what
is safe to retry, what is not, timing rules, and when retry becomes escalation.

Last updated: 2026-05-01

---

## 1. Core Rule

**Retry deliberately. Do not brute-force failure.**

A retry without diagnosis is noise. It masks the real problem, wastes budget, and
risks repeating the same bad outcome. Before any retry, identify the root cause.

**1 retry max per worker per ticket.** This is the hard limit from CLAUDE.md.
Same worker, same ticket, failed twice → escalate. Do not re-dispatch a third time.

---

## 2. Before Retrying

Answer these questions before dispatching a retry:

1. **What failed?** Exact error message, log line, or observed behavior.
2. **Why did it fail?** Root cause — not a guess, a diagnosis from evidence.
3. **What is different this time?** If nothing is different, the retry will fail the same way.
4. **Is this a safe-to-retry action?** See Section 3.

If you cannot answer question 3 with confidence: do not retry. Escalate with the diagnosis.

---

## 3. Safe vs. Unsafe Retry

### Safe to retry without authorization

These actions have no meaningful side effects when repeated:

- Read-only checks (health endpoints, file reads, Linear queries)
- Health probes (sentinel checks, service status)
- Pre-commit hook runs (these are idempotent)
- Test suite runs (tests should be idempotent)
- Worker dispatch after a classified Transient stall (network glitch, timeout with no output)

### NOT safe to retry without explicit authorization

These actions are not idempotent — running them twice may compound the problem:

| Action                                 | Risk                                                                             |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| Service restart                        | May interrupt in-flight work on a second attempt                                 |
| Database write or migration            | May duplicate data or fail partway through a second migration                    |
| Webhook trigger                        | Most webhooks fire their action on every call — double-firing creates duplicates |
| Dispatch to same worker on same ticket | Idempotency not guaranteed — two workers may produce conflicting output          |
| `git push` or merge                    | Cannot undo a push without force-push                                            |
| Email or Telegram message send         | User receives the message twice                                                  |

For any action in this list: diagnose first, then ask for authorization before retrying.

---

## 4. Timing

- **Minimum delay between retries:** 30 seconds. Do not immediate-retry — give transient conditions time to clear.
- **Maximum wait before escalation:** 5 minutes. If the retry has not completed or the
  block has not cleared within 5 minutes of the retry attempt: escalate.
- **Stall detection threshold:** heartbeat gap > 5 minutes with no terminal state → worker is stalled.
  Do not wait longer than this before classifying and responding.

---

## 5. Budget Pressure Effects

Budget state (from budget-governance.md) modifies retry behavior:

| Budget state | Retry policy                                                                                                |
| ------------ | ----------------------------------------------------------------------------------------------------------- |
| Safe         | Normal retry policy applies (1 retry max, diagnosis required)                                               |
| Watch        | No retries on non-critical tasks. Critical tasks (service outages, blocking failures) may still retry once. |
| Limit        | No retries under any circumstances. Diagnose and escalate.                                                  |

---

## 6. When Retry Becomes Escalation

Same worker, same ticket, failed twice → emit `STATUS: ESCALATE: REPEATED_FAILURE`.

The escalation message must include:

1. What failed both times (exact error, not a description of confusion).
2. What diagnosis was attempted.
3. A best recommendation for what should happen next (reassign to a different worker, change approach, hold the ticket).

Do not send "it failed again" as an escalation. Send a diagnosis and a recommendation.
The orchestrator or operator acts on escalations — they need something actionable, not a status update.

---

## 7. Relationship to Stall Classification

Retry/backoff governs the mechanics of retrying. Stall classification (CLAUDE.md)
governs how Claude Code categorizes a failure and what recovery action to take.

These two rules work together:

- **Transient stall** → one auto-retry is permitted (no diagnosis required — transient means the failure was environmental)
- **Ambiguous spec** → no retry — the spec must be clarified first
- **Dependency starvation** → no retry — the dependency must clear first
- **Human-required** → no retry — operator decision is the unblock

When the stall class is uncertain: classify as Human-required and escalate. Do not retry when the failure class is unknown.
