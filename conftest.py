"""Repo-root conftest — makes the project importable from pytest.

Without this, pytest can't resolve imports like `pm.app` because the repo
isn't pip-installed and pytest's auto-rootdir adds tests/ but not the repo
root to sys.path. Equivalent to `pip install -e .` for an unpackaged repo.

Required by the PRO-107 pre-PR hygiene layer's pytest pre-push hook so the
local gate behaves the same as CI (which gets the path right by accident
because of how GitHub Actions sets up the workspace).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
