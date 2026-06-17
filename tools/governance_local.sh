#!/usr/bin/env bash
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
CHANGED=$(git diff --name-only origin/main...HEAD 2>/dev/null || true)
[ -n "$CHANGED" ] || { echo "governance: no changes vs origin/main — skip"; exit 0; }
printf '%s\n' "$CHANGED" > "/tmp/.gov_miru.$$"
BODY=$(git log origin/main..HEAD --format='%B' 2>/dev/null || true)
MIRU_GOV_PR_BODY="$BODY" python3 tools/check_governance_change.py --changed-files "@/tmp/.gov_miru.$$"; rc=$?
rm -f "/tmp/.gov_miru.$$"; exit $rc
