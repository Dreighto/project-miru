"""Shadow-loop configuration — all knobs live here.

Every tunable is sourced from an environment variable with a documented
default. The loop reads these once at startup; restart to apply changes.

PRO-908 PR-A.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Config:
    # Ollama
    ollama_url: str
    primary_model: str
    validator_model: str
    request_timeout_s: int

    # Loop
    tick_seconds: int
    smoke_mode: bool  # if True, skip real Ollama calls; use canned responses (for CI)

    # Storage
    catalog_db: Path
    learning_pool_db: Path
    log_path: Path

    # Set scope — what cards to learn (PR-A scopes to OP01 only)
    set_scope_prefix: str


def load() -> Config:
    return Config(
        ollama_url=os.environ.get("SHADOW_LOOP_OLLAMA_URL", "http://127.0.0.1:11434"),
        primary_model=os.environ.get("SHADOW_LOOP_PRIMARY_MODEL", "qwen2.5:7b"),
        validator_model=os.environ.get("SHADOW_LOOP_VALIDATOR_MODEL", "mistral-small3:7b"),
        request_timeout_s=int(os.environ.get("SHADOW_LOOP_REQUEST_TIMEOUT_S", "180")),
        tick_seconds=int(os.environ.get("SHADOW_LOOP_TICK_SECONDS", "60")),
        smoke_mode=os.environ.get("SHADOW_LOOP_MODE", "real").lower() == "smoke",
        catalog_db=Path(
            os.environ.get("SHADOW_LOOP_CATALOG_DB", str(REPO_ROOT / "data" / "card_catalog.db"))
        ),
        learning_pool_db=Path(
            os.environ.get("SHADOW_LOOP_POOL_DB", str(REPO_ROOT / "data" / "miru_learning_pool.db"))
        ),
        log_path=Path(
            os.environ.get("SHADOW_LOOP_LOG_PATH", str(REPO_ROOT / "data" / "shadow_loop.log"))
        ),
        set_scope_prefix=os.environ.get("SHADOW_LOOP_SET_SCOPE", "OP01-"),
    )
