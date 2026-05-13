# Reference — Roadmap & Architecture State

```text
Reference: roadmap
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: planning new work, dispatching a major ticket, or onboarding a worker.
Last reviewed: 2026-05-13 (full sweep — verified against Linear PRO+LOS+NAS teams, `git log` to HEAD #199, board-hygiene script run)
```

This file is the canonical answer to **"where are we and where are we going."** Keep it current — stale roadmap = duplicated work or contradicted plans.

**This file is governed by `.miru/reference/source-of-truth.md`** — load that first when reconciling sources or deciding what gets logged where.

> **Drift note (2026-05-13):** the orchestration layer was extracted into its own repo (`Dreighto/LogueOS-Orchestrator`) on 2026-05-11 (LOS-10 + LOS-18 cutover). `CLAUDE.md`, `AGENTS.md`, `.miru/overlays/`, and `miru-context/team-charter.md` still contain pre-extraction references (e.g. `services/dispatch_listener/src/worktree.js`, `tools/miru_mcp_gateway/`, `D:\dev\miru-w1`, "second canonical repo" singular). De-Miru-ification was started under LOS-26/27 and continues; the canon cleanup is part of the LogueOS-improvement work the operator is queuing for GMI. Until that lands, treat repo-internal path references in worker-rule files as approximate and verify against the orchestrator repo.

---

## Current State (2026-05-13, verified)

### Three active repos + one dormant project

| Repo                            | Role                                                                                                                                                                                                                                                                              | Linear team                                                    | Worktree pool                                                                             |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `Dreighto/project-miru`         | PM Storefront + Miru AI + card catalog. A **governed client** of LogueOS. Worker-rule canon (`CLAUDE.md`, `AGENTS.md`, `.miru/`, `miru-context/`) currently still lives here, shared across repos per the source-of-truth meta-rule (kernel-canon migration LOS-35 will move it). | `PRO-`                                                         | `D:\dev\worktrees\project-miru\w{N}` (legacy `D:\dev\miru-w*` basenames still recognized) |
| `Dreighto/LogueOS-Orchestrator` | The extracted dispatch loop: listener, gateway, n8n routing, Gatekeeper, Hermes, recovery, worktree management.                                                                                                                                                                   | `LOS-` (projects: "LogueOS Orchestrator", "LogueOS Migration") | per-repo pool                                                                             |
| `Dreighto/LogueOS-Console`      | Operator-facing SvelteKit dashboard for the loop.                                                                                                                                                                                                                                 | `LOS-` (project: "LogueOS Console")                            | `D:\dev\worktrees\LogueOS-Console\w{N}`                                                   |
| `D:\nasdoom\`                   | **Dormant.** A planned SvelteKit PWA dashboard for a media/NAS stack (Plex/Sonarr/Radarr/SABnzbd/NZBGet/Tautulli).                                                                                                                                                                | `NAS-` (45 fully-spec'd backlog tickets, never started)        | —                                                                                         |

Worktree layout is repo-agnostic since LOS-14: `D:\dev\worktrees\<repo>\w{N}`. Per-repo pools enforced server-side (`WORKTREE_POOLS`) + client-side (approved-`target_repo` allowlist), kept in sync by a parity test. Adding a 4th repo: see `multi-repo-onboarding.md`.

### Substrate — DGAS (Deterministic Governed Autonomous System) — SHIPPED

The trust foundation (11 PRs on 2026-05-08, hardened since):

- Localhost-bind on `full_operator` profile. Hash-chained append-only JSONL audit logs (9 files, SHA-256 + daily anchor). Pre-commit secret scanner (Gitleaks). Pre-push hook refusing force-push/branch-delete on protected branches. Governance-file registry (`tools/check_governance_change.py`) — PRs touching gates need `GOVERNANCE_CHANGE_APPROVED=true` + CODEOWNERS operator merge. Fault-injection test per gate + a meta-test. Governance metrics writer. DGAS verifier hardening.
- **LOS-28** — `project_id` is now stamped at worker boot and flows through the DGAS audit chain (multi-repo audit integrity).
- **LOS-27** — kernel CI check for project-name leaks (canon contamination detector). **LOS-17** widened the filter-repo `PATH_EXCLUDES` after Miru business logic leaked into the orchestrator extraction.

Outcome: workers can't self-elevate, rewrite history, or leak secrets through PRs without tripping a gate. Audit trail intact across repos.

### LogueOS extraction — DONE (2026-05-11)

- **LOS-10** — dispatch system extracted from project-miru into `LogueOS-Orchestrator`. Step 1 (gateway `/canon/*` + `/canon-manifest` HTTP routes, PR #177); Step 2 (workers fetch canon via gateway with fail-closed semantics, LOS-13); Step 6 filter-repo pass; Step 8 production cutover of gateway + listener (**LOS-18**).
- **LOS-14** — project/repo-agnostic worktree layout. **LOS-26** — orchestrator canon de-Miru-ification (folder renames, version bump, governance cleanup). **LOS-34** — rename audit replacing `miru-*` identifiers in orchestrator code. **LOS-29** — gateway capability registry + per-project tool scoping. **LOS-36** — gemini-cli interactive-session dispatch path (fixed node-pty AttachConsole crash). **LOS-37** — orchestrator `GEMINI.md` repo-boundary fix.
- Migration tooling (`los_10_filter_repo.sh`, rename map at `.miru/reference/los-10-rename-map.md`) is one-shot and has served its purpose.

### Hybrid orchestration pivot — Phase 1 SHIPPED; Phase 2/3 PLANNED

- **Phase 1 (2026-05-06):** stripped dead Cursor/Codex handlers + Flask UI/WebSocket/Slack-bolt from `task_dispatcher.py` (port 19000 decommissioned); extracted `gatekeeper.py` + `frontmatter_parser.py` + `forwarder.py`; `task_dispatcher.py` is now a 60-line deprecation stub; `jobs.db` archived. 3-model bench locked `DEFAULT_MODEL = qwen2.5:7b` for Gatekeeper routing-validation.
- **Phase 2 (PLANNED — needs operator approval):** `cc_handoff` MCP tool invokes `gatekeeper.gate_dispatch()` instead of the caller hitting `dispatch_listener` directly; run in shadow mode (validate + log to `data/agent_decisions.jsonl`, don't gate yet). The `cc_handoff` tool exists; the gating path is not yet wired. Blocked on CH being back online OR CC fully owning the dispatch role first.
- **Phase 3 (PLANNED — after Phase 2 validated):** remove `dispatch_worker` from CH's tool profile; self-serve loophole closes structurally.

`miru-router:latest` (9 GB custom Qwen) is the Gatekeeper's routing-validation model — **separate from Hermes**. Gatekeeper validates + rejects bad dispatches; Hermes predicts + (eventually) routes good ones.

### Hermes — layered learning agent, partially live

| Stage                              | Status                 | What it is                                                                                                                                                                                                  |
| ---------------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 — Apprentice bridge              | DONE (PRO-312)         | `tools/hermes_apprentice.py`. Manual run, joins `routing_history.jsonl` + callbacks → `data/hermes_quality_labels.jsonl`. Read-only.                                                                        |
| 1 — Shadow predictor at spawn      | DONE (PRO-329)         | At every worker spawn, calls Ollama `qwen2.5:7b` → logs predicted route + confidence + risk to `data/hermes_predictions.jsonl`. Observation only, no authority, fire-and-forget.                            |
| 1.5 — Cost-estimate signal         | DONE (LOS-24 / LOS-30) | Shadow predictions enriched with a predicted cost-estimate alongside the route.                                                                                                                             |
| 2 — Routing authority              | NOT STARTED            | Once Stage 1/1.5 has enough track record (signal-driven, not time-gated), Hermes makes the actual routing call; CC + operator override remain as backstop. Open: which JSONL owns the override audit trail. |
| 3 — Learn from completion outcomes | NOT STARTED            | Consume the completion log (success/failure per route), refine the model; likely fine-tuning once `hermes_quality_labels.jsonl` is large enough.                                                            |
| N — NousResearch Hermes proper     | INDEFINITE             | Replace the Qwen substrate with the actual NousResearch fine-tuned model; hardware-bound.                                                                                                                   |

### Loop hardening — SHIPPED; loop reliable for routine unattended work

PRO-330 (terminal spawn-state taxonomy: `spawned / exited_clean / exited_failed / timed_out / killed / spawn_failed / no_output`), PRO-331 (Linear label+state intake tools), PRO-334 (worktree contamination fix — pre-spawn dirty refusal + post-worker cleanup + fork-safe merged-PR detection), PRO-335 (all four canonical statuses recognized + ESCALATE diagnostic block captured into `result.json`), PRO-336 (Session 0 boot fix — `shell:startup` shortcut, Session-0 self-check), PRO-338 (`clean_worktree.py --cwd` multi-repo support), PRO-339 (`.coderabbit.yaml` declares the 9 append-only files read-only to the reviewer), PRO-340 (multi-repo onboarding checklist + template dir), PRO-342 (false-CONFIRMED_WORKING when gemini skips `gh pr create`), PRO-326/327/328 (parent_watcher tests). LOS-38 (fail-loud on missing worktree dir at startup) is the remaining triage follow-up. LOS-33 hardened the remaining 5 scheduled-task installers (VBS-wrap, no-flash).

### LogueOS Console — P1a→P3 + deployment + hardening SHIPPED

SvelteKit 2 / Svelte 5 (runes) / Tailwind 4 / shadcn-svelte / lucide-svelte / LayerChart. 480px container. Tokens: `bg #0D1117`, `surface #161B22`, `cta #A3E635`, Mona Sans body / IBM Plex Mono metadata. 5 tabs: **Runs · Workers · Activity · Ask · Settings**.

DONE: LOS-1 (shell+nav) · LOS-2 (Runs wired to `cc_completion_log.jsonl`) · LOS-3 (run detail `/runs/[trace_id]`) · LOS-4 (Workers tab, live from dispatch-log NDJSON) · LOS-5 (Activity tab, ops feed) · LOS-6 (persistent deployment — adapter-node + scheduled task) · LOS-7 (worker-classification + timestamp fixes) · LOS-9 (`/api/runs` trace_id dedupe) · LOS-20 (stale-data-path fix + full MCP toolkit in `.gemini/settings.json`) · LOS-25 (testing toolkit: Vitest + Playwright + axe-core) · Team Memory sidebar (PR #22 in-repo) · usage tracker (PR #23 in-repo) · LOS-40/41 (mobile PWA usage tracker + nav-bar density, retroactive tickets).

OPEN: **LOS-8** (P5 Settings tab with operator write actions — notification toggles, worker enable/disable, kill switch, connection status — Triage, needs operator approval). **LOS-22** (`/usage` page: Sankey + Heatmap + Leaderboard + sparklines — In Review, blocked on LOS-19/20/21). **LOS-42** (evolve into mobile operator co-working interface: chat with CC/GMI + fire dispatch from the PWA — Backlog, needs planning pass).

### In flight (2026-05-13)

| Ticket  | State       | Notes                                                                                                                                                                                                  |
| ------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| PRO-361 | In Progress | Automated multi-project worktree selection & setup. gemini worker, project Miru Orchestration/Autonomy.                                                                                                |
| LOS-19  | In Review   | Capture token usage at worker exit; extend completion-marker schema with a telemetry block. (Foundation for LOS-21/22/23.)                                                                             |
| LOS-21  | In Review   | Pricing-API integration + `cost_usd` computation on every marker.                                                                                                                                      |
| LOS-22  | In Review   | Console `/usage` page (token visibility). Blocked on LOS-19/20/21.                                                                                                                                     |
| LOS-23  | In Review   | Backend pattern detectors (Retry Storm, Prompt Bounce, Passive Observer) → anomalies log. Blocked on LOS-19.                                                                                           |
| PRO-344 | Triage      | Untrack `.gemini/settings.json` + add template + pre-commit secret scan. **Real security item** (plaintext `GITHUB_PERSONAL_ACCESS_TOKEN` written to a tracked file).                                  |
| PRO-345 | Triage      | PR #190 R5 CR-fix: escape apostrophes in `$PoshMcpConfigPath` embedding.                                                                                                                               |
| PRO-346 | Triage      | PR #192 R1 CR-fix: markdown fences + workflow-dispatch prereq + pre-flight gates. (Confirm not already covered by the merged R4-R5 pass PRO-349 before re-dispatching.)                                |
| PRO-347 | Triage      | dispatch-cr-fix overlay: add active-worker collision check before dispatching (PR #192 R2 finding).                                                                                                    |
| LOS-8   | Triage      | Console P5 Settings tab — see above.                                                                                                                                                                   |
| LOS-11  | Triage      | n8n: generalize PRO-only filters + multi-repo PR URL in completion bridges. Was "blocked by LOS-10" — LOS-10 is DONE, so this is unblocked; move out of triage.                                        |
| LOS-15  | Triage      | Drop "MCP" infix from gateway env var names (consistency with service rename). Low.                                                                                                                    |
| LOS-16  | Triage      | `los_10_filter_repo.sh` rename-`origin`-after-filter-repo ordering bug. In "LogueOS Migration"; LOS-10 migration is done — this one-shot tool has served its purpose; likely close, not High-priority. |
| LOS-38  | Triage      | dispatch_listener: fail-loud on missing worktree dir at startup (multi-repo onboarding gap).                                                                                                           |

### Backlog (LogueOS — the active lane)

- **LOS-35** — Kernel-canon migration: workers in any repo resolve to ONE canon source (gateway `/canon/*`), retire the duplicate `.miru/` gates. High. _(This is what removes the drift note at the top of this file.)_
- **LOS-39** — Organizational learning layer, Phase 1: synthesis pathway Tier 0 → Tier 1. High. (Phase 0 signal-generation experiment LOS-32 done — it's the source of the dispatch-prompt "observation-emission" clause, PR #199.)
- **LOS-31** — n8n workflow modularity refactor — plug-and-play worker integration. Medium.
- **LOS-42** — Console as mobile operator co-working interface. Medium.

### Backlog (project-miru — low activity)

- **PRO-315** — OpenClaw control-plane research + SOUL.md draft. Research only. Low.
- **PRO-197** — Workers write to `miru_memory.db` on substantial completions (so future threads load execution context automatically). Medium.
- **PM Storefront cluster — PARKED:** PRO-7 (deck-builder container shape decision), PRO-10 (OP01 verification audit), PRO-14 (route split), PRO-15 (DockContainer component). PM is deprioritized; the operator confirmed keeping these parked rather than cancelling (2026-05-13).

---

## Roadmap

### Near-term

1. **GMI-led LogueOS improvement pass.** Operator is queuing work for Gemini to make the LogueOS system substantially better. Likely scope: kernel-canon migration (LOS-35), n8n modularity (LOS-31), de-Miru-ification cleanup of worker-rule canon, finishing the orchestrator extraction loose ends (LOS-11/15/16/38). File/triage sub-tickets as the operator scopes them.
2. **Token/cost visibility initiative** — LOS-19 → LOS-21 → LOS-22 + LOS-23. All In Review; finish the chain and ship the `/usage` page.
3. **LOS-8 — Console P5 Settings + write actions.** Operator approves → dispatch to gemini-cli, `target_repo=LogueOS-Console`. Last planned Console slice from the v1 spec.
4. **PRO-344 — untrack `.gemini/settings.json` + secret scan.** Real security item; promote out of triage.

### Mid-term

1. **LOS-35 — kernel-canon migration.** Single canon source via the gateway; retires `.miru/` duplicate gates. Removes the drift hazard between this repo's canon files and the orchestrator.
2. **LOS-39 — org learning layer Phase 1.** Tier 0 → Tier 1 synthesis pathway, building on the LOS-32 signal corpus.
3. **Hermes Stage 2 — routing authority.** Signal-driven on Stage 1/1.5 track record. When the shadow predictions would have routed correctly often enough, ship: Hermes routes, CC overrides, operator approves overrides via Telegram.
4. **Hybrid pivot Phase 2** — `cc_handoff` gating in shadow mode. Blocked on CH return or CC fully owning dispatch.
5. **LOS-42 — Console mobile co-working interface.** Operator works primarily from phone; this unifies Claude.ai + Gemini CLI + Linear + Console.

### Long-term

1. **Hybrid pivot Phase 3** — remove `dispatch_worker` from CH's profile (blocked on CH back online + Phase 2 validated).
2. **NASDOOM** — stand up the dormant PWA project when the operator chooses to start it (45 backlog tickets ready; triage framework configured).
3. **OpenClaw control plane** (PRO-315) — decide whether to build the self-hosted observability layer, vs. the LogueOS Console already covering most of it.
4. **NousResearch Hermes proper** — replace the Qwen substrate; hardware capability + maintenance cost vs. Qwen good-enough.
5. **CH return** — hand back Lead Architect / canon-promotion / Notion-default-writer roles; CC keeps backend / Python / test / verification ownership. Brief at `data/peer_reviews/2026-05-09_ch_role_brief.md`.

### Parked / indefinite

- **Codex** — fully retired (2026-05-12 roster, PR #197). Not "benched, revisit" — removed from the gateway allowlist, W2 router, and roster canon (PRO-304). Re-add only on an explicit operator decision.
- **Cursor** — operator-IDE-only; never in the auto-dispatch loop. Its headless-CLI plan (PRO-85 / PRO-253) was archived/cancelled.
- **PM Storefront** — parked (see backlog above).
- **PRO-242** (Miru-AI-runs-PM "Governed Autonomy" vision) — cancelled 2026-05-13.

---

## What NOT to do

- Do NOT begin Hybrid Pivot Phase 2 implementation without explicit operator approval — planning only until then.
- Do NOT modify CH's tool profile (remove `dispatch_worker`) until Phase 2 is verified in shadow mode.
- Do NOT re-introduce the old workstreams (file browser, runtime control, repo browser) when wiring Phase 2 — Gatekeeper is dispatch-validation-only.
- Do NOT commit `data/peer_reviews/` artifacts (operator's local research bundles; never in repo). Same for `.gemini/settings.json` once LOS-resolved (PRO-344).
- Do NOT dispatch gemini-cli into a target repo lacking `.gemini/settings.json` workspace-tier config — gemini hangs trying to use shell to read Linear/GitHub (file-based MCP discovery only, no `--mcp-config` flag). See `multi-repo-onboarding.md` (5-step setup).
- Do NOT add a new `target_repo` without also adding the matching `WORKTREE_POOLS` entry — the parity test fails CI but the error message is opaque if you don't know to look at both files.
- Do NOT treat this file (or `CLAUDE.md` / `team-charter.md`) as guaranteed-current for repo-internal paths until the kernel-canon migration (LOS-35) lands — verify against the orchestrator repo.

---

## Verification cadence

Part of the canon refresh discipline (`feedback_canon_refresh_cadence`). Re-verify: every 3 days minimum; after any ship that changes service topology, ticket states, or stage status; before any session that plans new work. "Verified" = checked against: (1) live service ports, (2) live Linear ticket states across PRO + LOS + NAS teams, (3) recent merged PRs (`gh pr list --state merged --limit 20` on each active repo), (4) the `decisions`/state tables in `miru_memory.db`, (5) the `peer_reviews/` folder for unsynthesized relay content.
