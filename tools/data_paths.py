"""Canonical data-path resolution for all emit_*.py helpers.

Phase 3 (LOS-55) introduced a single source of truth for where append-only
``data/*.jsonl`` files live. Before this, every emitter resolved its target
path via ``_repo_root() / "data" / <name>``, which was implicitly cwd-relative
(via ``git rev-parse --git-common-dir`` from the script's own directory). When
copies of the emitter scripts existed in multiple worktrees (project-miru,
LogueOS-Orchestrator, LogueOS-Orchestrator-w1, ...), each copy resolved to a
different physical directory and the chains diverged.

This helper resolves the data dir as follows (first match wins):

1. ``$LOGUEOS_DATA_DIR`` env var — set by ``services/dispatch_listener/src/spawn.js``
   when spawning workers, always pointing at the orchestrator's ``data/``.
2. ``<repo_root>/data`` where ``<repo_root>`` is derived from the calling
   script's location via ``git rev-parse --git-common-dir`` (legacy fallback,
   preserves backward compatibility for callers run outside a dispatch).

To use:

    from data_paths import data_path
    log = data_path("cc_completion_log.jsonl")
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repo_root_from(script_dir: str) -> str:
    """Resolve a repo root from a starting directory (the caller's __file__)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=script_dir,
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = os.path.normpath(os.path.join(script_dir, result.stdout.strip()))
            return os.path.dirname(common_dir)
    except Exception:
        pass
    return os.path.dirname(script_dir)


def data_dir(caller_file: str | None = None, repo_root_fn=None) -> Path:
    """Return the canonical data directory.

    Resolution order:
      1. ``$LOGUEOS_DATA_DIR`` env var (set by spawn.js for dispatched workers).
      2. ``repo_root_fn()`` if provided — lets each emit_*.py pass its own
         ``_repo_root`` callable so that test patches on the caller still
         take effect (LOS-55 backward-compat with existing test suites).
      3. ``<repo_root>/data`` derived from ``caller_file``'s git common dir.
    """
    env_dir = os.environ.get("LOGUEOS_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if repo_root_fn is not None:
        return Path(repo_root_fn()) / "data"
    if caller_file is None:
        caller_file = __file__
    script_dir = os.path.dirname(os.path.abspath(caller_file))
    return Path(_repo_root_from(script_dir)) / "data"


def data_path(name: str, caller_file: str | None = None, repo_root_fn=None) -> Path:
    """Return a canonical data file path for ``name``.

    See ``data_dir`` for resolution order. Pass ``repo_root_fn=_repo_root``
    from the caller's module so test ``patch.object(module, "_repo_root", ...)``
    still works.
    """
    return data_dir(caller_file, repo_root_fn) / name
