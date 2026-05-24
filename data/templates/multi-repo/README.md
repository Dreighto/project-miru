# multi-repo dispatch: onboarding templates

Copy these files into a new repo before adding it to the Miru dispatch loop.
See `D:\dev\LogueOS-Orchestrator\.logueos\reference\multi-repo-onboarding.md` for the full 5-step checklist
and failure-mode history.

---

## Files

| Template file              | Destination in new repo                             |
| -------------------------- | --------------------------------------------------- |
| `dot-gitignore`            | `.gitignore` (merge critical entries)               |
| `dot-gemini-settings.json` | `.gemini/settings.json`                             |
| `dot-mcp-json`             | `.mcp.json` (operator interactive only; gitignored) |

---

## 5-Step Onboarding Checklist

**Step 1: WORKTREE** -- clone to `D:\dev\<RepoName>\`, add worktree at
`D:\dev\<RepoName>-w1` parked on orphan branch `_parking_<RepoName>-w1`.

**Step 2: POOL REGISTRATION** -- add entry to `WORKTREE_POOLS` in
`services/dispatch_listener/src/worktree.js` keyed by `'<RepoName>'`.

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
