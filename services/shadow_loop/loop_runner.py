"""Main tick loop for the shadow loop.

Each tick:
  1. Pull next (canonical_code, print_id) from the priority queue.
  2. Ask primary + validator the canonical question for that card.
  3. Score the primary's answer via the verifier (dummy in PR-A).
  4. Run the Stage 3 auto-clear predicate (PRO-927).
  5. Write a row to miru_learning_pool.db.
  6. Sleep tick_seconds.

If the queue is empty, the loop reseeds from card_catalog (every card with
the configured set_scope_prefix is enqueued).

Hooks reserved for PR-C (verifier-of-verifier guards):
  * `sentinel.should_check_sentinel(tick_count)` — periodic sentinel run
  * `override_metric.should_halt_loop()` — auto-pause on operator-override-rate
  * `stale_requeue.stale_rows(conn)` — re-add stale rows to the queue
These imports are wrapped in try/except so PR-A can run without PR-C present.

PRO-908 PR-A.
PRO-927: run_one_tick accepts optional validator_client; runs stage3_autoclear
predicate and embeds the result in verifier_result before writing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Protocol

from .config import Config
from .priority_queue import PriorityQueue
from .question_template import build_question
from .stage3_autoclear import stage3_autoclear

log = logging.getLogger(__name__)

# Optional PR-C imports — fall back to no-op guards if PR-C isn't merged yet.
try:
    from . import sentinel as _sentinel_module  # type: ignore[attr-defined]
except ImportError:
    _sentinel_module = None  # type: ignore[assignment]

try:
    from . import override_metric as _override_module  # type: ignore[attr-defined]
except ImportError:
    _override_module = None  # type: ignore[assignment]

try:
    from . import stale_requeue as _stale_module  # type: ignore[attr-defined]
except ImportError:
    _stale_module = None  # type: ignore[assignment]


class ModelClient(Protocol):
    def ask_json(self, user_prompt: str) -> dict: ...


class Verifier(Protocol):
    def score(
        self,
        card: dict[str, Any],
        primary_answer: dict[str, Any],
        validator_answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class Writer(Protocol):
    def __call__(
        self,
        canonical_code: str,
        print_id: str,
        contributing_model: str,
        primary_answer: dict[str, Any],
        verifier_result: dict[str, Any],
        learned_from: str,
    ) -> int: ...


def seed_queue_from_catalog(queue: PriorityQueue, catalog_db, set_scope_prefix: str) -> int:
    """Add every (canonical_code, print_id) under the scope prefix. Returns count added."""
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT c.canonical_code, cv.print_id
            FROM card_variants cv JOIN cards c ON cv.card_id = c.id
            WHERE c.canonical_code LIKE ? || '%'
            ORDER BY c.canonical_code, cv.print_id
            """,
            (set_scope_prefix,),
        ).fetchall()
    finally:
        conn.close()
    for canonical_code, print_id in rows:
        queue.add(canonical_code, print_id, reason="initial_seed")
    return len(rows)


def fetch_card_row(catalog_db, canonical_code: str, print_id: str) -> dict[str, Any] | None:
    """Read the catalog row for one (canonical_code, print_id) as a dict."""
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT c.*, cv.print_id, cv.image_path, cv.image_url
            FROM card_variants cv JOIN cards c ON cv.card_id = c.id
            WHERE c.canonical_code = ? AND cv.print_id = ?
            LIMIT 1
            """,
            (canonical_code, print_id),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def run_one_tick(
    config: Config,
    queue: PriorityQueue,
    primary_client: ModelClient,
    verifier: Verifier,
    writer: Writer,
    tick_count: int,
    validator_client: ModelClient | None = None,
) -> bool:
    """One tick. Returns True if work was done; False if loop should halt."""
    # PR-C halt guard: operator-override-rate auto-pause.
    if _override_module is not None and _override_module.should_halt_loop():
        log.warning("override-rate halt threshold tripped — shutting down")
        return False

    # Ensure there's something to ask about.
    if queue.is_empty():
        added = seed_queue_from_catalog(queue, config.catalog_db, config.set_scope_prefix)
        log.info("queue was empty; reseeded %d cards under %s", added, config.set_scope_prefix)
        if queue.is_empty():
            log.warning("queue still empty after reseed — nothing to do")
            return True

    item = queue.next()
    if item is None:
        return True
    canonical_code, print_id = item

    card = fetch_card_row(config.catalog_db, canonical_code, print_id)
    if card is None:
        log.warning("card not found in catalog: %s %s — skipping", canonical_code, print_id)
        return True

    question = build_question(canonical_code, print_id)

    try:
        primary_answer = primary_client.ask_json(question)
    except Exception as exc:
        log.warning("primary model failed on %s %s: %s", canonical_code, print_id, exc)
        primary_answer = {}

    validator_answer: dict[str, Any] | None = None
    if validator_client is not None:
        try:
            validator_answer = validator_client.ask_json(question)
        except Exception as exc:
            log.warning("validator model failed on %s %s: %s", canonical_code, print_id, exc)

    verifier_result = verifier.score(card, primary_answer, validator_answer)

    # PRO-927: Stage 3 auto-clear — require BANDAI-TRACE AGREEMENT.
    stage3_advance, stage3_reason = stage3_autoclear(verifier_result)
    verifier_result["stage3_autoclear"] = {
        "advance": stage3_advance,
        "reason": stage3_reason,
    }
    if not stage3_advance:
        log.debug(
            "stage3_autoclear FAIL card=%s/%s reason=%s",
            canonical_code,
            print_id,
            stage3_reason,
        )

    writer(
        canonical_code=canonical_code,
        print_id=print_id,
        contributing_model=config.primary_model,
        primary_answer=primary_answer,
        verifier_result=verifier_result,
        learned_from=f"shadow_loop_tick_{tick_count}",
    )

    log.info(
        "tick %d  card=%s/%s  confidence=%.2f",
        tick_count,
        canonical_code,
        print_id,
        verifier_result.get("confidence_score", 0.0),
    )

    # PR-C sentinel hook (no-op if module isn't present yet).
    if _sentinel_module is not None and _sentinel_module.should_check_sentinel(tick_count):
        log.info("sentinel check scheduled for tick %d (PR-C hook)", tick_count)

    return True


def run_forever(
    config: Config,
    queue: PriorityQueue,
    primary_client: ModelClient,
    verifier: Verifier,
    writer: Writer,
    validator_client: ModelClient | None = None,
) -> None:
    """Drive the loop until shutdown signal or halt-guard trips."""
    seed_queue_from_catalog(queue, config.catalog_db, config.set_scope_prefix)
    log.info(
        "shadow loop starting — primary=%s tick=%ds", config.primary_model, config.tick_seconds
    )

    tick_count = 0
    while True:
        tick_count += 1
        should_continue = run_one_tick(
            config, queue, primary_client, verifier, writer, tick_count, validator_client
        )
        if not should_continue:
            log.info("shadow loop halting at tick %d", tick_count)
            return
        time.sleep(config.tick_seconds)
