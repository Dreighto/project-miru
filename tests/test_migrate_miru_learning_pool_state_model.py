"""Tests for tools/migrate_miru_learning_pool_2026-05-20_state-model.py (PRO-928).

The migration adopts card_catalog.db's three-axis state model (BORROW
decision): it replaces the single `promotion_status` column with
`readiness_state` / `approval_state` / `promotion_state`, drops the superseded
PRO-926 tables, and re-points the review-state index.

Coverage:
  - fresh migration: 3 new CHECK columns added, promotion_status + its index
    dropped, door_b_overrides / score_transitions dropped, new index created
  - data migration: existing rows preserved and defaulted to the pending combo
  - CHECK constraints: each new column rejects out-of-vocabulary values;
    promotion_state accepts '' as a real value
  - idempotency: is_already_migrated gate + main() no-op on a migrated DB
  - drift refusal: missing promotion_status / missing PRO-926 tables refused
  - verify(): catches row-count drift
  - main(): backup created, exit codes for dry-run / drift / missing DB
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# The migration script filename contains hyphens, so it cannot be imported via
# the normal import machinery. Load it by path under a unique module name.
_MIGRATION_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "migrate_miru_learning_pool_2026-05-20_state-model.py"
)
_spec = importlib.util.spec_from_file_location("_pro928_migration", str(_MIGRATION_SCRIPT))
_migration = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_migration)  # type: ignore[union-attr]

apply_migration = _migration.apply_migration
is_already_migrated = _migration.is_already_migrated
check_drift = _migration.check_drift
verify = _migration.verify
main = _migration.main
DROPPED_TABLES = _migration.DROPPED_TABLES
_columns = _migration._columns
_table_exists = _migration._table_exists
_index_exists = _migration._index_exists

# The vocabulary each new column must enforce. Hardcoded here on purpose: this
# test IS the contract — if the migration's CHECK vocab changes, this fails.
VALID_READINESS = (
    "not_ready",
    "ready_for_review",
    "blocked_by_guardrail",
    "ready_for_publish_candidate",
)
VALID_APPROVAL = ("pending_review", "approved_for_candidate", "rejected", "deferred")
VALID_PROMOTION = ("", "review_approved_candidate", "blocked_from_promotion", "deferred")

# ---------------------------------------------------------------------------
# Pre-migration fixture — a representative slice of the live pool schema as it
# stands after PRO-907 (create) + PRO-926 (qa-flow). The migration's drift
# check keys off `promotion_status` + the two PRO-926 tables, not an exact
# column count, so a compact-but-honest fixture exercises every code path.
# ---------------------------------------------------------------------------

_PRE_LEARNED_CARDS = """\
CREATE TABLE learned_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  canonical_code TEXT,
  print_id TEXT,
  contributing_model TEXT,
  card_name TEXT,
  confidence_score REAL,
  learned_from TEXT,
  last_verified TEXT,
  promotion_status TEXT NOT NULL DEFAULT 'experimental'
    CHECK (promotion_status IN ('experimental','review-ready','promoted','rejected')),
  validator_agreement TEXT,
  source_trace_json TEXT DEFAULT NULL,
  derived_from_json TEXT DEFAULT '[]'
);
"""

_PRE_INDEXES = """\
CREATE INDEX idx_learned_cards_identity ON learned_cards(canonical_code, print_id);
CREATE INDEX idx_learned_cards_promotion_status ON learned_cards(promotion_status);
CREATE INDEX idx_learned_cards_contributing_model ON learned_cards(contributing_model);
CREATE INDEX idx_learned_cards_last_verified ON learned_cards(last_verified);
"""

_PRE_QA_FLOW_TABLES = """\
CREATE TABLE door_b_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  print_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  operator TEXT NOT NULL,
  approved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  reason TEXT,
  snapshot_hash TEXT NOT NULL
);
CREATE TABLE score_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  print_id TEXT NOT NULL,
  prior_score INTEGER,
  new_score INTEGER NOT NULL,
  cause TEXT NOT NULL CHECK (cause IN ('assigned','attenuated','corrected','bounced')),
  actor TEXT NOT NULL,
  reason TEXT,
  transition_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""

_PRE_MIGRATION_SQL = _PRE_LEARNED_CARDS + _PRE_INDEXES + _PRE_QA_FLOW_TABLES


def _make_pre_migration_conn() -> sqlite3.Connection:
    """Return an in-memory connection at the pre-PRO-928 schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_PRE_MIGRATION_SQL)
    return conn


def _make_pre_migration_file(path: Path) -> None:
    """Write a pre-PRO-928 pool DB to ``path``."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_PRE_MIGRATION_SQL)
        conn.commit()
    finally:
        conn.close()


def _seed_row(conn: sqlite3.Connection, canonical_code: str, status: str = "experimental") -> None:
    conn.execute(
        "INSERT INTO learned_cards (canonical_code, print_id, contributing_model, "
        "card_name, promotion_status) VALUES (?, ?, ?, ?, ?)",
        (canonical_code, f"{canonical_code}-EN-R-001", "qwen2.5:7b", "Test Card", status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fresh migration — schema shape
# ---------------------------------------------------------------------------


class TestFreshMigration:
    def test_three_new_columns_added(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        cols = _columns(conn, "learned_cards")
        assert "readiness_state" in cols
        assert "approval_state" in cols
        assert "promotion_state" in cols

    def test_promotion_status_dropped(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        assert "promotion_status" not in _columns(conn, "learned_cards")

    def test_promotion_status_index_dropped(self) -> None:
        conn = _make_pre_migration_conn()
        assert _index_exists(conn, "idx_learned_cards_promotion_status")
        apply_migration(conn)
        assert not _index_exists(conn, "idx_learned_cards_promotion_status")

    def test_readiness_state_index_created(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        assert _index_exists(conn, "idx_learned_cards_readiness_state")

    def test_qa_flow_tables_dropped(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        for table in DROPPED_TABLES:
            assert not _table_exists(conn, table), f"{table} should be dropped"

    def test_column_count_net_plus_two(self) -> None:
        conn = _make_pre_migration_conn()
        before = len(_columns(conn, "learned_cards"))
        apply_migration(conn)
        after = len(_columns(conn, "learned_cards"))
        # -1 promotion_status, +3 state columns.
        assert after == before + 2

    def test_unrelated_indexes_survive(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        assert _index_exists(conn, "idx_learned_cards_identity")
        assert _index_exists(conn, "idx_learned_cards_contributing_model")
        assert _index_exists(conn, "idx_learned_cards_last_verified")


# ---------------------------------------------------------------------------
# Data migration — existing rows
# ---------------------------------------------------------------------------


class TestDataMigration:
    def test_existing_rows_preserved(self) -> None:
        conn = _make_pre_migration_conn()
        for code in ("OP01-001", "OP01-002", "OP01-003"):
            _seed_row(conn, code)
        apply_migration(conn)
        count = conn.execute("SELECT COUNT(*) FROM learned_cards").fetchone()[0]
        assert count == 3

    def test_existing_rows_get_pending_combo(self) -> None:
        conn = _make_pre_migration_conn()
        _seed_row(conn, "OP01-001", status="review-ready")
        apply_migration(conn)
        row = conn.execute(
            "SELECT readiness_state, approval_state, promotion_state "
            "FROM learned_cards WHERE canonical_code = ?",
            ("OP01-001",),
        ).fetchone()
        assert row == ("ready_for_review", "pending_review", "")

    def test_row_data_survives(self) -> None:
        conn = _make_pre_migration_conn()
        _seed_row(conn, "OP01-009")
        apply_migration(conn)
        row = conn.execute(
            "SELECT canonical_code, print_id, card_name FROM learned_cards "
            "WHERE canonical_code = ?",
            ("OP01-009",),
        ).fetchone()
        assert row == ("OP01-009", "OP01-009-EN-R-001", "Test Card")


# ---------------------------------------------------------------------------
# CHECK constraints on the three new columns
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    def test_readiness_state_accepts_valid_vocab(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        for value in VALID_READINESS:
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, readiness_state) VALUES (?, ?)",
                (f"OP01-{value}", value),
            )
        conn.commit()

    def test_readiness_state_rejects_invalid(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, readiness_state) VALUES (?, ?)",
                ("OP01-BAD", "bogus"),
            )

    def test_approval_state_accepts_valid_vocab(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        for value in VALID_APPROVAL:
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, approval_state) VALUES (?, ?)",
                (f"OP01-{value}", value),
            )
        conn.commit()

    def test_approval_state_rejects_invalid(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, approval_state) VALUES (?, ?)",
                ("OP01-BAD", "approved"),
            )

    def test_promotion_state_accepts_valid_vocab(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        for value in VALID_PROMOTION:
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, promotion_state) VALUES (?, ?)",
                (f"OP01-{value or 'empty'}", value),
            )
        conn.commit()

    def test_promotion_state_empty_string_is_a_real_value(self) -> None:
        """'' is the pre-promotion state — a member of the vocabulary, not absence."""
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        conn.execute(
            "INSERT INTO learned_cards (canonical_code, promotion_state) VALUES (?, ?)",
            ("OP01-EMPTY", ""),
        )
        conn.commit()
        val = conn.execute(
            "SELECT promotion_state FROM learned_cards WHERE canonical_code = ?",
            ("OP01-EMPTY",),
        ).fetchone()[0]
        assert val == ""

    def test_promotion_state_rejects_invalid(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO learned_cards (canonical_code, promotion_state) VALUES (?, ?)",
                ("OP01-BAD", "promoted"),
            )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_is_already_migrated_false_before(self) -> None:
        conn = _make_pre_migration_conn()
        assert is_already_migrated(conn) is False

    def test_is_already_migrated_true_after(self) -> None:
        conn = _make_pre_migration_conn()
        apply_migration(conn)
        assert is_already_migrated(conn) is True

    def test_main_second_run_is_no_op(self, tmp_path: Path, monkeypatch, capsys) -> None:
        db = tmp_path / "pool.db"
        _make_pre_migration_file(db)
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db)])
        assert main() == 0
        capsys.readouterr()
        # Second run: idempotency gate fires, no-op, exit 0.
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db)])
        assert main() == 0
        assert "no-op" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Drift refusal
# ---------------------------------------------------------------------------


class TestDriftRefusal:
    def test_clean_baseline_has_no_drift(self) -> None:
        conn = _make_pre_migration_conn()
        assert check_drift(conn) == []

    def test_missing_learned_cards_is_drift(self) -> None:
        conn = sqlite3.connect(":memory:")
        problems = check_drift(conn)
        assert any("learned_cards" in p for p in problems)

    def test_missing_promotion_status_is_drift(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE learned_cards (id INTEGER PRIMARY KEY, canonical_code TEXT);"
            + _PRE_QA_FLOW_TABLES
        )
        problems = check_drift(conn)
        assert any("promotion_status" in p for p in problems)

    def test_missing_qa_flow_table_is_drift(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(_PRE_LEARNED_CARDS + _PRE_INDEXES)  # no PRO-926 tables
        problems = check_drift(conn)
        assert any("door_b_overrides" in p for p in problems)
        assert any("score_transitions" in p for p in problems)

    def test_main_refuses_drifted_db(self, tmp_path: Path, monkeypatch) -> None:
        db = tmp_path / "drifted.db"
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE learned_cards (id INTEGER PRIMARY KEY);")
        conn.commit()
        conn.close()
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db)])
        assert main() == 3


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_passes_after_migration(self) -> None:
        conn = _make_pre_migration_conn()
        _seed_row(conn, "OP01-001")
        apply_migration(conn)
        assert verify(conn, expected_rows=1) == []

    def test_verify_catches_row_count_drift(self) -> None:
        conn = _make_pre_migration_conn()
        _seed_row(conn, "OP01-001")
        apply_migration(conn)
        failures = verify(conn, expected_rows=999)
        assert any("row count" in f for f in failures)


# ---------------------------------------------------------------------------
# main() end-to-end
# ---------------------------------------------------------------------------


class TestMainEndToEnd:
    def test_main_applies_and_writes_backup(self, tmp_path: Path, monkeypatch) -> None:
        db = tmp_path / "pool.db"
        _make_pre_migration_file(db)
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db)])
        assert main() == 0
        backups = list(tmp_path.glob("pool.db.bak.*"))
        assert len(backups) == 1, "a pre-migration backup must be written"
        conn = sqlite3.connect(db)
        try:
            assert is_already_migrated(conn)
        finally:
            conn.close()

    def test_main_dry_run_makes_no_changes(self, tmp_path: Path, monkeypatch) -> None:
        db = tmp_path / "pool.db"
        _make_pre_migration_file(db)
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db), "--dry-run"])
        assert main() == 0
        assert list(tmp_path.glob("pool.db.bak.*")) == [], "dry-run must not write a backup"
        conn = sqlite3.connect(db)
        try:
            assert is_already_migrated(conn) is False, "dry-run must not migrate"
        finally:
            conn.close()

    def test_main_missing_db_returns_2(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(tmp_path / "nope.db")])
        assert main() == 2
