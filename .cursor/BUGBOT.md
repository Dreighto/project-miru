# Project Miru — Bugbot Rules (Project-Wide)

## Ports — flag any hardcoded port numbers
- 18080 = PM UI, 18765 = Miru AI, 19000 = Task Dispatcher
- 8080 = RESERVED — never bind or reference in new code
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama (external dep, not Miru-owned)

## card_catalog.db — zero direct writes
- No INSERT/UPDATE/DELETE against card_catalog.db from any worker session
- Read path: sqlite-ro-snapshot MCP only
- Schema changes: propose to Claude Chat first, operator approves before execution

## File placement — service boundaries are hard
- `miru_ai/` owns all Miru AI service code; `pm/` owns all PM code; `dispatcher/` owns dispatcher
- `shared/` is for utilities imported by 2+ services — not a dumping ground
- Never create service code (.py, .html, .css, .js) at repo root
- New batch reports/audit CSVs → `data/batch_reports/`; logs → `logs/` (never root or `data/`)

## No cross-service coupling from PM
- PM runtime code must not import from `miru_ai.*`
- No Miru AI ownership moved into `pm/`

## Claude Code file restrictions
- Claude Code must not touch HTML/CSS/JS templates
- Claude Code must not modify `.mcp.json` or any MCP config
- Claude Code must not write to `card_catalog.db`

## Card images — Bandai CDN only
- Never load card art from TCGPlayer (JPEG compression causes color distortion confirmed 2026-04-16)
- Bandai CDN is the only approved source for card images

## Linear write access — READ-ONLY by default
- No Linear mutations unless operator explicitly delegates the task
- When delegated, scope is limited to what the issue describes — stop and report if scope drifts
- Status transitions: In Progress → In Review only; never set to Done

## Restart scripts — use canonical paths only
- PM: `windows/restart_pm.ps1`; Miru AI: `windows/restart_miru_ai.ps1`; Dispatcher: `windows/restart_dispatcher.ps1`
- Never call `nssm restart` directly; never create alternate restart scripts

## Repo boundary
- Never access, modify, or read files outside `D:\dev\miru` without explicit operator authorization

## No overlap
- Check git status and Notion job state before starting; stop and report if another worker owns the target file
