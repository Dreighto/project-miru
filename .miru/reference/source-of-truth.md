# Reference — Source of Truth + Scope

```text
Reference: source-of-truth
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: deciding where information belongs, resolving a conflict between sources, planning a canon refresh, or onboarding a worker.
Last reviewed: 2026-05-10
```

This file is the **meta-rule** that governs every other canon rule. When the rules here disagree with anything else: this file wins. When workers don't know where to put something or which source to trust: load this and follow the procedure.

Codified after the 2026-05-09 round of canon-drift incidents (gemini-cli role downgrade in canon refresh round 1, Notion 5-day staleness, GPT-still-thinks-CH-is-Router) revealed the system was operating without an explicit truth hierarchy. Synthesized from the 2026-05-09 GPT proposal "Reposition Notion as Canonical Context Authority" (`data/peer_reviews/2026-05-09_source_of_truth_proposal_gpt.md`) plus CC additions for current operating reality.

---

## The Truth Hierarchy

When two sources disagree, the higher-numbered source loses. ALWAYS. Recency is not authority — a 5-minute-old conversation note never beats a 5-day-old Linear ticket on questions of operational state.

| #   | Layer                           | What's authoritative                                                                                                                                                                                                                                                                                                                                                   |
| --- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Runtime reality**             | Live state of the running system: ports listening, processes alive, network responses.                                                                                                                                                                                                                                                                                 |
| 2   | **Verified execution evidence** | The 9-file DGAS audit chain: `cc_completion_log.jsonl`, `routing_history.jsonl`, `pending_callbacks.jsonl`, `dispatch_dlq.jsonl`, `cc_heartbeat_log.jsonl`, `vp_ops_supervision.jsonl`, `drift_scanner_log.jsonl`, `agent_decisions.jsonl`, `github_resource_ledger.jsonl`. SHA-256 hash chain + daily anchor. Append-only — never edit, never reorder.                |
| 3   | **Linear operational state**    | Ticket lifecycle, task queues, dispatch coordination, locked design specs in ticket descriptions. Source of truth for "what's the work right now."                                                                                                                                                                                                                     |
| 4a  | **Repo executable state**       | Code on `main` — applies per-repo across `Dreighto/project-miru` AND `Dreighto/LogueOS-Console` (and any future target repo in `WORKTREE_POOLS`). Source of truth for "what does the system DO." Multi-repo dispatch landed 2026-05-09; canon (4b) still lives in project-miru and is shared.                                                                          |
| 4b  | **Repo canonical state**        | Worker-read canon under `CLAUDE.md`, `AGENTS.md`, `miru-context/team-charter.md`, `.miru/overlays/*.md`, `.miru/reference/*.md`. Source of truth for "what are the rules."                                                                                                                                                                                             |
| 4c  | **Repo-tracked runtime DB**     | `data/miru_memory.db` — `worker_profile`, `stack_state`, `decisions`, `agenda`, etc. Source of truth for "what's the current operating state in machine-readable form." Append-decisions / update-stack_state — never silent rewrites.                                                                                                                                 |
| 5   | **Notion canon**                | Governance doctrine, architectural context, framework decisions, historical decisions worth preserving, reviewer-grounding context. Human-readable mirror of repo canon (4b) + DB (4c) + Linear (3) at slower cadence. Aimed at external reviewers (PXY, GPT, GMI, CH-when-back). NOT operational state, NOT a duplicate task tracker, NOT a runtime telemetry mirror. |
| 6   | **Worker memory**               | Per-worker persistent stores (e.g., `C:\Users\Dreighto\.claude\projects\D--dev-miru\memory\` for CC). Distilled facts the worker needs across sessions. NEVER overrides canon — if memory disagrees with canon, memory is wrong.                                                                                                                                       |
| 7   | **Conversation context**        | What's in the current chat window. NEVER authoritative. Always defer up the hierarchy when in doubt.                                                                                                                                                                                                                                                                   |

**Rule of thumb:** if you are about to write something based on what you remember from the conversation, STOP and check tier 1-5 first.

---

## System responsibilities

Each system has one job. If a piece of information would fit in two systems, the higher-tier one wins; don't duplicate.

| System                            | Role                                                                                         | What goes here                                                                                                                                       | What does NOT go here                                                                                                    |
| --------------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Runtime / Live process state**  | Source of "is it actually running"                                                           | Listening ports, process IDs, sessions, health-check responses                                                                                       | Specs, plans, history                                                                                                    |
| **Append-only audit logs (DGAS)** | Source of "what actually happened"                                                           | Completion markers, dispatch decisions, callbacks, heartbeats, supervision events, drift scans, agent decisions, GitHub resource lifecycle           | Specs, plans, current rules                                                                                              |
| **Linear**                        | Source of "what's the work"                                                                  | Tickets, ticket lifecycle, task queues, locked design specs in ticket descriptions, sprint coordination                                              | Architecture doctrine, framework decisions, completed-work history (those go to Notion canon + Work Log)                 |
| **Repo (code + canon + DB)**      | Source of "what does the system do" + "what are the rules" + "what's machine-readable state" | Code, tests, governance gates, worker rule files, canonical state docs (`roadmap.md`, `source-of-truth.md`), runtime DB, append-only audit logs      | External-reviewer onboarding context (Notion's job — repo canon is for workers and CI)                                   |
| **Notion (canon class)**          | Source of "what's the architecture + governance" for external reviewers                      | Governance doctrine, architectural context, framework decisions, historical decisions worth preserving, reviewer-grounding context, roster summaries | Live ticket queues, runtime telemetry, ephemeral brainstorming, duplicate operational state, scratch notes               |
| **Worker memory**                 | Per-worker session-bridging facts                                                            | Distilled rules the worker needs across sessions, references to canon (with pointers, not duplicates), operator-style preferences (e.g., tone)       | Anything that already lives in canon — point to canon instead of duplicating; speculative architecture; in-progress work |
| **Conversation context**          | Current task                                                                                 | The work in flight                                                                                                                                   | Authority over anything else                                                                                             |

---

## What "verified" means per layer

When the canon refresh discipline says "verified," it means a specific check per layer:

| Layer           | How to verify                                                                                                       |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| Runtime reality | `Get-NetTCPConnection`, process inspection, health endpoint hits.                                                   |
| Audit logs      | `tail` / `grep` against the JSONL files; SHA-256 chain validation if questioning integrity.                         |
| Linear          | `linear_get_issue` for each tracked ticket; cross-check ticket state vs. claimed shipped/parked.                    |
| Repo executable | `git log`, `gh pr list --state merged`, file existence checks, test runs.                                           |
| Repo canon      | `Read` the file and verify the `Last reviewed` stamp + content; `tools/check_canon_freshness.py` for cadence check. |
| DB              | Direct sqlite query against `miru_memory.db` (`stack_state`, `worker_profile`, `decisions`).                        |
| Notion canon    | `notion-search` + `notion-fetch`; cross-reference page timestamps vs. recent ships.                                 |
| Memory          | `Read` the memory index, scan for stale entries, cross-check against canon.                                         |

A claim is "verified" only when checked against the layer that's authoritative for that claim. "I remember it being..." is never verification.

---

## Conflict-resolution procedure

When the same fact disagrees across sources:

1. **Identify the authoritative layer** for that fact, using the Truth Hierarchy + System Responsibilities tables.
2. **Update lower-tier sources** to match the authoritative one. Never the reverse.
3. **Log the reconciliation** in the `decisions` table with `domain = canon_correction` and the source of truth that was treated as authoritative.
4. **If two sources at the same tier disagree** (e.g., two Notion pages, two memory files): file a `canon_drift` Linear ticket and stop using either as source until the operator resolves.
5. **Never silently accept a lower-tier source over a higher-tier one** — even if the lower-tier source is more recent. Recency is not authority.

**Worked example (2026-05-09 round 1):** Canon refresh shipped "gemini-cli = secondary loop worker" sourced from conversation context (#7). Linear PRO-304 (#3) said "gemini-cli = autonomous frontend." Per hierarchy: Linear wins. Round 4 corrected canon (4b), DB (4c), and Notion (5) to match. `decisions` log entry filed with `domain = canon_correction`. The conversation memory that produced the wrong wording was discarded — not preserved.

---

## Refresh trigger taxonomy

Which change classes trigger which source updates:

| Change class                              | What MUST update (in order)                                                                                                                            | Cadence                              |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| **Worker role / lane lock**               | Linear ticket → repo canon (team-charter / AGENTS / CLAUDE) → DB `worker_profile` → Notion 01 Now → memory                                             | Same session as the lock             |
| **Service ship (PR merged)**              | Repo `main` (auto, via merge) → DB `decisions` append → Notion Work Log append → `roadmap.md` update if status-bearing                                 | Within 24h of merge                  |
| **Service decommission**                  | Repo `main` (auto) → DB `stack_state` → `.miru/reference/ports-and-services.md` → Notion 01 Now → `roadmap.md`                                         | Same session as the decommission     |
| **New rule / lesson promoted**            | Repo `.miru/overlays/adopted-lessons.md` → DB `decisions` → Notion canon page if architectural                                                         | Same session as the promotion        |
| **New ticket filed**                      | Linear (auto on file) → `roadmap.md` if it changes near-term plan → memory `project_brainstorm_backlog`                                                | Within 24h of filing                 |
| **CH role transition (offline ↔ online)** | All canon files referencing CH → DB `stack_state` (`claude_chat_access_stage`, `orchestration_authority`) → memory `project_ch_status` → Notion 01 Now | Same session as the transition       |
| **Architecture pivot**                    | All of the above + a Work Log anchor entry in Notion                                                                                                   | Same session as the pivot            |
| **Periodic discipline check**             | Run `tools/check_canon_freshness.py` + read this file + verify against today's reality                                                                 | Every 3 days OR after any major ship |

The rule of thumb: **find your change in the table, do every column.** Don't skip Notion because "no one reads it" — external relays do.

---

## Operator-side vs worker-side context paths

Two distinct audiences consume canon. They have different paths and different entry points.

### Worker-side (loop workers — claude-code, gemini-cli, future Hermes)

- **Always loaded at dispatch:** `CLAUDE.md` core + `AGENTS.md` + `miru-context/team-charter.md`
- **Fetched on demand:** `.miru/overlays/*.md` (by task type), `.miru/reference/*.md` (for specific facts)
- **Authoritative spec source:** Linear ticket description
- **Rule:** workers never read Notion. If a fact isn't in repo canon (4b) or Linear (3), it doesn't exist for the worker.

### Operator-side (relay workers — PXY, GPT, GMI, CH-when-back; operator on phone or web)

- **Loaded via Notion connector:** `01 Now` (current state), `Work Log (Anchors)` (historical context), governance + architecture pages.
- **Loaded via Linear connector:** ticket state, sprint coordination.
- **Authoritative spec source:** Notion canon for architecture and governance; Linear for operational and current work.
- **Rule:** relay workers never load worker-only canon (`.miru/overlays/`, `.miru/reference/`). Notion is their context entry point. If a relay worker needs a worker-canon detail, the operator pastes it explicitly.

### When the same fact appears in both

The same fact in both must agree. When repo canon (worker path, 4b) and Notion (relay path, 5) disagree on a fact: repo wins (4b > 5), Notion gets updated next refresh. The operator-side path is a derived mirror.

---

## Notion as derived mirror, not primary write target

After the 2026-05-09 Notion API timeouts (which both landed but uncertainly), the operating model is:

- **Repo canon files are the primary write target.** Workers and CC update those first.
- **Notion is a derived mirror**, kept in sync at the canon-refresh cadence (every 3 days OR after a major ship).
- **Notion writes happen as a synchronization step**, not as the primary edit path.
- **If a Notion write fails or times out**, the repo source remains correct — re-sync on the next refresh. Always verify Notion writes via `notion-search` after a timeout; the API often persists despite the response failing.
- **Notion-only content** (governance philosophy that has never been worth bringing into repo canon) stays Notion-primary. CC has standing write authority for that per `.miru/overlays/domain-ops.md`. New canon should default to repo-primary unless there's a specific reason it belongs Notion-only (e.g., long-form architectural essays, multi-page strategic decisions).

This keeps the canon-source-of-truth question simple: it's the repo. Notion is the human-readable face for external reviewers.

---

## Acting roles while CH offline (2026-05-07 onward)

Until CH is back in the loop:

- **CC holds canon steward role.** Edit authority on all `.md` files (CLAUDE.md, AGENTS.md, miru-context/, .miru/overlays/, .miru/reference/) + Notion writes for maintenance categories.
- **CC holds Lead Architect role for tactical decisions.** Strategic decisions still require operator approval (per the existing operator-decision rules in `.miru/overlays/workflow-dispatch.md`).
- **CC files Linear loop tickets directly** via `linear_create_issue` — no operator paste step.
- **Hermes Stage 1 holds shadow-predictor role only.** No routing authority.
- **The Gatekeeper module holds dispatch validation role only.** No policy authority.

When CH returns: Lead Architect + Notion-default-writer + worker-prompt-authoring roles transition back to CH per the brief at `data/peer_reviews/2026-05-09_ch_role_brief.md`. CC keeps backend / Python / test / verification ownership and acting-orchestrator-when-CH-offline as a fallback.

---

## Verification Layer (concrete)

What "the verification layer" actually is — a collection of per-gate enforcement, not a centralized service:

- **VP Ops verification** — `tools/vp_ops_verify.py` + the `vp_ops_verify_ticket` MCP tool. Checks completion markers against git/Linear reality before any close.
- **DGAS substrate** (shipped 2026-05-08) — 9-file SHA-256 hash-chained audit log + governance file registry + fault-injection tests per gate + meta-test that every gate has fault-injection coverage + governance metrics writer.
- **Pre-flight gates** — `tools/check_kill_switch.py`, `tools/check_worktree_clean.py`, `tools/check_governance_change.py`, `tools/check_canon_freshness.py` (PRO-337, in flight).
- **Pre-commit hooks** — Gitleaks secret scanner, ruff, prettier, JSONL append-only invariant test, n8n workflow JSON schema validator.
- **Pre-push hook** — refuses force-push and branch-delete on protected branches.
- **CI workflows** — governance-check, hygiene, canon-freshness (PRO-337), n8n workflow validation.

These collectively are the "trust enforcement + compliance validation" layer. New gates are added per-need; the collection IS the layer.

---

## Peer review remains a first-class workflow

This hierarchy says where AUTHORITY lives. It does NOT demote peer review.

Today's loop got here through peer review:

- DGAS substrate sprint was driven by a 3-way (CC + GMI + GPT) synthesis (`data/peer_reviews/2026-05-08_dgas_three_way_synthesis.md`).
- PR-batching policy was synthesized from independent GMI + GPT proposals.
- This source-of-truth doc integrates GPT's proposal + CC's additions.

What changes under this scope rule:

- **Relay workers (PXY, GPT, GMI) get hydrated from Notion canon as their entry point**, not from prompt fragments. Smaller prompts, less drift, fewer rejected-architecture resurfaces.
- **Synthesis output gets promoted into repo canon** via `.miru/overlays/adopted-lessons.md` or this directory. The relay file in `data/peer_reviews/` stays as the audit trail.
- **Peer review is still the primary mode of work** for non-trivial decisions. Operator-relay → CC synthesis → repo canon → Notion mirror remains the sequence.

---

## What this rule does NOT change

To keep "lines up with the work we've been doing" honest, this file does not modify:

- The canon refresh cadence (`feedback_canon_refresh_cadence` — every 3 days OR after major ship).
- The PR-batching policy (workflow-git.md — 15 files / 800 LOC ceiling, governance one-per-PR).
- The required dispatch_worker prompt clauses (3 clauses in adopted-lessons.md).
- The Operator Communication Standard in AGENTS.md.
- Any existing role assignment, gate, or lane lock.

It adds a layer above all of these that says where they apply and how to reconcile when they conflict.

---

## When this rule disagrees with another canon rule

This file wins. Then update the other rule to match. Then log the reconciliation in `decisions`. Same procedure as any other source-of-truth conflict — applied to canon-about-canon.

---

## See also

- `.miru/reference/roadmap.md` — current state + near/mid/long-term roadmap
- `.miru/overlays/workflow-dispatch.md` — orchestrator decision authority + Gatekeeper architecture + Hermes layering
- `.miru/overlays/domain-ops.md` — Notion read/write rules
- `.miru/overlays/adopted-lessons.md` — including the canon refresh discipline
- `data/peer_reviews/2026-05-09_source_of_truth_proposal_gpt.md` — GPT's original proposal
- `data/peer_reviews/2026-05-09_ch_role_brief.md` — CH catch-up brief for re-engagement
- Memory `feedback_canon_refresh_cadence` — refresh cadence rule
- DB `stack_state` — current operating state, machine-readable
- DB `decisions` — log of every canon-affecting decision with source attribution
