# PM Boundary Law

> **Candidate for deletion, pending operator review** (flagged 2026-07-21). Most of this
> file duplicates `miru/AGENTS.md`, and the runtime it was written to guard is offline.
> Its one durable rule is the `pm/` <-> `miru_ai/` non-coupling rule below. An agent
> should not delete this file on its own initiative; that is the operator's call.

Ownership: `pm/` is the Project Miru runtime boundary for port `18080`.
**That runtime is PAUSED** (intentionally offline since 2026-05-19; no systemd unit,
nothing listening). This is a source-tree boundary only; nothing is running behind it.

## PM scope

- `pm/app.py` is the canonical PM server entrypoint.
- PM UI/server changes should stay inside `pm/` unless shared infrastructure is required.
- PM may consume neutral shared modules from `shared/` when needed.

## PM non-goals

- Do not move Miru AI ownership into PM.
- Do not import from `miru_ai.*` in PM runtime code.
- Do not change Miru AI startup/service paths from PM-only tasks.

## Path law

- Use repo-relative paths; do not hardcode absolute or Windows-era worktree paths.
- Keep PM data/config access compatible with worktree root `~/dev/miru`, and with the
  dispatch pool worktrees `~/dev/worktrees/project-miru/w1` through `w4`. (Path corrected
  2026-07-21 from `D:\dev\miru`, dead since the 2026-05-25 Linux migration.)

## Verification for PM edits

(Rewritten 2026-07-21. This section previously required `http://127.0.0.1:18080/` to
return `200`. PM has been intentionally PAUSED since 2026-05-19: no systemd unit, nothing
listening. That check cannot pass. A named check that is impossible to run is how a
fabricated CONFIRMED_WORKING gets written.)

- **There is no running PM service to verify against.** Do not attempt the old health
  check, and do not treat its absence as a pass.
- **Do NOT report `STATUS: CONFIRMED WORKING` on the basis of a check you could not run.**
  If your only evidence would have been a live 18080 response, the honest terminal state is
  `STATUS: INCONCLUSIVE`, with a diagnostic block saying the runtime is paused and naming
  what you actually did verify.
- Static verification that IS available and still expected: run the repo's tests for what
  you touched, and confirm the code imports cleanly.
- Confirm no new PM -> Miru AI import coupling was introduced. This rule is durable and
  outlives the runtime: it is a source-tree boundary, checkable by reading imports.
- If a task's acceptance criteria require a live 18080, the premise is wrong. Say so and
  escalate rather than improvising a substitute check.
