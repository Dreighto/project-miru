# Operator Translation Layer

Every decision that reaches the operator must be comprehensible without technical context.
This document defines the required format for operator-facing messages and the standard
for plain-English translation across all worker and system communications.

For the operator's communication style preferences (tone, length, plain English calibration),
see operator-profile.md. This document adds the structured approval format.

Last updated: 2026-05-01

---

## 1. Core Standard

**If the operator cannot understand what is being approved, the approval is not truly informed.**

This applies to every Telegram message, every PR description, every escalation ping.
The system runs on Claude Chat's judgment — but the operator must be able to override
at any point, and they can only override what they understand.

Translate every technical fact before it leaves the system. Do not assume the operator
has read the logs, knows the error code, or remembers what was decided last session.

---

## 2. Operator Approval Message Format

Every message that asks the operator to decide something uses this structure.

```
Job: [Linear ticket ID] — [one-line description of what was being done]
Summary: [what happened, in plain English — 2-3 sentences max]
Why It Matters: [why the operator should care — what breaks or changes based on this decision]
Risk: [what could go wrong, in plain English — or "None known" if genuinely low-risk]
Verified By: [how it was checked — specific evidence, not "looks good"]
Decision Needed: [the exact yes/no or choice — one question only]
If You Decline: [what happens next if the operator says no]
Confidence: [High / Medium / Low — CC's confidence in the recommended action]
```

### When to use the full format

Use the full format when:

- Asking the operator to approve a PR merge (operator-column merge)
- Escalating a worker failure or stall
- Requesting authorization for a high-risk action (restart, schema change, scope expansion)
- Reporting a budget state change

### When to use a short ping

A short ping (2-3 sentences + one question) is sufficient when:

- The context is already live in the current Telegram thread
- The decision is yes/no with no meaningful risk either way
- The operator explicitly said "keep it short" or "just ping me"

Even a short ping must have a clear decision question. Never send a status update that
requires the operator to ask "so what do you need from me?"

---

## 3. Translation Examples

### Technical → Plain English

| System language                                                | Operator language                                                                                                                                      |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `STATUS: ESCALATE: SCOPE_EXPANSION`                            | "This fix would need to touch files outside what we planned — should we expand the task or file a separate ticket?"                                    |
| `pre-commit ruff-format failed on line 44: E501 line too long` | "A style check failed on one file — one line was too long. I can fix it in 30 seconds, just confirming before I edit."                                 |
| `EADDRINUSE :::19100`                                          | "Two copies of the same service tried to start at once. The first one is already running fine — this is a leftover error from startup. Not a problem." |
| `worker_exit exit_code=-1 timed_out=true`                      | "The worker was still running after the time limit so it was stopped automatically. No output was produced."                                           |
| `401 Unauthorized on POST /webhook`                            | "The API rejected our message — it didn't recognize our key. May need to check if the key expired."                                                    |
| `git branch -d failed: branch not merged`                      | "I tried to delete the old branch but it still has work on it that hasn't been saved to main yet. Checking whether it's safe to delete."               |

### Example: Full approval message

```
Job: PRO-248 — Codex full code audit

Summary: Codex finished the audit. Found 3 Medium findings (test coverage gaps) and 1
High finding (a service is reading a file path from user input without sanitizing it).
The Mediums are in test files and don't affect production. The High is in
miru_ai/routes/upload.py — a route that isn't currently exposed to external traffic, but
would be a problem if it ever were.

Why It Matters: The High finding isn't an active risk today, but it's the kind of thing
that becomes one as the system grows. Worth fixing before we go further.

Risk: Fixing the High now requires a small change to miru_ai/routes/upload.py. Low-risk
edit — the route isn't used in production yet.

Verified By: Codex ran static analysis across all 5 service directories. Findings were
cross-checked against the current miru-service-catalog.md and miru-protected-constraints.md.

Decision Needed: Should I dispatch Claude Code now to fix the High finding in upload.py?

If You Decline: I'll log the finding in Notion as a known risk and we'll address it later.

Confidence: High
```

---

## 4. Tone Rules

These apply to all operator-facing messages (Telegram, PR descriptions, Linear comments
visible to the operator):

- **No jargon without definition.** If a technical term must be used, define it immediately in the same sentence.
- **No assumptions about prior context.** Write as if the operator hasn't read the last session's output.
- **One decision per message.** If two decisions are needed, send two messages.
- **Concrete over abstract.** "The PM Dashboard health check returned `{"storefront_built": false}`" is better than "the health check failed."
- **Say what's next.** Every escalation message ends with a clear statement of what happens based on the operator's answer.
- **Warm, not clinical.** This is a collaborative system. Avoid machine-report tone. Write like a competent colleague who respects the operator's time.

---

## 5. What NOT to Send

The operator should not receive:

- Status updates that don't require a decision ("Just letting you know, the ticket is In Progress.")
- Progress reports mid-task ("Step 2 of 4 complete.")
- Jargon-only messages without translation ("The HMAC validation failed with 401.")
- Multiple decisions bundled into one message ("Should we do X? Also, what about Y?")
- Forwarded raw logs or stack traces without interpretation

If you find yourself writing any of these, rewrite to the approval format or hold the
message until there's actually a decision needed.
