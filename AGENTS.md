# AGENTS.md — Project Miru Worker Baseline

This file is the shared worker baseline for Project Miru. Workers read this on every dispatch.
Worker-specific rule files (CLAUDE.md, GEMINI.md, CURSOR.md, etc.) layer on top of this baseline.

---

## Bugbot Findings Handling Contract (CC only, locked PRO-212 2026-04-29)

This contract applies to Claude Code (CC) only. It is not yet extended to Codex, Cursor, Gemini,
Copilot, or Windsurf — CC only until proven over several PRs.

After CC pushes a branch and opens a PR, and **before** declaring `CONFIRMED_WORKING`, CC must
execute the following Bugbot completion sequence:

### Step 1 — Poll for Bugbot check-run completion

First, obtain the PR head SHA: run `git rev-parse HEAD` after pushing, or call
`GET /repos/{owner}/{repo}/pulls/{number}` and read `.head.sha`.

Then poll `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` (substituting the head SHA) until
`Cursor Bugbot` has reached a terminal state (`completed`).

- `Cursor Bugbot Autofix` is advisory — do **not** wait for it. Proceed once `Cursor Bugbot` is
  `completed`, regardless of Autofix state.
- **Timeout:** if `Cursor Bugbot` has not reached `completed` after **10 minutes**, stop polling.
  Surface the timeout in the completion ping and proceed with whatever findings exist (or none).

Typical wait: 3–5 minutes. Poll with backoff; do not hammer the API.

### Step 2 — Evaluate Cursor Bugbot conclusion

- If `Cursor Bugbot` conclusion is **`success`** or **`neutral`**: skip to Step 5.
- If `Cursor Bugbot` conclusion is anything else (e.g. `failure`, `action_required`): continue to
  Step 3.

### Step 3 — Read and categorize findings

Read all review comments via `GET /repos/{owner}/{repo}/pulls/{number}/comments`.

Categorize each finding by Bugbot's severity classification:

| Severity   | Action                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------- |
| **Low**    | Attempt fix, commit, push. Wait for next Bugbot pass. **Maximum one iteration — do not loop.**    |
| **Medium** | Attempt fix, commit, push. Wait for next Bugbot pass. **Maximum one iteration — do not loop.**    |
| **High**   | Surface in completion ping with severity + recommendation. **Do NOT auto-fix.** Operator decides. |

### Step 4 — Override conditions (surface instead of auto-fix)

Even for Low/Medium findings, **do NOT auto-fix** if any of the following apply:

- The fix contradicts the Linear ticket spec.
- The fix requires touching a file on the ticket's `Don't touch` list.
- The finding appears incorrect or based on a misread of the code.
- Applying the fix would require changes outside the ticket's stated scope.

In any of these cases: surface the finding in the completion ping with your rationale.

### Step 5 — Declare terminal state

After Bugbot is clean (or all findings have been addressed or surfaced), append the
`cc_completion_log.jsonl` marker and report `CONFIRMED_WORKING` (or the appropriate terminal state)
to the operator.

### Scope

- **Applies to:** every PR CC opens, starting with the first dispatch after this contract is locked.
- **Does not apply to:** Codex, Cursor, Gemini, Copilot, Windsurf workers.
- **Does not modify:** dispatcher runtime code, W4, W7, or any other section of this file unrelated
  to PR completion and Bugbot handling.
