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
            "confidence_score, validator_agreement, "
            "readiness_state, approval_state, promotion_state, learned_from "
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
    # No stage3_autoclear in verifier_result -> fail-closed -> blocked_by_guardrail.
    assert row[10] == "blocked_by_guardrail"
    assert row[11] == "pending_review"
    assert row[12] == ""
    assert row[13] == "test_tick_1"


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


def _state(pool_db: Path, row_id: int) -> tuple[str, str, str]:
    conn = sqlite3.connect(pool_db)
    try:
        return conn.execute(
            "SELECT readiness_state, approval_state, promotion_state "
            "FROM learned_cards WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()


def test_upsert_stage3_pass_sets_ready_for_review(fresh_pool_db: Path):
    """When the Stage 3 gate advances, the row enters ready_for_review."""
    row_id = upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-002",
        print_id="OP01-002",
        contributing_model="qwen2.5:7b",
        primary_answer={"card_name": "Nami"},
        verifier_result={
            "field_outcomes": {},
            "confidence_score": 1.0,
            "stage3_autoclear": {"advance": True},
        },
        learned_from="tick_stage3_pass",
    )
    assert _state(fresh_pool_db, row_id) == ("ready_for_review", "pending_review", "")


def test_upsert_stage3_fail_sets_blocked_by_guardrail(fresh_pool_db: Path):
    """When the Stage 3 gate fails, the row is blocked_by_guardrail."""
    row_id = upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-003",
        print_id="OP01-003",
        contributing_model="qwen2.5:7b",
        primary_answer={"card_name": "Zoro"},
        verifier_result={
            "field_outcomes": {},
            "confidence_score": 0.0,
            "stage3_autoclear": {"advance": False, "reason": "no bandai trace"},
        },
        learned_from="tick_stage3_fail",
    )
    assert _state(fresh_pool_db, row_id) == ("blocked_by_guardrail", "pending_review", "")


def test_upsert_missing_stage3_is_fail_closed(fresh_pool_db: Path):
    """An absent stage3_autoclear result is treated as a gate failure."""
    row_id = upsert_learned_card(
        pool_db=fresh_pool_db,
        canonical_code="OP01-004",
        print_id="OP01-004",
        contributing_model="qwen2.5:7b",
        primary_answer={"card_name": "Usopp"},
        verifier_result={"field_outcomes": {}, "confidence_score": 0.0},
        learned_from="tick_no_stage3",
    )
    assert _state(fresh_pool_db, row_id) == ("blocked_by_guardrail", "pending_review", "")
