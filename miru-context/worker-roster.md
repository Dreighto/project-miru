# Worker Roster — Project Miru

This file is the single source of truth for which workers are active, what each one
is good at, and which model to use for each job. Read it when routing a task or
evaluating parallel execution options.

---

## AI Workers (CLI workers — dispatched via W4 on port 19100)

| Worker        | Binary       | Auth                                                                              | Best for                                                                                             |
| ------------- | ------------ | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `claude-code` | `claude.cmd` | OAuth (subscription, no charge) by default; `use_api_key: true` for complex tasks | Backend code, multi-file Python refactors, test writing, full task ownership                         |
| `gemini`      | `gemini.cmd` | Gemini CLI stored auth                                                            | Second opinions, large-context reads (whole service in one pass), multimodal, alternative approaches |
| `codex`       | `codex.cmd`  | Stored auth                                                                       | Cross-file bug hunting, contract verification, architecture audits, refactor planning                |

**Auth rule:** Routine dispatches default to OAuth (no API charge). Set `use_api_key: true`
in the dispatch payload for recovery dispatches or tasks that need full Sonnet-level reasoning.

---

## IDE Workers (Cursor — headless CLI wiring in progress)

| Worker   | Access              | Best for                                                                                       |
| -------- | ------------------- | ---------------------------------------------------------------------------------------------- |
| `cursor` | Cursor Pro+ desktop | UI/UX execution — HTML templates, CSS, JS, component work, mobile-first layout, gesture wiring |

**Dispatch note:** Cursor CLI (`cursor agent -p`) is confirmed available as a headless binary
and is the target dispatch path. Wiring into W4 is tracked in PRO-253 (Backlog).

Until PRO-253 ships: Claude Chat preps the ticket in Linear with full spec, then sends one
Telegram ping to the operator: "Cursor task ready — [PRO-XXX title]. Open Cursor and assign
when ready." Ticket stays in **Todo** (not In Progress) until Cursor starts. Operator marks
Done in Linear when Cursor finishes.

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

| Task                                  | Use this                               |
| ------------------------------------- | -------------------------------------- |
| Sentinel health check                 | `llama3.2:3b` (Ollama)                 |
| Stall routing decision                | `llama3.2:3b` (Ollama)                 |
| General task routing                  | `qwen2.5:7b` (Ollama)                  |
| Code change review                    | `qwen2.5-coder:7b` (Ollama)            |
| Deep code audit                       | `qwen2.5-coder:14b` (Ollama)           |
| Complex backend execution             | `claude-code` with `use_api_key: true` |
| Routine backend execution             | `claude-code` with OAuth (default)     |
| UI/UX execution (templates, CSS, JS)  | `cursor` (manual)                      |
| Cross-file bug / contract audit       | `codex` or `gemini`                    |
| Second opinion / alternative approach | `gemini`                               |
| Architecture decision                 | Claude Chat (Opus 4.7)                 |

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
| Model / effort tuneable        | Yes — OAuth (default) or `use_api_key: true` for Sonnet-level               |
| Cost bucket                    | Medium (API-billed on `use_api_key: true`; free via OAuth)                  |
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

| Attribute                      | Value                                                                                                        |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| Dispatch mode                  | Headless CLI (`cursor agent -p`, beta) — W4 wiring pending (PRO-253); fallback: operator Telegram ping       |
| Can run headless               | Yes (Cursor CLI beta) — W4 wiring not yet complete                                                           |
| Can be monitored via heartbeat | No — Cursor CLI does not emit Miru heartbeat format                                                          |
| Model / effort tuneable        | Via Cursor Pro+ settings                                                                                     |
| Cost bucket                    | None (subscription; no per-task API cost)                                                                    |
| Requires operator involvement  | Until PRO-253 ships: operator opens Cursor and assigns ticket. After: fully autonomous via W4.               |
| Known limitations              | CSS specificity and layout debugging less reliable without visual feedback; use for structure/logic/JS first |

---

## Load-on-demand trigger

Read this file when:

- Choosing which worker to dispatch a task to
- Evaluating whether to run workers in parallel
- Deciding which Ollama model to call for an automated task
- Checking a worker's cost bucket or dispatch mode for budget-aware routing
