# Miru AI Boundary Law

> **Candidate for deletion, pending operator review** (flagged 2026-07-21). Most of this
> file duplicates `miru/AGENTS.md`, and the runtime it was written to guard is gone. Its
> one durable rule is the `miru_ai/` <-> `pm/` non-coupling rule below. An agent should
> not delete this file on its own initiative; that is the operator's call.

Ownership: `miru_ai/` is the Miru AI / Dev runtime boundary for port `18765`.
**That runtime is decommissioned** (2026-05-25 Linux migration). This is a source-tree
boundary only; nothing is running behind it.

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

(Rewritten 2026-07-21. This section previously required `http://127.0.0.1:18765/api/health`
and `/dev` to return `200`. Port 18765 was decommissioned in the 2026-05-25 Linux
migration: no systemd unit, nothing listening. Those checks cannot pass. A named check
that is impossible to run is how a fabricated CONFIRMED_WORKING gets written.)

- **There is no running Miru AI service to verify against.** Do not attempt the old health
  checks, and do not treat their absence as a pass.
- **Do NOT report `STATUS: CONFIRMED WORKING` on the basis of a check you could not run.**
  If your only evidence would have been a live 18765 response, the honest terminal state is
  `STATUS: INCONCLUSIVE`, with a diagnostic block saying the runtime is decommissioned and
  naming what you actually did verify.
- Static verification that IS available and still expected: run the repo's tests for what
  you touched, and confirm the code imports cleanly.
- Confirm no new PM <-> Miru AI coupling was introduced. This rule is durable and outlives
  the runtime: it is a source-tree boundary, checkable by reading imports.
- If a task's acceptance criteria require a live 18765, the premise is wrong. Say so and
  escalate rather than improvising a substitute check.
