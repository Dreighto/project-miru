# AGENTS.md — Project Miru Worker Baseline

This file is the shared worker baseline for Project Miru. Workers read this on every dispatch.
Worker-specific rule files (CLAUDE.md, GEMINI.md, CURSOR.md, etc.) layer on top of this baseline.

**Read `miru-context/team-charter.md` on every dispatch.** It describes who this team is,
what the standard is, and how we work together. The rules in this file tell you what to do.
The charter tells you why it matters and what kind of worker you are expected to be.

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

---

## gh CLI Auth Bootstrap

`gh` CLI auth is required for CC to open PRs from its bash terminal. Without it, all automated PR
creation fails with "gh-not-authenticated". This step must be performed on any fresh ROOM setup
and after any GitHub PAT rotation.

### Command

```bash
echo "$GITHUB_TOKEN_WRITE" | gh auth login --with-token
```

`GITHUB_TOKEN_WRITE` is the PAT stored in `D:\dev\miru\.env`. Do not echo the token value into
logs or chat.

### When this is needed

- Fresh ROOM node setup (new machine or re-imaged OS)
- After rotating the `GITHUB_TOKEN_WRITE` PAT in `.env`
- After a `gh auth logout` or credential cache invalidation
- If CC reports "gh-not-authenticated" during a PR creation step

### How to verify

```bash
gh auth status
```

Expected output includes `Logged in to github.com` and the account name. If it shows
`You are not logged in`, repeat the bootstrap command above.

### History

CC hit "gh-not-authenticated" on 2026-04-25 during PRO-76 and PRO-77, blocking automated PR
creation both times. Fixed by operator running `gh auth login` manually (PRO-78).

---

## Return-to-main — Hard Rule (all workers, locked 2026-04-30)

Every task session ends on `main` with a clean working tree. This applies to every worker.

**After a task completes (CONFIRMED_WORKING):**

1. Complete post-merge cleanup (see worker-specific rule file for steps).
2. Run `git checkout main && git pull origin main`.
3. Confirm `git status` shows no staged or unstaged tracked changes.
4. Sign off. Do not leave the session on a feature branch.

**After a task ends without a merge (INCONCLUSIVE, FAILED, interrupted):**

1. On the task branch: stash in-progress work (`git stash push -m "<ticket>-wip"`) or make a WIP commit so nothing is lost.
2. Run `git checkout main`.
3. Confirm clean state, then sign off.

**Why this is a hard rule:** A worker that ends on a feature branch leaves the repo in an ambiguous state. The next session — by the same worker or a different one — starts blind to the checked-out branch and may cut a new task branch from the wrong base, or accidentally stage work from a prior task into a new PR. This failure mode occurred in PRO-214 cleanup and required operator intervention.
