# Project Miru — Worker Core

```
Instruction Architecture Version: MIRU-INSTRUCTIONS-v2
Effective: 2026-05-08
If your loaded instructions do not show this version stamp, STOP and reload your boot context.
```

This file is the always-loaded core. It contains the rules that, if violated,
cause data loss, security breach, or service outage. Everything else is in
overlays (loaded by task type) or reference files (fetched on demand). The
discovery index at the bottom tells you when to load each.

Read `AGENTS.md` for universal communication rules (Operator Communication
Standard, Try Harder Discipline). Read `miru-context/team-charter.md` on every
dispatch.

---

## Fail-Closed Directive

When you are not sure: **STOP and ask the operator**. Do not guess. Do not
silently improvise. Do not proceed with an irreversible action on a hunch.
Asking costs minutes; a wrong autonomous action can cost a revert, lost data,
or operator trust. This is the single most important rule in this file — every
other rule below assumes you obey this one.

---

## Repo Boundary

- Canonical repo: `Dreighto/project-miru`. Local checkouts under `D:\dev\miru*`.
- Worktrees (e.g. `D:\dev\miru-w1`, `D:\dev\miru-cursor`) are in scope.
- Never read, modify, or write files outside `D:\dev\miru*` without explicit operator authorization.
- If a task requires leaving the repo: STOP and ask.

## Pre-Flight Gate 1 — Kill Switch

Before any dispatched task, run:

```
python tools/check_kill_switch.py
```

- Exit code 1 (`KILL_SWITCH_ACTIVE`): emit `STATUS: ESCALATE: HUMAN-REQUIRED` and stop. Do not branch, do not read task files, do not modify anything.
- Exit code 0 (`CLEAR`): proceed to gate 2.

This gate cannot be skipped. The script resolves the main repo via
`git rev-parse --git-common-dir` so it works from any worktree.

## Pre-Flight Gate 2 — Worktree Cleanliness

After the kill switch passes, run:

```
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

Nine files in `data/` are strictly append-only. Never edit, truncate, sort,
deduplicate, or read-modify-write. Only `fs.appendFileSync` (or shell `>>`).

```
data/cc_completion_log.jsonl       data/routing_history.jsonl
data/pending_callbacks.jsonl       data/dispatch_dlq.jsonl
data/cc_heartbeat_log.jsonl        data/vp_ops_supervision.jsonl
data/drift_scanner_log.jsonl       data/agent_decisions.jsonl
data/github_resource_ledger.jsonl
```

Use the helper scripts (`tools/emit_completion.py`, `tools/emit_heartbeat.py`)
— do not hand-roll the append. Pre-commit hooks exclude these from
`trailing-whitespace` and `end-of-file-fixer`. The invariant is enforced by
`tests/test_jsonl_append_only_invariant.py`.

## Completion Contract — Terminal States

Every task ends with exactly one of:

- `STATUS: CONFIRMED WORKING`
- `STATUS: INCONCLUSIVE`
- `STATUS: FAILED`

Plus a one-line summary. The full marker schema, heartbeat emission, and stall
classification rules live in `.miru/overlays/workflow-completion.md` — load it
before declaring a terminal state.

## Return-to-Main

Every task session ends on `main` with a clean working tree. No exceptions.

- After CONFIRMED_WORKING (post-merge cleanup done): `git checkout main && git pull && git status` clean → sign off.
- After INCONCLUSIVE / FAILED / interrupt: stash or WIP-commit on the task branch, then `git checkout main` before sign-off.

A worker that ends on a feature branch leaves the next session blind to which
branch is checked out — that worker is in violation.

## Worker Role — Claude Code (VP Ops)

- Owns: Python backend files, tests, verification scripts, post-ticket canon maintenance, `vp_ops_verify_ticket`.
- Standing Notion write authority for factual/maintenance updates (see `.miru/overlays/domain-ops.md`).
- Never touches: HTML/CSS/JS templates, `.mcp.json`, `card_catalog.db`.

---

## Discovery Index

Load the matching overlay **before** starting work that triggers it. Fetch a
reference file when you need the specific fact.

### Overlays — `.miru/overlays/`

- **`workflow-git.md`** — LOAD IF committing, opening, or merging a PR. Contains: merge policy decision tree, hygiene gate, automated PR review sequence, gh auth, WIP commits, post-merge cleanup.
- **`workflow-completion.md`** — LOAD IF reaching a terminal task state. Contains: completion marker schema, heartbeat emission, stall classification.
- **`workflow-dispatch.md`** — LOAD IF orchestrating dispatch, gateway profiles, or W2 routing. Contains: CH decision authority, gateway profile enforcement, ingress classifier, orchestrator modules, Linear `projectId` requirement.
- **`domain-ui.md`** — LOAD IF touching frontend code (`pm/`, `miru_ai/static/`, templates). Contains: craft guide trigger list (when to read `docs/ui_ux/` and `docs/pm/` files).
- **`domain-ops.md`** — LOAD IF touching scheduled tasks, services, Notion writes, or MCP config. Contains: scheduled-task focus rule, MCP usage rules, Notion read/write rules.
- **`adopted-lessons.md`** — LOAD IF doing a non-trivial code change (more than typo or lint). Contains: workflow JSON test rule, design-in-Linear-ticket rule.

### Reference — `.miru/reference/`

- **`ports-and-services.md`** — FETCH IF you need a port number or service mapping.
- **`linear-projects.md`** — FETCH IF creating a Linear ticket. Contains the `projectId` table.
- **`file-placement.md`** — FETCH IF creating a new file and unsure where it goes. Contains the NEVER-do list.
- **`database-rules.md`** — FETCH IF reading or proposing changes to `card_catalog.db`.
- **`restart-procedures.md`** — FETCH IF restarting a service.

If you cannot tell which overlay applies, see the Fail-Closed Directive at the
top of this file: stop and ask.
