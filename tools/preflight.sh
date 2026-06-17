#!/usr/bin/env bash
# Local CI gate for project-miru — local replacement for the disabled GH Actions
# (hygiene / governance-check / canon-freshness). Run `make preflight` before a PR.
# Existing pre-commit pre-push hooks still auto-gate pushes; this is the full gate.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0; step(){ printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
step "pre-commit (commit-stage)"; pre-commit run --all-files || fail=1
step "pre-commit (pre-push stage)"; pre-commit run --hook-stage pre-push --all-files || fail=1
step "governance-change gate"; tools/governance_local.sh || fail=1
step "canon-freshness gate"; CANON_FRESHNESS_DAYS=7 CANON_FRESHNESS_WARN_DAYS=5 python3 tools/check_canon_freshness.py || fail=1
step "pytest (advisory — pre-existing failures tracked in PRO-109)"; tools/pytest_gate.sh || true
[ "$fail" = 0 ] && printf '\n\033[32m✓ preflight passed\033[0m\n' || printf '\n\033[31m✗ preflight FAILED\033[0m\n'
exit $fail
