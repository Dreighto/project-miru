# Codex — Project Miru

## Ports — Permanent Reference

- 18080 = Project Miru UI — ACTIVE
- 18765 = Miru AI — ACTIVE
- 19000 = Task Dispatcher — ACTIVE
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo Boundary — Hard Rule

- Canonical repo root: D:\dev\miru
- Never leave this repo unless the operator explicitly authorizes it for a specific task
- If a task requires leaving the repo: STOP. Explain what you need to do and why. Wait for operator decision before proceeding.
- Never access, modify, or read files outside the repo root without explicit operator authorization

## No Overlap Rule

- Before starting any task, check what is currently being worked on
- If another worker is actively working on the same file or feature: STOP. Report the conflict to the operator. Do not proceed until the operator decides.
- Never modify a file that is currently open and being edited by another worker

## Notion — Read Rules

- ALL workers may READ Notion to understand the current job, active tasks, and system state
- Only Claude Chat may WRITE to Notion
- No other worker may create, update, or modify any Notion page under any circumstances
- Use Notion reads to avoid overlapping with in-progress work

## MCP Tool Usage Rules

- Use MCP tools when they genuinely help the task
- Always use sequential-thinking MCP for complex multi-step tasks before executing — think first
- Always use sqlite-ro-snapshot MCP to read card data before writing any intelligence pipeline code
- Use perplexity MCP for research tasks only
- Use notion MCP to read current job state
- Use git MCP to check what files are currently changed before starting work
- Never use a tool just because it is available — only use it if it helps this specific task
- Never write to the database through any MCP tool

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
- `dispatcher/` — ALL code for the Task Dispatcher (port 19000): task_dispatcher.py, handlers/, templates/, static/
- `shared/` — Only utilities imported by 2+ services. Not a dumping ground.
- `windows/` — Windows operational scripts (.ps1, .cmd) for service management ONLY. No Python service code here.

### Where new files go
- New Python module for miru_ai → `miru_ai/` (appropriate subfolder: core/, workers/, governance/, ingestion/)
- New Python module for dispatcher → `dispatcher/handlers/` or `dispatcher/`
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
- Never write *.log files to repo root or data/ root — always use `logs/`
- Never write *.db files to repo root — always use `data/`
- Never write *.png screenshots to repo root — use `archive/screenshots/`
- If a file belongs to miru_ai, pm, or dispatcher — it lives in that service directory, nowhere else
- Never create files in `data/startup-logs/` — that path is deprecated; use `logs/`

---

## Worker-specific: Codex

### Role

- Complex multi-file analysis
- Heavy reasoning tasks
- Architecture review and code quality analysis
- Tasks requiring deep understanding of entire codebase structure

### File ownership

- Codex is an analysis and reasoning worker
- Codex proposes changes — Cursor or Claude Code executes them
- Codex does not own any files directly

### Must never

- Never execute changes directly without operator confirmation
- Never modify .mcp.json or MCP config files
- Never write to card_catalog.db
- Never touch HTML/CSS/JS templates
- Never modify context files (CLAUDE.md, GEMINI.md, CURSOR.md, CODEX.md, COPILOT.md)
- Avoid em dashes in any PowerShell or script files — use plain ASCII hyphens only

## Completion Contract

Every task must end with exactly one of:

- STATUS: CONFIRMED WORKING
- STATUS: INCONCLUSIVE
- STATUS: FAILED

Plus a summary of what changed and what did not.

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
