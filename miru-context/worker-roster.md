# Worker Roster — Project Miru

This file is the single source of truth for which workers are active, what each one
is good at, and which model to use for each job. Read it when routing a task or
evaluating parallel execution options.

---

## AI Workers (CLI workers — dispatched via W4 on port 19100)

| Worker        | Binary       | Auth                                                            | Best for                                                                                             |
| ------------- | ------------ | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `claude-code` | `claude.cmd` | `CLAUDE_CODE_OAUTH_TOKEN` (OAuth, subscription — no API charge) | Backend code, multi-file Python refactors, test writing, full task ownership                         |
| `gemini`      | `gemini.cmd` | Gemini CLI stored auth                                          | Second opinions, large-context reads (whole service in one pass), multimodal, alternative approaches |
| `codex`       | `codex.cmd`  | Stored auth                                                     | Cross-file bug hunting, contract verification, architecture audits, refactor planning                |

**Auth rule:** All dispatches use `CLAUDE_CODE_OAUTH_TOKEN` (OAuth, subscription — no API charge). `ANTHROPIC_API_KEY` is stripped from the child env at spawn time. There is no `use_api_key` dispatch flag — OAuth is the only supported auth path.

---

## IDE Workers (Cursor — manual dispatch only)

| Worker   | Access              | Best for                                                                                       |
| -------- | ------------------- | ---------------------------------------------------------------------------------------------- |
| `cursor` | Cursor Pro+ desktop | UI/UX execution — HTML templates, CSS, JS, component work, mobile-first layout, gesture wiring |

**Dispatch pattern — permanent manual (not wired to W4):**

Cursor CLI has confirmed failure modes that disqualify it from autonomous W4 dispatch on
Windows: concurrent spawn race condition (one process exits status 1 silently), `--print`
hangs with no output, silent exit 0 with empty output, and Windows path resolution writing
to AppData instead of the workspace. Assessed 2026-05-02 via production evidence.

The permanent dispatch pattern is:

1. Claude Chat files the Linear ticket with the full spec (scope, done-when, don't-touch list).
2. Claude Chat sends one Telegram ping to the operator with the ticket ID and a ready-to-paste prompt:
   `"[PRO-XXX] Cursor task ready — paste in Cursor: 'Read PRO-XXX in Linear and complete the task. Follow AGENTS.md and CLAUDE.md for all rules.'"`
3. Ticket stays in **Todo** until operator confirms Cursor is running.
4. Operator remote-opens Cursor on `miru-cursor` worktree and pastes the prompt.
5. Cursor reads the Linear ticket via MCP for the full spec — no elaborate prompt needed.
6. Operator marks the ticket Done in Linear when Cursor finishes (or Claude Chat reads the completion log if Cursor wrote a marker).

The Linear ticket carries the spec. The paste is only the handoff trigger. Claude Chat does
not need to generate a full prompt wrapper for Cursor — the ticket IS the prompt.

Cursor owns `pm/templates/`, `pm/static/js/`, `pm/static/css/`.
Python route changes go to `claude-code`, not Cursor.

---

## Local Ollama Models (running on port 11434 — always free, no rate limits)

| Model                   | Role                       | When to use                                                                          |
| ----------------------- | -------------------------- | ------------------------------------------------------------------------------------ |
| `llama3.2:3b`           | Dispatch / recovery router | Fast yes/no decisions, health checks, quick analysis. Default for MiruSentinel.      |
| `qwen2.5:7b`            | General task routing       | Routing decisions, task classification, general reasoning when llama3.2 isn't enough |
| `qwen2.5-coder:7b`      | Repo checks                | Daily code review, checking changed files for issues                                 |
| `qwen2.5-coder:14b`     | Deep repo checks           | Slower, stronger code review. Use when 7b flags something worth a second look.       |
| `qwen2.5:14b`           | Heavy router fallback      | Only when confidence is low on a routing decision                                    |
| `embeddinggemma:latest` | Retrieval / search         | Context lookup, semantic search over repo content                                    |

**Routing rule:** Always try the smaller/faster model first. Escalate to the larger model
only when the smaller one returns low confidence or flags something it cannot resolve.

---

## Cloud Workers (used via Claude Chat's MCP tools — not dispatched via W4)

| Worker                 | Access         | Best for                                                      |
| ---------------------- | -------------- | ------------------------------------------------------------- |
| Claude Chat (Opus 4.7) | claude.ai      | Architecture decisions, planning, routing, session management |
| Gemini (chat)          | Chat app       | Peer architect review, alternative approaches                 |
| Perplexity             | MCP + chat app | Research with citations, practitioner patterns                |
| ChatGPT                | Chat app       | Second opinion, structuring, orchestration help               |

---

## Model Assignments by Task Type

| Task                                  | Use this                     |
| ------------------------------------- | ---------------------------- |
| Sentinel health check                 | `llama3.2:3b` (Ollama)       |
| Stall routing decision                | `llama3.2:3b` (Ollama)       |
| General task routing                  | `qwen2.5:7b` (Ollama)        |
| Code change review                    | `qwen2.5-coder:7b` (Ollama)  |
| Deep code audit                       | `qwen2.5-coder:14b` (Ollama) |
| Complex backend execution             | `claude-code` (OAuth)        |
| Routine backend execution             | `claude-code` (OAuth)        |
| UI/UX execution (templates, CSS, JS)  | `cursor` (manual)            |
| Cross-file bug / contract audit       | `codex` or `gemini`          |
| Second opinion / alternative approach | `gemini`                     |
| Architecture decision                 | Claude Chat (Opus 4.7)       |

---

## Worker Capability Profiles

Structured profiles for routing and budget decisions. Routing uses "Best for";
budget governance uses "Cost bucket"; dispatch wiring uses "Dispatch mode."

### claude-code

| Attribute                      | Value                                                                       |
| ------------------------------ | --------------------------------------------------------------------------- |
| Dispatch mode                  | Headless CLI (via W4 / Dispatch Listener on port 19100)                     |
| Can run headless               | Yes                                                                         |
| Can be monitored via heartbeat | Yes — emits to `data/cc_heartbeat_log.jsonl`                                |
| Model / effort tuneable        | OAuth only — no per-dispatch model override                                 |
| Cost bucket                    | Free (OAuth subscription; no API billing)                                   |
| Requires operator involvement  | For operator-column PRs, ESCALATE signals, or REPEATED_FAILURE              |
| Known limitations              | Cannot touch HTML/CSS/JS templates, `.mcp.json` files, or `card_catalog.db` |

### codex

| Attribute                      | Value                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------- |
| Dispatch mode                  | Headless CLI (via W4 / Dispatch Listener on port 19100)                             |
| Can run headless               | Yes                                                                                 |
| Can be monitored via heartbeat | Yes — emits heartbeats if configured                                                |
| Model / effort tuneable        | Yes — standard Codex model                                                          |
| Cost bucket                    | Low-Medium (API-billed)                                                             |
| Requires operator involvement  | For scope expansion, ESCALATE signals                                               |
| Known limitations              | Does not autonomously edit CLAUDE.md or worker prompts; executes assigned work only |

### gemini

| Attribute                      | Value                                                                                                 |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Dispatch mode                  | Headless CLI (via W4 / Dispatch Listener on port 19100)                                               |
| Can run headless               | Yes                                                                                                   |
| Can be monitored via heartbeat | Limited — does not emit Miru heartbeat format by default                                              |
| Model / effort tuneable        | Yes — Gemini Pro, large context                                                                       |
| Cost bucket                    | Low                                                                                                   |
| Requires operator involvement  | For ESCALATE signals                                                                                  |
| Known limitations              | Better as validation/second-opinion worker than primary executor; large context is its main advantage |

### cursor

| Attribute                      | Value                                                                                                                                      |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Dispatch mode                  | Manual only — operator pastes ticket reference in Cursor IDE on `miru-cursor` worktree. Not wired to W4 (permanent, not deferred).         |
| Can run headless               | CLI exists but has critical failure modes on Windows: concurrent race condition, `--print` hangs, silent exit 0. Not used for automation.  |
| Can be monitored via heartbeat | No — Cursor does not emit Miru heartbeat format                                                                                            |
| Model / effort tuneable        | Via Cursor Pro+ settings                                                                                                                   |
| Cost bucket                    | None (subscription; no per-task API cost)                                                                                                  |
| Requires operator involvement  | Always — operator relays the dispatch by opening Cursor and pasting the ticket ID prompt                                                   |
| Known limitations              | CSS specificity and layout debugging less reliable without visual feedback; use for structure/logic/JS first. No autonomous dispatch path. |

---

## Load-on-demand trigger

Read this file when:

- Choosing which worker to dispatch a task to
- Evaluating whether to run workers in parallel
- Deciding which Ollama model to call for an automated task
- Checking a worker's cost bucket or dispatch mode for budget-aware routing
