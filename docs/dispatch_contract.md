# Miru Dispatch Contract

**Version:** 1.0
**Status:** Active
**Last updated:** 2026-04-30
**Owner:** Claude Chat (orchestrator)

---

## Purpose

This document defines the contract Claude Chat (orchestrator) uses when dispatching work to Claude Code (CC) workers via the `dispatch_worker` MCP tool. It encodes authority tiers, decision heuristics, escalation triggers, and completion criteria so workers operate autonomously without per-step check-ins.

Research basis: Perplexity deep research on dispatch contract design (2026-04-30) + MAST failure taxonomy analysis.

---

## How Dispatch Works

1. Claude Chat calls `dispatch_worker(worker="claude-code", prompt="...", ticket_id="PRO-XXX")` via MCP.
2. The MCP gateway writes the prompt to `data/n8n_inbox/<trace_id>.prompt.json` and POSTs to the dispatch listener (127.0.0.1:19100) with HMAC auth.
3. The listener spawns `claude.cmd --print --dangerously-skip-permissions`, feeding the prompt via stdin.
4. CC executes and writes its completion marker to `data/cc_completion_log.jsonl`.
5. Claude Chat polls `worker_availability` + `activity_since` to detect completion.

**Key constraint:** CC's stdout/stderr go to log files only (`logs/dispatch_listener_traces/<trace_id>.{stdout,stderr}.log`). CC cannot write back to Claude Chat directly — the completion log and Linear ticket comments are the only structured output channels.

---

## Prompt Template

Every dispatch prompt must include these sections. Fill in `{…}` fields from the Linear ticket.

```
═══════════════════════════════════════════════════════
MIRU WORKER DISPATCH — {ticket_id}
═══════════════════════════════════════════════════════

TICKET: {ticket_id} — {ticket_title}
WORKER: claude-code-1
WORKTREE: cut a new branch from origin/main

FIRST ACTION — emit session-start heartbeat before reading any files:
    python tools/emit_heartbeat.py --worker-id claude-code-1 --ticket-id {ticket_id} --step pre_flight --branch main

---

TASK
{ticket_description}

Acceptance criteria:
{ticket_acceptance_criteria}

---

AUTHORITY TIERS

TIER 0 — Execute silently:
  • Read files, grep, git status, git log
  • Modify files within stated scope
  • Add or update tests matching existing patterns
  • Run pre-commit / lint / pytest
  • Commit to feature branch or direct-to-main (per CLAUDE.md policy)
  • Create PRs (no auto-merge — CC-merge or operator-merge per policy)
  • Append to data/cc_completion_log.jsonl
  • Append to data/cc_heartbeat_log.jsonl

TIER 1 — Propose and wait (post a Linear comment, stop):
  • Multi-file refactor affecting >5 files
  • New external dependency or version upgrade
  • Breaking change to an existing API or function signature
  • Architecture change to module structure
  • Any change to docker/, windows/, .github/, or infrastructure

TIER 2 — Escalate immediately (post a Linear comment, stop):
  • Security issue detected (credentials, auth bypass, privilege escalation)
  • Irreversible operation (database migration, data deletion, file removal)
  • Scope expansion beyond ticket description
  • Ticket requirements are ambiguous and infer-and-report would not cover it
  • Repeated tool failure (>2 retries same action)

TIER 3 — Critical stop:
  • Never under any circumstances: touch card_catalog.db, .mcp.json, or port 8765
  • Never: force-push, --no-verify bypass without logging, write outside D:\dev\miru*
  • If any of these arise: STOP, post LINEAR comment, end session immediately

---

DECISION HEURISTIC

Before each action:
1. PATTERN CHECK — does existing code show how to do this? Yes → execute. No → propose.
2. SCOPE CHECK — is this within the ticket? Yes → continue. No → escalate.
3. CONFIDENCE CHECK — >90% sure? Execute. 70–90%? Propose with two options. <70%? Escalate.
4. REVERSIBILITY CHECK — reversible? Execute if confident. Irreversible? Escalate.

INFER AND REPORT (for minor ambiguity):
- List 2–3 interpretations with their implications.
- Choose the most conservative one that matches an existing pattern.
- Execute, document the choice in the commit message and completion log.
- Post a Linear comment if the decision affects anyone else.

---

SCOPE BOUNDARIES

Hard limits:
  • Max files: 10 (propose if exceeded)
  • Max lines added per file: 500 (propose if exceeded)
  • Do NOT modify: card_catalog.db, .mcp.json, any file outside D:\dev\miru*
  • Do NOT touch: port 8765, port 8080 (RESERVED in CLAUDE.md)

Soft limits (escalate if exceeded):
  • >5 files → propose before proceeding
  • >300 lines added → propose before proceeding
  • Estimated time >4 hours → escalate with estimate

---

HYGIENE GATE (required before every PR)

Run: `pre-commit run` (staged files scope)
Confirm green. If failures:
  - Fix if in scope of this ticket.
  - If pre-existing / out of scope: STOP, report, do NOT push.

---

HEARTBEAT PROTOCOL (mandatory — not optional)

Emit a heartbeat at each phase transition using this exact command:

    python tools/emit_heartbeat.py \
        --worker-id claude-code-1 \
        --ticket-id {ticket_id} \
        --step {step} \
        --branch {branch_or_main}

Required emit points — do NOT skip any of these:

  1. Session start, before reading any files          → --step pre_flight
  2. After branch is cut, before writing code         → --step writing_code
  3. Before running pre-commit or tests               → --step running_tests
  4. Before opening or updating a PR                  → --step opening_pr
  5. If waiting for CI / Bugbot (>60s wait expected)  → --step awaiting_bugbot
  6. After merge confirmed, before cleanup            → --step post_merge_cleanup

For any other step expected to take >60s: emit before starting it.

WHY THIS IS MANDATORY: The stall watcher polls every 3 minutes. If 5 minutes pass
with no heartbeat and no completion marker, it classifies you as STALLED and sends a
Telegram alert to the operator — triggering manual intervention. Skipping heartbeats
defeats autonomous operation. This is not a courtesy; it is how the system knows you
are alive.

---

COMPLETION CONTRACT

Write one row to data/cc_completion_log.jsonl with status CONFIRMED_WORKING, INCONCLUSIVE, or FAILED.
Then post a comment to the Linear ticket with the same status and a one-line summary.

STATUS: CONFIRMED_WORKING — all acceptance criteria met, hygiene green, deployed/merged as appropriate.
STATUS: INCONCLUSIVE — partial progress; post one specific question to Linear. Do not re-attempt.
STATUS: FAILED — blocked; explain what failed and what the operator needs to decide.

Heartbeat: see HEARTBEAT PROTOCOL section above — mandatory at each phase, exact
command provided. Missing heartbeats trigger false stall alerts to the operator.

---

ESCALATION FORMAT

When posting to Linear (Tier 1/2):

[PROPOSAL] or [ESCALATION]
Reason: {why you stopped}
Analysis: {what you found}
Options:
  1. {option A}: {tradeoffs}
  2. {option B}: {tradeoffs}
Recommended: {which + why}
Waiting for: {what decision you need}

═══════════════════════════════════════════════════════
```

---

## Worker Failure Mode Mitigations

Research finding: 44% of LLM worker failures happen pre-execution (bad spec, missing context). Mitigations built into this contract:

| Failure mode                                     | Mitigation                                                                               |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Transcript parsing (claims success when failing) | Require `CONFIRMED_WORKING` only when completion log row written + Linear ticket updated |
| Scope creep                                      | Hard file/line limits, escalate on scope expansion                                       |
| Silent failure                                   | Heartbeat log required; stall = no heartbeat in 5min                                     |
| Wrong pattern chosen                             | Pattern-check step in decision heuristic                                                 |
| Security regression                              | Tier 2/3 hard stops on any security-adjacent change                                      |
| Zombie prompt file                               | Listener reads prompt file synchronously before 202; gateway cleans up after             |

MAST taxonomy (3 categories, 14 failure modes):

- **System design failures (44%)**: address via explicit authority tiers + done-when criteria
- **Inter-agent misalignment (32%)**: address via single structured completion channel (cc_completion_log)
- **Task verification failures (24%)**: address via outcome-based verification (run tests, check live service)

---

## Orchestrator Flow

Claude Chat's dispatch loop for a ticket:

1. **Check availability** — `worker_availability()` to confirm idle.
2. **Draft prompt** — fill the template above from the Linear ticket.
3. **Dispatch** — `dispatch_worker(worker="claude-code", prompt=..., ticket_id="PRO-XXX", timeout_seconds=1200)`.
4. **Poll for completion** — check `activity_since` and `cc_completion_log.jsonl` every few minutes.
5. **On CONFIRMED_WORKING** — read the PR URL from the completion log, follow the CC-merge or operator-merge policy.
6. **On INCONCLUSIVE** — answer the question in Linear, re-dispatch if needed.
7. **On FAILED / STALLED** — escalate to operator via Telegram.

---

## Environment Variables

| Variable                     | Purpose                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `MIRU_DISPATCH_ENABLED=1`    | Enable the `dispatch_worker` MCP tool                          |
| `W4_LISTENER_HMAC_SECRET`    | HMAC key (shared with dispatch listener)                       |
| `MIRU_DISPATCH_LISTENER_URL` | Override listener base URL (default: `http://127.0.0.1:19100`) |
| `MIRU_RATE_LIMIT_DISPATCH`   | Max dispatches/minute (default: 5)                             |

---

## Relationship to CLAUDE.md

This document **extends** CLAUDE.md; it does not override it. When this document conflicts with CLAUDE.md, CLAUDE.md wins. Specifically:

- PR merge policy: follow CLAUDE.md exactly (CC-merge vs operator-merge columns).
- Append-only files: follow CLAUDE.md (completion log, heartbeat log, etc.).
- File placement: follow CLAUDE.md service boundary rules.
- Restart rules: follow CLAUDE.md (use approved PS1 scripts only).
