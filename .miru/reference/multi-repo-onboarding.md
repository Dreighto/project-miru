# Reference -- multi-repo dispatch onboarding

```text
Reference: multi-repo-onboarding
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: adding a new repo to the dispatch loop.
Last reviewed: 2026-05-10
```

This file documents the mandatory steps and known failure modes for onboarding
a new repository into the Miru dispatch loop. Templates live in
`data/templates/multi-repo/`.

---

## 5-Step Onboarding Checklist

**Step 1: WORKTREE** -- clone to `D:\dev\<RepoName>\`, add worktree at
`D:\dev\worktrees\<RepoName>\w1` parked on orphan branch
`_parking_<RepoName>-w1`.

> **LOS-14 layout (2026-05-11):** worktrees live under a centralized pool
> root (`D:\dev\worktrees\<RepoName>\w<N>`) rather than as siblings of the
> main checkout. The pool root is configurable via the
> `LOGUEOS_WORKTREE_BASE` env var. `parkingBranchForCwd` recognizes BOTH
> the LOS-14 layout AND the legacy `<RepoName>-w<N>` layout (for
> backward compat during the migration window). Existing `miru-w1..w6`
> and `LogueOS-Console-w1` still use the legacy paths until LOS-14 Part 2
> migrates them post-LOS-10 cutover.

**Step 2: POOL REGISTRATION** -- add entry to `WORKTREE_POOLS` in
`services/dispatch_listener/src/worktree.js` keyed by `'<RepoName>'`.
For new repos, use the `poolFor('<RepoName>', <slot_count>)` helper so
paths derive automatically from `LOGUEOS_WORKTREE_BASE`. Example:

```javascript
'NASDOOM': poolFor('NASDOOM', 2),  // → D:\dev\worktrees\NASDOOM\w1, ...\w2
```

**Step 3: CLIENT-SIDE ALLOWLIST** -- add `'<RepoName>'` to `_APPROVED_TARGET_REPOS`
in `tools/miru_mcp_gateway/dispatch_tools.py`. Parity enforced by
`tests/test_dispatch_tools_target_repo_parity.py`.

**Step 4: REPO-SIDE CONFIG** in the new repo on main:

a. `.gitignore` -- copy `data/templates/multi-repo/dot-gitignore`.
Critical: `.mcp.json`, `mcp.json` (per-spawn files; tracking them causes
dirty_worktree refusal on every subsequent dispatch).

b. `.gemini/settings.json` -- copy `data/templates/multi-repo/dot-gemini-settings.json`.
Without this, dispatched gemini workers have ZERO tools and hang.

c. (optional) `.mcp.json` -- only if operator interactive claude-code needed.

**Step 5: RESTART + SMOKE TEST** -- restart dispatch_listener, dispatch no-op
smoke test (both claude-code and gemini). Both must reach `STATUS: CONFIRMED WORKING`.

---

## Failure-Mode History

| #   | Failure                                               | Discovered              | Fix                       |
| --- | ----------------------------------------------------- | ----------------------- | ------------------------- |
| 1   | pre_spawn_dirty_refusal: unrecognized_worktree        | First smoke test        | PR #157                   |
| 2   | dirty_worktree: .mcp.json                             | Second smoke test       | Add to .gitignore         |
| 3   | worktree_auto_clean_failed: clean_worktree.py missing | Every non-miru dispatch | PRO-338                   |
| 4   | .mcp.json committed by git add .                      | LOS-2 retry             | git rm --cached .mcp.json |
| 5   | gemini zero tools (no .gemini/settings.json)          | LOS-2 first attempt     | Add .gemini/settings.json |

---

## History -- PRs that fixed each failure mode

| Failure # | PR / Ticket | Description                                                                |
| --------- | ----------- | -------------------------------------------------------------------------- |
| 1         | PR #157     | dispatch_listener: recognize non-miru worktree pools                       |
| 2         | PRO-340     | Add .mcp.json / mcp.json to multi-repo .gitignore template                 |
| 3         | PRO-338     | Ship clean_worktree.py to target repos at dispatch time                    |
| 4         | PRO-340     | Template .gitignore prevents accidental commit of .mcp.json                |
| 5         | PRO-340     | Template .gemini/settings.json ensures gateway tools available at dispatch |
