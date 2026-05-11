# Overlay -- Dispatch CR-Fix Work to a Worker

```text
Overlay: dispatch-cr-fix
Architecture: MIRU-INSTRUCTIONS-v2
Load when: CR posts a CHANGES_REQUESTED review on an open PR and the
fixes are mechanical enough to delegate (the usual case).
Last reviewed: 2026-05-11
```

## Why this exists

CC was hand-fixing every CR finding through the LOS-10 batch, which
worked but burned CC context on mechanical work that any capable worker
can do. Gemini (Gemini 3.1 Pro free tier, 1500 req/day) is fast, has
the same backend tooling via MCPs, and the discipline canon
(`pre-push-discipline.md` + `tools/pre_pr_review.py` + the pre-push git
hook) means a worker can't push a regression without tripping the gate.

This overlay codifies the delegation pattern: when CR fires, **CC
dispatches Gemini** to do the fix unless the finding is non-mechanical
(architectural, requires CC-level judgment, or crosses the rule canon).

---

## The dispatch decision tree

Run this on every CR CHANGES_REQUESTED review:

```
Are the findings...
  - Mechanical (renames, fsync calls, regex tweaks, error handling
    wrappers, 1-based indexing, etc.)?         -> DISPATCH GEMINI
  - Touching rule canon, dispatch logic,
    DGAS, gateway security?                    -> CC HANDLES
  - Conflict with each other, or with the
    operator's stated direction?               -> CC HANDLES
  - Findings CC believes are factually wrong
    (verified against current code)?           -> CC HANDLES (skip + reply)
  - Mix of mechanical + judgment?              -> CC HANDLES the judgment
                                                  parts, then dispatches
                                                  Gemini for the rest
```

When in doubt: dispatch Gemini with a tightly scoped prompt. CC reviews
the result before merging.

---

## Required pre-flight per dispatch

Before calling `dispatch_worker`:

1. **Pull current CR findings** via `python tools/cr_findings_extract.py
   <PR> --since <last-review-ts> --summary`. Capture the exact set the
   worker is responsible for.

2. **Verify each finding is still valid** against current code (`git
   show <branch>:<path>`). If a finding refers to code that was already
   fixed in a later commit, exclude it from the dispatch prompt with a
   note.

3. **File or update a Linear ticket** tracking this fix-round. Use the
   `Tooling / MCP Gateway` project (`cb5c362c-c1f4-4f55-b119-578fa017ca7d`)
   for project-miru PRs, or the matching LogueOS project for LOS-* PRs.
   Title format: `PR #<N> CR Rk fix: <one-line summary>`.

4. **Compose the dispatch prompt** from the template at
   `data/templates/cr-fix-worker-prompt.md`. Substitute the variables;
   do not add free-form scope expansions ("while you're in there, also...").

5. **Dispatch with `worker="gemini"`, `tool_profile="standard_worker"`.**
   That profile has read surface + `git_write` + `docs_write` +
   `linear_write` -- everything Gemini needs to apply the fix and
   commit. Do not use `full_operator`; the standard profile keeps Gemini
   inside the lane.

---

## Required clauses in every CR-fix dispatch prompt

(Carry-overs from `feedback_dispatch_prompt_required_clauses` --
non-negotiable.)

- **Max 1 fix round per dispatch.** If CR finds new issues after the
  worker's push, CC dispatches a new worker. The worker does not loop.
- **Skip-with-reason allowed.** Worker may skip a finding by leaving a
  PR comment that names the finding and explains the skip. Must include
  a code-grounded reason (file/line reference). Otherwise must apply
  the fix.
- **Run `tools/pre_pr_review.py --from-ref origin/main --strict` before
  pushing.** If it returns findings, fix those too before pushing.
- **Non-empty summary on INCONCLUSIVE.** If the worker can't finish,
  the marker must include a specific blocker (file path + reason).
- **Don't merge.** The worker pushes and nudges `@coderabbitai review`.
  CC reviews the diff and merges manually after CR clears or after
  applying the dismiss-and-merge pattern from `workflow-git.md`.

---

## After the worker returns

1. **Read the result marker** at `data/n8n_inbox/cc-<trace>.result.json`.
2. **Pull the worker's branch** and diff against the prior head to
   confirm the changes match what the prompt asked for.
3. **Wait for CR's re-review** (or dismiss-and-merge if the findings
   don't add up -- same rules as CC-authored fixes).
4. **Merge** when CR clears or when the skip-with-reason is documented.
5. **Update the Linear ticket** to Done with the merge commit SHA.

If the worker bounced with INCONCLUSIVE or FAILED:
- Inspect the marker's `stderr_tail` and `summary`.
- Fix the blocker yourself or dispatch again with a narrower prompt.
- Don't dispatch a third worker on the same finding -- CC takes over.

---

## What this overlay does NOT do

- It does not authorize Gemini to merge PRs. Merge authority stays with
  CC + operator. The worker pushes; CC merges.
- It does not authorize Gemini to dismiss CR reviews. That stays with
  CC (and only when the finding is documented-wrong, per #184 pattern).
- It does not change the rule canon ownership. Gemini doesn't edit
  CLAUDE.md, AGENTS.md, or `.miru/overlays/`/`.miru/reference/`. Those
  are CC's lane until CH returns.

---

## Source of authority

Adopted 2026-05-11 after the operator pointed out that hand-fixing
every CR finding was burning CC context. The dispatch loop already
existed; the discipline overlay (pre-push hook + `pre_pr_review.py`)
already enforces the gate; the only missing piece was the explicit
delegation pattern. This overlay closes that gap.
