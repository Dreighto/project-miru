from __future__ import annotations

import runpy
import sys
from importlib import import_module
from pathlib import Path


def _prepare_canonical_package_path() -> None:
    script_path = Path(__file__).resolve()
    tools_dir = str(script_path.parent)
    repo_root = str(script_path.parent.parent)
    sys.path[:] = [entry for entry in sys.path if entry and str(Path(entry).resolve()) != tools_dir]
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


if __name__ == "__main__":
    _prepare_canonical_package_path()
    runpy.run_module("miru_ai.core.ai", run_name="__main__")
else:
    sys.modules[__name__] = import_module("miru_ai.core.ai")
