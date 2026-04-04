# Miru AI Boundary Law

Ownership: `miru_ai/` is the Miru AI / Dev runtime boundary for port `18765`.

## Miru AI scope
- Canonical startup path: `python -m miru_ai.server`.
- Worker, ingestion, governance, and AI runtime behavior belong under `miru_ai/`.
- Shared cross-boundary infrastructure belongs under `shared/`.

## Miru AI non-goals
- Do not take ownership of PM runtime files under `pm/`.
- Do not introduce Miru AI -> PM runtime coupling unless explicitly required and approved.

## Worker path law
- Derive project paths from `Path(__file__).resolve()` and repo-relative roots.
- Do not hardcode deleted worktree paths such as `C:\Users\andre\.codex\worktrees\0814\tcg-watcher`.
- Keep data/log/config path references portable in this worktree.

## Compatibility law
- `tools/miru_*.py` compatibility wrappers are intentionally preserved for transition safety.
- Do not remove wrappers unless proven unused and validated by runtime checks.

## Verification for Miru AI edits
- Confirm `http://127.0.0.1:18765/api/health` returns `200`.
- Confirm `http://127.0.0.1:18765/dev` returns `200`.
- Confirm no new PM <-> Miru AI coupling was introduced.
