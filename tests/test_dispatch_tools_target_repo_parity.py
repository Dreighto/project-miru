"""Parity test: client-side _APPROVED_TARGET_REPOS must match server-side WORKTREE_POOLS keys.

Per CodeRabbit feedback on PR #156: the multi-repo allowlist is duplicated across
two languages (Python in dispatch_tools.py, JavaScript in worktree.js). They can
drift if a contributor adds a repo to one and forgets the other. This test
parses worktree.js's WORKTREE_POOLS keys via regex and asserts they match the
Python frozenset.

This is a lightweight catch — it does NOT validate slot paths, env vars, or
behavioral parity. It only catches "you added a repo to the JS pool map but
forgot to add it to the Python allowlist (or vice versa)."

If this test starts to feel brittle (e.g., the JS file gets reformatted in a
way that breaks the regex), the right next step is to extract the repo list
into a shared config file (e.g., data/config/dispatch_target_repos.json) that
both modules read at startup. Until then this is the cheapest source of truth
that catches the divergence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.miru_mcp_gateway.dispatch_tools import (  # noqa: E402
    _APPROVED_TARGET_REPOS,
    _DEFAULT_TARGET_REPO,
)

WORKTREE_JS = REPO_ROOT / "services" / "dispatch_listener" / "src" / "worktree.js"


def _extract_pool_keys_from_worktree_js() -> set[str]:
    """Parse worktree.js for the keys of WORKTREE_POOLS.

    The expected shape (one of):
        const WORKTREE_POOLS = {
          'project-miru': [...],
          'LogueOS-Console': [...],
          'LogueOS-Orchestrator': poolFor('LogueOS-Orchestrator', 1),
        };

    Pool values can be either an array literal (legacy, pre-LOS-14) or a
    `poolFor(...)` call (LOS-14 derived layout). The regex accepts both
    by matching only the key part and leaving the value shape unconstrained.
    Brittle to refactors that change the literal style; if the layout changes
    the test will fail loudly and that's the signal to update this regex
    (or extract repo list into a shared config file).
    """
    text = WORKTREE_JS.read_text(encoding="utf-8")

    # Find the WORKTREE_POOLS block.
    block_match = re.search(
        r"const\s+WORKTREE_POOLS\s*=\s*\{(?P<body>.*?)^\};",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert block_match is not None, (
        "Could not locate `const WORKTREE_POOLS = { ... };` block in worktree.js. "
        "If the structure changed, update this test's regex (or extract repo list "
        "into a shared config file)."
    )

    body = block_match.group("body")
    # Each pool key starts a line: "  'name': [..."  (array literal) or
    # "  'name': poolFor(...)" (LOS-14 derived). Match key only; value
    # shape is unconstrained. The lookahead `[\w[]` (identifier or `[`)
    # ensures we don't match keys with no value, while still accepting
    # both the array literal and the function-call form.
    keys = re.findall(r"^\s*['\"]([^'\"]+)['\"]\s*:\s*[\w\[]", body, re.MULTILINE)
    return set(keys)


def test_approved_target_repos_matches_worktree_pools_keys():
    """The Python allowlist and the JS pool map keys must be identical sets."""
    js_pool_keys = _extract_pool_keys_from_worktree_js()
    py_allowlist = set(_APPROVED_TARGET_REPOS)

    missing_in_python = js_pool_keys - py_allowlist
    missing_in_js = py_allowlist - js_pool_keys

    assert not missing_in_python, (
        f"target_repo names in worktree.js WORKTREE_POOLS but missing from "
        f"dispatch_tools.py _APPROVED_TARGET_REPOS: {sorted(missing_in_python)}. "
        f"Add them to the frozenset in tools/miru_mcp_gateway/dispatch_tools.py."
    )
    assert not missing_in_js, (
        f"target_repo names in dispatch_tools.py _APPROVED_TARGET_REPOS but "
        f"missing from worktree.js WORKTREE_POOLS: {sorted(missing_in_js)}. "
        f"Add them to the WORKTREE_POOLS map in "
        f"services/dispatch_listener/src/worktree.js."
    )


def test_default_target_repo_is_in_allowlist():
    """The default fallback must itself be a valid target_repo."""
    assert (
        _DEFAULT_TARGET_REPO in _APPROVED_TARGET_REPOS
    ), f"_DEFAULT_TARGET_REPO {_DEFAULT_TARGET_REPO!r} must be in _APPROVED_TARGET_REPOS"


def test_default_target_repo_is_in_worktree_pools():
    """The default fallback must also be a known pool on the server side."""
    js_pool_keys = _extract_pool_keys_from_worktree_js()
    assert (
        _DEFAULT_TARGET_REPO in js_pool_keys
    ), f"_DEFAULT_TARGET_REPO {_DEFAULT_TARGET_REPO!r} must be a key in worktree.js WORKTREE_POOLS"
