"""Tests for services/shadow_loop/stale_requeue.py (PR-C, PRO-912)."""

from __future__ import annotations

from pathlib import Path

from services.shadow_loop.stale_requeue import stale_rows
from tests.services.shadow_loop.conftest import insert_learned_row

_RECENT = "2099-01-01T00:00:00Z"  # Far future — always fresh
_OLD = "2000-01-01T00:00:00Z"  # Far past — always stale


def test_empty_when_db_missing(tmp_path: Path):
    assert stale_rows(pool_db=tmp_path / "no.db") == []


def test_empty_when_no_rows(fresh_pool_db: Path):
    assert stale_rows(pool_db=fresh_pool_db) == []


def test_empty_when_all_rows_fresh(fresh_pool_db: Path):
    insert_learned_row(fresh_pool_db, "OP01-001", "OP01-001", last_verified=_RECENT)
    insert_learned_row(fresh_pool_db, "OP01-002", "OP01-002", last_verified=_RECENT)
    result = stale_rows(pool_db=fresh_pool_db)
    assert result == []


def test_returns_stale_rows(fresh_pool_db: Path):
    insert_learned_row(fresh_pool_db, "OP01-001", "OP01-001", last_verified=_OLD)
    result = stale_rows(pool_db=fresh_pool_db)
    assert len(result) == 1
    assert result[0] == ("OP01-001", "OP01-001")


def test_excludes_fresh_rows(fresh_pool_db: Path):
    insert_learned_row(fresh_pool_db, "OP01-001", "OP01-001", last_verified=_OLD)
    insert_learned_row(fresh_pool_db, "OP01-002", "OP01-002", last_verified=_RECENT)
    result = stale_rows(pool_db=fresh_pool_db)
    assert len(result) == 1
    codes = [r[0] for r in result]
    assert "OP01-001" in codes
    assert "OP01-002" not in codes


def test_ordered_oldest_first(fresh_pool_db: Path):
    insert_learned_row(fresh_pool_db, "OP01-003", "OP01-003", last_verified="2001-01-01T00:00:00Z")
    insert_learned_row(fresh_pool_db, "OP01-001", "OP01-001", last_verified="2000-01-01T00:00:00Z")
    insert_learned_row(fresh_pool_db, "OP01-002", "OP01-002", last_verified="2000-06-01T00:00:00Z")
    result = stale_rows(pool_db=fresh_pool_db)
    assert len(result) == 3
    # Oldest first: OP01-001, OP01-002, OP01-003
    assert result[0][0] == "OP01-001"
    assert result[1][0] == "OP01-002"
    assert result[2][0] == "OP01-003"


def test_distinct_deduplicates_multiple_models(fresh_pool_db: Path):
    """Same card with two different models should appear once in the stale list."""
    insert_learned_row(
        fresh_pool_db,
        "OP01-001",
        "OP01-001",
        contributing_model="qwen2.5:7b",
        last_verified=_OLD,
    )
    insert_learned_row(
        fresh_pool_db,
        "OP01-001",
        "OP01-001",
        contributing_model="qwen2.5:14b",
        last_verified=_OLD,
    )
    result = stale_rows(pool_db=fresh_pool_db)
    # DISTINCT — only one (OP01-001, OP01-001) pair even though two models are stale
    assert len(result) == 1
    assert result[0] == ("OP01-001", "OP01-001")


def test_respects_custom_max_age(fresh_pool_db: Path):
    # Row with last_verified 2 hours ago in real-time would be stale at 1h but fresh at 3h.
    # We simulate with timestamps relative to "now" using SQLite modifiers directly.
    import sqlite3

    conn = sqlite3.connect(fresh_pool_db)
    try:
        conn.execute(
            "INSERT INTO learned_cards "
            "(canonical_code, print_id, contributing_model, "
            "promotion_status, confidence_score, last_verified, learned_from) "
            "VALUES (?, ?, ?, ?, ?, datetime('now', '-2 hours'), ?)",
            ("OP01-005", "OP01-005", "qwen2.5:7b", "experimental", 0.0, "test"),
        )
        conn.commit()
    finally:
        conn.close()

    # 1-hour threshold → row is stale
    result_1h = stale_rows(pool_db=fresh_pool_db, max_age_hours=1)
    codes_1h = [r[0] for r in result_1h]
    assert "OP01-005" in codes_1h

    # 3-hour threshold → row is NOT stale
    result_3h = stale_rows(pool_db=fresh_pool_db, max_age_hours=3)
    codes_3h = [r[0] for r in result_3h]
    assert "OP01-005" not in codes_3h
