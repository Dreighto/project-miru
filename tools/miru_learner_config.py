"""Learner mode and cadence configuration for safe activation.
Default mode is REVIEW_REQUIRED so Miru does not auto-publish when first activated.
SANDBOX = limited safe testing, no publish (same behavior as DRY_RUN)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

LEARNER_MODE_DRY_RUN = "DRY_RUN"
LEARNER_MODE_SANDBOX = "SANDBOX"  # Safe testing; same behavior as DRY_RUN
LEARNER_MODE_REVIEW_REQUIRED = "REVIEW_REQUIRED"
LEARNER_MODE_ACTIVE = "ACTIVE"

LEARNER_MODES = (LEARNER_MODE_DRY_RUN, LEARNER_MODE_SANDBOX, LEARNER_MODE_REVIEW_REQUIRED, LEARNER_MODE_ACTIVE)
DEFAULT_LEARNER_MODE = LEARNER_MODE_REVIEW_REQUIRED

ENV_LEARNER_MODE = "MIRU_LEARNER_MODE"
LEARNER_MODE_OVERRIDE_PATH = Path(__file__).resolve().parent.parent / "data" / "miru_learner_mode.json"


def _resolve_mode(raw: str) -> str:
    """Normalize SANDBOX to DRY_RUN for behavior; return valid mode or default."""
    r = (raw or "").strip().upper()
    if r == LEARNER_MODE_SANDBOX:
        return LEARNER_MODE_SANDBOX  # UI shows SANDBOX; engine treats as DRY_RUN
    if r in LEARNER_MODES:
        return r
    return DEFAULT_LEARNER_MODE


def get_learner_mode() -> str:
    """Current learner mode. File override > env > default. Default REVIEW_REQUIRED."""
    if LEARNER_MODE_OVERRIDE_PATH.is_file():
        try:
            data = json.loads(LEARNER_MODE_OVERRIDE_PATH.read_text(encoding="utf-8"))
            raw = str(data.get("mode") or "").strip().upper()
            if raw:
                return _resolve_mode(raw)
        except (json.JSONDecodeError, OSError):
            pass
    raw = (os.getenv(ENV_LEARNER_MODE) or "").strip().upper()
    if raw:
        return _resolve_mode(raw)
    return DEFAULT_LEARNER_MODE


def set_learner_mode(mode: str) -> bool:
    """Persist mode to override file. Returns True if written. Mode must be in LEARNER_MODES."""
    r = _resolve_mode(mode)
    if r not in LEARNER_MODES:
        return False
    try:
        LEARNER_MODE_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEARNER_MODE_OVERRIDE_PATH.write_text(json.dumps({"mode": r}, indent=0), encoding="utf-8")
        return True
    except OSError:
        return False


def is_publish_allowed() -> bool:
    """True only when mode is ACTIVE."""
    return get_learner_mode() == LEARNER_MODE_ACTIVE


def is_sandbox_or_dry_run() -> bool:
    """True when mode is SANDBOX or DRY_RUN (discovery only, no publish, no review queue)."""
    m = get_learner_mode()
    return m in (LEARNER_MODE_DRY_RUN, LEARNER_MODE_SANDBOX)


def is_review_required_mode() -> bool:
    """True when items must go to review queue instead of publishing."""
    return get_learner_mode() == LEARNER_MODE_REVIEW_REQUIRED


def is_dry_run() -> bool:
    """True when discovery/verification only, no publish or review queue (DRY_RUN or SANDBOX)."""
    return is_sandbox_or_dry_run()


# Cadence: intervals in hours (or days). Worker reads these; no continuous loops in this module.
DEFAULT_CADENCE: dict[str, Any] = {
    "discovery_interval_hours": 4,
    "image_check_interval_hours": 24,
    "rules_verification_interval_hours": 24,
    "publish_batch_after_verification": True,
}
ENV_CADENCE_PREFIX = "MIRU_CADENCE_"


def get_learner_cadence() -> dict[str, Any]:
    """Cadence settings. Can be overridden by env MIRU_CADENCE_* (e.g. MIRU_CADENCE_DISCOVERY_INTERVAL_HOURS=6)."""
    out = dict(DEFAULT_CADENCE)
    for key in list(out):
        env_key = ENV_CADENCE_PREFIX + key.upper()
        val = os.getenv(env_key)
        if val is not None:
            try:
                if isinstance(out[key], bool):
                    out[key] = val.strip().lower() in ("1", "true", "yes")
                elif isinstance(out[key], int):
                    out[key] = int(val)
                else:
                    out[key] = float(val) if "." in val else int(val)
            except ValueError:
                pass
    return out
