# Worker Roster — Project Miru

This file is the single source of truth for which workers are active, what each one
is good at, and which model to use for each job. Read it when routing a task or
evaluating parallel execution options.

---

## AI Workers (Claude Code workers — dispatched via W4 on port 19100)

| Worker        | Binary       | Auth                                                                              | Best for                                                              |
| ------------- | ------------ | --------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `claude-code` | `claude.cmd` | OAuth (subscription, no charge) by default; `use_api_key: true` for complex tasks | Backend code, multi-file refactors, test writing, full task ownership |
| `gemini`      | `gemini.cmd` | Gemini CLI stored auth                                                            | General coding tasks, analysis, alternative approach pressure-testing |
| `codex`       | `codex.cmd`  | Stored auth                                                                       | Code analysis, large-context reads                                    |

**Auth rule:** Routine dispatches default to OAuth (no API charge). Set `use_api_key: true`
in the dispatch payload for recovery dispatches or tasks that need full Sonnet-level reasoning.

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

| Task                      | Use this                               |
| ------------------------- | -------------------------------------- |
| Sentinel health check     | `llama3.2:3b` (Ollama)                 |
| Stall routing decision    | `llama3.2:3b` (Ollama)                 |
| General task routing      | `qwen2.5:7b` (Ollama)                  |
| Code change review        | `qwen2.5-coder:7b` (Ollama)            |
| Deep code audit           | `qwen2.5-coder:14b` (Ollama)           |
| Complex backend execution | `claude-code` with `use_api_key: true` |
| Routine backend execution | `claude-code` with OAuth (default)     |
| Architecture decision     | Claude Chat (Opus 4.7)                 |

---

## Load-on-demand trigger

Read this file when:

- Choosing which worker to dispatch a task to
- Evaluating whether to run workers in parallel
- Deciding which Ollama model to call for an automated task
