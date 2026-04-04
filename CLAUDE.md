# Project Miru Worktree Boundary Law

Root of truth: `D:\dev\tcg-watcher-worktree`

## Canonical environment law
- `18080` = Project Miru (`pm/`)
- `18765` = Miru AI / Dev (`miru_ai/`)
- `8080` = reserved future publish lane

## Subsystem ownership map
- `pm/` owns Project Miru runtime code and UI behavior for port `18080`.
- `miru_ai/` owns Miru AI server, workers, ingestion, and governance for port `18765`.
- `shared/` is neutral shared infrastructure (`shared.intel`, env helpers, and shared services).

## Worker path law
- Never hardcode deleted worktree paths (for example `C:\Users\andre\.codex\worktrees\0814\tcg-watcher`).
- Build paths from `Path(__file__).resolve()` relative to this repo root.
- Keep `data/`, `config/`, and `secrets/` repo-relative and portable.

## Coupling law
- PM code must not import `miru_ai.*`.
- Miru AI code must not take ownership of PM runtime files.
- Shared code must live in `shared/`, not in PM- or AI-owned runtime packages.

## Compatibility law
- `tools/miru_*.py` compatibility shims are intentionally preserved.
- Do not remove shims unless proven unused and explicitly validated in runtime checks.

After edits, verify runtime health:
- `http://127.0.0.1:18080/`
- `http://127.0.0.1:18765/api/health`
- `http://127.0.0.1:18765/dev`
