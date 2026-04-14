# Codex — Project Miru

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
- Never use nssm restart directly
- Never create alternate restart scripts

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
