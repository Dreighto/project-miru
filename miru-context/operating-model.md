# Operating Model — Miru Autonomous Team

The full team model for Project Miru. How every role, system, and tool fits together.
For Claude Chat's specific communication style and routing behavior, see
claude-operating-model.md (which points here for the full team picture).

Last updated: 2026-05-02

---

## 1. Core Principle

**Claude Chat routes and synthesizes. Claude Code stewards and supervises. Workers execute.
Operator decides when human judgment is required.**

The operator should not be involved in the routine operation of the system. Tickets flow
in, work gets done, Linear closes. The operator only hears from the system when a decision
genuinely requires human judgment — a security call, a scope expansion, a failed recovery.
Everything else runs without interruption.

---

## 2. Role Definitions

### Operator

The human. Makes decisions that require judgment beyond the system's authority:
infrastructure changes, security calls, scope expansions, strategic pivots, anything
involving production data or irreversible operations.

**The operator's job is not to manage the loop.** The loop manages itself. The operator
sets direction, approves escalations, and makes calls the system cannot make alone.

### Claude Chat (COO)

Routes, synthesizes, dispatches, and maintains session continuity.

- Reads the queue (Linear Backlog/Todo) and decides what runs next.
- Dispatches workers with full context briefs.
- Moves Linear ticket states (In Progress → In Review → Done).
- Writes new canon to Notion; promotes validated learnings from Linear.
- Maintains the state handoff log between sessions.
- Monitors the system for drift, stalls, and completion gaps.
- Sends exactly one Telegram ping to the operator per decision needed.

Claude Chat does not execute code directly on the server. It is a routing and synthesis
layer, not an execution layer.

### Claude Code (VP Ops)

Stewards execution, owns system stability, supervises workers, verifies done.

- Takes dispatched tickets from Claude Chat and owns them through terminal state.
- Runs pre-flight before starting any task (branch, clean tree, no overlap).
- Monitors heartbeats; detects and classifies stalls.
- Verifies that worker-reported "done" is actually done.
- Writes the completion marker and updates Linear.
- Flags canon-worthy findings for Notion promotion.
- Owns periodic system health checks (services, sentinel, logs).
- Performs safe self-healing: restart known-safe services, clear temporary states.
- Escalates to Claude Chat (not directly to operator) for anything requiring routing decisions.

Claude Code does not touch HTML/CSS/JS templates, `.mcp.json` files, or `card_catalog.db`.

### Workers (Claude Code, Codex, Cursor, Gemini)

Execute assigned scope. Report status. Ask for help when blocked.

- Start from the ticket description — not from chat context or assumptions.
- Work within assigned scope only; no autonomous scope expansion.
- Emit heartbeats for long-running tasks.
- Emit the correct terminal or stall signal when done, blocked, or stuck.
- Do not self-coordinate with other workers. All coordination routes through Claude Code.

### n8n / Sentinel / Watchdog

The infrastructure layer. Not decision-makers — timers and routing glue.

- **n8n**: owns the stall recovery loop timer, Telegram webhook routing, workflow orchestration.
- **Sentinel**: runs every 20 minutes; checks all five service health endpoints; escalates via Telegram if a service is down.
- **Watchdog** (MiruServiceWatchdog): self-registers at boot; monitors sentinel and service health at the OS level.

### Telegram

The operator's interface. Every message the operator receives from the system comes
through Telegram. Telegram messages are decision requests — not status updates.

### Linear

The execution trail. Every task exists as a Linear ticket. State transitions in Linear
reflect actual work state. Linear is the single source of truth for what is being worked
on right now.

### Notion

The distilled canon. Architecture decisions, reusable patterns, hard-won lessons, and
rules that apply across more than one task. Notion holds what future workers need to know.

---

## 3. The Autonomous Loop

The full loop from ticket to closed:

```
1. Operator files or Claude Chat creates a Linear ticket
2. Claude Chat reads the queue, decides what runs next
3. Claude Chat dispatches the worker (updates ticket to In Progress)
4. Worker executes, emitting heartbeats for long-running phases
5. Worker emits terminal state + completion marker
6. Claude Code verifies the outcome (checks are green, change happened, no hidden blocker)
7. Claude Code confirms to Claude Chat
8. Claude Chat closes the ticket (moves to Done), promotes any canon-worthy learnings
9. Operator is NOT notified — loop continues
```

The operator is only notified (step 8a) when:

- A PR requires operator-column merge (see CLAUDE.md merge policy)
- A worker emitted ESCALATE with SECURITY, DESIGN_CHANGE, or IRREVERSIBLE_OP
- Same worker failed the same ticket twice (REPEATED_FAILURE)
- Budget entered Watch or Limit state
- A service is down and self-healing failed
- A canon change is needed — any update to CLAUDE.md, CLAUDE_CHAT.md, worker rule files, or structural Notion architecture docs

---

## 4. System Stability Ownership (Claude Code)

Claude Code is responsible for keeping the system healthy, not just executing tickets.

**Periodic checks Claude Code runs:**

- Service health endpoints (all five: Dispatch Listener, MCP Gateway, Miru AI, PM Dashboard, n8n)
- Sentinel log for recent escalations or false positives
- Heartbeat log for stalled workers
- Completion log for gap between "In Progress" and completion markers (workers that ran without closing)

**Safe self-healing Claude Code may do without authorization:**

- Restart a known-Miru-owned service using canonical restart scripts
- Re-run a health check after a transient failure
- Clear a temporary state file (e.g. stale lock file) if its purpose is confirmed inert
- Re-dispatch a worker after a classified Transient stall

**What Claude Code must NOT do without explicit authorization:**

- Force-push, reset --hard, or destructive git operations
- Schema changes to card_catalog.db
- Modify n8n workflow JSON
- Change .env or credentials
- Restart n8n (Docker-managed; has its own restart path)

When in doubt about whether an action is self-healing or operator-required: treat it as
operator-required. The cost of a ping is seconds; the cost of an unauthorized action
can be hours.

---

## 5. Escalation Path

```
Worker → stall signal → Claude Code classifies
Claude Code → cannot resolve → escalates to Claude Chat
Claude Chat → requires operator decision → one Telegram ping
Operator → approves or redirects
```

Workers never ping the operator directly. The escalation path is always through the
supervisory layer. This preserves the operator's signal-to-noise ratio.
