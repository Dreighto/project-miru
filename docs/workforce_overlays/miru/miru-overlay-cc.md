# Miru overlay — Claude Chat / Claude Code

This is the **Miru-specific** overlay for Claude Chat (CH) and Claude Code (CC).

Global foundations live outside the repo at:

- `C:\Users\Dreighto\.claude\foundations\`

## Miru ports (permanent reference)

- 18080 = Project Miru UI — ACTIVE
- 18765 = Miru AI — ACTIVE
- 19000 = Task Dispatcher — DECOMMISSIONED (code kept, service stopped)
- 8080 = RESERVED — do not touch
- 8765 = NEVER TOUCH under any circumstances
- 11434 = Ollama — local dependency, not Miru-owned

## Repo boundary (Miru hard rule)

- Canonical repo: `Dreighto/project-miru`. Local checkouts live under `D:\dev\miru*`.
- Never access/modify/read files outside `D:\dev\miru*` worktrees without explicit operator authorization.

## Miru pre-flight gates (hard)

- Kill switch: `python tools/check_kill_switch.py`
- Worktree cleanliness: `python tools/check_worktree_clean.py`

## Miru workflow / canon entry points

- Worker baseline: [`AGENTS.md`](../../../AGENTS.md)
- CC/CH canon: [`CLAUDE.md`](../../../CLAUDE.md)
- Team ethos: [`miru-context/team-charter.md`](../../../miru-context/team-charter.md)
- Stewardship (CC): [`miru-context/job-stewardship.md`](../../../miru-context/job-stewardship.md)
