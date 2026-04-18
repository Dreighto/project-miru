# PM Boundary Law

Ownership: `pm/` is the Project Miru runtime boundary for port `18080`.

## PM scope
- `pm/app.py` is the canonical PM server entrypoint.
- PM UI/server changes should stay inside `pm/` unless shared infrastructure is required.
- PM may consume neutral shared modules from `shared/` when needed.

## PM non-goals
- Do not move Miru AI ownership into PM.
- Do not import from `miru_ai.*` in PM runtime code.
- Do not change Miru AI startup/service paths from PM-only tasks.

## Path law
- Use repo-relative paths; do not hardcode deleted C-drive worktree paths.
- Keep PM data/config access compatible with worktree root `D:\dev\miru`.

## Verification for PM edits
- Confirm `http://127.0.0.1:18080/` returns `200`.
- Confirm no new PM -> Miru AI import coupling was introduced.
