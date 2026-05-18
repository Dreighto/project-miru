"""Tests for the shadow-review API layer (PRO-909 PR-A)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# Repo's conftest.py adds `tools/` to sys.path, which shadows the `miru_ai`
# package with `tools/miru_ai.py` (a compat wrapper). Strip `tools/` BEFORE
# importing so the real package wins. Same pattern that test_miru_ai_boundary
# would need; leaving it inline rather than touching the global conftest.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = str(_REPO_ROOT / "tools")
sys.path[:] = [
    p for p in sys.path if p != _TOOLS_DIR and Path(p).resolve() != Path(_TOOLS_DIR).resolve()
]

import pytest  # noqa: E402

from miru_ai.shadow_review import (  # noqa: E402
    fetch_item,
    fetch_queue,
    submit_verdict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CREATE_SCRIPT = REPO_ROOT / "tools" / "create_miru_learning_pool.py"


def _seed_row(
    pool_db: Path,
    canonical_code: str = "OP01-001",
    print_id: str = "OP01-001",
    contributing_model: str = "qwen2.5:7b",
    promotion_status: str = "experimental",
    confidence_score: float = 0.0,
    validator_agreement: dict | None = None,
    last_verified: str = "2026-05-18T03:00:00Z",
    created_at: str = "2026-05-18T02:00:00Z",
) -> None:
    """Insert a learned_cards row directly so tests can exercise the API layer."""
    conn = sqlite3.connect(pool_db)
    try:
        conn.execute(
            "INSERT INTO learned_cards "
            "(canonical_code, print_id, contributing_model, promotion_status, "
            "confidence_score, validator_agreement, last_verified, created_at, learned_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical_code,
                print_id,
                contributing_model,
                promotion_status,
                confidence_score,
                json.dumps(validator_agreement or {}),
                last_verified,
                created_at,
                "test_fixture",
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fresh_pool_db(tmp_path: Path) -> Path:
    pool_db = tmp_path / "miru_learning_pool.db"
    result = subprocess.run(
        [sys.executable, str(CREATE_SCRIPT), "--db", str(pool_db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"create script failed: {result.stderr}"
    return pool_db


def test_empty_queue_when_no_rows(fresh_pool_db: Path):
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result == {"items": [], "total": 0}


def test_queue_missing_db_returns_empty(tmp_path: Path):
    result = fetch_queue(pool_db=tmp_path / "nope.db")
    assert result == {"items": [], "total": 0}


def test_review_ready_row_appears_in_queue(fresh_pool_db: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-001",
        promotion_status="review-ready",
        confidence_score=1.0,
        validator_agreement={
            "card_name": {"tier": "hard", "outcome": "verified-correct", "reason": "ok"},
            "cost": {"tier": "hard", "outcome": "verified-correct", "reason": "ok"},
        },
    )
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["canonical_code"] == "OP01-001"
    assert item["promotion_status"] == "review-ready"
    assert item["confidence_score"] == 1.0
    assert item["inconclusive_field_count"] == 0


def test_experimental_with_inconclusive_appears_in_queue(fresh_pool_db: Path):
    """experimental rows that have at least one inconclusive field show up."""
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-002",
        promotion_status="experimental",
        confidence_score=0.5,
        validator_agreement={
            "card_name": {"tier": "hard", "outcome": "verified-correct", "reason": ""},
            "effect_text": {
                "tier": "soft",
                "outcome": "inconclusive",
                "reason": "judge unavailable",
            },
            "traits": {"tier": "soft", "outcome": "inconclusive", "reason": "judge unavailable"},
        },
    )
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result["total"] == 1
    assert result["items"][0]["inconclusive_field_count"] == 2


def test_experimental_with_zero_inconclusive_not_in_queue(fresh_pool_db: Path):
    """Clean experimental rows aren't review work — they shouldn't appear."""
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-003",
        promotion_status="experimental",
        confidence_score=1.0,
        validator_agreement={
            "card_name": {"tier": "hard", "outcome": "verified-correct", "reason": ""},
            "cost": {"tier": "hard", "outcome": "verified-correct", "reason": ""},
        },
    )
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result == {"items": [], "total": 0}


def test_promoted_and_rejected_not_in_queue(fresh_pool_db: Path):
    """Terminal statuses don't surface in the review queue."""
    _seed_row(fresh_pool_db, canonical_code="OP01-004", promotion_status="promoted")
    _seed_row(fresh_pool_db, canonical_code="OP01-005", promotion_status="rejected")
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result == {"items": [], "total": 0}


def test_review_ready_sorted_before_experimental(fresh_pool_db: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-010",
        promotion_status="experimental",
        validator_agreement={"x": {"tier": "hard", "outcome": "inconclusive", "reason": "?"}},
        last_verified="2099-12-31T00:00:00Z",
    )
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-011",
        promotion_status="review-ready",
        last_verified="2000-01-01T00:00:00Z",
    )
    result = fetch_queue(pool_db=fresh_pool_db)
    assert result["items"][0]["canonical_code"] == "OP01-011"
    assert result["items"][1]["canonical_code"] == "OP01-010"


def test_fetch_item_returns_shape(fresh_pool_db: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-001",
        contributing_model="qwen2.5:7b",
        promotion_status="review-ready",
        confidence_score=1.0,
        validator_agreement={
            "card_name": {
                "tier": "hard",
                "outcome": "verified-correct",
                "reason": "matches",
                "model_value": "Roronoa Zoro",
                "catalog_value": "Roronoa Zoro",
                "bandai_value": "Roronoa Zoro",
            },
            "cost": {
                "tier": "hard",
                "outcome": "verified-wrong",
                "reason": "primary=6 != catalog=5",
                "model_value": 6,
                "catalog_value": 5,
            },
        },
    )
    item = fetch_item(
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        pool_db=fresh_pool_db,
    )
    assert item is not None
    assert item["canonical_code"] == "OP01-001"
    assert item["promotion_status"] == "review-ready"
    assert item["bandai_url"] is not None
    assert "OP01-001" in item["bandai_url"]
    assert item["tcgplayer_url"] is not None
    field_names = {f["field"] for f in item["field_outcomes"]}
    assert field_names == {"card_name", "cost"}
    cost_outcome = next(f for f in item["field_outcomes"] if f["field"] == "cost")
    assert cost_outcome["outcome"] == "verified-wrong"
    assert cost_outcome["primary_value"] == 6
    assert cost_outcome["catalog_value"] == 5


def test_fetch_item_missing_returns_none(fresh_pool_db: Path):
    item = fetch_item(
        canonical_code="OP01-NEVER",
        print_id="OP01-NEVER",
        contributing_model="qwen2.5:7b",
        pool_db=fresh_pool_db,
    )
    assert item is None


def test_submit_verdict_correct_promotes_row(fresh_pool_db: Path, tmp_path: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-001",
        contributing_model="qwen2.5:7b",
        promotion_status="review-ready",
        confidence_score=1.0,
    )
    jsonl = tmp_path / "overrides.jsonl"
    result = submit_verdict(
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        verdict="correct",
        sources_checked=["bandai", "catalog"],
        pool_db=fresh_pool_db,
        overrides_jsonl=jsonl,
    )
    assert result["ok"] is True
    assert result["new_promotion_status"] == "promoted"
    assert result["event_logged"] is True

    # DB state changed
    conn = sqlite3.connect(fresh_pool_db)
    try:
        row = conn.execute(
            "SELECT promotion_status FROM learned_cards "
            "WHERE canonical_code = ? AND print_id = ? AND contributing_model = ?",
            ("OP01-001", "OP01-001", "qwen2.5:7b"),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "promoted"

    # JSONL has one event
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["operator_verdict"] == "correct"
    assert event["verdict"] == "agree"
    assert event["sources_checked"] == ["bandai", "catalog"]


def test_submit_verdict_wrong_rejects_row(fresh_pool_db: Path, tmp_path: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-001",
        contributing_model="qwen2.5:7b",
        promotion_status="review-ready",
    )
    jsonl = tmp_path / "overrides.jsonl"
    result = submit_verdict(
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        verdict="wrong",
        sources_checked=["bandai"],
        pool_db=fresh_pool_db,
        overrides_jsonl=jsonl,
    )
    assert result["new_promotion_status"] == "rejected"

    event = json.loads(jsonl.read_text(encoding="utf-8").strip())
    assert event["operator_verdict"] == "wrong"
    assert event["verdict"] == "override"


def test_submit_verdict_defer_leaves_status_unchanged(fresh_pool_db: Path, tmp_path: Path):
    _seed_row(
        fresh_pool_db,
        canonical_code="OP01-001",
        contributing_model="qwen2.5:7b",
        promotion_status="review-ready",
    )
    jsonl = tmp_path / "overrides.jsonl"
    result = submit_verdict(
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        verdict="defer",
        sources_checked=[],
        pool_db=fresh_pool_db,
        overrides_jsonl=jsonl,
    )
    assert result["new_promotion_status"] == "review-ready"

    conn = sqlite3.connect(fresh_pool_db)
    try:
        row = conn.execute(
            "SELECT promotion_status FROM learned_cards WHERE canonical_code = ?",
            ("OP01-001",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "review-ready"

    event = json.loads(jsonl.read_text(encoding="utf-8").strip())
    assert event["operator_verdict"] == "defer"
    assert event["verdict"] == "defer"
    assert event["sources_checked"] == []


def test_submit_verdict_rejects_invalid_verdict_value(fresh_pool_db: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="verdict must be"):
        submit_verdict(
            canonical_code="OP01-001",
            print_id="OP01-001",
            contributing_model="qwen2.5:7b",
            verdict="maybe",
            sources_checked=["bandai"],
            pool_db=fresh_pool_db,
            overrides_jsonl=tmp_path / "overrides.jsonl",
        )


def test_submit_verdict_requires_sources_for_correct(fresh_pool_db: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="sources_checked"):
        submit_verdict(
            canonical_code="OP01-001",
            print_id="OP01-001",
            contributing_model="qwen2.5:7b",
            verdict="correct",
            sources_checked=[],
            pool_db=fresh_pool_db,
            overrides_jsonl=tmp_path / "overrides.jsonl",
        )


def test_submit_verdict_requires_sources_for_wrong(fresh_pool_db: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="sources_checked"):
        submit_verdict(
            canonical_code="OP01-001",
            print_id="OP01-001",
            contributing_model="qwen2.5:7b",
            verdict="wrong",
            sources_checked=[],
            pool_db=fresh_pool_db,
            overrides_jsonl=tmp_path / "overrides.jsonl",
        )
