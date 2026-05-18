"""Tests for the shadow-loop DB writer.

Uses a freshly-created learning pool DB in a temp dir via the same script
that creates the production pool, so the schema is verified end-to-end.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from services.shadow_loop.db_writer import upsert_learned_card

REPO_ROOT = Path(__file__).resolve().parents[3]
CREATE_SCRIPT = REPO_ROOT / "tools" / "create_miru_learning_pool.py"


@pytest.fixture
def fresh_pool_db(tmp_path: Path) -> Path:
    """Create a fresh learning pool DB in tmp using the canonical creator."""
    pool_db = tmp_path / "miru_learning_pool.db"
    result = subprocess.run(
        [sys.executable, str(CREATE_SCRIPT), "--db", str(pool_db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"create script failed: {result.stderr}"
    assert pool_db.exists()
    return pool_db


def test_upsert_writes_row_with_expected_shape(fresh_pool_db: Path):
    primary_answer = {
        "card_name": "Monkey D. Luffy",
        "cost": 5,
        "power": "5000",
        "counter": "-",
        "color": "Red",
        "card_type": "Leader",
    }
    verifier_result = {
        "field_outcomes": {
            "card_name": {"outcome": "inconclusive", "reason": "test"},
        },
        "confidence_score": 0.0,
        "all_hard_verified_correct": False,
    }

    row_id = upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        primary_answer=primary_answer,
        verifier_result=verifier_result,
        learned_from="test_tick_1",
    )
    assert row_id > 0

    conn = sqlite3.connect(fresh_pool_db)
    try:
        rows = conn.execute(
            "SELECT canonical_code, print_id, contributing_model, "
            "card_name, cost, power, color, card_type, "
            "confidence_score, validator_agreement, promotion_status, learned_from "
            "FROM learned_cards WHERE id = ?",
            (row_id,),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "OP01-001"
    assert row[1] == "OP01-001"
    assert row[2] == "qwen2.5:7b"
    assert row[3] == "Monkey D. Luffy"
    assert row[4] == 5
    assert row[5] == "5000"
    assert row[6] == "Red"
    assert row[7] == "Leader"
    assert row[8] == 0.0
    # validator_agreement holds field_outcomes JSON
    field_outcomes = json.loads(row[9])
    assert field_outcomes["card_name"]["outcome"] == "inconclusive"
    assert row[10] == "experimental"
    assert row[11] == "test_tick_1"


def test_upsert_replaces_prior_row_for_same_card_and_model(fresh_pool_db: Path):
    primary_answer_v1 = {"card_name": "Wrong Name"}
    primary_answer_v2 = {"card_name": "Monkey D. Luffy"}
    verifier_result = {
        "field_outcomes": {},
        "confidence_score": 0.0,
        "all_hard_verified_correct": False,
    }
    upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        primary_answer=primary_answer_v1,
        verifier_result=verifier_result,
        learned_from="tick_1",
    )
    upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        primary_answer=primary_answer_v2,
        verifier_result=verifier_result,
        learned_from="tick_2",
    )

    conn = sqlite3.connect(fresh_pool_db)
    try:
        rows = conn.execute(
            "SELECT card_name, learned_from FROM learned_cards "
            "WHERE canonical_code = ? AND print_id = ? AND contributing_model = ?",
            ("OP01-001", "OP01-001", "qwen2.5:7b"),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "Monkey D. Luffy"
    assert rows[0][1] == "tick_2"


def test_different_models_get_distinct_rows(fresh_pool_db: Path):
    """Same card, different contributing_model → two separate rows."""
    primary_answer = {"card_name": "Monkey D. Luffy"}
    verifier_result = {
        "field_outcomes": {},
        "confidence_score": 0.0,
        "all_hard_verified_correct": False,
    }
    upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:7b",
        primary_answer=primary_answer,
        verifier_result=verifier_result,
        learned_from="tick_1",
    )
    upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-001",
        print_id="OP01-001",
        contributing_model="qwen2.5:14b",
        primary_answer=primary_answer,
        verifier_result=verifier_result,
        learned_from="tick_1",
    )

    conn = sqlite3.connect(fresh_pool_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM learned_cards WHERE canonical_code = ?", ("OP01-001",)
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 2
