"""
agent_bus.py — durable asynchronous A2A message bus client (PRO-290, Phase 1).

Wraps the agent_messages table in data/miru_memory.db. Every connection sets
WAL mode and busy_timeout=5000 so concurrent writers do not deadlock.

Usage from a worker (claude-code, codex, gemini, etc.):

    from tools import agent_bus

    # Send a consult_request
    msg_id = agent_bus.enqueue(
        from_agent="cc",
        to_agent="ch",
        message_type="consult_request",
        payload={"question": "Should I bundle PR #82 with #83?"},
        priority=7,
        ttl_seconds=600,
        trace_id="cc-PRO-290-aaa-bbb",
        ticket_id="PRO-290",
    )

    # Receiver claims the next pending message
    msg = agent_bus.claim_next(agent_id="ch", claim_ttl_seconds=120)
    if msg is not None:
        # ... process payload ...
        agent_bus.respond(
            message_id=msg["id"],
            claim_token=msg["claim_token"],
            response_payload={"answer": "Keep separate PRs."},
        )

    # Periodic sweeper (run via cron/n8n/schedule):
    counts = agent_bus.sweep_stale(retry_limit=3)
    # -> {"requeued": N, "failed": M, "expired_pending": K}

The sweeper is the deadlock prevention layer: claimed messages whose
claim TTL has elapsed get requeued (if attempt_count < retry_limit) or
marked failed. This is the contract that lets us trust suspend-consult-
resume cycles to terminate.

Design boundaries (Phase 1 scope):
  * No new MCP tools. Workers invoke this module directly via Python.
    Tool Access and Canon Authority are separate gates; Phase 1 does
    not touch either.
  * No canon-write behavior. The bus is data-only.
  * No Subagent Isolation hooks (Phase 3). No Ingress Classifier hooks
    (Phase 4). No emit_decision integration (Phase 2).

Append-only invariant: this table is NOT append-only — claim/respond
flows mutate row state. The append-only invariant test in
tests/test_jsonl_append_only_invariant.py applies to data/*.jsonl files
only, not to miru_memory.db tables.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---- Configuration ---------------------------------------------------------

DEFAULT_TTL_SECONDS = 3600  # message TTL on enqueue
DEFAULT_CLAIM_TTL_SECONDS = 600  # claim TTL on claim_next
DEFAULT_REQUEUE_TTL_SECONDS = 3600  # fresh window when sweeper requeues
DEFAULT_RETRY_LIMIT = 3  # max attempts before marking failed
BUSY_TIMEOUT_MS = 5000

# ---- Path resolution -------------------------------------------------------


def _repo_root() -> Path:
    """Return the main repo root, works from any linked worktree.

    Mirrors the pattern in tools/emit_completion.py so the bus writes to
    the canonical miru_memory.db regardless of which worktree calls it.
    """
    script_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=str(script_dir),
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = (script_dir / result.stdout.strip()).resolve()
            return common_dir.parent
    except Exception:
        pass
    return script_dir.parent


def _default_db_path() -> Path:
    """Resolve the miru_memory.db path. Honors MIRU_MEMORY_DB_PATH override
    so tests can point at a temp db without monkey-patching."""
    override = os.environ.get("MIRU_MEMORY_DB_PATH", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (_repo_root() / p).resolve()
    return _repo_root() / "data" / "miru_memory.db"


# ---- Connection ------------------------------------------------------------


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with WAL + busy_timeout=5000 + foreign_keys ON.

    isolation_level=None means autocommit; callers explicitly BEGIN
    IMMEDIATE for racy transactions. This makes the locking story
    explicit instead of relying on Python's implicit transaction.
    """
    p = db_path or _default_db_path()
    if not p.exists():
        raise FileNotFoundError(
            f"agent_bus: miru_memory.db not found at {p}. "
            f"Apply migration tools/migrations/m005_agent_messages.sql first."
        )
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---- Time helpers ----------------------------------------------------------


def _utc_iso(now: datetime | None = None) -> str:
    """ISO 8601 UTC with milliseconds, matching SQLite default format."""
    t = now or datetime.now(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _utc_iso_offset(seconds: int, *, now: datetime | None = None) -> str:
    base = now or datetime.now(UTC)
    return _utc_iso(base + timedelta(seconds=seconds))


# ---- Public API ------------------------------------------------------------


def enqueue(
    *,
    from_agent: str,
    to_agent: str,
    message_type: str,
    payload: dict[str, Any],
    priority: int = 5,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    trace_id: str | None = None,
    ticket_id: str | None = None,
    response_to_id: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Insert a new pending message. Returns the new message id.

    payload is JSON-serialised into payload_json. Reasonable upper bound:
    16 KB per payload. Larger payloads should be written to a file and
    referenced by path inside payload.
    """
    if priority < 0 or priority > 10:
        raise ValueError(f"priority must be 0..10, got {priority}")
    if not from_agent or not to_agent or not message_type:
        raise ValueError("from_agent, to_agent, message_type are required")

    payload_str = json.dumps(payload, ensure_ascii=False)
    expires_at = _utc_iso_offset(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None

    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO agent_messages (
                trace_id, ticket_id, from_agent, to_agent, message_type,
                status, payload_json, priority, expires_at, response_to_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            RETURNING id
            """,
            (
                trace_id,
                ticket_id,
                from_agent,
                to_agent,
                message_type,
                payload_str,
                priority,
                expires_at,
                response_to_id,
            ),
        )
        row = cur.fetchone()
        return row["id"]
    finally:
        conn.close()


def claim_next(
    *,
    agent_id: str,
    claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the highest-priority pending message addressed to
    agent_id. Returns the message dict (with claim_token to use on respond)
    or None if nothing is pending.

    Race-safe: the UPDATE...WHERE status='pending' is atomic. Concurrent
    claimers may both target the same row; only one's UPDATE matches the
    WHERE and the others' rowcount is 0.
    """
    if not agent_id:
        raise ValueError("agent_id is required")

    conn = _connect(db_path)
    try:
        # Find a candidate. Don't hold a lock yet — the UPDATE below
        # provides the atomicity. Worst case we re-loop on contention.
        candidate = conn.execute(
            """
            SELECT id, attempt_count
            FROM agent_messages
            WHERE to_agent = ? AND status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (agent_id,),
        ).fetchone()
        if candidate is None:
            return None
        msg_id = candidate["id"]

        claim_token = secrets.token_hex(16)
        now = datetime.now(UTC)
        claimed_at = _utc_iso(now)
        claim_expires_at = _utc_iso_offset(claim_ttl_seconds, now=now)

        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'claimed',
                claimed_by = ?,
                claim_token = ?,
                claimed_at = ?,
                expires_at = ?,
                attempt_count = attempt_count + 1
            WHERE id = ? AND status = 'pending'
            """,
            (agent_id, claim_token, claimed_at, claim_expires_at, msg_id),
        )
        if cur.rowcount != 1:
            # Lost the race — caller can retry by calling claim_next again.
            return None

        # Read back the full row for the caller. Must include claim_token
        # because respond() requires it as proof-of-claim.
        full = conn.execute("SELECT * FROM agent_messages WHERE id = ?", (msg_id,)).fetchone()
        result = dict(full)
        # Decode payload_json back to a dict for caller convenience.
        try:
            result["payload"] = json.loads(result["payload_json"])
        except (TypeError, json.JSONDecodeError):
            result["payload"] = None
        return result
    finally:
        conn.close()


def respond(
    *,
    message_id: str,
    claim_token: str,
    response_payload: dict[str, Any],
    response_message_type: str = "consult_response",
    response_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    db_path: Path | None = None,
) -> str:
    """Mark message responded and create a threaded response message.

    Verifies claim_token matches before accepting the response — prevents
    a stale or stolen claim from completing. Returns the new response
    message id.
    """
    conn = _connect(db_path)
    try:
        original = conn.execute(
            "SELECT id, status, claim_token, claimed_by, from_agent, to_agent, "
            "       trace_id, ticket_id "
            "FROM agent_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if original is None:
            raise LookupError(f"agent_bus.respond: message {message_id!r} not found")
        if original["status"] != "claimed":
            raise RuntimeError(
                f"agent_bus.respond: message {message_id!r} is in status "
                f"{original['status']!r}, not 'claimed'"
            )
        if original["claim_token"] != claim_token:
            raise PermissionError(
                f"agent_bus.respond: claim_token mismatch for message {message_id!r}"
            )

        responded_at = _utc_iso()

        # Mark original responded.
        conn.execute(
            """
            UPDATE agent_messages
            SET status = 'responded',
                responded_at = ?
            WHERE id = ? AND claim_token = ? AND status = 'claimed'
            """,
            (responded_at, message_id, claim_token),
        )

        # Insert response message (threaded via response_to_id). Direction
        # flips: from_agent of response = to_agent of original.
        response_payload_str = json.dumps(response_payload, ensure_ascii=False)
        expires_at = _utc_iso_offset(response_ttl_seconds) if response_ttl_seconds > 0 else None
        cur = conn.execute(
            """
            INSERT INTO agent_messages (
                trace_id, ticket_id, from_agent, to_agent, message_type,
                status, payload_json, priority, expires_at, response_to_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 5, ?, ?)
            RETURNING id
            """,
            (
                original["trace_id"],
                original["ticket_id"],
                original["to_agent"],
                original["from_agent"],
                response_message_type,
                response_payload_str,
                expires_at,
                message_id,
            ),
        )
        return cur.fetchone()["id"]
    finally:
        conn.close()


def cancel(
    *,
    message_id: str,
    reason: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Mark a pending or claimed message as cancelled. Returns True if the
    state transition happened, False if the message was already in a terminal
    state."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'cancelled',
                last_error = COALESCE(?, last_error)
            WHERE id = ? AND status IN ('pending', 'claimed')
            """,
            (reason, message_id),
        )
        return cur.rowcount == 1
    finally:
        conn.close()


def supersede(
    *,
    message_id: str,
    reason: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Mark a pending or claimed message as superseded — usually because
    a later message obsoletes its question. Returns True on transition."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'superseded',
                last_error = COALESCE(?, last_error)
            WHERE id = ? AND status IN ('pending', 'claimed')
            """,
            (reason, message_id),
        )
        return cur.rowcount == 1
    finally:
        conn.close()


def sweep_stale(
    *,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    requeue_ttl_seconds: int = DEFAULT_REQUEUE_TTL_SECONDS,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Sweeper. For all expired claimed messages: requeue if under retry
    limit, else mark failed. Also marks long-pending unclaimed messages
    as expired.

    Returns counts: {'requeued': N, 'failed': M, 'expired_pending': K}.

    Idempotent: safe to run repeatedly. Designed for daily n8n cron OR
    a Windows Scheduled Task. Run frequency should be << CLAIM_TTL so
    stuck claims don't sit too long.
    """
    now = now or datetime.now(UTC)
    now_iso = _utc_iso(now)
    fresh_window = _utc_iso_offset(requeue_ttl_seconds, now=now)

    conn = _connect(db_path)
    try:
        # 1. Expired claims that still have retry budget -> requeue.
        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'pending',
                claimed_by = NULL,
                claim_token = NULL,
                claimed_at = NULL,
                expires_at = ?,
                last_error = COALESCE(last_error, 'claim expired; requeued')
            WHERE status = 'claimed'
              AND expires_at IS NOT NULL
              AND expires_at < ?
              AND attempt_count < ?
            """,
            (fresh_window, now_iso, retry_limit),
        )
        requeued = cur.rowcount or 0

        # 2. Expired claims at retry limit -> failed.
        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'failed',
                last_error = COALESCE(last_error, '') ||
                             CASE WHEN COALESCE(last_error, '') = '' THEN '' ELSE '; ' END ||
                             'exceeded retry limit (' || attempt_count || ' attempts)'
            WHERE status = 'claimed'
              AND expires_at IS NOT NULL
              AND expires_at < ?
              AND attempt_count >= ?
            """,
            (now_iso, retry_limit),
        )
        failed = cur.rowcount or 0

        # 3. Long-pending unclaimed messages whose original TTL elapsed.
        cur = conn.execute(
            """
            UPDATE agent_messages
            SET status = 'expired',
                last_error = COALESCE(last_error, 'message TTL elapsed without claim')
            WHERE status = 'pending'
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (now_iso,),
        )
        expired_pending = cur.rowcount or 0

        return {
            "requeued": requeued,
            "failed": failed,
            "expired_pending": expired_pending,
        }
    finally:
        conn.close()


# ---- Read helpers (audit / ops) --------------------------------------------


def get_message(message_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_pending(
    *,
    to_agent: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if to_agent is None:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE status = 'pending' "
                "ORDER BY priority DESC, created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_messages "
                "WHERE to_agent = ? AND status = 'pending' "
                "ORDER BY priority DESC, created_at ASC LIMIT ?",
                (to_agent, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- CLI for manual ops ----------------------------------------------------


def _main() -> int:
    """Tiny CLI: `python tools/agent_bus.py sweep` runs the sweeper.

    Useful for cron / Windows Scheduled Task. JSON output to stdout.
    """
    if len(sys.argv) < 2 or sys.argv[1] != "sweep":
        print("usage: python tools/agent_bus.py sweep", file=sys.stderr)
        return 2
    counts = sweep_stale()
    print(json.dumps({"ok": True, "counts": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
