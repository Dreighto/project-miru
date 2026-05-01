# Miru Vocab — Operator Language Guide

This file maps the operator's natural language, shorthand, and recurring phrases to their project-specific meanings. Claude should recognize these automatically without asking for clarification.

---

## How This File Works

The operator's vocabulary grows over time. New shorthand appears as the project evolves. When Claude encounters a phrase it hasn't seen before, it asks once, then adds it here. Once a phrase is confirmed, Claude uses it consistently from that point forward — no re-asking.

Abbreviations are expected. The operator frequently shortens repeated or long phrases. Claude should treat abbreviations the same as the full phrase once the meaning is established.

---

## Operator Direction Phrases

These phrases carry real authority. When the operator says them, Claude treats them as standing direction until explicitly overridden.

| Operator says                                        | What it means                                                                                                                    |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| "You're driving" / "your call" / "this is your call" | Claude makes the decisions and acts. No need to check in on each step. This is standing direction until the operator re-engages. |
| "Keep going" / "continue" / "proceed"                | The current approach is approved. Keep executing. Don't re-ask.                                                                  |
| "No pushback" / "no push back"                       | Full agreement with what Claude just proposed. Execute as stated.                                                                |
| "We can proceed" / "let's proceed" / "go ahead"      | Approval to execute. Don't re-confirm.                                                                                           |
| "I will be observing"                                | Testing mode. The operator wants to watch Claude work autonomously. Make decisions, explain reasoning, act.                      |

These are not one-time approvals. If the operator says "you're driving" in one thread and the work continues into the next thread, that direction carries over unless the operator says otherwise.

---

## Action Phrases

| Operator says                                               | What it means                                                 | Claude should do                                                                   |
| ----------------------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| "Park this" / "park it"                                     | Stop this topic. Defer to backlog. Don't delete.              | Move Linear ticket to Backlog if applicable. Log as deferred if needed. Move on.   |
| "Let the loop carry it"                                     | File a Linear ticket without a worker label. Let W2 route it. | Create ticket in Todo, no worker label. Don't draft a copy-paste prompt.           |
| "Wrap this thread" / "new thread" / "switch threads"        | End the conversation. Prepare handoff.                        | Ask: "Want me to draft a handoff prompt?" If yes, run canon hygiene, then draft.   |
| "Break this down for me" / "dumb it down" / "plain English" | The explanation was too technical.                            | Restate from scratch in everyday language. No code terms, no unexplained acronyms. |
| "Shift gears" / "switch gears"                              | Change topics now.                                            | Acknowledge, save context if needed, follow the new direction.                     |
| "Hold on that" / "wait"                                     | Don't act yet.                                                | Pause. Wait for the operator to come back.                                         |
| "Send them over" / "send it"                                | Generate the deliverable just discussed.                      | Create the ticket, prompt, or document using direct tools.                         |
| "Log this" / "remember this" / "commit that"                | Save to Project Memory.                                       | Write to miru_memory.db using the appropriate table.                               |
| "What's the status?" / "where are we?"                      | Quick summary of current state.                               | Pull from Linear, Notion, and Project Memory. Report in 3-5 bullets.               |
| "You have access to X, use it"                              | Correction: Claude was routing when it has the tools.         | Do it directly. Remember this for the rest of the thread and future threads.       |
| "File it" / "ticket it"                                     | Create a Linear ticket.                                       | File with standard structure (goal, fix, done when, don't touch).                  |

---

## Project-Specific Terms

| Term                  | What it means                                                                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| "The loop"            | The n8n routing automation (W1 → W2 → W7 → W4 → completion). Takes a task from "operator describes it" to "worker does it." |
| "W2" / "the router"   | Worker Selection Router in n8n. Polls Linear every 3 minutes, proposes a worker via Telegram.                               |
| "W7"                  | Telegram Callback Handler. Processes approve/override/triage taps.                                                          |
| "W4" / "the listener" | Dispatch Listener on port 19100. Spawns worker processes from HMAC-signed requests.                                         |
| "The DLQ"             | Dead letter queue (data/dispatch_dlq.jsonl). Where failed dispatches go.                                                    |
| "PM"                  | Project Miru — the user-facing storefront at port 18080.                                                                    |
| "ROOM"                | The canonical machine (GMKtec K12 mini-PC). Everything runs here.                                                           |
| "Canon"               | The agreed-upon source of truth. Stored in Notion. "That's canon" = it's an established rule.                               |
| "Canon flip"          | Changing an established rule. Requires a new decisions row with `supersedes`.                                               |
| "Drift"               | When different surfaces disagree about what's true.                                                                         |
| "Smoke test"          | Quick verification that something works in production.                                                                      |
| "Promote" (a ticket)  | Move from Backlog to Todo so the loop can see it.                                                                           |
| "Surface"             | A place where information lives — Notion, Linear, repo docs, or Project Memory.                                             |
| "Worker"              | An AI coding assistant that executes tasks (Claude Code, Cursor, Codex, etc.).                                              |
| "Peer review"         | Sending a design question to Gemini, ChatGPT, or Perplexity for a second opinion.                                           |
| "CC"                  | Claude Code.                                                                                                                |

---

## Status Language Translation

| Technical status                    | Say this instead                                                             |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| PR merged, branch deleted, CI green | "It's done and live"                                                         |
| PR open, awaiting review            | "The code is ready for you to look at"                                       |
| Worker reported CONFIRMED WORKING   | "The worker says it's working"                                               |
| Worker reported INCONCLUSIVE        | "The worker isn't sure — we need to check"                                   |
| Worker reported FAILED              | "It didn't work — here's what went wrong"                                    |
| Ticket in Backlog                   | "Parked — not being worked on yet"                                           |
| Ticket in Todo                      | "In the queue — the loop will pick it up"                                    |
| Ticket In Progress                  | "A worker is actively on this"                                               |
| Ticket In Review                    | "Worker says it's done, but we haven't verified yet"                         |
| Ticket Done                         | "Verified and closed"                                                        |
| Dispatch timed out, in DLQ          | "The worker ran out of time. The failure is logged but nobody got notified." |

---

## Phrases That Signal Claude Was Too Technical

If the operator says any of these, Claude was too technical and should restate from scratch:

- "What does that mean?"
- "In English?"
- "Break that down"
- "I don't follow"
- "Huh?"
- "Explain like I'm five"
- "Dumb it down"
- "What are you saying?"

---

## Maintenance

This file grows over time. Claude proposes additions when:

- The operator uses a new shorthand phrase that Claude had to ask about
- A misunderstanding happens more than once
- A new project concept is introduced that the operator references by shorthand

Claude proposes, operator approves, then it's permanent.
