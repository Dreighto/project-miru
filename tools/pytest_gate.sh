#!/usr/bin/env bash
# Advisory pytest (matches old CI continue-on-error; pre-existing failures PRO-109).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
[ "${PREFLIGHT_TESTS:-1}" = 1 ] || { echo "pytest: skipped"; exit 0; }
VENV="$(git rev-parse --git-dir)/ci-venv"
[ -x "$VENV/bin/pytest" ] || { python3 -m venv "$VENV" && "$VENV/bin/pip" -q install pytest pytest-timeout; }
"$VENV/bin/pytest" tests/ --tb=short -q --timeout=30 \
  --ignore=tests/test_dashboard_app.py --ignore=tests/test_miru_ai_server.py \
  --ignore=tests/test_miru_project_sync.py --ignore=tests/test_miru_learning_engine.py \
  --ignore=tests/test_miru_card_intel.py --ignore=tests/test_miru_image_ingestion.py \
  --ignore=tests/test_miru_source_registry.py || echo "⚠ pytest reported failures (advisory — see PRO-109)"
