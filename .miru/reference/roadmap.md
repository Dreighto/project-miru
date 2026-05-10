# Reference — Roadmap & Architecture State

```text
Reference: roadmap
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: planning new work, dispatching a major ticket, or onboarding a worker.
Last reviewed: 2026-05-10 (post-late-evening sweep — verified against Linear + git log + LogueOS-Console PR list)
```

This file is the canonical answer to **"where are we and where are we going."** Keep it current — stale roadmap = duplicated work or contradicted plans.

**This file is governed by `.miru/reference/source-of-truth.md`** — load that first when reconciling sources or deciding what gets logged where. Roadmap entries follow the truth hierarchy and refresh trigger taxonomy defined there.

---

## Current State (2026-05-10, verified)

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

The dispatch loop is reliable enough to run unattended for routine work as of 2026-05-10:

- **PRO-330** (terminal spawn-state logging) — DONE. Worker terminal taxonomy: `spawned`, `exited_clean`, `exited_failed`, `timed_out`, `killed`, `spawn_failed`, `no_output`.
- **PRO-331** (Linear label + state intake tools) — DONE (PR #147). CC can now move tickets to Todo + add labels via gateway tools (no raw GraphQL fallback).
- **PRO-334** (worktree contamination fix) — DONE (PR #150 squash 4663fbae). Pre-spawn dirty refusal + post-worker cleanup with stash-failure-aborts-cleanup + fork-safe merged-PR detection.
- **PRO-335** (worker status pattern + ESCALATE diagnostic capture) — DONE (PR #149 squash 31b9aa71). All four canonical statuses recognized; diagnostic block captured into `result.json` with `summary` + `escalation_category`.
- **PRO-336** (Session 0 boot fix) — DONE 2026-05-09 (PRs #154 + #155). Listener now boots into Session 1+ via `windows\install_dispatch_listener_startup_shortcut.ps1` shell:startup shortcut. Wrapper has self-check that exits 1 if `SessionId == 0`. Eliminates the cross-session kill wall that previously required operator-elevated relaunch after every reboot.
- **PRO-338** (clean_worktree.py multi-repo support) — DONE 2026-05-10 (PR #160). Added `--cwd <PATH>` flag; spawn.js now invokes the script via absolute `execFileSync` from REPO_ROOT (not from worker cwd). Eliminates `worktree_auto_clean_failed` warnings on every dispatch into a non-miru worktree. 14 Python tests + 7 JS tests.
- **PRO-339** (.coderabbit.yaml append-only declaration) — DONE 2026-05-10 (PR #159, dispatched manually to Codex). Added `path_filters` excluding the 9 append-only `data/*.jsonl` files from line-by-line review + `path_instructions` block telling CodeRabbit never to suggest in-place edits to those files. Prevents the false-positive "rewrite the row_hash chain" comments that wasted review cycles on PR #158.

### Multi-repo dispatch infrastructure — SHIPPED 2026-05-09

Dispatch loop now serves multiple repos via the `target_repo` parameter on `dispatch_worker`:

- **PR #156** — Per-repo worktree pools (`WORKTREE_POOLS` map in `services/dispatch_listener/src/worktree.js`). Backward-compat: callers omitting `target_repo` land in `project-miru`. New `target_repo` parameter on `tools/miru_mcp_gateway/dispatch_tools.py` validated against `_APPROVED_TARGET_REPOS = frozenset({"project-miru", "LogueOS-Console"})`. Parity test (`tests/test_dispatch_tools_target_repo_parity.py`) ensures the Python allowlist and JS pool keys can't drift.
- **PR #157** — Generalized `parkingBranchForCwd` for non-miru worktrees. Legacy basenames (`miru-w1`..`miru-w6`, `miru-cursor`) keep the short-form `_parking_w1` convention via an explicit `LEGACY_MIRU_SLOT_BASENAMES` Set; everything else maps to full-basename `_parking_<repo>-w<N>` (e.g. `_parking_LogueOS-Console-w1`).
- First active second pool: `LogueOS-Console` (1 slot at `D:\dev\LogueOS-Console-w1`). LOS-1 + LOS-2 both shipped through it on 2026-05-10.

### LogueOS Console — P1a through P3 + deployment hardening SHIPPED 2026-05-10

Operator-facing dashboard for the dispatch loop. Replaces "ask CC how the loop is doing" with a glance-able SvelteKit UI. Lives at `Dreighto/LogueOS-Console` (separate repo from project-miru).

- **LOS-1 — P1a — bootstrap shell + 5-tab nav** — DONE (PR #1 squash e21ba0b8). SvelteKit 2 + Svelte 5 (runes) + Tailwind 4 + shadcn-svelte + lucide-svelte + LayerChart. 5 tabs in order: **Runs · Workers · Activity · Ask · Settings**. Locked design tokens: `bg #0D1117`, `surface #161B22`, `cta #A3E635`, Mona Sans body / IBM Plex Mono metadata. 480px max-width container. Chart isolation pattern in `src/lib/charts/`.
- **LOS-2 — P1b — Runs tab data wiring** — DONE (PR #2 squash 8651bb0b). `/api/runs` server endpoint reads `D:\dev\miru\data\cc_completion_log.jsonl` via `$lib/server/config.ts`; `/api/runs?limit=N` returns the most recent N rows in reverse chronological order. RunCard component renders worker badge (5 worker identity colors), status icon (5 status colors with traffic-light semantics), trace_id chip, ticket_id, summary preview, duration, PR link. Polling pauses when tab is hidden via `document.visibilityState`.
- **LOS-3 — P1c — Run detail view** — DONE (PR #3 squash a28d2e6). Tap a run card → `/runs/[trace_id]` route showing full summary, branch, files_touched, full PR link. Gemini-cli dispatch, `target_repo=LogueOS-Console`.
- **LOS-4 — P2 — Workers tab: live status from dispatch log** — DONE (PR #4). Workers tab reads live worker-state events from `logs/dispatch_listener_stdout.log` NDJSON; shows active/idle/error per worker with last-seen timestamps. Gemini-cli dispatch, `target_repo=LogueOS-Console`.
- **LOS-5 — P3 — Activity tab: recent ops events feed** — DONE. Activity tab wired to dispatch log NDJSON stream; surfaces `worker_spawned`, `worker_exited`, `pre_spawn_dirty_refusal`, `worktree_parked`, and error events in chronological feed. Gemini-cli dispatch, `target_repo=LogueOS-Console`.
- **P4** — Not scoped in the v1 spec. The locked phase sequence runs P1a→P1b→P1c→P2→P3→P5. P4 was deliberately omitted from the original plan.
- **LOS-6 — persistent deployment (adapter-node + scheduled task)** — DONE (PR #6, completion marker PR #173). Switched from Vite dev mode to adapter-node build; start script + Windows scheduled task; mirrors PM/Miru AI pattern. Gemini-cli dispatch, `target_repo=LogueOS-Console`.
- **LOS-7 — fix worker classification + empty-timestamp on Run cards** — DONE (PR #8, completion marker PR #174). Trust `row.worker` when present, fall back to `deriveWorkerFromTraceId`; safe timestamp display fallback for null/NaN values. Gemini-cli dispatch, `target_repo=LogueOS-Console`.
- **LOS-8 — P5 — Settings tab with operator write actions** — TRIAGE. Notification toggles, worker enable/disable, kill switch, connection status. Replaces the Settings placeholder tab. Gemini-cli dispatch, `target_repo=LogueOS-Console`. Needs operator approval to move to Todo.
- **LOS-9 — /api/runs dedupe duplicate trace_id rows** — TRIAGE. LOS-2/3 each appear twice in Recent Runs due to duplicate append-log entries. Fix: dedupe by trace_id in the server endpoint. Gemini-cli dispatch, `target_repo=LogueOS-Console`. Needs operator approval to move to Todo.

### Active tickets (2026-05-10, post-late-evening sweep)

| Ticket  | State  | Notes                                                                                                                                                        |
| ------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| PRO-292 | Todo   | E2E test ticket (do-not-dispatch flag). Audit deployment pipeline for stale env vars.                                                                        |
| PRO-343 | Todo   | n8n W1 DNS error (transient). Labeled `triage` + `n8n-error-queue`. Needs operator decision: archive or investigate. CC cannot auto-dispatch (triage label). |
| LOS-8   | Triage | Console P5 Settings tab. Needs operator approval → Todo before Gemini dispatch. `target_repo=LogueOS-Console`.                                               |
| LOS-9   | Triage | Console /api/runs dedupe. Needs operator approval → Todo before Gemini dispatch. `target_repo=LogueOS-Console`.                                              |
| LOS-10  | Done   | Step 1 (gateway `/canon/*` + `/canon-manifest` HTTP routes) shipped PR #177. Full orchestrator extraction plan underway — Steps 2-9 TBD per migration plan.  |

**Recently DONE (2026-05-10, late-evening batch):**

- LOS-6 (Console adapter-node deployment) → Done, PR #6 + completion marker PR #173.
- LOS-7 (worker classification + timestamp fix) → Done, PR #8 + completion marker PR #174.
- LOS-10 Step 1 (gateway canon HTTP routes) → Done, PR #177 + completion marker PR #179.
- PR #175 (Playwright MCP added to dispatched-worker `.mcp.json`). Workers can now drive Chrome for UI testing.
- PR #178 (LogueOS projects restructure — Migration vs Orchestrator). Created `LogueOS Migration` for the one-time extraction event; renamed `LogueOS Orchestration` → `LogueOS Orchestrator` for standing ongoing work. LOS-10 → Migration; LOS-11 → Orchestrator.

**Recently DONE (2026-05-10, evening batch):**

- PRO-322 (Linear board hygiene script) → Done, PR #166. `tools/linear_board_hygiene.py` + full test suite.
- PRO-326 (parent_watcher `_evaluate_parent` unit tests) → Done, PR on 2026-05-10.
- PRO-327 (parent_watcher `_is_forward_transition` edge case tests) → Done, PR #119.
- PRO-340 (multi-repo onboarding checklist) → Done, PR #164. `data/templates/multi-repo/` + `.miru/reference/multi-repo-onboarding.md`.
- LOS-3 (Console P1c Run detail) → Done, PR #3 (LogueOS-Console).
- LOS-4 (Console P2 Workers tab) → Done, PR #4 (LogueOS-Console).
- LOS-5 (Console P3 Activity tab) → Done (LogueOS-Console).

**Recently DONE (2026-05-09 + early 2026-05-10):**

- PRO-333 → reframed as LOS-1 + LOS-2 (LogueOS Console moved to its own repo + team). Original ticket cancelled.
- PRO-336 (Session 0 boot fix) → Done, PRs #154 + #155.
- PRO-338 (clean_worktree.py multi-repo support) → Done, PR #160.
- PRO-339 (.coderabbit.yaml append-only declaration) → Done, PR #159 (operator dispatched manually to Codex; Codex still operator-routable for scoped tickets).
- LOS-1 (Console P1a shell) → Done, PR #1 on LogueOS-Console.
- LOS-2 (Console P1b Runs data) → Done, PR #2 on LogueOS-Console.

---

## Roadmap

### Near-term (this week — next week)

1. **LOS-8 — Console P5 Settings + write actions.** Triage → operator approves → dispatch to Gemini-cli, `target_repo=LogueOS-Console`. Last planned Console slice from the v1 spec.
2. **LOS-9 — Console /api/runs dedupe.** Triage → operator approves → dispatch to Gemini-cli, `target_repo=LogueOS-Console`. Cosmetic data bug but visible on dashboard.
3. **LOS-10 Steps 2-9 — orchestrator extraction.** Step 1 (gateway HTTP routes) done. Next steps per the locked plan in `data/peer_reviews/2026-05-10_orchestrator-extraction-plan_gmi.md`. CC lane. File sub-tickets as each step is approved.
4. **PRO-343 — n8n W1 DNS error triage.** Operator decision needed: one-time blip (archive) or systemic (investigate). Labeled `triage` + `n8n-error-queue`. CC cannot auto-dispatch (triage label).
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
- Do NOT dispatch gemini-cli into a target repo that lacks `.gemini/settings.json` workspace-tier config — gemini will hang trying to use shell to read Linear/GitHub (no `--mcp-config` CLI flag exists, only file-based discovery). See `multi-repo-onboarding` checklist (PRO-340) for the 5-step setup.
- Do NOT add a new `target_repo` to dispatch_tools.py without also adding the matching `WORKTREE_POOLS` entry in worktree.js — `tests/test_dispatch_tools_target_repo_parity.py` will fail CI, but the manifest-only error message is opaque if you don't know to look at both files.

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
