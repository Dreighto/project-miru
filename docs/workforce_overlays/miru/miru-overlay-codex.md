# Miru overlay — Codex

This is the **Miru-specific** overlay for the Codex worker.

Global foundations live outside the repo at:

- `C:\Users\Dreighto\.codex\skills\`

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

## Miru worker-local constraints

See [`CODEX.md`](../../../CODEX.md) for the project-scoped “must never”, DB rules, restart rules, and append-only JSONL boundaries.
