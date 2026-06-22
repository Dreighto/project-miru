# Project Miru — Governed-Client Overlay

```text
Instruction Architecture Version: MIRU-INSTRUCTIONS-v3
Last reviewed: 2026-06-22
Effective: 2026-05-13
Overlay scope: project-miru only.
Kernel canon: ~/dev/LogueOS-Orchestrator/CLAUDE.md + .logueos/
If your loaded instructions do not show this version stamp, STOP and reload your boot context.
```

This file is project-miru's worker-rule overlay. If you are dispatched to a miru worktree,
read the orchestrator's `CLAUDE.md` and `AGENTS.md` **first** — they hold the kernel canon
(Fail-Closed Directive, Pre-Flight Gates, Completion Contract, universal worker rules). This
file layers miru-product-specific rules on top. The orchestrator's kernel canon governs any
rule not explicitly stated here.

> ⚠️ **Kernel-rule duplication retained pending kernel boot-path fix — do not slim until that lands.**
> Some sections below intentionally duplicate kernel canon (Fail-Closed Directive,
> Pre-Flight Gates, Completion Contract, Return-to-Main, append-only file rules) as a
> load-bearing safety net. Workers dispatched into project-miru worktrees do not reliably
> reach the orchestrator's `CLAUDE.md` today — `dispatch_listener/src/index.js` does not
> prepend kernel canon into the dispatch prompt, and Claude Code's auto-load doesn't walk
> from `~/dev/worktrees/project-miru/w{N}` up to `~/dev/LogueOS-Orchestrator/`. Until
> the kernel boot-path fix lands (separate ticket — prepend kernel `CLAUDE.md` + `AGENTS.md`
> in the dispatch prompt), the duplication here is what workers actually run on. **Do not
> slim the duplicated sections in this file until that fix is live and verified.**

Read `AGENTS.md` for miru-specific behavioral constraints.

**When sources disagree, consult `.logueos/reference/source-of-truth.md` in the orchestrator.**
The truth hierarchy is: Runtime > Audit logs > Linear > Repo (code/canon/DB) > Notion
canon > Worker memory > Conversation context. Recency is not authority.

---

## Fail-Closed Directive

When you are not sure: **STOP and ask the operator**. Do not guess. Do not
silently improvise. Do not proceed with an irreversible action on a hunch.
Asking costs minutes; a wrong autonomous action can cost a revert, lost data,
or operator trust. This is the single most important rule in this file — every
other rule below assumes you obey this one.

---

## Repo Boundary

- **This repo:** `Dreighto/project-miru`. Local checkout at `~/dev/miru`. Contains PM Storefront + Miru AI + card catalog product code.
- **Three-repo system:** project-miru (this — product code), `LogueOS-Console` (operator dashboard), `LogueOS-Orchestrator` (kernel — dispatch loop, gateway, governance canon).
- **Worktree pool:** Dispatched workers land in `~/dev/worktrees/project-miru/w{N}`. The pool is managed by the orchestrator's dispatch_listener — see `LogueOS-Orchestrator` for pool config.
- **Kernel canon** (dispatch rules, gateway profiles, worker routing) lives in the orchestrator's `.logueos/`. This file is miru's thin overlay on top of that kernel.
- **Worker dispatch** prefers `cc_handoff` (governed path); the legacy `dispatch_worker` tool is a break-glass fallback for when the Gatekeeper is unavailable — not decommissioned (reclassified 2026-06-14 / PRO-973 once `cc_handoff` repo-routing was repaired).
- Never read, modify, or write files outside the active worktree without explicit operator authorization.
- If a task requires leaving the worktree: STOP and ask.

## Pre-Flight Gate 1 — Kill Switch

Before any dispatched task, run:

```bash
python tools/check_kill_switch.py
```

- Exit code 1 (`KILL_SWITCH_ACTIVE`): emit `STATUS: ESCALATE: HUMAN-REQUIRED` and stop. Do not branch, do not read task files, do not modify anything.
- Exit code 0 (`CLEAR`): proceed to gate 2.

This gate cannot be skipped. The script resolves the main repo via
`git rev-parse --git-common-dir` so it works from any worktree.

## Pre-Flight Gate 2 — Worktree Cleanliness

After the kill switch passes, run:

```bash
python tools/check_worktree_clean.py
```

- Exit code 1 (`DIRTY`): emit `STATUS: ESCALATE: HUMAN-REQUIRED` and stop. Report dirty files.
- Exit code 0 (`CLEAN`): proceed.

Run from the worktree root (uses `os.getcwd()`).

## No Overlap

Before starting work, check what is being worked on. If another worker is
actively touching the same file or feature: STOP and report. Never modify a
file currently open in another worker's session.

## Append-Only Data Files

The orchestration append-only chains (`cc_completion_log`, `routing_history`,
`pending_callbacks`, `dispatch_dlq`, `cc_heartbeat_log`, `vp_ops_supervision`,
`drift_scanner_log`, `agent_decisions`, `github_resource_ledger`) **live in
the orchestrator** at `~/dev/LogueOS-Orchestrator/data/` (Migration Phase 3,
LOS-55, 2026-05-14). They are not stored in this repo.

Workers in this worktree still call the local `tools/emit_completion.py`
helper — the dispatch_listener sets `LOGUEOS_DATA_DIR` so every helper
resolves the canonical orchestrator path automatically. Use the helpers
(`tools/emit_completion.py`, `tools/emit_heartbeat.py`); never hand-roll the
append.

The only append-only file that stays miru-side is `data/miru_worker_runs.jsonl`
— miru-product specific, governed by `tests/test_jsonl_append_only_invariant.py`
in this repo. Same rules: never edit, truncate, sort, dedupe, or
read-modify-write.

## Completion Contract — Terminal States

Every task ends with exactly one of:

- `STATUS: CONFIRMED WORKING`
- `STATUS: INCONCLUSIVE`
- `STATUS: FAILED`
- `STATUS: ESCALATE: <category>` — non-terminal stall signal; categories: `HUMAN-REQUIRED`, `SECURITY`, `SCOPE_EXPANSION`, `DESIGN_CHANGE`, `IRREVERSIBLE_OP`, `REPEATED_FAILURE`

Plus a one-line summary that is **never empty** (an empty INCONCLUSIVE summary is treated as a
worker failure and bounced). The full marker schema, heartbeat emission, and stall
classification rules live in `.logueos/overlays/workflow-completion.md` in the orchestrator
— load it before declaring a terminal state.

## Return-to-Main

Every task session ends on `main` with a clean working tree. No exceptions.

- After CONFIRMED_WORKING (post-merge cleanup done): `git checkout main && git pull && git status` clean → sign off.
- After INCONCLUSIVE / FAILED / interrupt: stash or WIP-commit on the task branch, then `git checkout main` before sign-off.

A worker that ends on a feature branch leaves the next session blind to which
branch is checked out — that worker is in violation.

## Worker Role — Claude Code (VP Ops)

- Owns: Python backend files, tests, verification scripts, post-ticket canon maintenance, `vp_ops_verify_ticket`.
- Standing Notion write authority for factual/maintenance updates (see `.logueos/overlays/domain-ops.md`).
- Restarts services autonomously (gateway, dispatch_listener, PM, Miru AI) — don't ask operator for routine restarts. See `.logueos/reference/restart-procedures.md` for service launch paths.
- Files Linear loop tickets directly via `linear_create_issue` (not file-then-paste).
- **`card_catalog.db` writes are in scope** when work requires them — set population (OP01–OP15), provenance backfills, meta-relevancy / insight columns, image-asset linkage. Always `cp data/card_catalog.db data/card_catalog.db.bak.<timestamp>` before any UPDATE/INSERT/DELETE batch, log the change to a `data/*.log` file, and surface the diff in commit messages.
- Never touches: HTML/CSS/JS templates, `.mcp.json`.

## Active Loop Workers

**Auto-dispatched (via dispatch_listener):**

- **CC (Claude Code)** — autonomous backend + VP Ops. Python, tests, scripts, verification, canon maintenance.
- **Gemini CLI** — autonomous frontend. UI/UX, HTML/CSS/JS templates, mobile layout.
- **Hermes (Qwen-via-Ollama)** — shadow predictor. Logs predicted routes alongside actual dispatches for evaluation. Does not hold routing authority.

**Operator-driven (not in dispatch loop):**

- **Cursor** — operator-driven from the IDE; not loop-dispatched.
- **Claude Chat (CH)** — Lead Architect role (currently offline for orchestration work). When active: architecture decisions, planning, worker prompt authoring, Notion writes.

See `miru-context/miru-service-catalog.md` for current service state and `miru-context/miru-protected-constraints.md` for the hard invariants.

---

## Discovery Index

Load the matching overlay **before** starting work that triggers it. All overlays and
reference files live in the orchestrator's `.logueos/` at `~/dev/LogueOS-Orchestrator` —
the single source of cross-cutting kernel canon.

### Overlays — `.logueos/overlays/` (in `~/dev/LogueOS-Orchestrator`)

- **`workflow-interactive.md`** — LOAD IF in an interactive co-working session (no dispatch envelope, operator typing directly).
- **`workflow-git.md`** — LOAD IF committing, opening, or merging a PR. Contains: merge policy decision tree, hygiene gate, automated PR review sequence, gh auth, WIP commits, post-merge cleanup.
- **`workflow-completion.md`** — LOAD IF reaching a terminal task state. Contains: completion marker schema, heartbeat emission, stall classification.
- **`workflow-salvage.md`** — LOAD IF reviewing a draft PR labelled `salvaged`.
- **`workflow-dispatch.md`** — LOAD IF orchestrating dispatch, gateway profiles, or W2 routing.
- **`dispatch-preflight.md`** — LOAD IF building or sending a worker dispatch. Pre-flight checklist: resolve ticket UUID, confirm slot idle + worktree clean, build prompt with acceptance criteria + scope guard, pass `shadow_mode=False`, run `vp_ops_verify_ticket` after. Failure-mode table + `target_repo` resolution pointer.
- **`dispatch-cr-fix.md`** — LOAD IF CR posts a CHANGES_REQUESTED review on an open PR.
- **`domain-ui.md`** — LOAD IF touching frontend code (`pm/`, `miru_ai/static/`, templates).
- **`domain-ops.md`** — LOAD IF touching scheduled tasks, services, Notion writes, or MCP config.
- **`adopted-lessons.md`** — LOAD IF doing a non-trivial code change (more than typo or lint).
- **`pre-push-discipline.md`** — LOAD IF about to push commits to a branch with an open PR (or about to open a new PR).

### Reference — `.logueos/reference/` (in `~/dev/LogueOS-Orchestrator`)

- **`source-of-truth.md`** — FETCH IF deciding where information belongs, resolving a conflict between sources, or planning a canon refresh. This is the meta-rule that governs every other canon rule.
- **`roadmap.md`** — FETCH IF planning new work or onboarding a worker.
- **`ports-and-services.md`** — FETCH IF you need a port number or service mapping.
- **`linear-projects.md`** — FETCH IF creating a Linear ticket. Contains the `projectId` table.
- **`file-placement.md`** — FETCH IF creating a new file and unsure where it goes.
- **`database-rules.md`** — FETCH IF reading or proposing changes to `card_catalog.db`.
- **`restart-procedures.md`** — FETCH IF restarting a service.
- **`multi-repo-onboarding.md`** — FETCH IF adding a new repo to the dispatch loop.
- **`architecture-decisions.md`** — FETCH IF designing features that touch the kernel boundary or dispatching workers across projects.
- **`model-tiering.md`** — FETCH IF selecting a model tier or effort level for a dispatch.

### Miru-product context — `miru-context/` (in this repo)

- **`miru-protected-constraints.md`** — Hard invariants for the miru product. Read before touching card catalog, PM, or Miru AI.
- **`miru-service-catalog.md`** — Service definitions and ports for miru-specific services.
- **`miru-vocab.md`** — Miru-specific terminology and domain vocabulary.

If you cannot tell which overlay applies, see the Fail-Closed Directive at the
top of this file: stop and ask.
