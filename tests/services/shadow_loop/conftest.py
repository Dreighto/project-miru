"""Shared fixtures for shadow_loop guard-module tests (PR-C, PRO-912)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CREATE_SCRIPT = REPO_ROOT / "tools" / "create_miru_learning_pool.py"


@pytest.fixture
def fresh_pool_db(tmp_path: Path) -> Path:
    """Create a fresh miru_learning_pool.db in a temp dir via the canonical creator."""
    pool_db = tmp_path / "miru_learning_pool.db"
    result = subprocess.run(
        [sys.executable, str(CREATE_SCRIPT), "--db", str(pool_db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"create script failed:\n{result.stderr}"
    assert pool_db.exists()
    return pool_db


def insert_learned_row(
    pool_db: Path,
    canonical_code: str,
    print_id: str,
    contributing_model: str = "qwen2.5:7b",
    confidence_score: float = 0.0,
    last_verified: str = "2026-05-18T03:00:00Z",
) -> None:
    """Insert a minimal learned_cards row with explicit last_verified for
    time-sensitive tests.

    The three-axis state columns (readiness_state / approval_state /
    promotion_state, PRO-928) take their schema DEFAULTs — these tests key off
    `last_verified`, not review state.
    """
    conn = sqlite3.connect(pool_db)
    try:
        conn.execute(
            "INSERT INTO learned_cards "
            "(canonical_code, print_id, contributing_model, "
            "confidence_score, last_verified, learned_from) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                canonical_code,
                print_id,
                contributing_model,
                confidence_score,
                last_verified,
                "test_fixture",
            ),
        )
        conn.commit()
    finally:
        conn.close()
