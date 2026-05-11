# Overlay -- Pre-Push Discipline

```text
Overlay: pre-push-discipline
Architecture: MIRU-INSTRUCTIONS-v2
Load when: about to push commits to a branch with an open PR (or about
to open a new PR).
Last reviewed: 2026-05-11
```

## Why this exists

CR finds issues at every push because the worker (CC or Gemini) ships
code without running the same checks CR runs. Each CR round burns
operator attention, worker context, and calendar time. The previous
6-PR batch averaged 3+ CR rounds per PR; the goal is **one CR round per
PR**, with the round catching genuinely subtle issues, not "you forgot
to fsync."

This overlay is a checklist + tool invocation that catches the patterns
CR consistently flags so they don't reach CR in the first place.

---

## The protocol (MUST run before every push)

### Step 1 -- run `tools/pre_pr_review.py` against the diff

```bash
python tools/pre_pr_review.py --from-ref origin/main --strict
```

We compare against `origin/main` (the actual merge target) rather than
local `main` so the check catches anything pushed to remote main since
the last `git fetch`. The pre-push git hook (below) uses the same ref,
so manual runs and hook runs return the same findings. If you have not
fetched recently, the hook will fall back to local `main`; do a `git
fetch` first to avoid that asymmetry.

If the tool returns any findings, fix them before pushing. If a finding
is a clear false positive, harden the detector before suppressing the
warning. The detector catalog is institutional memory; every false
positive that escapes today becomes a future-CR finding tomorrow.

Tool catalog (see `tools/pre_pr_review.py` docstring for current state):

- **P1 path-traversal** -- f-string filename interpolating user input without
  visible validation. Traces through `.replace`/`.strip` chains.
- **P2 fsync-rename** -- `os.replace`/`os.rename` without follow-up parent-
  directory fsync (POSIX rename durability).
- **P3 fsync-readonly** -- `os.fsync` on a read-only fd (Windows EBADF).
- **P5 relative-after-cd** -- shell var assigned relative path before `cd`,
  used after.
- **P8 corrupt-vs-empty** -- multi-branch function returning `(None, None)`
  without a distinguishing flag.
- **P9 dash-only-check** -- bash `--*` arg-match missing `-*` short flags.

### Step 2 -- walk the adversarial checklist

For each line you changed, ask:

1. **Path-handling**: does any user-input (CLI arg, env var, function
   param) flow into a filename, shell command, or file path? Validate
   the input first via regex/parse helper. Re-render from the validated
   form rather than interpolating the raw input.

2. **Atomic file ops**: does the code call `os.replace` / `os.rename` /
   `shutil.copy*` / `cp -r`? Pair it with a follow-up parent-directory
   fsync (POSIX durability). Use a shared helper if you have multiple
   atomic-rename sites.

3. **Distinct-state sentinels**: does a function return `(None, ...)` or
   `(..., None)` in more than one branch? Add a distinguishing flag so
   the caller can tell "empty" from "corrupt" / "missing" from "error".
   Conflated sentinels are the most common silent-corruption pattern.

4. **Hardcoded vs configurable**: if you added a configurable thing (env
   var, CLI flag), audit the file for any string/path that should
   derive from it instead of being hardcoded. The "I introduced
   LOGUEOS_WORKTREE_BASE but hardcoded its default's basename elsewhere
   in the same PR" mistake should never happen.

5. **Workstation-specific paths**: any path starting with `D:\dev\miru\`,
   `C:\Users\Dreighto\`, or similar? Derive from `Path(__file__)` (Python),
   `$PSScriptRoot` (PowerShell), or `path.resolve(__dirname, ...)` (Node).
   The tool must work from any clone location.

6. **Argument parsing**: bash arg-value validation should reject any
   leading `-` (so `-h` and `--foo` both fail), not just `--*`. Validate
   each option's value before `shift 2`.

7. **Subprocess error contracts**: do helpers that call `git`, `npm`,
   `python` etc. swallow non-zero exit codes? If the function signature
   is "return list of X", a git failure returning `[]` silently disables
   downstream checks. Raise a typed exception and let `main()` convert
   to the documented exit code.

8. **Inherited state from clones**: `cp -r .git`-style operations copy
   remotes, hooks, config. Rename or remove the inherited `origin`
   before later instructions try to `git remote add origin`.

9. **Test the failure path**: it's not enough that the happy path works.
   For every refusal/refuse-to-proceed branch you added, write at least
   one test that hits it.

10. **Re-render after parse**: when validating a string used in a
    filename, parse it (datetime/regex) and re-render via strftime/
    canonical form. Don't pass the raw input through.

### Step 3 -- run the test suite

```bash
# Python
python -m pytest tests/ -x --tb=short

# Node (dispatch_listener)
node --test services/dispatch_listener/test/*.test.js
```

For every fix you push, write at least one regression test that
exercises the bug class. If the test is hard to write because of
function-private state, the function probably needs a structural
change, not just the fix.

### Step 4 -- mental "what would CR find"

Re-read your own diff. For each modified line, ask: "if CR is running
its adversarial pass against this, what does it say?" If you can't
answer, you haven't read the diff carefully enough yet.

### Step 5 -- only then push

```bash
git push
gh pr comment <PR> --body "@coderabbitai review"
```

The `@coderabbitai review` nudge triggers immediate CR re-review
(otherwise CR's queue can take 30+ minutes).

---

## Pre-push git hook (recommended)

`tools/git-hooks/pre-push` (set up via `git config core.hooksPath tools/git-hooks`):

```bash
#!/usr/bin/env bash
# Block local pushes that fail the pre-PR review (--strict).
# Skips if --no-verify is passed (operator override).

set -e

# Fail closed if pre_pr_review.py isn't present.
if [ ! -f "tools/pre_pr_review.py" ]; then
    echo "[pre-push] BLOCKED: tools/pre_pr_review.py not found." >&2
    exit 1
fi

python tools/pre_pr_review.py --from-ref origin/main --strict
```

The hook intentionally compares against `origin/main` (the merge target)
rather than just the working tree, so it catches anything new in your
branch's history that hasn't been pushed yet.

The hook **fails closed** when `tools/pre_pr_review.py` is missing
rather than silently skipping — a missing tool on a stale checkout
shouldn't bypass the gate. To override (e.g. on a branch predating the
tool), use `git push --no-verify`, which keeps the bypass visible in
shell history.

---

## Failure mode catalog (CR findings I've seen, by pattern)

| Pattern                                     | Example                                                                                  | Detector                    |
| ------------------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------- |
| Path traversal via f-string filename        | `frozen_name = f"...-{ts_safe}.jsonl"` where `ts_safe = arg.replace(...)`                | P1                          |
| Missing parent-dir fsync after rename       | `os.replace(tmp, dst)` with no `_fsync_dir(dst.parent)` after                            | P2                          |
| `os.fsync` on read-only fd                  | `fh = open("rb"); os.fsync(fh.fileno())` raises EBADF on Windows                         | P3                          |
| Hardcoded vs configurable                   | `LOS14_POOL_ANCHOR = "worktrees"` (introduced env var same PR but hardcoded its default) | manual                      |
| Workstation-specific path                   | `POSH_MCP_CONFIG = "D:\\dev\\miru\\..."`                                                 | manual / project-aware lint |
| Conflated `(None, None)` sentinel           | `_read_last_v2_state` returning `(None, None)` for both "empty" and "corrupt"            | P8                          |
| `--*` arg-match missing `-h`                | `[[ "$1" == --* ]]` in bash                                                              | P9                          |
| Inherited `origin` after `cp -r .git`       | filter-repo script `cp -r src dst` then `git remote add origin <new>` later fails        | manual                      |
| Silent git failures                         | `_changed_files()` returning `[]` on any git error including bad ref                     | manual / propagate          |
| Stale signature artifacts on unsigned rerun | `--force` clears manifest but leaves `.sig` behind                                       | manual                      |
| Basename match where full-path was needed   | `grandparent.toLowerCase() === LOS14_POOL_ANCHOR` matches unrelated paths                | manual                      |

Detectors marked "manual" are not yet codified in `pre_pr_review.py`.
**Each round of CR findings should grow the detector list** -- if CR
flags a pattern, that pattern's detector goes into the catalog so the
next worker (CC or Gemini, or future-CC) doesn't repeat it.

---

## When this overlay does NOT apply

- **First commit on a branch with no remote yet**: there's no PR yet, no
  CR to placate. Discipline still helps but isn't blocking.
- **Pure-formatting commits** (ruff-format / prettier auto-fixes): the
  pre-commit hook already handles those, and they don't change behavior.
- **Reverts**: a `git revert` of a known-bad commit doesn't need the
  full discipline -- the revert is the discipline.

Otherwise, run the protocol. It takes 2-3 minutes; CR rounds take hours.

---

## Source of authority

Adopted 2026-05-11 after the operator pointed out that CR was
babysitting rather than reviewing. Discipline gap was in the worker
(CC), not the tools -- `tools/pre_pr_review.py` existed but wasn't being
run pre-push. This overlay codifies the protocol so it's not "I'll
remember next time" but a checklist the worker walks every push.
