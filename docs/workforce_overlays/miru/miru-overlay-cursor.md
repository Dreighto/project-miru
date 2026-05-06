# Miru overlay — Cursor

This is the **Miru-specific** overlay for the Cursor worker.

Global foundations live outside the repo at:

- `C:\Users\Dreighto\.cursor\foundations\`

## Miru ports (permanent reference)

- 18080 = Project Miru UI — ACTIVE
- 18765 = Miru AI — ACTIVE
- 19000 = Task Dispatcher — DECOMMISSIONED (code kept, service stopped)
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo boundary (Miru hard rule)

- Canonical repo: `Dreighto/project-miru`. Local checkout: `D:\dev\miru`.
- Never access/modify/read files outside `D:\dev\miru*` worktrees without explicit operator authorization.

## Miru lane ownership + craft guides

See [`CURSOR.md`](../../../CURSOR.md) for Miru lane ownership (PM storefront UI), must-never boundaries, and the craft-guide triggers under `docs/ui_ux/` and `docs/pm/`.
