# Miru overlay — Claude Code (CC)

This is the **Miru-specific** overlay for Claude Code (CC), the default canon owner as of
the operator's 2026-07-12 SOP shift. Claude Chat (CH) is named below only where this file
already referenced it historically — CH is no longer the active canon owner or session
driver; see `CLAUDE_CHAT.md` at the repo root for its archived operating manual.

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
- CC canon: [`CLAUDE.md`](../../../CLAUDE.md)

Note: this section previously linked to `miru-context/team-charter.md` and
`miru-context/job-stewardship.md` — neither file exists in this repo. Removed rather than
left dangling; if those docs get written, add them back here.
