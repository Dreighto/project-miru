"""Smoke test for the shadow-loop tick runner.

Uses fake clients + verifier + writer to exercise the tick logic without
hitting Ollama or the real DB. Verifies that one tick:
  - pops from the queue
  - calls the primary client
  - calls the verifier
  - calls the writer with the expected arguments
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.shadow_loop.config import Config
from services.shadow_loop.loop_runner import run_one_tick
from services.shadow_loop.priority_queue import PriorityQueue


class _FakePrimary:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ask_json(self, user_prompt: str) -> dict[str, Any]:
        self.calls.append(user_prompt)
        return {"card_name": "Fake Luffy", "cost": 5}


class _FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def score(
        self,
        card: dict[str, Any],
        primary_answer: dict[str, Any],
        validator_answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((card, primary_answer))
        return {
            "field_outcomes": {"card_name": {"outcome": "inconclusive", "reason": "fake"}},
            "confidence_score": 0.0,
            "all_hard_verified_correct": False,
        }


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def __call__(
        self,
        canonical_code: str,
        print_id: str,
        contributing_model: str,
        primary_answer: dict[str, Any],
        verifier_result: dict[str, Any],
        learned_from: str,
    ) -> int:
        self.writes.append(
            {
                "canonical_code": canonical_code,
                "print_id": print_id,
                "contributing_model": contributing_model,
                "primary_answer": primary_answer,
                "verifier_result": verifier_result,
                "learned_from": learned_from,
            }
        )
        return len(self.writes)


@pytest.fixture
def tick_config(tmp_path: Path) -> Config:
    """A Config that points at the real catalog DB for catalog reads but a tmp pool path."""
    repo_root = Path(__file__).resolve().parents[3]
    catalog = repo_root / "data" / "card_catalog.db"
    if not catalog.exists():
        pytest.skip("card_catalog.db not present — skipping integration-leaning tick test")
    return Config(
        ollama_url="http://127.0.0.1:11434",
        primary_model="qwen2.5:7b",
        validator_model="qwen2.5:14b",
        request_timeout_s=10,
        tick_seconds=0,
        smoke_mode=True,
        catalog_db=catalog,
        learning_pool_db=tmp_path / "fake_pool.db",
        log_path=tmp_path / "fake.log",
        set_scope_prefix="OP01-",
    )


def test_one_tick_pops_calls_models_and_writes(tick_config: Config):
    queue = PriorityQueue()
    queue.add("OP01-001", "OP01-001", "seed")
    primary = _FakePrimary()
    verifier = _FakeVerifier()
    writer = _FakeWriter()

    cont = run_one_tick(
        config=tick_config,
        queue=queue,
        primary_client=primary,
        verifier=verifier,
        writer=writer,
        tick_count=1,
    )

    assert cont is True
    assert len(primary.calls) == 1
    assert len(verifier.calls) == 1
    assert len(writer.writes) == 1
    write = writer.writes[0]
    assert write["canonical_code"] == "OP01-001"
    assert write["print_id"] == "OP01-001"
    assert write["contributing_model"] == "qwen2.5:7b"
    assert write["primary_answer"]["card_name"] == "Fake Luffy"
    assert write["learned_from"] == "shadow_loop_tick_1"


def test_one_tick_handles_primary_failure_gracefully(tick_config: Config):
    """Primary raising should not crash the tick — empty answer is written."""
    queue = PriorityQueue()
    queue.add("OP01-001", "OP01-001", "seed")

    class _ExplodingPrimary:
        def ask_json(self, user_prompt: str) -> dict[str, Any]:
            raise RuntimeError("ollama is on fire")

    verifier = _FakeVerifier()
    writer = _FakeWriter()

    cont = run_one_tick(
        config=tick_config,
        queue=queue,
        primary_client=_ExplodingPrimary(),
        verifier=verifier,
        writer=writer,
        tick_count=2,
    )

    assert cont is True
    assert len(writer.writes) == 1
    assert writer.writes[0]["primary_answer"] == {}


def test_empty_queue_reseeds_from_catalog(tick_config: Config):
    """If the queue is empty at tick start, it should reseed from card_catalog."""
    queue = PriorityQueue()
    assert queue.is_empty()

    primary = _FakePrimary()
    verifier = _FakeVerifier()
    writer = _FakeWriter()

    run_one_tick(
        config=tick_config,
        queue=queue,
        primary_client=primary,
        verifier=verifier,
        writer=writer,
        tick_count=1,
    )

    # After the tick, one card has been processed and the queue still has many to go.
    assert len(writer.writes) == 1
    assert len(queue) > 100  # OP01 has 218 printings; one popped, the rest remain
