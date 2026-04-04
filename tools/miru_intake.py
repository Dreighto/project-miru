from __future__ import annotations

import runpy
import sys
from importlib import import_module


if __name__ == "__main__":
    runpy.run_module("miru_ai.ingestion.intake", run_name="__main__")
else:
    sys.modules[__name__] = import_module("miru_ai.ingestion.intake")

