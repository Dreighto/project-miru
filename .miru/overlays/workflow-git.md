# Overlay — workflow-git

```text
Overlay: workflow-git
Architecture: MIRU-INSTRUCTIONS-v2
Load when: committing, opening a PR, merging a PR, or running pre-PR hygiene.
Last reviewed: 2026-05-08
```

This overlay carries the rules that govern git operations: PR merge policy,
hygiene gate, automated PR review sequence, gh CLI auth, WIP commit
checkpoints, and post-merge cleanup.

---

## PR Merge Policy — CC self-merges low-risk PRs

CC may self-merge PRs that fall in the low-risk column below. Operator reviews and merges anything in the high-risk column.

**No PR needed — commit direct to main:**

Small, obviously-correct changes that carry no meaningful risk of breakage may be committed directly to main without opening a PR. Bugbot and CI do not need to run on these.

- Version bumps in CI config (e.g. `node-version`, action runner pins) — one-liners
- Typo or wording fixes in worker rule files (CLAUDE.md, AGENTS.md, etc.) — no logic change
- Completion log entries (`data/cc_completion_log.jsonl` appends)
- Lint / format-only auto-fixes with no logic change

**CC merges (fixes):**

- Single-file edits to existing files
- Single-workflow JSON changes
- Bug fixes following a known canon-lesson pattern
- Config changes (.env, docker-compose env vars)
- Test fixtures, log rotation, hygiene tasks
- Lint / format / comment-only changes
- Worker rule file additions or substantive edits (CLAUDE.md, AGENTS.md, CURSOR.md, etc.) — new rules, not typos
- PRs that reference one Linear ticket
- Bugbot not required — skip Bugbot wait for PRs in this column

**Operator merges (changes):**

- New files or new directories
- Multi-workflow changes
- Schema or data model changes
- Anything touching `card_catalog.db` or its schema
- Anything that changes `routing_history.jsonl` schema
- Infrastructure (gateway, MCPs, port assignments)
- First implementation of something new (e.g. W3 build)

**Mandatory pre-commit decision — workers must run this before every commit:**

Before staging any files, evaluate which tier applies to this change. Do not default to opening a PR. Direct-to-main and CC-merge are valid and preferred when the change qualifies.

```text
1. Does ANY file match the direct-to-main list above?
   → Commit direct to main. No PR, no Bugbot wait.

2. Do ALL files match the CC-merge list?
   → Open PR, CC self-merges after CONFIRMED WORKING.

3. Does ANY file match the operator-merge list?
   → Open PR, ping operator via Claude Chat. Do not merge.

4. Unsure?
   → Treat as operator-merge (fail-closed). Open PR, ping operator.
```

Workers that skip this evaluation and default to opening a PR for direct-to-main changes are wasting operator attention. Workers that skip this and commit infrastructure changes direct to main are in violation.

**Principle:** CC merges fixes. Operator merges changes. Fix = restore expected behavior of something that already exists. Change = add capability or alter the contract. When unsure, default to opening the PR for operator review (fail-closed). The cost of waiting for an operator review is minutes; the cost of a wrong self-merge is a revert plus context loss.

**Hard requirements before CC self-merges:**

1. PR is in the CC-merge column above (CC must explicitly check)
2. CC's own completion contract reports CONFIRMED WORKING (not INCONCLUSIVE)
3. Branch was cut clean from main (no concern braiding)
4. Bugbot: not required for CC-merge column — do not wait for it

If any of those fail: open the PR for operator review, do not self-merge.

**Never self-merge:**

- Force-push or destructive git operations (these are hard rules under access progression, not just merge policy)

**Post-merge cleanup — worker responsibility (locked 2026-04-28 per PRO-180, updated 2026-05-07):**

Whoever opened the PR is responsible for post-merge cleanup. The operator should NOT be cleaning up branches manually after merging.

After a PR is merged (whether self-merged or operator-merged):

1. The worker (or Claude Chat, if it owned the PR) checks out `main`.
2. Pulls latest.
3. Attempts `git branch -d <branch-name>` (lowercase `-d`, safe-delete).
4. If `-d` fails with "not fully merged" (normal for squash merges): verify via `gh pr list --head <branch-name> --state merged` that a merged PR exists. If verified, use `git branch -D <branch-name>` (uppercase `-D`, force-delete). If no merged PR is found: STOP and report.
5. Reports deletion. If anything looks off (working tree unexpectedly dirty, etc.): STOP and report.

**Verified force-delete rule (replaces blanket `-D` prohibition, set 2026-05-07):**

Workers may use `git branch -D` ONLY after verifying via `gh pr list --head <branch> --state merged` that a merged PR exists for that branch. No merged PR = no force-delete. No exceptions. This is necessary because squash merges (our standard merge strategy) make git unable to detect that a branch was merged, causing `-d` to always fail.

**Automated branch cleanup — safety net:**

`tools/prune_merged_branches.py` is the centralized cleanup script. It finds local branches whose remote tracking ref is gone, verifies each via GitHub API, and force-deletes only verified-merged branches. Skips branches checked out in worktrees and protected patterns (main, develop, _parking_\*).

- Dry run: `python tools/prune_merged_branches.py --dry-run`
- Execute: `python tools/prune_merged_branches.py`
- JSON output: add `--json-output`

Workers should run this during pre-flight when they notice stale branches accumulating. It catches branches from operator-merged PRs, terminated sessions, and any other gaps in worker cleanup.

If operator merges via the GitHub UI and the worker is not present in that session, the next worker that picks up a ticket on `main` is responsible for noticing stale branches in their pre-flight and running the prune script before cutting a new branch.

Operator should never have to ask a worker to clean up a branch. If you find yourself doing it, that's a discipline violation worth noting.

Source: locked 2026-04-25 after CC shipped 4 clean ticket fixes (PRO-60, PRO-65, PRO-72, PRO-68 + PRO-73) with consistent pre-flight discipline. Post-merge cleanup rule added 2026-04-28 per PRO-180 retro. Verified force-delete and prune script added 2026-05-07 after 28 stale branches accumulated from squash-merge cleanup failures.

---

## Bundling Policy — Risk-Based PR Granularity (set 2026-05-09)

The merge tiers above (direct-to-main / CC-merge / operator-merge) decide WHO merges. This section decides WHAT'S IN a single PR — granularity, not authority.

**The default is no longer "one logical change per PR".** That rule was designed for many-author / many-reviewer OSS projects where strangers review each other's code. In a single-operator + AI-worker setup the bottleneck is one human reviewing many AI-authored small commits; PR ceremony per atomic change taxes the reviewer for insurance the architecture doesn't need at this scale.

Replace it with risk-based batching. The matrix below works alongside the existing merge tiers — bundling decides the contents of one PR; merge tier decides who clicks merge.

**Bundle freely (target: 1–3 PRs per focused work session):**

- Scaffolding work — internal infrastructure with no customer-facing surface
- Cleanup and refactoring with no behavioral change
- Documentation updates
- Test-only changes
- Mechanical changes (rename, move, lint, format) with no logic shift
- Infrastructure hardening that does NOT touch the gates themselves

**Stay one-per-PR (atomic, never bundled):**

- Anything in the governance file registry: `gatekeeper/`, `.miru/overlays/`, `.miru/reference/`, `.pre-commit-config.yaml`, `tools/check_*.py`, `tools/validate_*.py`, `data/config/w2_profile_rules.json`, `tools/miru_mcp_gateway/profiles.py`
- Security boundary changes — auth gates, profile permissions, MCP gateway entry middleware (`tools/miru_mcp_gateway/server.py` `_is_local_origin`, `_ProfileExtractor`, related)
- Customer-facing behavior — anything users (or the claude.ai connector) can observe
- Data migrations — schema changes, large data rewrites, anything irreversible
- Cross-service orchestration changes spanning multiple services (n8n + dispatcher + listener) — each service's changes in its own atomic PR, ship in dependency order

**Bundling requirements (when allowed):**

A bundled PR MUST include a manifest in the description:

1. Each contained change as a numbered item.
2. Risk class per item (one of: scaffolding, cleanup, docs, test, refactor, hardening).
3. Files touched per item.
4. Tests run + pass counts.
5. Per-change rollback notes — which commit (or sub-revert) restores the codebase if just that one item turns out bad.

Use atomic commits inside the branch (one commit per logical item) so individual sub-reverts are clean. Squash-merge keeps `main` history one-PR-per-merge while the PR description's manifest preserves the change list the squash hides.

**The poison-pill rule:**

If even one item in a planned bundle falls under "stay one-per-PR" above, the entire bundle splits. The high-risk item becomes its own atomic PR. The remaining low-risk items can still be bundled. Do NOT smuggle a governance change inside a documentation PR — the per-change audit gates exist precisely to surface gates as gates.

**Hard ceiling:**

Bundled PR maximum: **15 files OR 800 lines of diff (additions + deletions), whichever comes first.** Beyond that, cognitive review load exceeds the bundling savings. Split into multiple bundled PRs.

**Bundling decision (run before opening a PR):**

```text
1. Does ANY changed file fall in "stay one-per-PR" above?
   → That item is its own PR (atomic).

2. Are all remaining changed files in "bundle freely" AND total ≤15 files / ≤800 LOC?
   → Bundle is allowed. Include the manifest in the PR description.

3. Mixed bundle (one-per-PR item + low-risk items)?
   → Split the high-risk item out (its own PR), then bundle the rest.

4. Bundle exceeds the 15-file / 800-LOC ceiling?
   → Split into two or more bundled PRs along the most natural seam.
```

This decision runs alongside the merge-tier decision tree above. Together they produce four shapes:

- **Atomic + CC-merge** — single CC-mergeable change, worker self-merges.
- **Atomic + operator-merge** — single operator-merge change (governance, infra, etc.), worker opens, operator merges.
- **Bundle + CC-merge** — multiple CC-mergeable changes in one PR with manifest, worker self-merges.
- **Bundle + operator-merge** — bundle that includes any operator-merge file, OR exceeds the worker's self-merge confidence; worker opens with manifest, operator merges.

**Source:** synthesized 2026-05-09 from independent reviews by GMI (Tier 4 "Milestone Batching" with the 15-file / 800-LOC ceiling and the Functional-State vs System-Governance split) and GPT (risk-based granularity with manifest pattern and "one high-risk item poisons the batch" rule). Trigger: the DGAS sprint shipped 11 single-file PRs in one day; the work was high-velocity, the per-PR ceremony was the slow part. Relay bundles preserved at `data/peer_reviews/2026-05-08_pr_batching_policy_{gmi,gpt}.txt`.

---

## Hygiene Gate (locked 2026-04-25 per PRO-107)

Tasks involving code changes are not complete until lint + format + schema validation pass locally before PR creation. Worker MUST run `pre-commit run` (default scope: staged files) and confirm green before opening a PR. Local hygiene gate runs lint, format, and schema validation. Pytest is enforced via CI on every PR (`.github/workflows/hygiene.yml`). Local pytest will be re-enabled once the test suite is clean — see PRO-109.

If hygiene fails:

- Fix the issues if they're in scope of the current task.
- If issues are pre-existing or out of scope: STOP, report the failures to operator, do NOT push a PR with known lint failures hoping CI will catch them.

Bypass policy: `git commit --no-verify` is allowed only for emergency hotfixes. The bypass MUST be logged in the commit message (`HYGIENE BYPASS: <reason>`) and reported to operator. Legacy files (those not touched by the current PR) are not subject to retroactive lint enforcement. Hooks fire on changed files only.

---

## Automated PR Review Completion Sequence (all workers + CH, locked 2026-05-04)

This contract applies to ALL workers (CC, Codex, Cursor, Gemini, Copilot, Windsurf) and Claude Chat when it owns a PR. Supersedes the previous CC-only Bugbot contract (PRO-212).

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

```text
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
