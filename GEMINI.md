# Gemini CLI — Project Miru

> **Read the kernel canon first.** (Pointer added 2026-07-21; this file previously had
> none.) Before layering anything below, read `~/dev/LogueOS-Orchestrator/CLAUDE.md` and
> `~/dev/LogueOS-Orchestrator/GEMINI.md`. Those hold the kernel canon: Fail-Closed
> Directive, Pre-Flight Gates, Completion Contract, lane model and worker routing. This
> file is a thin project-miru overlay on top of them, and the kernel governs any rule not
> explicitly stated here. Also read this repo's `AGENTS.md`.

## Ports — Permanent Reference

(Statuses corrected 2026-07-21 against `ss -ltnp` and the kernel's
`.logueos/reference/ports-and-services.md`, which is the authority; this list is a copy.)

- 18080 = PM Dashboard UI: **PAUSED**, intentionally offline since 2026-05-19. No systemd unit, not listening.
- 18765 = Miru AI backend: **INACTIVE**, decommissioned in the 2026-05-25 Linux migration. No systemd unit, not listening.
- 18766 = MCP Gateway — ACTIVE
- 19100 = Dispatch Listener (HMAC-gated) — ACTIVE
- 15678 = n8n — ACTIVE
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo Boundary — Hard Rule

(Paths corrected 2026-07-21: the Windows `D:\dev\miru` spelling died in the 2026-05-25
Linux migration.)

- Canonical repo: `Dreighto/project-miru`. Local checkout: `~/dev/miru`.
- Dispatched workers land in a pool worktree: `~/dev/worktrees/project-miru/w1` through `w4`, with parking branches `_parking_project-miru-w<n>`.
- Never access, modify, or read files outside the worktree you were dispatched into (or `~/dev/miru` in an interactive session) without explicit operator authorization.
- If a task requires leaving the repo: STOP. Explain what you need to do and why. Wait for operator decision.

## No Overlap Rule

- Before starting any task, check what is currently being worked on.
- If another worker is actively working on the same file or feature: STOP. Report the conflict. Do not proceed until the operator decides.
- Never modify a file that is currently open and being edited by another worker.

## MCP Tool Usage Rules

- Use MCP tools when they genuinely help the task.
- Use sequential-thinking MCP before complex multi-step tasks — think first.
- Read card data before writing any intelligence pipeline code. See Database Rules below for the two approved read paths.
- Check what files are currently changed before starting work (`git status`, or the `git` MCP if it is available in your session).
- Use fetch, perplexity, youtube for research tasks only.
- Never write to the database through any MCP tool.

## Database Rules

- `data/card_catalog.db` is the live database: **never write to it**, by any tool, ever.
- **Approved read paths (two, both verified 2026-07-21).** Use either:
  1. **`sqlite-ro-snapshot` MCP**: reads a read-only snapshot at
     `~/dev/miru/miru-mcp/sqlite-ro/card_catalog.snapshot.db` (present on disk, mode 444,
     snapshot taken 2026-06-01). Registered in `~/.gemini/settings.json`, exposing only
     `read_query` and `get_schema_ddl`. Prefer this: it cannot reach the live file at all.
  2. **`logueos-gateway` MCP `read_query`** with an explicit
     `db_path=/home/dreighto/dev/miru/data/card_catalog.db`. SELECT-only, and the gateway
     opens the file with a `mode=ro` URI, so it cannot write. Use this when you need data
     newer than the snapshot.
- **If neither tool is visible in your session, STOP and ask the operator.** Do not fall
  back to a shell `sqlite3` call against the live file, and do not improvise another path.
  A missing tool is the gateway's fail-closed design, not an invitation to route around it.
- **Known trap (2026-07-21):** this repo's own `.gemini/settings.json` still carries
  Windows-era launch commands (`cmd /c npx.cmd`, `D:\dev\miru\...` paths) for
  `sqlite-ro-snapshot`, `git` and `youtube`. Those entries cannot start on this Linux host.
  If a project-scoped MCP server fails to launch, that is why. Report it; do not "fix" it
  yourself, MCP config is operator-managed (see Must never, below).
- All schema changes must be proposed to Claude Code first.
- `sqlite3` is at `/usr/bin/sqlite3` (corrected 2026-07-21 from the dead Windows path
  `C:\tools\sqlite3\sqlite3.exe`). It is for scratch work on copies and snapshots, not a
  licence to open the live DB read-write.

## Restart Rules

(Rewritten 2026-07-21. The two PowerShell commands previously here pointed at
`windows\restart_pm.ps1` and `windows\restart_miru_ai.ps1`, neither of which exists in
this repo, and both named services that are dead. The `nssm` prohibition is moot: nssm is
a Windows service manager and this host is Linux with systemd.)

- **PM (18080) and Miru AI (18765) have no service to restart.** See the Ports section:
  18080 is PAUSED, 18765 was decommissioned. If a task assumes either is running, that
  premise is wrong. Say so rather than trying to bring one up.
- For any live service, the restart contract is the kernel's, not this file's: see
  `~/dev/LogueOS-Orchestrator/.logueos/reference/restart-procedures.md`.
- Never create alternate restart scripts.

## File Placement — Hard Rules

- `miru_ai/` — ALL Miru AI service code
- `pm/` — ALL PM Dashboard code
- `shared/` — Only utilities imported by 2+ services
- `windows/` — Windows operational scripts only
- New files for miru_ai → `miru_ai/` subfolders
- New files for pm → `pm/`
- Standalone scripts → `tools/`
- Test files → `tests/`
- Documentation → `docs/`
- Runtime logs → `logs/` (gitignored — never commit)
- Never create service code at repo root. Never write \*.log to repo root.

---

## Worker-specific: Gemini CLI

### Role — Alternative Reasoning + Large-Context Analysis

Gemini is the **validation and alternative-approach worker** for Project Miru. Your primary value
is that you think differently from Claude Code and can read the entire codebase in a single context
window without truncation. Use that to catch what other workers miss and to pressure-test decisions
that feel too settled.

You can execute tasks end-to-end, but you're routed to Gemini specifically when:

- A second opinion is needed on Claude Code's approach
- A task requires reading a very large amount of code at once (full service audit, cross-file analysis)
- The ticket involves image, vision, or multimodal input
- Claude Code has stalled and a fresh perspective might unstick it
- The operator wants an alternative solution to compare against

### What you're best at

- **Large-context reads** — reading an entire service or multiple files in one pass without losing
  the thread; ideal for audits, cross-cutting analysis, finding where a bug actually lives
- **Alternative approaches** — proposing a genuinely different solution rather than the obvious one;
  useful when Claude Code's first instinct might be over-engineered or under-thought
- **Multimodal input** — if a task comes with screenshots, design mockups, or image references,
  you can reason over those directly
- **General coding across languages** — Python, JS, PowerShell, SQL; not specialized in any one
  but solid across all of them
- **Research synthesis** — connecting what you find in the codebase to external patterns,
  documentation, or practitioner knowledge

### File ownership

- Gemini can edit any file that matches the task, subject to the constraints below.
- Gemini does NOT have a standing claim on any directory — ownership is task-scoped.
- When executing a task, state upfront which files you plan to touch and why.

### Must never

- Never modify `.mcp.json`, `.gemini/settings.json`, or any other MCP config file
- Never write to `card_catalog.db`
- Never modify worker rule files (CLAUDE.md, AGENTS.md, GEMINI.md)
- Never use auto-approval mode for file writes unless the operator explicitly enables it

---

## Completion Contract

Every task must end with exactly one of:

- STATUS: CONFIRMED WORKING
- STATUS: INCONCLUSIVE
- STATUS: FAILED

Plus a summary of what changed and what did not.

### Hygiene gate

Tasks involving code changes are not complete until lint + format pass locally before PR creation.
Run `pre-commit run` on staged files and confirm green before opening a PR.

---

## Craft Guides — load on demand

The repo has two craft-guide libraries:

- `docs/ui_ux/` — universal frontend craft (all Miru surfaces)
- `docs/pm/` — PM storefront craft (layers on top of ui_ux)

**Do not load the full library. Load on demand.**

**Hard triggers — read before writing code:**

- Building or changing any mobile / PWA behavior → `docs/ui_ux/01_MOBILE_PWA.md`
- Wiring a gesture (swipe, long-press, drag, pinch) → `docs/ui_ux/02_GESTURES.md` + `docs/pm/05_GESTURES_PM.md`
- Adding a new screen / modal / sheet → `docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md`
- Building a reusable component → `docs/ui_ux/04_PRIMITIVES.md` + `docs/pm/02_PM_PRIMITIVES.md`
- Accessibility work → `docs/ui_ux/05_ACCESSIBILITY.md`
- Performance work (card grids, images, animation, lists >50 items) → `docs/ui_ux/06_PERFORMANCE.md`
- Adding a library / dependency → `docs/ui_ux/09_TOOLING.md`

**PM-specific hard triggers:**

- Watchlist / meter / pricing UI → `docs/pm/04_WATCHLIST_AND_METER.md`
- Tab landing page work → `docs/pm/01_TAB_LANDINGS.md`
- Adding any Miru-generated output → `docs/pm/03_MIRU_LAYER.md`
- Writing copy for Miru or PM → `docs/pm/00_PRINCIPLES.md` + `docs/pm/03_MIRU_LAYER.md`
- Before shipping any new PM feature → `docs/pm/08_PM_ANTI_PATTERNS.md`

**When craft guides conflict with operator directives:** operator directives win. Flag the conflict; don't silently override.
