# Project Miru — Full System Context

**Date:** 2026-05-06
**Author:** Claude Code (VP Ops), compiled for peer-LLM review
**Purpose:** This document gives you complete context on Project Miru's architecture, autonomous worker system, and current state so you can answer specific questions about the system.

---

## What Project Miru Is

Project Miru is an autonomous multi-agent software development system built and operated by Dreighto (the "Captain"). Dreighto is not a programmer — he is a builder and systems thinker who designs the architecture and makes decisions while AI workers execute the code.

The system runs on a local machine (GMKtec K12: Ryzen 7 8745H, 32GB DDR5, Radeon 780M iGPU) and coordinates multiple AI coding agents to work on software projects autonomously. The operator approves work via Telegram, and workers handle everything from branch creation to PR merging without human coding intervention.

**Core philosophy:** Governed Autonomy — workers operate independently within strict behavioral guardrails. The operator makes decisions; workers execute.

---

## Architecture Overview

### Services (always running)

| Service           | Port       | What it does                                   |
| ----------------- | ---------- | ---------------------------------------------- |
| PM Storefront     | 18080      | User-facing web app (card market intelligence) |
| Miru AI           | 18765      | AI backend service                             |
| Ollama            | 11434      | Local LLM inference (free, no API cost)        |
| Dispatch Listener | 19100      | Receives dispatch requests, spawns workers     |
| MCP Gateway       | (internal) | Tool access control layer for workers          |
| n8n               | (Docker)   | Workflow automation engine (14 workflows)      |

### The Autonomous Dispatch Loop

This is the core automation. End to end:

```
1. TICKET CREATED → Linear (Todo status)
         ↓
2. W2 ROUTER (n8n, polls every 3 min)
   - Finds new Todo tickets
   - Classifies: which worker, what risk, what tool profile
   - Sends Telegram proposal to operator
         ↓
3. OPERATOR TAPS APPROVE (Telegram)
         ↓
4. W7 CALLBACK HANDLER (n8n)
   - Records approval in pending_callbacks.jsonl
         ↓
5. W4 DISPATCH (n8n)
   - Reads approval + ticket spec from Linear
   - Assembles prompt with worker rules
   - HTTP POST to Dispatch Listener (:19100) with HMAC signature
   - Moves ticket to "In Progress" in Linear
         ↓
6. DISPATCH LISTENER (Node.js on :19100)
   - Verifies HMAC signature
   - Checks idempotency (no duplicate dispatches)
   - Leases a worktree slot (6 slots: miru-w1 through miru-w6)
   - Calls spawn.js
         ↓
7. SPAWN.JS
   - Probes worker binary (claude --version)
   - Sanitizes environment:
     * Strips ROOM_TOKEN_OPERATOR (full-access GitHub token)
     * Injects GH_TOKEN = ROOM_TOKEN_WORKER (restricted token)
     * Strips all legacy auth vars
   - Writes prompt to temp file (avoids cmd.exe escaping issues)
   - Spawns worker process with timeout
         ↓
8. WORKER RUNS (Claude Code or Gemini CLI)
   - Pre-flight: kill switch check, worktree cleanliness check
   - Cuts branch, reads ticket, writes code, runs tests
   - Opens PR, waits for CodeRabbit review, fixes findings
   - Self-merges if low-risk, or pings operator for approval
   - Emits heartbeats to cc_heartbeat_log.jsonl during work
   - Writes completion marker to cc_completion_log.jsonl when done
   - Cleans up branch, returns to main
         ↓
9. COMPLETION BRIDGE (n8n, every 60s)
   - Reads new completion markers
   - Updates Linear ticket → Done
   - Posts completion comment on ticket
         ↓
10. CC COMPLETION PING (n8n, every 30s)
    - Sends Telegram notification to operator: "Done. Here's what shipped."
```

### Parallel Safety Systems

| System                    | Frequency          | What it does                                                                                            |
| ------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------- |
| Stall Watcher             | Every 3 min        | Checks heartbeat freshness. If last heartbeat > 5 min old with no completion marker → worker is STALLED |
| Recovery Router           | On stall detection | 1 auto-retry per ticket, then escalates to operator via Telegram                                        |
| DLQ Watcher               | Continuous         | Monitors dispatch dead-letter queue for failed spawns                                                   |
| Drift Scanner             | Daily              | Compares Linear ticket states vs completion markers for consistency                                     |
| Pending Approval Watchdog | Periodic           | Cleans up stale approvals that were never dispatched                                                    |

---

## Worker Roster

### Production Autonomous Workers (dispatched via the loop)

| Worker        | Binary     | Auth                                            | Best for                                                                 | Cost                |
| ------------- | ---------- | ----------------------------------------------- | ------------------------------------------------------------------------ | ------------------- |
| `claude-code` | claude.cmd | OAuth token (subscription, no per-token charge) | Backend Python, multi-file refactors, test writing, full task ownership  | Free (subscription) |
| `gemini`      | gemini.cmd | Gemini CLI stored auth                          | Second opinions, large-context reads, multimodal, alternative approaches | Low                 |

### Manual Workers

| Worker   | How dispatched                                   | Best for                                        |
| -------- | ------------------------------------------------ | ----------------------------------------------- |
| `cursor` | Operator manually pastes ticket ID in Cursor IDE | UI/UX — HTML templates, CSS, JS, component work |

### Benched Workers

| Worker  | Status                   | Reason                                                            |
| ------- | ------------------------ | ----------------------------------------------------------------- |
| `codex` | Benched since 2026-05-04 | MCP transport stalls, 17-min hangs, not autonomously dispatchable |

### Local Ollama Models (free, no API cost)

| Model             | Role                                                          |
| ----------------- | ------------------------------------------------------------- |
| llama3.2:3b       | Fast routing decisions, health checks                         |
| qwen2.5:7b        | Task classification, general reasoning, Gatekeeper validation |
| qwen2.5-coder:7b  | Daily code review                                             |
| qwen2.5-coder:14b | Deep code review (when 7b flags something)                    |
| qwen2.5:14b       | Heavy routing fallback (low confidence decisions)             |

### Cloud Workers (not dispatched — used via chat)

| Worker                 | Access         | Best for                                              |
| ---------------------- | -------------- | ----------------------------------------------------- |
| Claude Chat (Opus 4.7) | claude.ai      | Lead architect, planning, routing, session management |
| Gemini (chat)          | Chat app       | Peer review, alternative approaches                   |
| Perplexity             | MCP + chat app | Research with citations                               |
| ChatGPT                | Chat app       | Second opinions, structuring                          |

---

## Tool Profile System (MCP Gateway)

Workers get different levels of tool access based on their assigned profile. The MCP Gateway enforces these restrictions.

| Profile           | Who gets it                   | Can do                                                              | Cannot do                                                                                 |
| ----------------- | ----------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `drift_executor`  | Routine audit tasks           | Read everything (filesystem, GitHub, n8n, logs, memory, Perplexity) | Write to Linear, n8n, docs, git; send Telegram; dispatch; restart services; VP Ops verify |
| `reviewer`        | Ambiguous/investigation tasks | Same as drift_executor                                              | Same restrictions as drift_executor                                                       |
| `standard_worker` | Normal ticket execution       | Everything drift_executor can + write to Linear, n8n, docs, git     | Send Telegram; dispatch other workers; restart services; VP Ops verify                    |
| `vp_ops`          | VP Ops verification           | Everything standard_worker can + VP Ops verification                | Send Telegram; dispatch; restart services                                                 |
| `full_operator`   | Operator's direct session     | Everything (unrestricted)                                           | Nothing restricted                                                                        |

The W2 Router automatically assigns profiles based on task classification:

- **Routine tasks** (audit, read-only, scan) → `drift_executor`
- **Judgment tasks** (bugs, features, improvements) → `standard_worker`
- **Ambiguous tasks** (investigate, explore, unclear) → `reviewer` (plan-only mode)
- **Blocked tasks** → no dispatch

---

## Token Architecture

Two-tier GitHub Personal Access Token system:

| Token                 | Scope                                            | Who uses it                                |
| --------------------- | ------------------------------------------------ | ------------------------------------------ |
| `ROOM_TOKEN_OPERATOR` | Full repo access + account admin (repo creation) | Operator's direct sessions, `gh` CLI auth  |
| `ROOM_TOKEN_WORKER`   | Repo read/write only, no account admin           | All spawned workers (injected as GH_TOKEN) |

`spawn.js` strips all operator-level tokens from the child environment before injecting the worker token. Workers cannot accidentally use the operator's credentials.

---

## n8n Workflows (14 total)

| Workflow                     | Purpose                                                             |
| ---------------------------- | ------------------------------------------------------------------- |
| w2_worker_selection_router   | Polls Linear for Todo tickets, classifies, sends Telegram proposals |
| w7-telegram-callback-handler | Processes operator approvals/rejections from Telegram               |
| w4-dispatch-button-handler   | Reads approvals, assembles prompts, POSTs to dispatch listener      |
| w-stall-watcher              | Monitors heartbeat freshness, triggers recovery                     |
| w-linear-completion-bridge   | Polls completion log, updates Linear tickets to Done                |
| w-cc-completion-ping         | Sends Telegram notifications when workers finish                    |
| w-dlq-watcher                | Monitors dead-letter queue for failed dispatches                    |
| w-drift-scanner              | Daily Linear-vs-completion consistency check                        |
| w2_pending_approval_watchdog | Cleans up stale pending approvals                                   |
| w8_callbacks_gc              | Garbage collection for old callback entries                         |
| w8-telegram-command-handler  | Processes Telegram slash commands                                   |
| w1-planning-intake           | Planning workflow intake                                            |
| w1-error-handler             | Global error handling                                               |
| w-mcp-n8n-write-notify       | Notifications for n8n write operations                              |

---

## Data Files (Append-Only Audit Trail)

These files are strictly append-only. Never edit, truncate, sort, or deduplicate.

| File                            | Purpose                                                                 |
| ------------------------------- | ----------------------------------------------------------------------- |
| `data/cc_completion_log.jsonl`  | Terminal completion markers (CONFIRMED_WORKING / INCONCLUSIVE / FAILED) |
| `data/cc_heartbeat_log.jsonl`   | Worker liveness signals during tasks                                    |
| `data/routing_history.jsonl`    | W2 routing decisions with rationale                                     |
| `data/pending_callbacks.jsonl`  | Telegram approval/rejection ledger                                      |
| `data/dispatch_dlq.jsonl`       | Dead-letter queue for failed dispatches                                 |
| `data/vp_ops_supervision.jsonl` | VP Ops verification records                                             |
| `data/drift_scanner_log.jsonl`  | Daily drift scan results                                                |
| `data/agent_decisions.jsonl`    | Phase 2 Judgment Trail / agent decision records                         |

---

## Project Management (Linear)

Linear is the ticket system. Organized into Teams and Projects:

**Team: Project Miru**

- PM Storefront — user-facing card market features
- Miru Orchestration / Autonomy — worker dispatch, routing, autonomy rules
- Tooling / MCP Gateway — MCP server config, tool permissions
- Automation / Integrations — n8n workflows, Telegram bots, alerts
- Memory / Context System — memory files, context boot, session continuity
- Docs / Canon / Process — operating docs, process rules
- Research / Experiments — spikes, evals, benchmarks

**Team: NASDOOM**

- NASDOOM Dashboard — NAS management UI (SvelteKit, Plex/Sonarr/Radarr/SABnzbd)

Workers communicate through Linear comments. A worker finishing a task can leave notes for the next worker picking up related work.

---

## Worker Framework (Universal SOP)

All workers follow a universal operating baseline defined in `docs/worker-framework/`:

- **AGENTS.md** — communication standards, PR review sequence, merge policy, completion contract, try-harder discipline (applies to ALL workers)
- **CLAUDE.md** — Claude Code specific rules (pre-flight gates, file ownership, heartbeat emission)
- **GEMINI.md** — Gemini CLI specific rules (configuration, UI quality standards, headless output format)

New projects copy these 3 files, add a project-specific overlay, and workers are operational immediately.

### Key Rules

**Merge Policy (3 tiers):**

1. Direct-to-main — typos, version bumps, log entries (no PR needed)
2. Worker self-merge — single-file fixes, config changes, test fixtures
3. Operator merge — new files/directories, schema changes, infrastructure

**Completion Contract:** Every task ends with exactly one status:

- CONFIRMED_WORKING — verified, merged, system in expected state
- INCONCLUSIVE — attempted but couldn't confirm (must include specific question)
- FAILED — attempted, does not meet acceptance criteria

**Try-Harder Discipline:** Before saying "I can't," workers must: check the canon docs, search the repo, try at least one alternative approach, THEN ask with evidence of what was tried.

---

## Current Brainstorming Topics (Active Design — Not Yet Built)

### 1. Job Splitter (Parallel Dispatch)

Big tickets should be automatically split into smaller scoped sub-tickets that run in parallel. Design:

- Complexity classifier in W2 detects multi-service/multi-file tickets
- LLM-powered splitter proposes 2-3 sub-tickets with file boundaries
- Operator gets ONE Telegram message with the proposed split
- On approval, sub-tickets auto-approve and dispatch independently
- Parent ticket auto-closes when all sub-tickets complete
- Assembly-line model: workers do their piece, shared quality gate (PR/review/merge) at the end

### 2. Dispatcher Toolkit Packing

Instead of every worker getting the same generic toolbox, the dispatcher should pack a task-specific briefcase:

- Database ticket → sqlite-ro-snapshot tool, relevant table names, last migration
- Frontend ticket → preview tools, dev server command, design token file
- Workflow ticket → n8n tools, workflow ID, last failed execution
  Workers start productive immediately instead of spending time orienting.

### 3. OpenClaw Integration (Observability Layer)

OpenClaw (github.com/openclaw/openclaw) as a high-density management dashboard — NOT replacing Telegram, but complementing it:

- Telegram stays for quick mobile approvals (W7 callback handler)
- OpenClaw dashboard for forensic auditing of Judgment Trail, agent decisions, routing history
- Reading JSONL reasoning blocks on a dashboard vs a phone screen

### 4. Hermes Agent (Learning Worker)

Hermes Agent (github.com/NousResearch/hermes-agent) as a potential new worker type:

- Core feature: skill persistence — learns from completed tasks, reuses patterns
- Could run on Ollama locally ($0 cost)
- Handle repetitive/routine tasks that benefit from learned patterns
- Subagent spawning with file coordination (relevant to job splitting)
- Not a replacement for the dispatch system — an ingredient

---

## Repo Structure

```
D:\dev\miru\
├── miru_ai/          — Miru AI service (port 18765)
├── pm/               — PM Storefront (port 18080)
├── gatekeeper/       — Local Governance Gatekeeper (dispatch validation)
├── shared/           — Utilities imported by 2+ services
├── tools/            — Standalone scripts and MCP gateway
│   ├── miru_mcp_gateway/  — MCP Gateway server (tool access control)
│   ├── orchestrator/      — Stall detector, recovery router
│   ├── emit_completion.py — Writes completion markers
│   ├── emit_heartbeat.py  — Writes heartbeat signals
│   ├── check_kill_switch.py — Pre-flight safety gate
│   └── vp_ops_verify.py   — VP Ops ticket verification
├── services/
│   └── dispatch_listener/ — Node.js dispatch listener (port 19100)
│       └── src/
│           ├── spawn.js      — Worker spawning with token isolation
│           ├── allowlist.js  — Worker binary allowlist
│           └── worktree.js   — 6-slot worktree management
├── docker/
│   └── n8n/workflows/     — 14 n8n workflow JSON files
├── docs/
│   └── worker-framework/  — Universal worker SOP (AGENTS.md, CLAUDE.md, GEMINI.md)
├── data/                  — Append-only JSONL logs, configs, databases
├── miru-context/          — Operator profile, job stewardship, worker roster
├── tests/                 — Test suite
├── windows/               — Windows service management scripts
├── config/                — Configuration files
├── CLAUDE.md              — Project-level Claude Code rules
├── AGENTS.md              — Project-level worker baseline
└── .env                   — Environment variables (secrets, tokens)
```

---

## Hardware

- **Machine:** GMKtec K12 ("ROOM Node")
- **CPU:** AMD Ryzen 7 8745H (8 cores)
- **GPU:** Radeon 780M (RDNA3 integrated)
- **RAM:** 32GB DDR5
- **OS:** Windows 11 Pro
- **Ollama:** Running locally on port 11434, Vulkan backend

---

## Key Design Principles

1. **Governed Autonomy** — workers are autonomous within guardrails, not fully independent
2. **Fail-closed** — when unsure, stop and ask the operator
3. **Append-only audit trail** — every decision, dispatch, completion, and stall is logged permanently
4. **Token isolation** — workers never get operator-level credentials
5. **Canon freshness** — stale instructions are an existential risk; update docs immediately when truth changes
6. **Assembly line** — the future direction: workers do their piece, quality gates are shared
7. **Local-first** — data stays on the ROOM node; external services are tools, not dependencies
