"""Stub: miru_runtime_preflight — runtime health checks for maintenance cycles.

This module was missing from the repo (identified in the 2026-04-16 audit).
It provides a minimal stub so that ``tools.miru_maintenance`` can import
``build_runtime_preflight_report`` without raising ``ImportError``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def build_runtime_preflight_report(
    *,
    target: str = "all",
    check_server_port_available: bool = True,
    check_worker_lock_available: bool = True,
) -> dict[str, Any]:
    """Return a preflight health-check dict.

    Stub implementation — always reports ok=True with an empty summary.
    """
    log.warning(
        "miru_runtime_preflight: build_runtime_preflight_report not yet implemented"
    )
    return {
        "ok": True,
        "summary": {},
    }
