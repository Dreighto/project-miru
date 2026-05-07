"""
task_deps.py — Cross-worker dependency bus (PRO-311).

Workers register inter-ticket dependencies and check upstream readiness
before starting work. Upstream workers mark themselves ready (with optional
artifact payload) so downstream workers know when to proceed.

Usage:

    from tools import task_deps

    # Downstream registers: "PRO-320 depends on PRO-319"
    task_deps.register("PRO-320", depends_on="PRO-319")

    # Upstream completes and signals readiness with contract artifact
    task_deps.mark_ready("PRO-319", artifact={"schema": "v2", "entry": "api.py:42"})

    # Downstream checks before starting
    if not task_deps.all_ready("PRO-320"):
        blockers = task_deps.get_blockers("PRO-320")
        # -> [{"depends_on": "PRO-319", "status": "pending", ...}]
        # Worker emits STATUS: BLOCKED_ON: PRO-319

Shares the same miru_memory.db as agent_bus.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_MS = 5000


def _repo_root() -> Path:
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
    override = os.environ.get("MIRU_MEMORY_DB_PATH", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (_repo_root() / p).resolve()
    return _repo_root() / "data" / "miru_memory.db"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or _default_db_path()
    if not p.exists():
        raise FileNotFoundError(f"task_deps: miru_memory.db not found at {p}")
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _utc_iso() -> str:
    t = datetime.now(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def ensure_table(db_path: Path | None = None) -> None:
    """Create task_dependencies table if it doesn't exist. Idempotent."""
    migration = Path(__file__).resolve().parent / "migrations" / "m006_task_dependencies.sql"
    sql = migration.read_text(encoding="utf-8")
    p = db_path or _default_db_path()
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    try:
        conn.executescript(sql)
    finally:
        conn.close()


# ---- Public API ------------------------------------------------------------


def register(
    ticket_id: str,
    *,
    depends_on: str,
    trace_id: str | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Register that ticket_id depends on depends_on. Returns the dep row id.

    Idempotent: if the same (ticket_id, depends_on) pair already exists,
    returns the existing id without modification.
    """
    if not ticket_id or not depends_on:
        raise ValueError("ticket_id and depends_on are required")
    if ticket_id == depends_on:
        raise ValueError("a ticket cannot depend on itself")

    conn = _connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM task_dependencies WHERE ticket_id = ? AND depends_on = ?",
            (ticket_id, depends_on),
        ).fetchone()
        if existing:
            return existing["id"]

        cur = conn.execute(
            """
            INSERT INTO task_dependencies (ticket_id, depends_on, trace_id, notes)
            VALUES (?, ?, ?, ?)
            RETURNING id
            """,
            (ticket_id, depends_on, trace_id, notes),
        )
        return cur.fetchone()["id"]
    finally:
        conn.close()


def mark_ready(
    ticket_id: str,
    *,
    artifact: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> int:
    """Mark all pending dependencies ON ticket_id as ready.

    Called by the upstream worker when it completes. Returns the number of
    dependency rows transitioned to 'ready'.
    """
    if not ticket_id:
        raise ValueError("ticket_id is required")

    artifact_json = json.dumps(artifact, ensure_ascii=False) if artifact else None
    resolved_at = _utc_iso()

    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE task_dependencies
            SET status = 'ready',
                artifact_json = COALESCE(?, artifact_json),
                resolved_at = ?
            WHERE depends_on = ? AND status = 'pending'
            """,
            (artifact_json, resolved_at, ticket_id),
        )
        return cur.rowcount or 0
    finally:
        conn.close()


def mark_failed(
    ticket_id: str,
    *,
    notes: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Mark all pending dependencies ON ticket_id as failed.

    Called when the upstream worker fails. Returns the count transitioned.
    """
    if not ticket_id:
        raise ValueError("ticket_id is required")

    resolved_at = _utc_iso()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE task_dependencies
            SET status = 'failed',
                resolved_at = ?,
                notes = COALESCE(?, notes)
            WHERE depends_on = ? AND status = 'pending'
            """,
            (resolved_at, notes, ticket_id),
        )
        return cur.rowcount or 0
    finally:
        conn.close()


def cancel(
    ticket_id: str,
    depends_on: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Cancel a specific dependency. Returns True if transitioned."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE task_dependencies
            SET status = 'cancelled', resolved_at = ?
            WHERE ticket_id = ? AND depends_on = ? AND status = 'pending'
            """,
            (_utc_iso(), ticket_id, depends_on),
        )
        return (cur.rowcount or 0) == 1
    finally:
        conn.close()


def check(ticket_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all dependency rows for ticket_id."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM task_dependencies WHERE ticket_id = ? ORDER BY created_at",
            (ticket_id,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("artifact_json"):
                try:
                    d["artifact"] = json.loads(d["artifact_json"])
                except (json.JSONDecodeError, TypeError):
                    d["artifact"] = None
            else:
                d["artifact"] = None
            results.append(d)
        return results
    finally:
        conn.close()


def all_ready(ticket_id: str, *, db_path: Path | None = None) -> bool:
    """Return True if all dependencies for ticket_id are in 'ready' state.

    Returns True if ticket_id has no dependencies registered (vacuously true).
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) as ready_count
            FROM task_dependencies WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
        total = row["total"] or 0
        ready_count = row["ready_count"] or 0
        return total == 0 or total == ready_count
    finally:
        conn.close()


def get_blockers(ticket_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return dependency rows that are NOT ready (pending or failed)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM task_dependencies
            WHERE ticket_id = ? AND status IN ('pending', 'failed')
            ORDER BY created_at
            """,
            (ticket_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_dependents(ticket_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all tickets that depend on ticket_id (pending only)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM task_dependencies
            WHERE depends_on = ? AND status = 'pending'
            ORDER BY created_at
            """,
            (ticket_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_artifact(
    ticket_id: str, depends_on: str, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Retrieve the artifact published by an upstream dependency.

    Returns the parsed artifact dict, or None if no artifact was published
    or the dependency doesn't exist.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT artifact_json FROM task_dependencies WHERE ticket_id = ? AND depends_on = ?",
            (ticket_id, depends_on),
        ).fetchone()
        if row and row["artifact_json"]:
            try:
                return json.loads(row["artifact_json"])
            except (json.JSONDecodeError, TypeError):
                return None
        return None
    finally:
        conn.close()
