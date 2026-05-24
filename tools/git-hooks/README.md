# Custom git hooks for project-miru

Hooks that supplement the standard `.git/hooks/` and the pre-commit
framework (`.pre-commit-config.yaml`). The pre-commit hooks run on
every commit and handle formatting/lint; the hooks here run at other
git-event boundaries (pre-push, pre-rebase, etc.) where the pre-commit
framework doesn't fire.

## Install

```bash
git config core.hooksPath tools/git-hooks
```

That's per-clone, not committed to the repo's config. Each new clone
needs to opt in. The hooks themselves are tracked under this directory.

## Hooks

### `pre-push`

Blocks `git push` if `tools/pre_pr_review.py` finds any HIGH-severity
pattern in the cumulative diff vs `origin/main`. Bypass with
`git push --no-verify` (use sparingly -- the hook exists because
direct edits to dispatch-loop code or canon-relevant files need the
discipline catalog applied first).

See `D:\dev\LogueOS-Orchestrator\.logueos\overlays\pre-push-discipline.md` for the rationale + the
full pre-push protocol.

## Uninstall / temporarily disable

```bash
# Disable for one push:
git push --no-verify

# Disable permanently (revert to default hooks path):
git config --unset core.hooksPath
```
