"""Entry point for the shadow-loop service.

Run with:
    python -m services.shadow_loop.launch

For real mode, requires Ollama running with the primary + validator models
pulled. For smoke mode (CI / no models available), set:
    SHADOW_LOOP_MODE=smoke

PRO-908 PR-A.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from . import config as config_mod
from .bandai_source import BandaiSource
from .db_writer import upsert_learned_card
from .dummy_verifier import DummyVerifier
from .ollama_client import primary as build_primary
from .ollama_client import validator as build_validator
from .priority_queue import PriorityQueue
from .real_verifier import RealVerifier


def configure_logging(log_path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(stream)


class SmokeClient:
    """Canned-response client used when SHADOW_LOOP_MODE=smoke.

    Returns an empty JSON answer for every question. Lets the loop run end-to-end
    without Ollama for tests / first-deploy smoke check.
    """

    def ask_json(self, user_prompt: str) -> dict[str, Any]:
        _ = user_prompt
        return {}


def _build_verifier(cfg, log: logging.Logger):
    """Construct the active verifier per SHADOW_LOOP_VERIFIER env var.

    `real` (default): RealVerifier with BandaiSource + validator-LLM as semantic judge.
    `dummy`: DummyVerifier — PR-A behaviour, every field inconclusive.

    In smoke mode, RealVerifier is still used (deterministic against catalog),
    but the semantic judge for SOFT fields is disabled — soft fields land
    inconclusive instead of hitting Ollama.
    """
    import os

    mode = os.environ.get("SHADOW_LOOP_VERIFIER", "real").lower()
    if mode == "dummy":
        log.info("SHADOW_LOOP_VERIFIER=dummy — using DummyVerifier (PR-A behaviour)")
        return DummyVerifier()

    bandai = BandaiSource(cfg.learning_pool_db.parent / "bandai_op01_crawl.json")
    if cfg.smoke_mode:
        log.info("SHADOW_LOOP_VERIFIER=real (smoke mode) — no semantic judge for SOFT fields")
        judge = None
    else:
        judge = build_validator(cfg.ollama_url, cfg.validator_model, cfg.request_timeout_s)
        log.info("SHADOW_LOOP_VERIFIER=real — validator-as-judge model=%s", cfg.validator_model)
    return RealVerifier(bandai=bandai, judge=judge)


def main() -> int:
    cfg = config_mod.load()
    configure_logging(cfg.log_path)
    log = logging.getLogger(__name__)

    if not cfg.learning_pool_db.exists():
        log.error(
            "learning pool DB missing at %s — run tools/create_miru_learning_pool.py first",
            cfg.learning_pool_db,
        )
        return 2
    if not cfg.catalog_db.exists():
        log.error("catalog DB missing at %s", cfg.catalog_db)
        return 2

    if cfg.smoke_mode:
        log.info("SHADOW_LOOP_MODE=smoke — using canned client")
        primary_client = SmokeClient()
        validator_client = SmokeClient()
    else:
        primary_client = build_primary(cfg.ollama_url, cfg.primary_model, cfg.request_timeout_s)
        validator_client = build_validator(
            cfg.ollama_url, cfg.validator_model, cfg.request_timeout_s
        )

    verifier = _build_verifier(cfg, log)
    queue = PriorityQueue()

    def writer(
        canonical_code: str,
        print_id: str,
        contributing_model: str,
        primary_answer: dict[str, Any],
        verifier_result: dict[str, Any],
        learned_from: str,
    ) -> int:
        return upsert_learned_card(
            pool_db=cfg.learning_pool_db,
            canonical_code=canonical_code,
            print_id=print_id,
            contributing_model=contributing_model,
            primary_answer=primary_answer,
            verifier_result=verifier_result,
            learned_from=learned_from,
        )

    from .loop_runner import run_forever

    try:
        run_forever(cfg, queue, primary_client, verifier, writer, validator_client)
    except KeyboardInterrupt:
        log.info("interrupted — shutting down cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
