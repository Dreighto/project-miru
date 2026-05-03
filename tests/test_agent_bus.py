"""Phase 1 (PRO-290) regression tests for the A2A bus.

Coverage:
- Migration applies cleanly to a fresh sqlite db.
- WAL mode and busy_timeout=5000 are active on every connection.
- Schema has all 18 required columns from the brief.
- Lifecycle: pending -> claimed -> responded.
- Lifecycle: pending -> claimed -> expired -> requeued (under retry limit).
- Lifecycle: pending -> claimed -> expired -> failed (at retry limit).
- Lifecycle: pending -> expired (TTL elapsed without claim).
- Cancel and supersede transitions.
- Exclusive claim under thread contention (the race the bus exists to prevent).
- Concurrent writes don't deadlock under WAL+busy_timeout.

Each test uses its own temp db. No tests touch the real
data/miru_memory.db, so they're safe to run repeatedly without polluting
the production schema.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "tools" / "migrations" / "m005_agent_messages.sql"
TOOLS_DIR = REPO_ROOT / "tools"

# Add tools/ to sys.path so we can import agent_bus directly without
# depending on package layout.
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _apply_migration(db_path: Path) -> None:
    """Apply m005 migration to a fresh sqlite db."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def _fresh_module(db_path: Path):
    """Reload agent_bus with MIRU_MEMORY_DB_PATH pointing at our temp db.

    Per-test isolation: each test gets a fresh module reference so the
    DEFAULT_TTL constants and connection helpers can't leak state.
    """
    os.environ["MIRU_MEMORY_DB_PATH"] = str(db_path)
    import agent_bus

    importlib.reload(agent_bus)
    return agent_bus


class _BusTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "miru_memory_test.db"
        _apply_migration(self.db_path)
        self.bus = _fresh_module(self.db_path)


class MigrationAndPragmaTests(_BusTestBase):
    """Schema + WAL + busy_timeout verification."""

    def test_table_exists_with_required_columns(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute("PRAGMA table_info(agent_messages)").fetchall()
        finally:
            conn.close()
        names = {r[1] for r in rows}
        required = {
            "id",
            "trace_id",
            "ticket_id",
            "from_agent",
            "to_agent",
            "message_type",
            "status",
            "payload_json",
            "priority",
            "created_at",
            "expires_at",
            "response_to_id",
            "claimed_by",
            "claim_token",
            "claimed_at",
            "responded_at",
            "attempt_count",
            "last_error",
        }
        missing = required - names
        self.assertFalse(missing, f"agent_messages missing columns: {missing}")

    def test_status_check_constraint_rejects_invalid_value(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO agent_messages "
                    "(from_agent, to_agent, message_type, status, payload_json) "
                    "VALUES ('a', 'b', 't', 'invalid_state', '{}')"
                )
        finally:
            conn.close()

    def test_wal_mode_active_after_migration(self) -> None:
        conn = self.bus._connect(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal", f"journal_mode={mode!r}, expected wal")

    def test_busy_timeout_set_to_5000_ms(self) -> None:
        conn = self.bus._connect(self.db_path)
        try:
            timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(timeout_ms, 5000)

    def test_pending_index_exists(self) -> None:
        """Hot-path index for claim_next must be present — otherwise queue
        reads degrade to full scan as the table grows."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND tbl_name = 'agent_messages'"
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertIn("idx_agent_messages_pending", indexes)
        self.assertIn("idx_agent_messages_claimed_expiry", indexes)


class EnqueueAndClaimTests(_BusTestBase):
    def test_enqueue_creates_pending_with_payload(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="consult_request",
            payload={"q": "test question"},
            ticket_id="PRO-290",
            db_path=self.db_path,
        )
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertIsNotNone(msg)
        self.assertEqual(msg["status"], "pending")
        self.assertEqual(msg["from_agent"], "cc")
        self.assertEqual(msg["to_agent"], "ch")
        self.assertEqual(msg["ticket_id"], "PRO-290")
        self.assertEqual(json.loads(msg["payload_json"]), {"q": "test question"})

    def test_enqueue_rejects_invalid_priority(self) -> None:
        with self.assertRaises(ValueError):
            self.bus.enqueue(
                from_agent="a",
                to_agent="b",
                message_type="t",
                payload={},
                priority=99,
                db_path=self.db_path,
            )

    def test_claim_next_returns_pending_and_marks_claimed(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="consult_request",
            payload={"q": "x"},
            db_path=self.db_path,
        )
        claimed = self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], msg_id)
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["claimed_by"], "ch")
        self.assertIsNotNone(claimed["claim_token"])
        self.assertEqual(claimed["attempt_count"], 1)
        # Caller-friendly decoded payload.
        self.assertEqual(claimed["payload"], {"q": "x"})

    def test_claim_next_returns_none_when_nothing_pending(self) -> None:
        result = self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        self.assertIsNone(result)

    def test_claim_next_respects_to_agent(self) -> None:
        """A message addressed to ch must not be claimed by codex."""
        self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="t",
            payload={},
            db_path=self.db_path,
        )
        claimed = self.bus.claim_next(agent_id="codex", db_path=self.db_path)
        self.assertIsNone(claimed)
        # And the message stays pending.
        rows = self.bus.list_pending(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")

    def test_claim_next_picks_highest_priority_first(self) -> None:
        ids = []
        for prio in [3, 9, 5, 1, 7]:
            mid = self.bus.enqueue(
                from_agent="cc",
                to_agent="ch",
                message_type="t",
                payload={"p": prio},
                priority=prio,
                db_path=self.db_path,
            )
            ids.append((prio, mid))
        # Claim 5 in order — should drain by priority desc.
        claimed_priorities = []
        for _ in range(5):
            c = self.bus.claim_next(agent_id="ch", db_path=self.db_path)
            self.assertIsNotNone(c)
            claimed_priorities.append(c["priority"])
        self.assertEqual(claimed_priorities, [9, 7, 5, 3, 1])


class ExclusiveClaimTests(_BusTestBase):
    """The race condition the bus exists to prevent: two agents must NOT
    both claim the same pending message."""

    def test_exclusive_claim_under_thread_contention(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="t",
            payload={"i": 0},
            db_path=self.db_path,
        )

        n_threads = 10
        results: list[dict | None] = [None] * n_threads
        barrier = threading.Barrier(n_threads)

        def worker(idx: int) -> None:
            barrier.wait()  # synchronize start
            results[idx] = self.bus.claim_next(agent_id="ch", db_path=self.db_path)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        winners = [r for r in results if r is not None]
        self.assertEqual(
            len(winners),
            1,
            f"exactly one thread must claim; got {len(winners)} winners",
        )
        self.assertEqual(winners[0]["id"], msg_id)
        # All other threads got None.
        self.assertEqual(sum(1 for r in results if r is None), n_threads - 1)

        # And the message is in 'claimed' state with attempt_count=1.
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "claimed")
        self.assertEqual(msg["attempt_count"], 1)


class RespondAndCancelTests(_BusTestBase):
    def test_respond_threads_response_back_to_request(self) -> None:
        req_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="consult_request",
            payload={"q": "ping"},
            trace_id="cc-PRO-290-aa-bb",
            ticket_id="PRO-290",
            db_path=self.db_path,
        )
        claimed = self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        resp_id = self.bus.respond(
            message_id=req_id,
            claim_token=claimed["claim_token"],
            response_payload={"a": "pong"},
            db_path=self.db_path,
        )
        # Original is responded.
        original = self.bus.get_message(req_id, db_path=self.db_path)
        self.assertEqual(original["status"], "responded")
        self.assertIsNotNone(original["responded_at"])
        # Response carries the original's trace + ticket and is threaded.
        response = self.bus.get_message(resp_id, db_path=self.db_path)
        self.assertEqual(response["status"], "pending")
        self.assertEqual(response["from_agent"], "ch")
        self.assertEqual(response["to_agent"], "cc")
        self.assertEqual(response["response_to_id"], req_id)
        self.assertEqual(response["trace_id"], "cc-PRO-290-aa-bb")
        self.assertEqual(response["ticket_id"], "PRO-290")
        self.assertEqual(response["message_type"], "consult_response")
        self.assertEqual(json.loads(response["payload_json"]), {"a": "pong"})

    def test_respond_rejects_wrong_token(self) -> None:
        req_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="consult_request",
            payload={},
            db_path=self.db_path,
        )
        self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        with self.assertRaises(PermissionError):
            self.bus.respond(
                message_id=req_id,
                claim_token="not_the_real_token",
                response_payload={},
                db_path=self.db_path,
            )

    def test_respond_rejects_when_not_claimed(self) -> None:
        req_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="t",
            payload={},
            db_path=self.db_path,
        )
        with self.assertRaises(RuntimeError):
            self.bus.respond(
                message_id=req_id,
                claim_token="anything",
                response_payload={},
                db_path=self.db_path,
            )

    def test_cancel_works_when_pending(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="t",
            payload={},
            db_path=self.db_path,
        )
        self.assertTrue(
            self.bus.cancel(message_id=msg_id, reason="user requested", db_path=self.db_path)
        )
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "cancelled")

    def test_cancel_works_when_claimed(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        self.assertTrue(self.bus.cancel(message_id=msg_id, db_path=self.db_path))
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "cancelled")

    def test_cancel_noop_on_terminal_status(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        claimed = self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        self.bus.respond(
            message_id=msg_id,
            claim_token=claimed["claim_token"],
            response_payload={},
            db_path=self.db_path,
        )
        # Already responded — cancel should not transition.
        self.assertFalse(self.bus.cancel(message_id=msg_id, db_path=self.db_path))
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "responded")

    def test_supersede_works(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        self.assertTrue(
            self.bus.supersede(
                message_id=msg_id, reason="newer message exists", db_path=self.db_path
            )
        )
        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "superseded")


class SweeperTests(_BusTestBase):
    """Stale-message lifecycle: claimed-but-not-responded must not stick
    forever, and unclaimed messages past TTL must transition out of pending."""

    def _backdate_expires(self, msg_id: str, seconds_ago: int) -> None:
        """Force a row's expires_at into the past for the sweeper to find."""
        past = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("UPDATE agent_messages SET expires_at = ? WHERE id = ?", (past, msg_id))
            conn.commit()
        finally:
            conn.close()

    def test_sweep_requeues_expired_claim_when_under_retry_limit(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        # Force the claim into the past.
        self._backdate_expires(msg_id, seconds_ago=120)

        counts = self.bus.sweep_stale(retry_limit=3, db_path=self.db_path)
        self.assertEqual(counts["requeued"], 1)
        self.assertEqual(counts["failed"], 0)

        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "pending")
        self.assertEqual(msg["attempt_count"], 1)
        # Claim fields cleared so the next claim_next can pick it up cleanly.
        self.assertIsNone(msg["claimed_by"])
        self.assertIsNone(msg["claim_token"])
        self.assertIsNone(msg["claimed_at"])
        # last_error captures the requeue reason.
        self.assertIn("expired", (msg["last_error"] or "").lower())

    def test_sweep_marks_failed_at_retry_limit(self) -> None:
        """After retry_limit attempts, a perpetually-expiring claim is marked
        failed permanently."""
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        # Loop: claim, expire, sweep — until status becomes 'failed'.
        retry_limit = 3
        for _ in range(retry_limit + 1):
            msg = self.bus.get_message(msg_id, db_path=self.db_path)
            if msg["status"] == "failed":
                break
            if msg["status"] == "pending":
                self.bus.claim_next(agent_id="ch", db_path=self.db_path)
                self._backdate_expires(msg_id, seconds_ago=120)
            self.bus.sweep_stale(retry_limit=retry_limit, db_path=self.db_path)

        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "failed", f"after retries, status={msg['status']!r}")
        self.assertGreaterEqual(msg["attempt_count"], retry_limit)
        self.assertIn("retry limit", (msg["last_error"] or "").lower())

    def test_sweep_marks_pending_message_expired_when_ttl_elapses(self) -> None:
        msg_id = self.bus.enqueue(
            from_agent="cc",
            to_agent="ch",
            message_type="t",
            payload={},
            ttl_seconds=1,
            db_path=self.db_path,
        )
        # Force expires_at into the past instead of sleeping.
        self._backdate_expires(msg_id, seconds_ago=120)

        counts = self.bus.sweep_stale(db_path=self.db_path)
        self.assertEqual(counts["expired_pending"], 1)
        self.assertEqual(counts["requeued"], 0)
        self.assertEqual(counts["failed"], 0)

        msg = self.bus.get_message(msg_id, db_path=self.db_path)
        self.assertEqual(msg["status"], "expired")

    def test_sweep_is_idempotent(self) -> None:
        """Running the sweeper twice in a row with no new state changes
        produces zero counts on the second run."""
        msg_id = self.bus.enqueue(
            from_agent="cc", to_agent="ch", message_type="t", payload={}, db_path=self.db_path
        )
        self.bus.claim_next(agent_id="ch", db_path=self.db_path)
        self._backdate_expires(msg_id, seconds_ago=120)

        first = self.bus.sweep_stale(db_path=self.db_path)
        second = self.bus.sweep_stale(db_path=self.db_path)

        self.assertEqual(first["requeued"], 1)
        self.assertEqual(second, {"requeued": 0, "failed": 0, "expired_pending": 0})


class ConcurrentWriteTests(_BusTestBase):
    """WAL+busy_timeout under contention: many concurrent writers must
    succeed without raising sqlite3.OperationalError('database is locked')."""

    def test_many_concurrent_enqueues_all_land(self) -> None:
        n_threads = 4
        per_thread = 25
        errors: list[Exception] = []
        lock = threading.Lock()

        def writer(tid: int) -> None:
            try:
                for i in range(per_thread):
                    self.bus.enqueue(
                        from_agent=f"thread-{tid}",
                        to_agent="ch",
                        message_type="t",
                        payload={"tid": tid, "i": i},
                        db_path=self.db_path,
                    )
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.time() - start

        self.assertEqual(errors, [], f"concurrent writes raised: {errors}")
        # Verify all rows landed.
        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, n_threads * per_thread)
        # Wallclock sanity: under WAL with a sane CPU this completes well
        # under the 5s busy_timeout window.
        self.assertLess(elapsed, 30, f"concurrent writes took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
