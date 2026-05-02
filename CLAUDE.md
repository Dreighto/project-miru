# Claude Chat + Claude Code — Project Miru

## Ports — Permanent Reference

- 18080 = Project Miru UI — ACTIVE
- 18765 = Miru AI — ACTIVE
- 19000 = Task Dispatcher — DECOMMISSIONED (PRO-234, 2026-04-30; code kept, service stopped)
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo Boundary — Hard Rule

- Canonical repo: `Dreighto/project-miru`. Local checkouts live under `D:\dev\miru*`.
- Worktrees: each worker may have its own Git worktree under a sibling path (e.g. `D:\dev\miru-cursor`, `D:\dev\miru-codex`). All worktrees share the same repo, same branches, same canon. Operating inside any of them counts as operating within the repo.
- The boundary rule applies to the **repo**, not the working directory path. A worker in its own worktree is in scope for normal work.
- Never access, modify, or read files outside `D:\dev\miru*` worktrees without explicit operator authorization.
- If a task requires leaving the repo: STOP. Explain what you need to do and why. Wait for operator decision before proceeding.
- Worktree pre-flight: `main` is checked out in CC's worktree at `D:\dev\miru`. Git refuses to check out `main` a second time in another worktree, which is correct behavior. Workers in sibling worktrees should `git fetch origin` then cut their branch from `origin/main` directly: `git checkout -b <branch> origin/main`. Do NOT try to `git checkout main` first.

## Kill Switch — Pre-flight Gate

Before starting any dispatched task, run:

```
python tools/check_kill_switch.py
```

- **Exit code 1** (prints `KILL_SWITCH_ACTIVE`) → emit `STATUS: ESCALATE: HUMAN-REQUIRED` and stop immediately. Do not create a branch, do not read task files, do not modify anything. Report: "Kill switch active — data/system_halt is present. Autonomous work paused."
- **Exit code 0** (prints `CLEAR`) → proceed normally through the rest of pre-flight.

The script resolves the main repo root via `git rev-parse --git-common-dir` so it works correctly from any worktree. Do NOT check `data/system_halt` as a relative path — from a sibling worktree that resolves to the wrong directory.

This check runs before branch creation, before reading any task files, before every other pre-flight step. It cannot be skipped.

See `miru-context/kill-switch.md` for the full contract.

## No Overlap Rule

- Before starting any task, check what is currently being worked on
- If another worker is actively working on the same file or feature: STOP. Report the conflict to the operator. Do not proceed until the operator decides.
- Never modify a file that is currently open and being edited by another worker

## Notion — Read/Write Rules

- ALL workers may READ Notion to understand the current job, active tasks, and system state
- Claude Chat is the default Notion writer
- Other workers (including Claude Code) may write to Notion only when the operator explicitly authorizes a specific task — the authorization is per-task, not standing
- Use Notion reads to avoid overlapping with in-progress work

## Adopted Lessons — Hard Rules

Lessons promoted from Provisional to Adopted via the Lesson Promotion Discipline (Notion canon, 2026-04-28). These are battle-tested patterns that prevent specific failure modes we've already hit.

### Test the JS as it lives in the workflow JSON (PRO-189 retro, adopted 2026-04-28)

When testing JavaScript embedded in workflow JSON files (e.g. `docker/n8n/workflows/*.json`), the test MUST:

1. Load the JSON file from disk via `fs.readFileSync` and `JSON.parse`.
2. Extract the `jsCode` string from the relevant node.
3. Eval it as JS via `new Function(jsCode)` or `vm.Script(jsCode)` to confirm it parses without `SyntaxError`.
4. Exercise the algorithm against that loaded code path — NOT a clean extracted copy of the algorithm.

**Why this is a hard rule:** PRO-160 shipped with two latent bugs (SyntaxError from a literal newline inside a string literal, and a missing `$getWorkflowStaticData('global')` call). PRO-160's tests passed because they imported a clean copy of the diff function and exercised it directly. The deploy-time mangling and the embedded-newline bug both happened at the boundary between "JS source in the JSON file" and "JS that n8n actually runs," and the tests were structurally unable to see across that boundary. The watcher crashed on every poll for 12 minutes in production before being deactivated.

PRO-189 added the boundary-crossing test, which catches both bug classes and any future deploy-pipeline mangling.

**Applies to:** any change to a workflow JSON file under `docker/n8n/workflows/` that touches a `jsCode` field.

### Lock design in the Linear ticket description, not in the prompt wrapper (PRO-180 retro, adopted 2026-04-28)

When dispatching a non-trivial worker task, the design specification belongs in the Linear ticket description. The prompt wrapper handles execution mechanics (model, reasoning level, pre-flight, completion contract) and points back at the ticket for the design.

**What goes in the Linear ticket:**

- Schema, rules, scope.
- Don't-touch list.
- Done-when criteria.
- Provisional flag and promotion criteria if applicable.
- Investigation steps if the bug isn't fully understood yet.

**What stays in the prompt wrapper:**

- Worker selection (model, reasoning level).
- Pre-flight checks (branch hygiene, working tree state).
- Completion contract format.
- Escalation rules.
- Post-merge cleanup steps.

**Why this is a hard rule:** the design survives if the worker session restarts mid-task or if anyone else picks up the ticket later. The prompt wrapper does not — it's ephemeral. Putting the design in the ticket also makes ticket-only dispatch viable (operator taps Telegram dispatch button without Claude Chat drafting an elaborated prompt first), which is critical for autonomy.

PRO-180 shipped cleanly via ticket-only dispatch in 3 minutes. The Linear ticket description carried the full design; CC executed three coordinated edits across three files without needing my prompt wrapper.

**Applies to:** any worker dispatch that's more than a one-line change. Trivial fixes (typos, lint) don't need a locked design.

## PR Merge Policy — CC self-merges low-risk PRs

CC may self-merge PRs that fall in the low-risk column below. Operator reviews and merges anything in the high-risk column.

**No PR needed — commit direct to main:**

Small, obviously-correct changes that carry no meaningful risk of breakage may be committed directly to main without opening a PR. Bugbot and CI do not need to run on these.

- Version bumps in CI config (e.g. `node-version`, action runner pins) — one-liners
- Typo or wording fixes in worker rule files (CLAUDE.md, AGENTS.md, etc.) — no logic change
- Completion log entries (`data/cc_completion_log.jsonl` appends)
- Lint / format-only auto-fixes with no logic change

**CC merges (fixes):**

- Single-file edits to existing files
- Single-workflow JSON changes
- Bug fixes following a known canon-lesson pattern
- Config changes (.env, docker-compose env vars)
- Test fixtures, log rotation, hygiene tasks
- Lint / format / comment-only changes
- Worker rule file additions or substantive edits (CLAUDE.md, AGENTS.md, CURSOR.md, etc.) — new rules, not typos
- PRs that reference one Linear ticket
- Bugbot not required — skip Bugbot wait for PRs in this column

**Operator merges (changes):**

- New files or new directories
- Multi-workflow changes
- Schema or data model changes
- Anything touching `card_catalog.db` or its schema
- Anything that changes `routing_history.jsonl` schema
- Infrastructure (gateway, MCPs, port assignments)
- First implementation of something new (e.g. W3 build)

**Principle:** CC merges fixes. Operator merges changes. Fix = restore expected behavior of something that already exists. Change = add capability or alter the contract. When unsure, default to opening the PR for operator review (fail-closed). The cost of waiting for an operator review is minutes; the cost of a wrong self-merge is a revert plus context loss.

**Hard requirements before CC self-merges:**

1. PR is in the CC-merge column above (CC must explicitly check)
2. CC's own completion contract reports CONFIRMED WORKING (not INCONCLUSIVE)
3. Branch was cut clean from main (no concern braiding)
4. Bugbot: not required for CC-merge column — do not wait for it

If any of those fail: open the PR for operator review, do not self-merge.

**Never self-merge:**

- Force-push or destructive git operations (these are hard rules under access progression, not just merge policy)

**Post-merge cleanup — worker responsibility (locked 2026-04-28 per PRO-180):**

Whoever opened the PR is responsible for post-merge cleanup. The operator should NOT be cleaning up branches manually after merging.

After a PR is merged (whether self-merged or operator-merged):

1. The worker (or Claude Chat, if it owned the PR) checks out `main`.
2. Pulls latest.
3. Verifies the merged branch shows up under `git branch --merged main` (squash-merges may not — see PRO-157/PRO-159/PRO-160 pattern; safe to delete with `git branch -d` when remote tracking is gone).
4. Runs `git branch -d <branch-name>` (lowercase `-d`, safe-delete only — never `-D`).
5. Reports deletion. If anything looks off (branch not merged, working tree unexpectedly dirty, etc.): STOP and report.

If operator merges via the GitHub UI and the worker is not present in that session, the next worker that picks up a ticket on `main` is responsible for noticing stale branches in their pre-flight and cleaning them up before cutting a new branch. Pre-flight already requires "branch does NOT exist locally or remotely" — a stale local branch from a merged PR violates that and must be deleted before proceeding.

Operator should never have to ask a worker to clean up a branch. If you find yourself doing it, that's a discipline violation worth noting.

Source: locked 2026-04-25 after CC shipped 4 clean ticket fixes (PRO-60, PRO-65, PRO-72, PRO-68 + PRO-73) with consistent pre-flight discipline. Post-merge cleanup rule added 2026-04-28 per PRO-180 retro.

**Return-to-main — Hard Rule (locked 2026-04-30):**

Every task session ends on `main` with a clean working tree. No exceptions.

- After post-merge cleanup (steps 1–5 above): confirm `git branch --show-current` is `main` and `git status` shows no staged or unstaged tracked changes before signing off.
- If a task ends without a merge (INCONCLUSIVE, FAILED, or mid-session interruption): stash or WIP-commit any in-progress work on the task branch, then `git checkout main` before ending the session.
- A worker that ends a session on a feature branch — even with a clean working tree — is in violation. The next session starts blind to which branch is checked out and will cut work from the wrong base.

This rule was added after PRO-214 cleanup required operator intervention to restore a clean `main` state.

## Append-only data files — Hard Rule

Five files in `data/` are strictly append-only. Never edit, never truncate, never sort, never deduplicate, never read-modify-write. Only `fs.appendFileSync` (or the equivalent strict-append shell `>>`) is allowed.

- `data/cc_completion_log.jsonl` — completion markers (tracked)
- `data/routing_history.jsonl` — W2 routing decisions (gitignored)
- `data/pending_callbacks.jsonl` — Telegram callback ledger (gitignored)
- `data/dispatch_dlq.jsonl` — dispatch dead-letter queue (gitignored)
- `data/cc_heartbeat_log.jsonl` — worker heartbeat / liveness signal (gitignored)

Pre-commit hooks `trailing-whitespace` and `end-of-file-fixer` exclude `^data/.*\.jsonl$` so they cannot rewrite these files structurally (locked 2026-04-28 per PRO-159). If you find yourself wanting to weaken that exclude or add a hook that read-modify-writes any of the five: STOP, escalate to operator. The append-only invariant is enforced by `tests/test_jsonl_append_only_invariant.py` — that test failing means the contract is breaking.

Full root-cause history and rationale: `docs/n8n/WORKFLOW_MAP.md` (PRO-159 entry).

## MCP Tool Usage Rules

- Use MCP tools when they genuinely help the task
- Always use sequential-thinking MCP for complex multi-step tasks before executing — think first
- Always use sqlite-ro-snapshot MCP to read card data before writing any intelligence pipeline code
- Use perplexity MCP for research tasks only
- Use notion MCP to read current job state
- Use git MCP to check what files are currently changed before starting work
- Never use a tool just because it is available — only use it if it helps this specific task
- Never write to the database through any MCP tool
- `git_commit_and_push` (PRO-187) is for Claude Chat / orchestrator-scoped commits only. It may commit allowlisted canon/docs/skills files after hygiene, but must not be used for worker code changes, workflow JSON, DB files, append-only JSONL files, force-push, branch creation, rebase, reset, merge, cherry-pick, amend, or `--no-verify`.

## Database Rules

- card_catalog.db is the live database — never write to it directly from a worker session
- sqlite-ro-snapshot is the only approved DB access path for reads
- All schema changes must be proposed to Claude Chat first and approved by the operator before execution
- sqlite3 is available system-wide at C:\tools\sqlite3\sqlite3.exe

## Restart Rules

- PM (18080): `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1`
- Miru AI (18765): `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1`
- Dispatcher (19000): `powershell -ExecutionPolicy Bypass -File windows\restart_dispatcher.ps1`
- Never use nssm restart directly
- Never create alternate restart scripts

## File Placement — Hard Rules

Every file created must go in the correct location. These rules are non-negotiable.

### Service boundaries — files belong to their service

- `miru_ai/` — ALL code for the Miru AI service (port 18765): Python modules, workers, templates, static, tools, migrations
- `pm/` — ALL code for the PM Dashboard (port 18080): app.py, templates, static
- `dispatcher/` — Task Dispatcher code — **DECOMMISSIONED** (PRO-234, 2026-04-30). Code kept for reference; service stopped. Do not add new files here.
- `shared/` — Only utilities imported by 2+ services. Not a dumping ground.
- `windows/` — Windows operational scripts (.ps1, .cmd) for service management ONLY. No Python service code here.

### Where new files go

- New Python module for miru_ai → `miru_ai/` (appropriate subfolder: core/, workers/, governance/, ingestion/)
- New Python module for pm → `pm/`
- Standalone data/AI utility scripts → `tools/`
- Test files → `tests/`
- Documentation → `docs/`
- Config JSON → `config/`
- Batch run outputs, reports, audit CSVs → `data/batch_reports/`
- Official snapshots → `data/snapshots/`
- DB overlay/correction files → `data/overlays/`
- Runtime logs → `logs/` (gitignored — never commit logs)
- Test temp artifacts → `tests/_tmp/` (gitignored)
- Debug screenshots → `archive/screenshots/`

### NEVER do these

- Never create service code (.py, .html, .css, .js) at repo root
- Never create temp, scratch, or debug files at repo root
- Never write \*.log files to repo root or data/ root — always use `logs/`
- Never write \*.db files to repo root — always use `data/`
- Never write \*.png screenshots to repo root — use `archive/screenshots/`
- If a file belongs to miru_ai, pm, or dispatcher — it lives in that service directory, nowhere else
- Never create files in `data/startup-logs/` — that path is deprecated; use `logs/`

---

## Autonomous Operations — Claude Chat Decision Authority

Claude Chat is the lead orchestrator. The default operating mode is **decide → act → report**.
Asking the operator is the exception, not the norm. When in doubt: if the decision is local and
reversible, make it and note it. If it's irreversible or external, ask first.

### Decisions Claude Chat makes without asking

**Routing and dispatch:**

- Which worker to assign a ticket to (use worker-roster.md and the ticket's nature as inputs)
- Whether to run workers in parallel or sequentially (based on file overlap and dependency check)
- Which Ollama model to use for a routing or analysis step (use model assignment table in worker-roster.md)
- Whether to retry a failed dispatch (1 retry max per ticket per worker, then escalate)

**Ticket lifecycle:**

- Moving a Linear ticket to In Progress when a worker is dispatched
- Moving to In Review when a PR is opened
- Moving to Done when the completion marker is confirmed and PR merged
- Filing follow-up Linear tickets for out-of-scope findings discovered during a task

**Execution judgment:**

- Filling minor spec gaps that don't affect architecture or external contracts — note the fill in the completion report
- Choosing PR title, description, and branch name
- Whether a PR qualifies for CC self-merge (apply the merge policy table in this file)
- Post-merge cleanup: branch deletion, return-to-main
- Ordering tasks within a sprint when priorities are clear from ticket state

**Ops:**

- Re-dispatching a stalled worker within the recovery_router.py auto-retry budget
- Reading any log, completion marker, or state file to assess system health before a dispatch

### When to send a Telegram and wait for the operator

Ask before acting if **any** of these apply:

- **Infrastructure** — new port assignment, new service, new external API integration, new scheduled task
- **Schema or data model** — any change to card_catalog.db, routing_history.jsonl schema, or append-only file structure
- **Scope expansion** — completing the ticket would require touching files outside the original scope, or adds capability not in the spec
- **Security** — anything touching auth, secrets, credentials, or access control
- **Irreversible ops** — force-push, drop table, delete branch with unmerged work, clear production data
- **Strategy** — "should we build X or Y?" where the operator's product judgment is the input, not engineering reasoning
- **Repeated failure** — same worker, same ticket, failed more than twice

### Minimal escalation format

When escalating to the operator via Telegram, state exactly one decision needed — not a status
update, not a list of options to consider. The operator should be able to reply in one word or
tap a button. If you need more than one decision, send one message per decision.

---

## Worker-specific: Claude Chat + Claude Code

### Role

- **Claude Chat:** Lead Architect. Architecture decisions, planning, worker prompt authoring, Notion read AND write (default writer), session continuity.
- **Claude Code:** Primary Python execution worker. Complex multi-file Python refactoring, test writing, verification scripts. Handles large or surgical edits to Claude Chat's normally-owned surfaces (Notion canon pages, CLAUDE.md, worker prompts) when the operator explicitly authorizes it for a given task — e.g. when Claude Chat is unavailable or the edit volume is impractical in chat.

### File ownership

- Claude Code owns: Python backend files, test scripts, verification scripts
- Claude Chat owns by default: CLAUDE.md, GEMINI.md, CURSOR.md, CODEX.md, COPILOT.md, all worker prompts — Claude Code may edit these when the operator explicitly authorizes it for that task

### Must never

- Claude Code must never touch HTML/CSS/JS templates
- Claude Code must never modify .mcp.json or any MCP config files
- Claude Code must never write to card_catalog.db
- Claude Chat must never execute code directly on the server

## Completion Contract

> For Claude Code's supervisory responsibilities surrounding these terminal states — what
> "done" means, the stewardship checklist, and verification methods — see **miru-context/job-stewardship.md**.

Every task must end with exactly one of:

- STATUS: CONFIRMED WORKING
- STATUS: INCONCLUSIVE
- STATUS: FAILED

Plus a summary of what changed and what did not.

### Bugbot findings handling (CC) — see AGENTS.md

Before declaring `CONFIRMED_WORKING` on any PR, CC must execute the Bugbot completion sequence
defined in `AGENTS.md` (repo root). That sequence covers: polling for Bugbot check-run completion,
categorizing findings by severity, auto-fixing Low/Medium (one iteration max), surfacing High
findings and override-condition findings to the operator. Do not declare `CONFIRMED_WORKING` until
Bugbot is clean or all findings have been addressed or surfaced.

### Stall classification (PROVISIONAL — promote to adopted after first validated use)

Terminal states (above) cover task completion. Workers also signal stall conditions during a task using the four classes below. Sourced from Augment Code's published multi-agent failure taxonomy (PRO-178); flagged provisional until a real stall-recovery event in this project validates the schema.

| Class                     | Worker emits                                                                                                                                    | Orchestrator response                                                                                                                             |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Transient**             | Heartbeat lapse past TTL (PRO-180) with no error and no terminal state. No worker emit required — orchestrator infers from heartbeat staleness. | Auto-unstick: branch hygiene, rebase-on-main, missing env key the orchestrator controls, ambiguous spec covered by locked design.                 |
| **Ambiguous spec**        | `STATUS: INCONCLUSIVE` plus one specific question. Question MUST be a single concrete item, not "I'm not sure how to proceed."                  | Orchestrator checks the locked design (Linear ticket description). If covered → answer via Linear comment. If not covered → escalate to operator. |
| **Dependency starvation** | `STATUS: BLOCKED_ON: <ticket_id>` (e.g. `BLOCKED_ON: PRO-180`). Worker stops, does not retry.                                                   | Orchestrator reroutes, resequences, or marks task as waiting. Not a stall — expected behavior in parallel-worker setups.                          |
| **Human-required**        | `STATUS: ESCALATE: <category>` where category is one of `SECURITY`, `SCOPE_EXPANSION`, `DESIGN_CHANGE`, `IRREVERSIBLE_OP`, `REPEATED_FAILURE`.  | Orchestrator writes Linear comment, pings operator via Telegram, parks task.                                                                      |

Rules:

- Existing terminal states (CONFIRMED_WORKING / INCONCLUSIVE / FAILED) are unchanged. The new states (BLOCKED_ON, ESCALATE) are non-terminal stall signals — task continues once the block clears or operator decides.
- For `INCONCLUSIVE` with an ambiguous-spec question: the question must be answerable in one Linear comment. If the worker needs more than one back-and-forth, escalate instead.
- For `ESCALATE`: the category determines orchestrator behavior. `SECURITY` and `IRREVERSIBLE_OP` always go to operator immediately. `SCOPE_EXPANSION` may be filed as a follow-up Linear ticket and the in-scope work continued. `DESIGN_CHANGE` always goes to operator. `REPEATED_FAILURE` (same worker stalling on same task >2 times) always goes to operator.
- `routing_decisions.outcome` enum (success / failure / partial / deferred / legacy) is sufficient — these stall signals are mid-task states, not terminal outcomes, so the existing outcome enum doesn't need expansion.

Promotion criteria: first validated stall-recovery event in this project (orchestrator correctly classifies a real worker stall, takes the matching action, and the recovery succeeds) → promote section to "adopted" via the Lesson Promotion Discipline (Notion canon, 2026-04-28).

### Hygiene gate (locked 2026-04-25 per PRO-107)

Tasks involving code changes are not complete until lint + format + schema validation pass locally before PR creation. Worker MUST run `pre-commit run` (default scope: staged files) and confirm green before opening a PR. Local hygiene gate runs lint, format, and schema validation. Pytest is enforced via CI on every PR (`.github/workflows/hygiene.yml`). Local pytest will be re-enabled once the test suite is clean — see PRO-109.

If hygiene fails:

- Fix the issues if they're in scope of the current task.
- If issues are pre-existing or out of scope: STOP, report the failures to operator, do NOT push a PR with known lint failures hoping CI will catch them.

Bypass policy: `git commit --no-verify` is allowed only for emergency hotfixes. The bypass MUST be logged in the commit message (`HYGIENE BYPASS: <reason>`) and reported to operator. Legacy files (those not touched by the current PR) are not subject to retroactive lint enforcement. Hooks fire on changed files only.

### Heartbeat emission (PROVISIONAL — promote after first validated stall-recovery use)

Workers emit a heartbeat row to `data/cc_heartbeat_log.jsonl` during long-running tasks so the orchestrator can detect stalls without operator intervention. The file is append-only (gitignored) — same hard rules as the other five append-only files. Use `tools/emit_heartbeat.py` to write rows; do not hand-roll the append logic per-task.

**Schema (one JSON object per line):**

```jsonl
{
  "ts": "2026-04-28T08:12:00Z",
  "worker_id": "claude-code-1",
  "ticket_id": "PRO-XXX",
  "status": "IN_PROGRESS",
  "step": "running_pre_commit",
  "branch": "dreighto/pro-xxx-...",
  "last_file_written": "tests/test_x.py",
  "stall_signal": null,
  "outputs": []
}
```

Field definitions:

- `ts` (ISO 8601 UTC with `Z`) — heartbeat emit time.
- `worker_id` (string) — stable per-worker identifier (e.g. `claude-code-1`).
- `ticket_id` (string) — Linear ticket the worker is on.
- `status` (enum) — `IN_PROGRESS` only. Terminal states go in `cc_completion_log.jsonl`.
- `step` (string) — short label of current phase (e.g. `pre_flight`, `writing_tests`, `running_pre_commit`, `opening_pr`, `awaiting_bugbot`, `post_merge_cleanup`).
- `branch` (string or null) — current git branch.
- `last_file_written` (string or null) — most recently written/staged file.
- `stall_signal` (string or null) — populated when the worker detects a likely stall (e.g. `"awaiting_external: bugbot"`, `"deny_rule_hit: <rule>"`, `"ambiguous_spec_question_pending"`). Null otherwise.
- `outputs` (array of strings) — artifact paths produced so far. Used by dependent tickets.

**Emit cadence:** at the start of each major phase, before any operation expected to take >60 s (CI wait, Bugbot wait), and on significant state changes (branch cut, PR opened).

**Stall detection (orchestrator side):** if `now − max(heartbeat.ts for ticket_id) > 5 minutes` AND no terminal marker exists in `cc_completion_log.jsonl`, the worker is considered `STALLED`. Threshold is tunable; 5 min is the starting point. Source: PRO-180 (research-sourced, 2026-04-28).

### Orchestrator-side modules (PRO-187 follow-on, 2026-04-28)

Production worker coordination helpers live under `tools/orchestrator/`. Workers should not create parallel implementations elsewhere.

- `stall_detector.py` reads `data/cc_heartbeat_log.jsonl` and `data/cc_completion_log.jsonl` to emit `StallEvent` rows using the PRO-178 taxonomy.
- `recovery_router.py` maps stall classes to deterministic recovery actions and forces human escalation for schema, security, scope expansion, or irreversible-operation contexts.
- `task_store.py` owns active worker task state and prompt-hash idempotency in `worker_tasks`.
- `worktree_manager.py` owns orchestrator-side file collision claims in `worktree_registry`; this augments, but does not replace, git worktree isolation.

## Craft Guides — load on demand

The repo has two craft-guide libraries at:

- `docs/ui_ux/` — universal frontend craft (applies to any Miru surface: PM, Dispatcher, Dev Review Hub, future work)
- `docs/pm/` — PM-specific craft (only applies to `pm/storefront/` work; layers on top of ui_ux)

Do not load the full library. Load on demand.

**Hard triggers — read the matching doc before writing code:**

- Building or changing any mobile / PWA behavior → read `docs/ui_ux/01_MOBILE_PWA.md`
- Wiring a gesture (swipe, long-press, drag, pinch) → read `docs/ui_ux/02_GESTURES.md` + `docs/pm/05_GESTURES_PM.md` if PM
- Adding a new screen / modal / sheet → read `docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md`
- Building a reusable component (button, input, chip, card tile) → read `docs/ui_ux/04_PRIMITIVES.md` + `docs/pm/02_PM_PRIMITIVES.md` if PM
- Accessibility work (focus, contrast, ARIA, keyboard, screen reader) → read `docs/ui_ux/05_ACCESSIBILITY.md`
- Performance work (card grids, images, animation, lists >50 items) → read `docs/ui_ux/06_PERFORMANCE.md`
- Adding a library / dependency → read `docs/ui_ux/09_TOOLING.md`

**PM-specific hard triggers:**

- Watchlist / meter / pricing UI → read `docs/pm/04_WATCHLIST_AND_METER.md`
- Tab landing page work (Home, Cards, Deck Builder, Leaders, Profile) → read `docs/pm/01_TAB_LANDINGS.md`
- Adding any Miru-generated output (insight, suggestion, ambient filter) → read `docs/pm/03_MIRU_LAYER.md`
- Writing copy for Miru or PM → read `docs/pm/00_PRINCIPLES.md` + `docs/pm/03_MIRU_LAYER.md`
- Before shipping any new PM feature → run the 10-question gut-check in `docs/pm/08_PM_ANTI_PATTERNS.md`

**Soft triggers — consult if relevant:**

- Visual / styling decision → `docs/pm/06_DESIGN_LANGUAGE.md`
- Card tile changes → `docs/pm/02_PM_PRIMITIVES.md`
- Understanding how PM differs from competitors → `docs/pm/07_OPTCG_STUDY.md`
- Designing a pattern from scratch → `docs/ui_ux/07_COMPETITIVE_STUDY.md`
- Pre-ship sanity check → `docs/ui_ux/08_ANTI_PATTERNS.md` + `docs/pm/08_PM_ANTI_PATTERNS.md`

**Skip entirely for:**
typo fixes, one-line style tweaks, bugfixes that don't change interaction model, backend-only work (routes, data, scrapers).

**When craft guides conflict with CLAUDE.md / operator directives:** operator directives win, always. Flag the conflict; don't silently override.

## Completion-marker convention (locked 2026-04-25)

When CC completes a task with `CONFIRMED WORKING` status, CC MUST append one structured row to `data/cc_completion_log.jsonl` immediately before reporting completion to the operator in chat.

This is how Claude Chat verifies completion without the operator manually relaying CC's chat report. The file is append-only — never edit, never truncate.

### Schema (one JSON object per line, no array wrapping)

- `timestamp` (ISO 8601 string, UTC) — when the task completed.
- `ticket_id` (string) — Linear ticket identifier (e.g. "PRO-80"). Use null if no ticket.
- `phase` (string or null) — sub-phase label if relevant (e.g. "A").
- `status` (enum) — `CONFIRMED_WORKING` | `INCONCLUSIVE` | `FAILED`.
- `summary` (string) — one-line plain-English description of what shipped.
- `branch` (string or null) — git branch name if applicable.
- `pr_number` (int or null) — GitHub PR number if applicable.
- `merge_commit_sha` (string or null) — merge commit SHA if merged.
- `files_touched` (array of strings) — repo-relative paths edited or created.
- `linear_state_after` (string or null) — final Linear ticket state (e.g. "In Review", "Done").
- `deploy_actions` (array of strings) — short descriptions of any deploys, redeploys, or service restarts ("w7 redeployed via deploy-workflow.ps1, active state preserved").
- `test_evidence` (string) — one-line summary of how the work was verified ("15/15 fixtures pass", "7-step self-test on live Telegram").
- `follow_up_tickets_filed` (array of strings) — Linear ticket IDs filed during this work for out-of-scope items.
- `notes` (string) — anything Claude Chat needs to know that doesn't fit above. Empty string if none.

### When to write

Write the row at the moment CC would otherwise produce a `CONFIRMED WORKING` chat report. The chat report still happens (operator visibility is still useful), but the marker is the structured truth Claude Chat reads.

For `INCONCLUSIVE` or `FAILED` outcomes: write the row too, with status set accordingly. `notes` field should explain what blocked or broke. This gives Claude Chat visibility into stalled work.

### When NOT to write

- Mid-task progress updates. The marker is for terminal task state only.
- Sub-task milestones inside a multi-phase ticket. Wait for the phase to land.
- Diagnostic-only or read-only work that produces no commit, no merge, no deploy. (CC can still chat-report, just no marker needed.)

### How to write — use the script, not a raw file open

Always write the marker via `tools/emit_completion.py`. This script resolves the
correct path regardless of which worktree the worker is running in (miru-w1, miru-w2, etc.):

```bash
python tools/emit_completion.py <<'EOF'
{"timestamp":"...","ticket_id":"PRO-XXX", ...}
EOF
```

Or from Python:

```python
import json, subprocess
marker = {"timestamp": "...", "ticket_id": "PRO-XXX", ...}
subprocess.run(["python", "tools/emit_completion.py"],
               input=json.dumps(marker), text=True, check=True)
```

**Never open `data/cc_completion_log.jsonl` directly with a relative path** — from a worktree
that resolves to the wrong directory and the orchestrator will never see the entry.

### Rules

- Append only. Never read-modify-write the file. Never sort it. Never deduplicate it.
- One JSON object per line. No trailing commas, no array wrapping.
- ISO 8601 UTC timestamps with `Z` suffix.
- If a field is genuinely unknown or not applicable, use `null` (not empty string, not omitted).
- `tools/emit_completion.py` handles serialisation — pass a dict from Python or a JSON string from shell.

### Verification by Claude Chat

Claude Chat reads this file via Filesystem MCP when the operator says "task done" or asks for completion verification. Claude Chat then cross-checks the marker against GitHub PR state, Linear ticket state, file changes, and (for n8n workflows) deploy state. Discrepancies between the marker and ground truth get flagged for operator review.
