# Copilot — Project Miru

## Ports — Permanent Reference

- 18080 = Project Miru UI — ACTIVE
- 18765 = Miru AI — ACTIVE
- 19000 = Task Dispatcher — ACTIVE
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo Boundary — Hard Rule

- Canonical repo root: D:\dev\tcg-watcher-worktree
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

## Worker-specific: Copilot

### Role

- Lightweight single-function fixes
- Inline targeted edits only
- Small, well-scoped tasks with clear boundaries

### File ownership

- Copilot handles targeted single-function fixes only
- Never takes on structural or multi-file tasks

### Must never

- Never refactor across multiple files
- Never modify .mcp.json or MCP config files
- Never write to card_catalog.db
- Never touch HTML/CSS/JS templates (Gemini CLI owns these)
- Never modify context files
- Never take on tasks that span more than one file
- Always use inline completion mode — never rewrite entire files

## Completion Contract

Every task must end with exactly one of:

- STATUS: CONFIRMED WORKING
- STATUS: INCONCLUSIVE
- STATUS: FAILED

Plus a summary of what changed and what did not.
