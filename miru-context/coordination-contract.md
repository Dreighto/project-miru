# Coordination Contract

How workers coordinate with each other, with Claude Code (VP Ops), and with the operator.
This document governs behavioral coordination — who communicates what, when, and through
which channel. For parallelism limits and file conflict rules, see concurrency-policy.md.

Last updated: 2026-05-01

---

## 1. Task Ownership

**One worker owns a task.** When a ticket is dispatched to a worker, that worker owns it
from the moment they start until they emit a terminal state or a handoff is explicitly made.

- Workers do not modify files outside their assigned scope without explicit operator authorization.
- Workers do not self-reassign to other tickets. If blocked, they report status and wait.
- If a worker discovers that completing the task requires touching files owned by another
  worker or outside the ticket's scope, they STOP, report the scope expansion, and wait for
  a routing decision.

**Before starting any task**, the worker must check Linear for related active work. If
another worker is actively working on the same file or feature: STOP. Report the conflict
to Claude Code. Do not proceed until the conflict is resolved.

---

## 2. No Direct Worker-to-Worker Coordination

Workers do not communicate with each other directly. All coordination routes through
Claude Code or documented handoffs.

**Why:** Direct worker coordination creates untracked state changes, racing writes to
shared files, and contradictory decisions that neither Claude Code nor the operator can
see until something breaks.

**Handoff pattern (when a task must pass between workers):**

1. The handing-off worker emits a terminal state with a clear summary of what's done and what remains.
2. Claude Code reads the terminal state, assesses what the next worker needs, and dispatches with a full context brief.
3. The receiving worker starts from the brief, not from the prior worker's chat output.

---

## 3. Status Reporting

Workers report status to Claude Code using the standard terminal and stall signals
defined in CLAUDE.md. Claude Code reports to the operator via Telegram.

### Worker → Claude Code

| Situation                                                        | Signal                                                                                                |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Task started                                                     | Heartbeat to `data/cc_heartbeat_log.jsonl` with `status: IN_PROGRESS`, `step: pre_flight`             |
| Long-running phase started                                       | Heartbeat with current `step` label                                                                   |
| Blocked waiting for something                                    | `STATUS: BLOCKED_ON: <ticket_id>` — stop, do not retry                                                |
| Spec is ambiguous and cannot be resolved from available evidence | `STATUS: INCONCLUSIVE` + one specific question                                                        |
| Task complete                                                    | `STATUS: CONFIRMED WORKING` or `STATUS: FAILED` + completion marker in `data/cc_completion_log.jsonl` |
| Needs scope expansion or security decision                       | `STATUS: ESCALATE: <category>`                                                                        |

### Claude Code → Operator (Telegram)

Claude Code pings the operator for exactly these situations — not as status updates, but
as decision requests:

| Trigger                                                           | Message type                                             |
| ----------------------------------------------------------------- | -------------------------------------------------------- |
| PR opened and ready for operator review                           | "PR #NN ready for merge — [one-line summary]"            |
| Worker stalled and recovery options exhausted                     | "PRO-### stalled: [exact blocker + CC's recommendation]" |
| Worker escalated with SECURITY, DESIGN_CHANGE, or IRREVERSIBLE_OP | Forward escalation + one decision needed                 |
| Job failed twice (REPEATED_FAILURE)                               | "PRO-### failed twice — reassign or cancel?"             |
| Budget entering Watch or Limit state                              | "Budget warning: [current state + recommended action]"   |
| System health issue (service down, sentinel alert)                | "Service down: [which service + restart status]"         |

**Silence is healthy.** Claude Code does not send routine progress updates to the operator.
The operator only hears from Claude Code when a decision is needed or something is wrong.

---

## 4. Requesting Help

A worker requests help when one of these is true:

- **Low confidence**: the worker cannot determine the right approach and proceeding risks wasted work or a wrong outcome.
- **Repeated failure**: the same failure mode has occurred twice on the same task.
- **Missing knowledge**: the worker lacks information it cannot find from available sources (codebase, docs, Linear, logs).
- **Capability exceeded**: the task requires something the worker cannot do (e.g. a browser action, a manual deployment step, an operator decision).
- **Higher-than-expected risk**: the worker discovers the task touches something more sensitive than the ticket described.

**How to request help:**

Emit the appropriate stall signal (INCONCLUSIVE, BLOCKED_ON, or ESCALATE) as defined in
CLAUDE.md. Do not send a narrative description of confusion. The signal must include:

1. The specific fact or decision that is missing (not a description of uncertainty).
2. The worker's best current hypothesis, so Claude Code can assess without starting from scratch.

**What Claude Code does with a help request:**

- Reassigns the task to a different worker (if the current worker hit a capability ceiling).
- Dispatches a supporting worker to provide a specific answer (e.g. Codex for static analysis).
- Changes the approach and re-dispatches the original worker with updated spec.
- Escalates to the operator if none of the above resolve the block.

---

## 5. Scope Boundaries

Workers operate within their assigned scope. These boundaries are hard:

- **Claude Code** owns Python backend, tests, and verification scripts.
- **Claude Chat** owns CLAUDE.md, worker prompts, and Notion canon pages by default. Claude Code may edit these only when the operator explicitly authorizes it for a specific task.
- **Codex** executes assigned work only. Never autonomously edits CLAUDE.md or worker prompts.
- **Cursor** handles IDE-guided manual edits as directed by the operator.

**When scope must expand:** file a follow-up Linear ticket for the out-of-scope finding.
Complete the in-scope work. Do not expand scope mid-task without authorization.
