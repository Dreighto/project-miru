# Kill Switch — Global Stop Contract

This document defines the behavioral contract for a global stop mechanism. The contract
is defined here. Enforcement wiring is deferred — Claude Chat and Claude Code do not yet
check the flag. Wiring is a separate implementation task.

The operator needs a reliable way to halt autonomous activity without shutting down
infrastructure or interrupting running services.

Last updated: 2026-05-01

---

## 1. The Flag File Contract

**Flag file:** `data/system_halt`

**Presence of this file** is a signal to halt autonomous dispatch. The file's contents
are not parsed — only its presence matters.

- File exists → halt autonomous dispatch
- File does not exist → normal operation

This file is NOT append-only. It is a presence/absence flag. It may be created and
deleted freely. Do not apply append-only rules to it.

**Do NOT create this file during normal operations.** The file defines the contract;
the flag is only created when the operator intends to halt the system.

---

## 2. What the Contract Says Stops

When `data/system_halt` is present (once wiring is implemented):

| Activity                                        | Stops? |
| ----------------------------------------------- | ------ |
| Worker dispatch (new job starts)                | Yes    |
| Ticket promotion (Backlog → Todo → In Progress) | Yes    |
| Retries of failed tasks                         | Yes    |
| Speculative or low-priority background work     | Yes    |

---

## 3. What the Contract Says Continues

The kill switch is not a system shutdown. Infrastructure stays up.

| Activity                                                                  | Continues?                          |
| ------------------------------------------------------------------------- | ----------------------------------- |
| Sentinel health checks                                                    | Yes — monitoring must always run    |
| Telegram alert sending (health alerts, escalation pings)                  | Yes                                 |
| Status reads (Linear, Notion, repo)                                       | Yes                                 |
| n8n (Docker)                                                              | Yes — kill switch does not stop n8n |
| Service processes (Dispatch Listener, MCP Gateway, Miru AI, PM Dashboard) | Yes                                 |
| Claude Chat session for operator interaction                              | Yes                                 |

**Sentinel is never gated by the kill switch.** Health monitoring cannot be suspended
by the same mechanism used to halt work — that would create a blind spot precisely when
the operator needs visibility most.

---

## 4. How to Use (Once Wired)

**To halt:**

```bash
echo "halt" > data/system_halt
```

Or via Telegram command (deferred — see PRO-249 for /snooze routing wiring).

**To resume:**

```bash
del data/system_halt    # Windows
rm data/system_halt     # bash
```

Or via Telegram command (same wiring as halt).

---

## 5. What This Does NOT Replace

- Individual service restart scripts (`windows\restart_*.ps1`) — those manage service processes, not autonomous dispatch
- Sentinel snooze state (`logs/sentinel_state.json`) — that gates Telegram alert noise, not dispatch
- Per-ticket hold (moving a Linear ticket to Backlog or Cancelled) — that gates a specific task

The kill switch gates the entire autonomous dispatch loop. Individual service scripts and
ticket management remain independent.

---

## 6. Implementation Status

**Contract:** Defined in this document.

**Enforcement wiring:** Wired in rule files (2026-05-01).

- `CLAUDE.md` — Kill Switch Pre-flight Gate section: Claude Code checks for `data/system_halt`
  before starting any dispatched task. If present: emits `STATUS: ESCALATE: HUMAN-REQUIRED`
  and stops immediately.
- `CLAUDE_CHAT.md` — Dispatch protocol step 4 (Kill switch gate): Claude Chat checks for
  `data/system_halt` via `fs_get_file_info` before calling `dispatch_worker`. If present:
  leaves ticket in Todo and sends one Telegram ping to operator.

The flag file now has mechanical effect — both workers honor it as a hard stop.

**Telegram command wiring:** Deferred (PRO-249 covers /snooze and /unsnooze; kill
switch Telegram command is a follow-on from that work).

**Do not create `data/system_halt` during normal operations.** The file should only
exist when the operator intends to halt the system.
