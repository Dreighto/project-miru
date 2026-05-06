# AGENTS.md — Universal Worker Baseline

# Source: Dreighto/worker-framework

This file is the universal operating baseline for all workers across all projects.
The canonical version lives at `Dreighto/worker-framework`. Project-specific AGENTS.md
files copy this baseline and append project-specific overlays below the divider.

**Precedence (lowest to highest):**

1. This file — universal baseline, applies to every worker on every project
2. Worker rule file (CLAUDE.md, GEMINI.md, etc.) — adds worker-type rules
3. Project AGENTS.md — project-specific additions and overrides
4. Task prompt — most specific scope

When rules conflict, higher-precedence rules win. Project overlays may tighten
universal rules but must not weaken safety or operator-protection rules.

**For new projects:** Copy this file to the project repo as `AGENTS.md`, add the header
below, then append project-specific rules after the `--- PROJECT OVERLAY ---` marker.

```
# AGENTS.md — [Project Name] Worker Overlay
# Framework source: Dreighto/worker-framework | Last synced: YYYY-MM-DD
# Project-specific rules are appended below the universal baseline.
```

---

## Operator Communication Standard — Hard Rule (all workers)

Every output that reaches the operator for review must open with a plain-English summary.
No exceptions. No jargon. No walls of file paths.

**The operator is not a developer.** Technical status does not communicate "is this done
and what do I need to do." Workers that skip the plain-English layer are making the
operator do translation work — which defeats the purpose of having autonomous workers.

### Required format for all operator-facing outputs

```
What happened:       [one sentence, no jargon]
Does it work:        [Yes / No / Partially — plus one plain-English reason]
What you need to do: [specific action, or "Nothing — it's done"]
```

Technical content (file paths, commit SHAs, test output, JSON) goes below a `---`
divider. Other workers will find what they need there. The operator will not have to
scroll past jargon to understand what happened.

### Rules

- The plain-English block comes **first**. Always. No preamble before it.
- "What happened" is one sentence. If you need more than one, you are over-explaining.
- "Does it work" must be a definitive answer. "It should work" is not an answer.
  If you cannot say yes, say Partially or No, and say why in one plain sentence.
- "What you need to do" must be actionable. "Approve the PR at [url]" is actionable.
  "Review the changes" is not. If there is nothing to do, write "Nothing — it's done."

### What counts as operator-facing

- Telegram notifications
- In-chat completion reports from any worker in the operator's session
- PR titles and the opening section of PR descriptions
- Linear comments on a ticket the operator is watching or reviewing
- Escalation messages (ESCALATE, INCONCLUSIVE, BLOCKED_ON)

### What is exempt

- Worker-to-worker coordination: Linear internal comments, heartbeat logs, JSON
  completion records — these stay technical. Workers can read code.
- Internal logs, test output, diffs — never operator-facing.

### Why this exists

The operator runs a multi-worker autonomous system. Their job is to make decisions,
not to translate technical status into plain language. Every minute spent parsing jargon
is a minute not spent on the next decision. Workers that communicate clearly make the
whole system faster. Workers that bury the status in word vomit make it slower.

---

## PR Review Completion Sequence — Hard Rule (all workers)

Before declaring `CONFIRMED_WORKING` on any PR, the worker MUST:

### Step 1 — Wait for all automated reviewers to complete

Poll until all automated reviewers reach a terminal state:

- **CodeRabbit** — AI code review (if configured)
- **CI hygiene** — lint, format, schema validation
- Any project-configured automated reviewer

**Timeout:** if any reviewer has not completed after **10 minutes**, stop polling.
Surface the timeout in the completion report and proceed with existing findings.

### Step 2 — Read every finding

Read all review comments via `gh api repos/{owner}/{repo}/pulls/{number}/comments`
and `gh api repos/{owner}/{repo}/pulls/{number}/reviews`. Categorize each finding:

**Actionable:** Code bugs, missing fields that break downstream consumers,
false-positive keyword matches, test gaps required by adopted lessons, permission
contradictions, any finding rated P1/P2 or flagged as a potential issue.

**Not actionable:** Style preferences that conflict with project conventions, docstring
coverage warnings (project convention: no docstrings unless non-obvious), suggestions
to add features beyond the PR scope.

### Step 3 — Fix valid findings

Push a follow-up commit addressing each actionable issue. Either fix it or explain in
the commit message why it is not applicable.

### Step 4 — Re-run and poll

After pushing fixes, wait for the next review cycle. Repeat Steps 2–4 until no new
actionable findings remain.

### Step 5 — Merge per merge policy

All status checks must show pass/success before declaring done. Self-merge if the
change qualifies; ping the operator only if it requires operator-level review.

**A PR with unaddressed P1 findings that gets merged is a discipline violation.**

---

## gh CLI Auth Bootstrap (Windows + GitHub)

`gh` CLI auth is required for any worker that opens PRs from an automated session.
Without it, all automated PR creation fails.

### Command

```bash
echo "$ROOM_TOKEN_OPERATOR" | gh auth login --with-token
```

Store `ROOM_TOKEN_OPERATOR` in the project's `.env` file. Never echo the token value
into logs or chat output.

**Dispatched workers** do not need this command — `spawn.js` injects `GH_TOKEN` set
to `ROOM_TOKEN_WORKER` automatically. The bootstrap above is for the operator's
direct CC session only.

### When this is needed

- Fresh machine setup or re-imaged OS
- After rotating `ROOM_TOKEN_OPERATOR` in `.env`
- After `gh auth logout` or credential cache invalidation
- If CC reports "gh-not-authenticated" during PR creation in a direct session

### Verify

```bash
gh auth status
```

Expected output includes `Logged in to github.com` and the account name.

---

## Return-to-main — Hard Rule (all workers)

Every task session ends on `main` with a clean working tree. No exceptions.

**After a task completes (CONFIRMED_WORKING):**

1. Complete post-merge cleanup (delete feature branch: `git branch -d <branch>`).
2. Run `git checkout main && git pull origin main`.
3. Confirm `git status` shows no staged or unstaged tracked changes.
4. Sign off. Do not leave the session on a feature branch.

**After a task ends without a merge (INCONCLUSIVE, FAILED, interrupted):**

1. Stash in-progress work (`git stash push -m "<ticket>-wip"`) or make a WIP commit.
2. Run `git checkout main`.
3. Confirm clean state, then sign off.

**Why:** A worker that ends on a feature branch leaves the repo in an ambiguous state.
The next session starts blind to the checked-out branch and may cut work from the
wrong base, or accidentally stage prior-task work into a new PR.

---

## Try Harder Discipline — All Workers

Before emitting `INCONCLUSIVE`, every worker must complete all four steps below.
Asking for help before trying is not acceptable. Asking after trying — with documented
attempts — is expected.

### Step 1 — Check the canon

Read AGENTS.md, worker rule files (CLAUDE.md, GEMINI.md, etc.), project context docs,
and prior completion markers for the same area of the codebase. The answer is often
already there.

### Step 2 — Search the repo

Use grep, glob, and file reads to find how similar problems were solved. Consistency
with the existing codebase is almost always the right call.

### Step 3 — Try at least one alternative approach

If the first approach is blocked, reason through a second and attempt it. A different
angle, a simpler implementation, a fallback that satisfies the done-when criteria
without the blocked path. Document both attempts.

### Step 4 — Then ask — with evidence

If genuinely blocked after all of the above, emit `INCONCLUSIVE` with:

- What you tried (specific, not vague — name the approach and what it hit)
- Why each attempt failed or is insufficient
- One specific question that, if answered, unblocks you

**Required format:**

> I tried [X] — it failed because [specific reason]. I tried [Y] — it failed because
> [specific reason]. Question: should I [A] or [B]?

**Not acceptable:**

> I'm not sure how to proceed. Can you clarify?

"I don't know how to proceed" is not a question. A question has a specific, answerable
option embedded in it. Every premature INCONCLUSIVE costs a full operator loop.

---

## Merge Policy — All Workers

Evaluate every commit against this tiered policy before committing. Do not default to
opening a PR. Direct-to-main and self-merge are valid and preferred when the change
qualifies.

### Tier 1 — Direct-to-main (no PR)

Trivially correct changes with no meaningful risk of breakage:

- Typo or wording fixes in worker rule files — no logic change
- Version bumps in CI config — one-liners
- Completion log entries (append-only JSONL)
- Lint/format-only auto-fixes with no logic change

### Tier 2 — Worker self-merge (open PR, worker merges when checks pass)

- Single-file edits to existing files
- Bug fixes to existing behavior (known pattern)
- Config changes
- Test fixtures and hygiene tasks
- Worker rule file additions or substantive edits (new rules, not typos)

**Before self-merging:** CI green, automated reviewer(s) clean or timed out with no
actionable findings, branch cut clean from main, task reports CONFIRMED_WORKING.
Self-merge = squash merge + delete branch.

### Tier 3 — Operator merge (open PR, ping operator, wait)

- New files or new directories
- Multi-system or multi-workflow changes
- Schema or data model changes
- Infrastructure (gateway, MCPs, port assignments, services)
- First implementation of something new (no prior pattern to follow)

**When unsure:** default to operator merge (fail-closed).

**Fix vs Change:** Fix = restore expected behavior of something that already exists.
Change = add capability or alter the contract. When unsure, default to Tier 3.

**Mandatory pre-commit decision — run this before every commit:**

```
1. Does ANY file match the Tier 1 list? → Commit direct to main.
2. Do ALL files match the Tier 2 list?  → Open PR, self-merge after checks pass.
3. Does ANY file match the Tier 3 list? → Open PR, ping operator. Do not merge.
4. Unsure?                              → Treat as Tier 3 (fail-closed).
```

---

## Completion Contract — All Workers

Every task must end with exactly one terminal state:

- `STATUS: CONFIRMED WORKING` — change verified, merged, system in expected state
- `STATUS: INCONCLUSIVE` — attempted but verification could not confirm outcome
- `STATUS: FAILED` — attempted and result does not meet acceptance criteria

Plus a plain-English summary of what changed and what did not.

### Mid-task stall signals

| Signal                | Format                                         | When to use                                                                 |
| --------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- |
| Ambiguous spec        | `STATUS: INCONCLUSIVE` + one specific question | After full try-harder discipline                                            |
| Dependency starvation | `STATUS: BLOCKED_ON: <ticket_id>`              | Waiting on another ticket                                                   |
| Human required        | `STATUS: ESCALATE: <category>`                 | SECURITY, SCOPE_EXPANSION, DESIGN_CHANGE, IRREVERSIBLE_OP, REPEATED_FAILURE |

For `ESCALATE`: the category determines what happens next. SECURITY and IRREVERSIBLE_OP
go to operator immediately. SCOPE_EXPANSION may be filed as a follow-up ticket while
in-scope work continues. DESIGN_CHANGE and REPEATED_FAILURE always go to operator.

---

## Append-Only Log Files — Hard Rule

Structured log files used for audit trails, routing history, heartbeats, or completion
records are strictly append-only. Never edit, truncate, sort, or deduplicate them.
Only append new lines. Pre-commit hooks should exclude these files from reformatting.

This invariant is enforced by tests. If you find yourself wanting to edit a JSONL log
file: STOP, escalate to the operator. The append-only contract is what makes the audit
trail trustworthy.
