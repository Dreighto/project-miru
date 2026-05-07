# AGENTS.md — Project Miru Worker Baseline

# Framework source: Dreighto/worker-framework | docs/worker-framework/AGENTS.md

# Last synced: 2026-05-06

#

# Universal rules (Operator Communication Standard, PR Review Sequence, Return-to-main,

# Try Harder Discipline, Merge Policy, Completion Contract) are maintained in the

# framework and embedded here. Miru-specific rules follow the universal sections.

This file is the shared worker baseline for Project Miru. Workers read this on every dispatch.
Worker-specific rule files (CLAUDE.md, GEMINI.md, CURSOR.md, etc.) layer on top of this baseline.

**Read `miru-context/team-charter.md` on every dispatch.** It describes who this team is,
what the standard is, and how we work together. The rules in this file tell you what to do.
The charter tells you why it matters and what kind of worker you are expected to be.

---

## Operator Communication Standard — Hard Rule (all workers, set 2026-05-06)

Every output that reaches the operator for review must open with a plain-English summary.
No exceptions. No jargon. No walls of file paths.

**The operator is not a developer.** Technical status does not communicate "is this done and
what do I need to do." Workers that skip the plain-English layer are making the operator do
translation work — which defeats the purpose of having autonomous workers.

### Required format for all operator-facing outputs

```
What happened:      [one sentence, no jargon]
Does it work:       [Yes / No / Partially — plus one plain-English reason]
What you need to do: [specific action, or "Nothing — it's done"]
```

Technical content (file paths, commit SHAs, test output, JSON) goes below a `---` divider.
Other workers will find what they need there. The operator will not have to scroll past
jargon to understand what happened.

### Rules

- The plain-English block comes **first**. Always. No preamble before it.
- "What happened" is one sentence. If you need more than one, you are over-explaining.
- "Does it work" must be a definitive answer. "It should work" is not an answer. If you
  cannot say yes, say Partially or No, and say why in one plain sentence.
- "What you need to do" must be actionable. "Approve the PR at [url]" is actionable.
  "Review the changes" is not. If there is nothing to do, write "Nothing — it's done."

### What counts as operator-facing

- Telegram notifications
- In-chat completion reports from any worker in the operator's session
- PR titles and the opening section of PR descriptions
- Linear comments on a ticket the operator is watching or reviewing
- Escalation messages (ESCALATE, INCONCLUSIVE, BLOCKED_ON)

### What is exempt

- Worker-to-worker coordination: Linear internal comments, heartbeat logs, the JSON
  completion record — these stay technical. Workers can read code.
- Internal logs, test output, diffs — never operator-facing.

### Why this exists

The operator runs a multi-worker autonomous system. Their job is to make decisions, not
to translate technical status into plain language. Every minute spent parsing jargon is a
minute not spent on the next decision. Workers that communicate clearly make the whole
system faster. Workers that bury the status in word vomit make it slower.

---

## Automated PR Review Completion Sequence (all workers + CH, locked 2026-05-04)

This contract applies to ALL workers (CC, Codex, Cursor, Gemini, Copilot, Windsurf) and Claude
Chat when it owns a PR. Supersedes the previous CC-only Bugbot contract (PRO-212).

Before declaring `CONFIRMED_WORKING` on any PR, the worker (or CH if it owns the PR) MUST:

### Step 1 — Wait for all automated reviewers to complete

Poll `gh pr checks <number> --watch` or `GET /repos/{owner}/{repo}/commits/{sha}/check-runs`
until all automated reviewers reach a terminal state:

- **CodeRabbit** — AI code review
- **Bugbot** (chatgpt-codex-connector) — automated bug detection
- **CI hygiene** — lint, format, schema validation

**Timeout:** if any reviewer has not completed after **10 minutes**, stop polling. Surface the
timeout in the completion report and proceed with whatever findings exist.

### Step 2 — Read every finding

Read all review comments via `gh api repos/{owner}/{repo}/pulls/{number}/comments` and
`gh api repos/{owner}/{repo}/pulls/{number}/reviews`. Categorize each finding:

**Actionable:** Code bugs, missing fields that break downstream consumers, false-positive keyword
matches, test gaps the adopted lesson requires (PRO-189 boundary-crossing tests), permission
contradictions (e.g. telling a read-only worker to write), and any finding rated P1/P2 or flagged
as a potential issue.

**Not actionable:** Style preferences that conflict with project conventions, docstring coverage
warnings (project convention: no docstrings unless non-obvious), and suggestions to add features
beyond the PR scope.

### Step 3 — Fix valid findings

Push a follow-up commit addressing each actionable issue. For each finding, either fix it or
explain in a commit message why it's not applicable.

### Step 4 — Re-run and poll

After pushing fixes, wait for the next review cycle to complete. Repeat Steps 2–4 until no new
actionable findings remain.

### Step 5 — Confirm green and declare terminal state

All status checks must show pass/success before declaring done. `CHANGES_REQUESTED` from an
automated reviewer with no remaining actionable comments is acceptable only if all specific
findings have been addressed in commits.

Append the `cc_completion_log.jsonl` marker and report `CONFIRMED_WORKING` (or the appropriate
terminal state) to the operator.

### Scope

- **Applies to:** every PR any worker or CH opens, starting 2026-05-04.
- A PR with unaddressed P1 findings that gets merged is a discipline violation.

---

## gh CLI Auth Bootstrap

`gh` CLI auth is required for CC to open PRs from its bash terminal. Without it, all automated PR
creation fails with "gh-not-authenticated". This step must be performed on any fresh ROOM setup
and after any GitHub PAT rotation.

### Command

```bash
echo "$ROOM_TOKEN_OPERATOR" | gh auth login --with-token
```

`ROOM_TOKEN_OPERATOR` is the operator-level PAT stored in `D:\dev\miru\.env`. Do not echo the
token value into logs or chat. Dispatched workers authenticate via `GH_TOKEN` (injected by
`spawn.js`) and do not need to run this command.

### When this is needed

- Fresh ROOM node setup (new machine or re-imaged OS)
- After rotating the `ROOM_TOKEN_OPERATOR` PAT in `.env`
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

---

## Try Harder Discipline — All Workers (locked PRO-269 2026-05-02)

Before emitting `INCONCLUSIVE`, every worker must complete all four steps below. Asking for help
before trying is not acceptable. Asking after trying — with documented attempts — is expected.

### Step 1 — Check the canon

Read CLAUDE.md, AGENTS.md, team-charter.md, and any miru-context/ files relevant to the problem.
Read prior completion markers for the same area of the codebase (`data/cc_completion_log.jsonl`).
The answer is often already there.

### Step 2 — Search the repo

Use grep, glob, and file reads to find how similar problems were solved before. Consistency with
the existing codebase is almost always the right call. If another ticket touched the same file or
function, read that diff.

### Step 3 — Try at least one alternative approach

If the first approach is blocked, reason through a second one and attempt it. A different angle,
a simpler implementation, a fallback that satisfies the ticket's done-when criteria without the
blocked path. Document both attempts in your INCONCLUSIVE report.

### Step 4 — Then ask — with evidence

If you are genuinely blocked after all of the above, emit `INCONCLUSIVE` with:

- What you tried (specific, not vague — name the approach and what it hit)
- Why each attempt failed or is insufficient
- One specific question that, if answered, unblocks you

**Required format:**

> I tried [X] — it failed because [specific reason]. I tried [Y] — it failed because [specific reason].
> Question: should I [A] or [B]?

**Not acceptable:**

> I'm not sure how to proceed. Can you clarify?

"I don't know how to proceed" is not a question. A question has a specific, answerable option embedded in it.

### Why this matters

Every premature INCONCLUSIVE costs a full operator loop and breaks the autonomous flow. Workers
that ask before trying are not saving time — they are spending the operator's time instead of
their own. Try harder first. The team gets better when workers solve more problems themselves.

---

## WIP Commit Checkpoints — All Workers (set 2026-05-07, PRO-318)

Workers MUST commit in-progress work to the task branch periodically during long-running tasks.
Git commits are the checkpoint mechanism. If a worker times out or crashes, committed work can be
recovered by the salvage scanner (`tools/salvage_worktree.py`). Uncommitted work in a dirty tree
is fragile and often lost.

### When to commit

Commit a WIP checkpoint at each of these moments:

1. **After branch creation and pre-flight pass** -- confirms the workspace is set up correctly.
2. **After each major implementation phase** -- tests written, source code done, config updated.
3. **Before any operation expected to take >60 seconds** -- CI wait, Bugbot poll, large test suite.
4. **Before the final cleanup** -- so a crash during squash/rebase doesn't lose the work.

### Commit message format

```
WIP: <TICKET-ID> - <phase label>
```

Examples:

- `WIP: PRO-318 - tests written`
- `WIP: PRO-318 - implementation complete, pre-commit next`
- `WIP: PRO-318 - awaiting bugbot`

### Squash before PR

WIP commits are internal checkpoints, not PR history. Before opening a PR:

1. Squash all WIP commits into a single clean commit (or a small series of logical commits).
2. Write a proper commit message per project conventions.
3. The WIP prefix must not appear in the final PR commit history.

Use `git rebase -i` (non-interactive: `git reset --soft <base> && git commit`) to squash.

### What NOT to do

- Do not skip WIP commits because "the task is almost done" -- timeouts don't care how close you
  are to finishing.
- Do not leave WIP commits in the PR -- they clutter history and signal incomplete work.
- Do not commit secrets, `.env` files, or `node_modules` in WIP commits. The usual rules apply.

### Why this exists

PRO-312 completed all work (427 lines of code, 24 passing tests) but timed out during `--print`
mode output buffering. All stdout was lost. The code survived only because an operator manually
salvaged the dirty worktree. Periodic WIP commits would have made that salvage automatic and
reliable. The Atomix research paper (arxiv:2602.14849) confirms: periodic checkpointing reduces
unrecoverable failures from 23% to under 2%.
