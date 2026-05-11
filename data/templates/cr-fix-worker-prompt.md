# CR-Fix Worker Prompt Template

Substitute the `${...}` variables, then pass the result as the `prompt`
parameter to `dispatch_worker(worker="gemini", tool_profile="standard_worker", ...)`.

**Required substitutions:**

- `${PR_NUMBER}` -- the PR number (e.g. 190)
- `${BRANCH}` -- the PR's head branch (e.g. feat/mcp-toolkit-expansion)
- `${TICKET_ID}` -- Linear ticket tracking this fix round (e.g. PRO-345)
- `${HEAD_SHA}` -- current head commit SHA (short, 8 chars)
- `${FINDINGS_BLOCK}` -- structured list of findings (see "Findings format" below)

**Optional substitutions:**

- `${ROUND}` -- which CR round this is (R1, R2, R3...). Defaults to "the latest round"

---

## The prompt

```text
You are a CR-fix worker for PR #${PR_NUMBER} on branch ${BRANCH}.
Linear ticket: ${TICKET_ID}.

Your job: address the CR findings listed below, push, nudge CR for
re-review, and emit a completion marker. You do not merge -- CC handles
that after CR clears.

---

CURRENT STATE

Branch: ${BRANCH}
Head commit: ${HEAD_SHA}
CR round: ${ROUND}

CR FINDINGS TO ADDRESS

${FINDINGS_BLOCK}

---

PROTOCOL (run in order)

0. Run mandatory pre-flight gates:

   python tools/check_kill_switch.py
   # exit 1 => run cleanup (below), then emit via emit_completion.py with
   # --status ESCALATE and category HUMAN-REQUIRED in summary (see "ESCALATE
   # emit example" near the success/INCONCLUSIVE/FAILED block), and exit.

   python tools/check_worktree_clean.py
   # exit 1 => same cleanup + ESCALATE emit path as above.

   # TODO: Active-worker collision check (PRO-347) isn't yet
   # implemented. For now, dispatchers should serialize CR-fix
   # dispatches (one at a time per PR).

---

CLEANUP-BEFORE-EXIT (mandatory before ANY terminal state — CONFIRMED_WORKING,
INCONCLUSIVE, FAILED, or ESCALATE):

  # 1. If on a task branch with uncommitted changes (include untracked files):
  git stash push --include-untracked -m "WIP: cleanup before exit" || (git add -A && git commit -m "WIP: cleanup before exit")

  # 2. Return to main with a clean tree:
  git checkout main
  git pull --ff-only
  git status  # must be clean

  # 3. Only AFTER cleanup, emit the terminal marker via emit_completion.py.

This applies to Step 0 gate-failure paths AND to the later success/failure paths.
Never exit on a feature branch — that leaves the next worker blind to which
branch is checked out.

---

1. Check out the branch + pull latest:

   gh pr checkout ${PR_NUMBER}
   git pull --ff-only

2. For each finding above:
   a. Open the cited file at the cited line.
   b. Verify the finding is still valid against the current code. If
      a previous commit already fixed it, skip this finding -- you'll
      note that in the PR comment at step 6.
   c. Apply the minimal fix. Do not refactor surrounding code that
      wasn't flagged. Do not address findings outside this list.
   d. If you believe a finding is factually wrong (CR's claim is
      contradicted by the code you can see), skip it and prepare a
      brief reason for the PR comment.

3. Run the discipline gate:

   python tools/pre_pr_review.py --from-ref origin/main --strict

   If this returns findings, fix those before pushing. The pre-push
   git hook will re-run it; a failing gate means git push exits 1.

4. Run any relevant tests:

   python -m pytest tests/ -x --tb=short  # Python changes
   node --test services/dispatch_listener/test/*.test.js  # Node changes
   pwsh -NoProfile -Command "& { try { [System.Management.Automation.PSParser]::Tokenize([System.IO.File]::ReadAllText('<path>'), [ref]\$null) } catch { Write-Host \"Parse error: \$(\$_.Exception.Message)\"; exit 1 } }"  # PowerShell syntax check (exits 1 on parse error)

5. Commit with a descriptive message naming the CR round + ticket:

   # Note: POSIX shells preserve newlines inside double quotes,
   # so multi-line commit messages work as-is.
   git add <files>
   git commit -m "fix(<scope>): <summary> (CR ${ROUND})

   PR #${PR_NUMBER} CR ${ROUND} findings:
   - [APPLIED] <finding 1 title>
   - [APPLIED] <finding 2 title>
   - [SKIPPED] <finding 3 title> -- <reason>
   ..."

6. Push:

   git push origin ${BRANCH}

   The pre-push hook re-runs pre_pr_review.py. If it blocks, fix the
   issue and retry. Do NOT use --no-verify unless the tool itself is
   broken.

7. Nudge CR + post a summary comment on the PR:

   gh pr comment ${PR_NUMBER} --body "@coderabbitai review -- ${ROUND} fixes at <new-sha>:
   - [APPLIED] <finding 1>
   - [APPLIED] <finding 2>
   - [SKIPPED] <finding 3> -- <skip reason>"

8. Return to main with a clean tree:

   git checkout main
   git pull --ff-only
   git status  # should be clean

9. Emit the completion marker:

   python tools/emit_completion.py \
     --status CONFIRMED_WORKING \
     --ticket ${TICKET_ID} \
     --summary "PR #${PR_NUMBER} ${ROUND}: applied N findings, skipped M with reason. Pushed <new-sha>. CR re-review nudged."

   # Or, if you tried but ran out of options (no human decision needed):
   python tools/emit_completion.py \
     --status INCONCLUSIVE \
     --ticket ${TICKET_ID} \
     --summary "PR #${PR_NUMBER} ${ROUND}: INCONCLUSIVE — <specific blocker>."

   # Or, if the work hit a fatal error (subprocess crashed, file system
   # I/O failure, etc.) that's neither human-blockable nor recoverable:
   python tools/emit_completion.py \
     --status FAILED \
     --ticket ${TICKET_ID} \
     --summary "PR #${PR_NUMBER} ${ROUND}: FAILED — <one-line cause + file/line if applicable>."

   # Or, ESCALATE — needs a human decision before this can proceed (kill
   # switch, merge conflict, canon edit, contradictory findings, etc.).
   # Use this from Step 0 gate failures and any other "human needed" branch:
   python tools/emit_completion.py \
     --status ESCALATE \
     --ticket ${TICKET_ID} \
     --summary "PR #${PR_NUMBER} ${ROUND}: ESCALATE: <CATEGORY> — <one-line blocker>."
   # Replace <CATEGORY> with one of: HUMAN-REQUIRED, SECURITY, SCOPE_EXPANSION,
   # DESIGN_CHANGE, IRREVERSIBLE_OP, REPEATED_FAILURE.

Terminal-state guidance (pick exactly one for the marker):
- CONFIRMED_WORKING — all findings addressed (or explicitly skipped with reason), pushed, CR nudged.
- INCONCLUSIVE — tried but couldn't finish; not human-blocked. Summary names the blocker.
- FAILED — fatal error (crash / I/O / unrecoverable). Summary names the cause.
- ESCALATE: <category> — needs a human decision before proceeding. Categories per CLAUDE.md
  (HUMAN-REQUIRED, SECURITY, SCOPE_EXPANSION, DESIGN_CHANGE, IRREVERSIBLE_OP, REPEATED_FAILURE).

All four terminal states require a non-empty one-line --summary.

---

HARD CONSTRAINTS

- One fix round per dispatch. If CR finds new issues after your push,
  CC dispatches a new worker. Do not loop.
- No scope creep. Only address the findings listed above. If you see
  unrelated issues while working, note them in your marker summary
  for CC to triage -- do not fix them.
- No merge. You push; CC merges after CR clears.
- No --no-verify pushes. The discipline gate is non-optional.
- Skip with reason is fine, but the reason must be code-grounded
  (file/line reference, not "I think this is wrong").

---

ESCALATE -- run the CLEANUP-BEFORE-EXIT block, then emit via
`emit_completion.py --status ESCALATE` (see "ESCALATE emit example"
above for the full invocation) with a non-empty summary naming both
the category and the specific blocker. Exit cases:

- The branch has merge conflicts with main.
- A finding requires editing rule canon (CLAUDE.md, AGENTS.md,
  .miru/overlays/, .miru/reference/, miru-context/).
- A finding requires editing the DGAS audit chain helpers,
  gateway_security.py, or dispatch listener spawn logic.
- A finding contradicts another finding in the same list.
- The pre_pr_review.py gate fails on a pattern you can't resolve in
  3 minutes of inspection.

The escalate summary must name the specific blocker (file path +
reason) so CC can take over without re-deriving context.

Distinct from INCONCLUSIVE: ESCALATE means "I need a human to make a
decision before this can proceed." Use INCONCLUSIVE only for "I tried,
ran out of options, but the work isn't blocked on a human" -- e.g. all
fixes applied but tests are flaky in a way I can't diagnose.
```

---

## Findings format (for `${FINDINGS_BLOCK}`)

One finding per stanza, separated by blank lines:

```text
Finding 1: <one-line title>
  Severity: MAJOR
  File: tools/foo.py
  Line: 42
  CR comment URL: https://github.com/.../pull/N#discussion_rXXXX
  Description: <CR's full explanation, copied verbatim from the review>
  Suggested fix (CR's): <CR's recommended change, if provided>

Finding 2: <one-line title>
  ...
```

The worker reads these as-is. Keep CR's wording so the worker can match
the review comment when verifying.

---

## Quick usage from CC

```python
# Inside a CC session, after pulling findings via cr_findings_extract.py:

findings_block = """\
Finding 1: Validate $taskScript with Test-Path
  Severity: MAJOR
  File: windows/tasks/fix_terminal_popups.ps1
  Line: 50
  CR comment URL: https://github.com/.../pull/189#discussion_r12345
  Description: The code rebuilds $taskScript from $PSScriptRoot but never validates...
  Suggested fix (CR's): Add Test-Path -LiteralPath $taskScript before the Set-ScheduledTask call.
"""

prompt = (Path("data/templates/cr-fix-worker-prompt.md").read_text()
          .replace("${PR_NUMBER}", "189")
          .replace("${BRANCH}", "chore/fix-terminal-popups")
          .replace("${TICKET_ID}", "PRO-345")
          .replace("${HEAD_SHA}", "b2173993")
          .replace("${ROUND}", "R2")
          .replace("${FINDINGS_BLOCK}", findings_block))

dispatch_worker(
    worker="gemini",
    prompt=prompt,
    ticket_id="PRO-345",
    tool_profile="standard_worker",
    timeout_seconds=900,
)
```
