# Reference — Roadmap & Architecture State

```text
Reference: roadmap
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: planning new work, dispatching a major ticket, or onboarding a worker.
Last reviewed: 2026-05-09 (verified against Linear + git log + actually-running services)
```

This file is the canonical answer to **"where are we and where are we going."** Keep it current — stale roadmap = duplicated work or contradicted plans.

---

## Current State (2026-05-09, verified)

### Substrate — DGAS (Deterministic Governed Autonomous System) — SHIPPED

The trust foundation. Eleven PRs in one day (2026-05-08) hardened the loop substrate:

- **Localhost-bind** on `full_operator` profile (PR #136). Blocks non-trusted origins from self-elevating.
- **Hash-chained append-only JSONL audit logs** — 9 files, SHA-256 chain with daily anchor. Files: `cc_completion_log.jsonl`, `routing_history.jsonl`, `pending_callbacks.jsonl`, `dispatch_dlq.jsonl`, `cc_heartbeat_log.jsonl`, `vp_ops_supervision.jsonl`, `drift_scanner_log.jsonl`, `agent_decisions.jsonl`, `github_resource_ledger.jsonl`.
- **Pre-commit secret scanner** (Gitleaks).
- **Pre-push hook** refusing force-push and branch-delete on protected branches.
- **Governance file registry** (`tools/check_governance_change.py` `GOVERNANCE_PATTERNS`) — PRs touching gates require `GOVERNANCE_CHANGE_APPROVED=true` + operator merge via CODEOWNERS.
- **Fault-injection tests** for each gate, plus a meta-test that each registered gate has a fault-injection test.
- **Governance metrics writer** — per-gate fired/blocked counts.
- **DGAS verifier hardening** (PR #137) — fix silent-pass on row-1 break and `--files` empty trap.

Outcome: workers cannot self-elevate, cannot rewrite history, cannot leak secrets through PRs without tripping a gate. The audit trail is intact.

### Hybrid orchestration pivot — Phase 1 SHIPPED, Phase 2/3 PLANNED

**Phase 1 (SHIPPED 2026-05-06)** — Three coordinated PRs:

- PR #93 (PRO-300, Cursor): strip dead Cursor + Codex handlers from `dispatcher/handlers/`.
- PR #94 (PRO-301, Codex): strip Flask UI + WebSocket + Slack-bolt from `task_dispatcher.py` (port 19000 decommissioned).
- PR #95 (PRO-302, CC): extract `gatekeeper/gatekeeper.py` (760 lines) + `gatekeeper/frontmatter_parser.py` (173) + `gatekeeper/forwarder.py` (238). Replace `task_dispatcher.py` with a 60-line deprecation stub. Archive `jobs.db` as `jobs.db.legacy`.

3-model bench (2026-05-06): qwen2.5:7b, mistral:7b-instruct, qwen2.5:14b. **Locked `DEFAULT_MODEL = qwen2.5:7b`** for Gatekeeper routing decisions — best balance of speed (p50=27.7s, p95=31s) + correct rejection-vocab usage. Bench evidence at `data/batch_reports/bench_*`.

**Phase 2 (PLANNED — needs operator approval before implementation):**

- Add `cc_handoff` MCP tool that invokes `gatekeeper.gate_dispatch()` instead of CH calling `dispatch_listener` directly.
- Run in **shadow mode** — Gatekeeper validates and logs decisions to `data/agent_decisions.jsonl` for calibration, but does NOT gate dispatch yet.
- Additive on CH (`dispatch_worker` still present in tool profile during shadow).
- Spec: Notion page `358c5d34-0141-817c-8dda-e2f91a50a9c5`.
- **Blocked on:** CH being back online (or CC absorbing CH's dispatch role first).

**Phase 3 (PLANNED — after Phase 2 validated):**

- Remove `dispatch_worker` from CH's tool profile entirely. CH only has `cc_handoff`.
- Self-serve loophole closes structurally rather than instructionally.

### Hermes — layered architecture, partially live

| Stage                                                | Status                                  | What it is                                                                                                                                                                                                                                                                    |
| ---------------------------------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Stage 0 — Apprentice bridge**                      | DONE (PRO-312)                          | `tools/hermes_apprentice.py` (24 tests). Manual-invocation Python script. Joins `routing_history.jsonl` + callbacks → produces structured "learning cases" in `data/hermes_quality_labels.jsonl` (120 rows backfilled). Read-only observer.                                   |
| **Stage 1 — Shadow predictor at spawn**              | DONE (PRO-329, PR #144 squash 245a16a7) | `services/dispatch_listener/src/spawn.js:625`. Calls Ollama `qwen2.5:7b` at every worker spawn. Logs predicted route + confidence + risk to `data/hermes_predictions.jsonl` next to actual dispatch. Observation only. No routing authority. ~8-18s latency, fire-and-forget. |
| **Stage 2 — Hermes assumes routing authority**       | NOT YET STARTED                         | Once Stage 1 builds enough track record, Hermes makes the actual routing decision (CC/operator override remains as backstop). Open question: which JSONL file owns the override audit trail.                                                                                  |
| **Stage 3 — Hermes learns from completion outcomes** | NOT YET STARTED                         | Hermes consumes the completion log (success/failure per route) and refines its prediction model. Likely fine-tuning on `hermes_quality_labels.jsonl` once it's grown to ~1000+ rows.                                                                                          |
| **Stage N — NousResearch Hermes proper**             | INDEFINITE                              | Replace Qwen substrate with the actual NousResearch Hermes fine-tuned model. Open: whether the local hardware (Ryzen 7 8745H + Radeon 780M, 32 GB DDR5) can run it at acceptable latency.                                                                                     |

A custom model `miru-router:latest` (9 GB, derived from one of the qwens) exists locally and is used by the Gatekeeper for routing-validation decisions — **separate from Hermes**. Don't confuse them: the Gatekeeper validates + rejects bad dispatches; Hermes predicts + (eventually) routes good ones.

### Loop hardening — Q2 SHIPPED

The dispatch loop is reliable enough to run unattended for routine work as of 2026-05-09:

- **PRO-330** (terminal spawn-state logging) — DONE. Worker terminal taxonomy: `spawned`, `exited_clean`, `exited_failed`, `timed_out`, `killed`, `spawn_failed`, `no_output`.
- **PRO-331** (Linear label + state intake tools) — DONE (PR #147). CC can now move tickets to Todo + add labels via gateway tools (no raw GraphQL fallback).
- **PRO-334** (worktree contamination fix) — DONE (PR #150 squash 4663fbae). Pre-spawn dirty refusal + post-worker cleanup with stash-failure-aborts-cleanup + fork-safe merged-PR detection.
- **PRO-335** (worker status pattern + ESCALATE diagnostic capture) — DONE (PR #149 squash 31b9aa71). All four canonical statuses recognized; diagnostic block captured into `result.json` with `summary` + `escalation_category`.

### Active tickets (2026-05-09)

| Ticket  | State            | Notes                                                                                                                                              |
| ------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| PRO-336 | Backlog          | Boot dispatch_listener into Session 1+ (eliminate Session 0 cross-session kill wall). Operator-merge tier. Blocked on operator review of approach. |
| PRO-333 | Backlog (PARKED) | LogueOS Console P1a — bootstrap SvelteKit shell + 5-tab nav. Ready for dispatch once PRO-336 lands.                                                |
| PRO-326 | In Review        | parent_watcher `_evaluate_parent` unit tests. CC dispatched.                                                                                       |
| PRO-327 | In Review        | parent_watcher `_is_forward_transition` edge case tests. CC dispatched.                                                                            |
| PRO-292 | Todo             | E2E test ticket (do-not-dispatch flag). Audit deployment pipeline for stale env vars.                                                              |

---

## Roadmap

### Near-term (this week — next week)

1. **PRO-336 — Session 0 boot fix.** Move dispatch_listener startup to `shell:startup` shortcut so the Node listener always lands in operator's interactive session. Self-check at wrapper start: if `SessionId == 0`, exit 1. Operator-merge.
2. **PRO-333 — LogueOS Console P1a.** SvelteKit dashboard for watching the loop in real time (worker status, recent dispatches, prediction-vs-actual comparison from Hermes Stage 1, completion log). Replaces the "ask CC for status" loop with a glance-able UI. Dispatch once PRO-336 lands.
3. **Move PRO-329 → Done in Linear** (DONE 2026-05-09 — was stale at "Todo" while shipped).
4. **Move PRO-336 → Backlog** (DONE 2026-05-09 — was in Triage where I just filed it).
5. **MiruOpsDigest failing daily at 9 AM** — Last result: 1 (failed). Not blocking but worth diagnosing. File ticket if it's not a one-off.

### Mid-term (next 2-4 weeks)

1. **Hermes Stage 2 — assume routing authority.** After Stage 1 builds 100+ predictions of track record (currently 2 entries, growing per dispatch), evaluate whether qwen2.5:7b's predictions would have routed correctly. If yes for ≥85% of cases, ship Stage 2: Hermes routes, CC overrides, operator approves overrides via Telegram.
2. **Hybrid pivot Phase 2 — `cc_handoff` MCP tool in shadow mode.** CC absorbs CH's dispatch role (since CH is offline), then `cc_handoff` becomes the path. Gatekeeper validates + logs to `data/agent_decisions.jsonl`. Does not gate yet.
3. **Hybrid pivot Phase 3 — remove `dispatch_worker` from CH profile.** Self-serve loophole closes structurally. **Blocked on:** CH being back online.
4. **Parent_watcher n8n integration** — file ticket. The Python parent_watcher works (PRO-323 done) but isn't wired into n8n yet. Without integration it doesn't auto-fire.
5. **Dispatcher toolkit packing wired into W4 prompt builder** — PRO-324 shipped the toolkit (13 signal rules, 34 tests) but it's not actually invoked from W4 yet. File ticket.
6. **Linear transport unification** — Codex ticket (relay file `data/peer_reviews/2026-05-09_codex_ticket_linear_transport_unification.md`). Not yet filed in Linear. Decide whether to file or de-prioritize.
7. **PRO-337+ — backfill canon discipline checks.** Add `tools/check_canon_freshness.py` that fails CI if any canon file's "Last reviewed" stamp is more than 7 days old. Codify the every-3-days rule in code, not just discipline.

### Long-term (next quarter)

1. **LogueOS extraction.** Move orchestration layer (dispatch_listener, worktree management, gateway, n8n routing, Gatekeeper, Hermes) out of `D:\dev\miru` into a standalone `D:\dev\LogueOS` repo. Project Miru becomes a tenant. LogueOS becomes a framework other projects (NASDOOM, future) can adopt. Framework docs already exist at `D:\dev\LogueOS\01_roles.md`..`07_file_conventions.md` + `workers/`.
2. **OpenClaw Control Plane (PRO-315 research).** Self-hosted observability layer. Hybrid model with Telegram (Telegram for approvals, OpenClaw dashboard for auditing). Research-only today. Decide post-LogueOS-extraction whether to build.
3. **NousResearch Hermes proper** — replace Qwen substrate. Open question: hardware capability + maintenance cost vs. Qwen's good-enough.
4. **Integration steward + Unified PR.** Concept from the brainstorm backlog. Not ticketed. Depends on job splitter being battle-tested first.
5. **CH return.** When CH is back in the loop: hand back Lead Architect / canon-promotion / Notion-default-writer roles. CC keeps backend / Python / test / verification ownership. Hand off the brief at `data/peer_reviews/2026-05-09_ch_role_brief.md`.

### Indefinite / parked

- **Multi-agent parallel dispatch** — operator runs Cursor Pro+ alongside autonomous workers. Third worker slot planned when Cursor CLI stabilizes (memory: `project_multi_agent_intent`).
- **Codex unbench.** Revisit when MCP transport stabilizes (rmcp transport stalls were the bench reason).
- **PRO-292** — E2E test for stale env-var audit. Do-not-dispatch flag is on; manual audit.

---

## What NOT to do

- Do NOT begin Hybrid Pivot Phase 2 implementation without explicit operator approval — planning only until then. (Reaffirmed 2026-05-09; was set 2026-05-06.)
- Do NOT modify CH's tool profile (remove `dispatch_worker`) until Phase 2 is verified working in shadow mode.
- Do NOT re-introduce the old workstreams (file browser, runtime control, repo browser) when wiring Phase 2 — Gatekeeper is dispatch-validation-only.
- Do NOT trust the Gatekeeper bench's `cost_weighted_score` for model differentiation — confidence scoring is broken (numeric historical 0–1 vs enum predicted high/medium/low). Use validity + latency until synthetic corpus exists.
- Do NOT commit `data/peer_reviews/` artifacts (operator's local research bundles, never in repo).
- Do NOT dispatch PRO-333 (LogueOS Console) until PRO-336 (Session 0 fix) lands — the loop reliability gap would burn the dispatch.

---

## Verification cadence

This file is part of the canon refresh discipline (`feedback_canon_refresh_cadence` memory). It MUST be re-verified:

- Every 3 days minimum
- After any ship that changes service topology, ticket states, or stage status
- Before any session that needs to plan new work

The verification is **not** "I think this is right" — it's checking against:

1. Live service ports (`Get-NetTCPConnection`)
2. Live Linear ticket states (`linear_get_issue` for each tracked ticket)
3. Recent merged PRs (`gh pr list --state merged --limit 20`)
4. The `decisions` table in `miru_memory.db` for entries since the last refresh
5. The peer_reviews folder for unsynthesized research/relay content
