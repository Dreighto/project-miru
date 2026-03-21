#!/usr/bin/env python
"""Official rules / legality / block-rotation / rulings Q&A intelligence layer.

Structured storage for Bandai-approved official rules notices, legality history,
format/block context, and official card rulings (Q&A). Ethics-first: only official
sources; current vs upcoming vs historical by effective date; no fabricated state.

Use:
- get_current_legality_state(), get_upcoming_legality_state()
- is_effective_now(), get_current_format_context(), block_rotation_active_for()
- get_current_rulings_for_card(), get_rulings_for_topic(), search_official_rulings()
- get_best_official_ruling_match(), format_source_citation(), get_rulings_for_card_with_citations()
- Staged import: ingest_notice_json(), ingest_legality_row(); rulings via tools.miru_official_rulings_ingest
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_DB_PATH = PROJECT_ROOT / "data" / "miru_official_rules.db"

# Notice types for official_rule_notices
NOTICE_TYPE_RULES_UPDATE = "rules_update"
NOTICE_TYPE_BANLIST = "banlist"
NOTICE_TYPE_BLOCK_UPDATE = "block_update"
NOTICE_TYPE_RULING = "ruling"
NOTICE_TYPE_ERRATA = "errata"
NOTICE_TYPE_TOURNAMENT_RULE = "tournament_rule"
NOTICE_TYPE_OTHER = "other"
NOTICE_TYPES = (
    NOTICE_TYPE_RULES_UPDATE,
    NOTICE_TYPE_BANLIST,
    NOTICE_TYPE_BLOCK_UPDATE,
    NOTICE_TYPE_RULING,
    NOTICE_TYPE_ERRATA,
    NOTICE_TYPE_TOURNAMENT_RULE,
    NOTICE_TYPE_OTHER,
)

# Status for notices and history
STATUS_CURRENT = "current"
STATUS_UPCOMING = "upcoming"
STATUS_HISTORICAL = "historical"
STATUS_SUPERSEDED = "superseded"

# Source types for official rulings (Q&A / FAQ / errata)
RULING_SOURCE_FAQ = "faq"
RULING_SOURCE_RULING = "ruling"
RULING_SOURCE_ERRATA = "errata"
RULING_SOURCE_RULES_UPDATE = "rules_update"
RULING_SOURCE_OTHER_OFFICIAL = "other_official"
RULING_SOURCE_TYPES = (
    RULING_SOURCE_FAQ,
    RULING_SOURCE_RULING,
    RULING_SOURCE_ERRATA,
    RULING_SOURCE_RULES_UPDATE,
    RULING_SOURCE_OTHER_OFFICIAL,
)


def _conn(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _ensure_rules_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_conn(path)) as conn:
        ensure_official_rules_schema(conn)
        conn.commit()


def ensure_official_rules_schema(conn: sqlite3.Connection) -> None:
    """Create all official rules tables if missing. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS official_rule_notices (
            notice_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            region TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT 'standard',
            notice_type TEXT NOT NULL DEFAULT 'other',
            published_at TEXT NOT NULL DEFAULT '',
            effective_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'current',
            summary TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_official_rule_notices_effective
            ON official_rule_notices(effective_at, status);
        CREATE INDEX IF NOT EXISTS idx_official_rule_notices_format_status
            ON official_rule_notices(format_name, status);

        CREATE TABLE IF NOT EXISTS official_legality_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_code TEXT NOT NULL,
            format_name TEXT NOT NULL DEFAULT 'standard',
            region TEXT NOT NULL DEFAULT '',
            legality_state TEXT NOT NULL,
            effective_start TEXT NOT NULL DEFAULT '',
            effective_end TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            notice_id TEXT NOT NULL DEFAULT '',
            is_current INTEGER NOT NULL DEFAULT 0,
            is_upcoming INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            UNIQUE(card_code, format_name, region, effective_start)
        );
        CREATE INDEX IF NOT EXISTS idx_official_legality_history_card_format
            ON official_legality_history(card_code, format_name, region);
        CREATE INDEX IF NOT EXISTS idx_official_legality_history_current
            ON official_legality_history(card_code, format_name, region, is_current) WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_official_legality_history_upcoming
            ON official_legality_history(card_code, format_name, region, is_upcoming) WHERE is_upcoming = 1;

        CREATE TABLE IF NOT EXISTS official_format_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT 'standard',
            block_rotation_active INTEGER NOT NULL DEFAULT 0,
            effective_at TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            notice_id TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_official_format_context_region_format
            ON official_format_context(region, format_name);

        CREATE TABLE IF NOT EXISTS official_card_rulings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_code TEXT NULL,
            topic_key TEXT NOT NULL DEFAULT '',
            ruling_text TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            effective_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'current',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_official_card_rulings_card
            ON official_card_rulings(card_code) WHERE card_code IS NOT NULL;
    """)
    _migrate_card_rulings_columns(conn)


def _migrate_card_rulings_columns(conn: sqlite3.Connection) -> None:
    """Add extended columns to official_card_rulings if missing (idempotent)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(official_card_rulings)").fetchall()}
    new_columns = [
        ("ruling_id", "TEXT"),
        ("question_text", "TEXT NOT NULL DEFAULT ''"),
        ("normalized_summary", "TEXT NOT NULL DEFAULT ''"),
        ("source_type", "TEXT NOT NULL DEFAULT 'other_official'"),
        ("source_title", "TEXT NOT NULL DEFAULT ''"),
        ("source_url", "TEXT NOT NULL DEFAULT ''"),
        ("source_anchor", "TEXT NOT NULL DEFAULT ''"),
        ("tags", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, spec in new_columns:
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE official_card_rulings ADD COLUMN {name} {spec}")
            except sqlite3.OperationalError:
                pass
    # Index for ruling_id lookups and topic
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_official_card_rulings_ruling_id "
            "ON official_card_rulings(ruling_id) WHERE ruling_id IS NOT NULL AND ruling_id != ''"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_official_card_rulings_topic "
            "ON official_card_rulings(topic_key) WHERE topic_key != ''"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_official_card_rulings_status "
            "ON official_card_rulings(status)"
        )
    except sqlite3.OperationalError:
        pass


def is_effective_now(effective_at: str | None) -> bool:
    """True if effective_at is empty (interpret as now) or parsed date <= today UTC."""
    s = (effective_at or "").strip()
    if not s:
        return True
    try:
        # Support ISO date or YYYY-MM-DD
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return dt <= now
    except Exception:
        return False


def parse_effective_date(effective_at: str | None) -> datetime | None:
    """Parse effective_at to datetime (UTC) or None if invalid/empty."""
    s = (effective_at or "").strip()
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_current_legality_state(
    rules_db_path: Path,
    card_code: str,
    format_name: str = "standard",
    region: str = "",
) -> dict[str, Any] | None:
    """Return the current official legality record for (card_code, format, region) or None."""
    path = Path(rules_db_path)
    if not path.is_file():
        return None
    code = (card_code or "").strip().upper()
    fmt = (format_name or "standard").strip().lower()
    reg = (region or "").strip()
    if not code:
        return None
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT card_code, format_name, region, legality_state, effective_start, effective_end,
                          source_id, source_reference, notice_id, is_current, is_upcoming, notes, updated_at
                   FROM official_legality_history
                   WHERE card_code = ? AND format_name = ? AND region = ? AND is_current = 1
                   LIMIT 1""",
                (code, fmt, reg),
            ).fetchone()
            if row:
                return dict(row)
    except Exception:
        pass
    return None


def get_upcoming_legality_state(
    rules_db_path: Path,
    card_code: str,
    format_name: str = "standard",
    region: str = "",
) -> list[dict[str, Any]]:
    """Return list of upcoming official legality records for (card_code, format, region)."""
    path = Path(rules_db_path)
    if not path.is_file():
        return []
    code = (card_code or "").strip().upper()
    fmt = (format_name or "standard").strip().lower()
    reg = (region or "").strip()
    if not code:
        return []
    out: list[dict[str, Any]] = []
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                """SELECT card_code, format_name, region, legality_state, effective_start, effective_end,
                          source_id, source_reference, notice_id, is_current, is_upcoming, notes, updated_at
                   FROM official_legality_history
                   WHERE card_code = ? AND format_name = ? AND region = ? AND is_upcoming = 1
                   ORDER BY effective_start""",
                (code, fmt, reg),
            ):
                out.append(dict(row))
    except Exception:
        pass
    return out


def get_current_format_context(
    rules_db_path: Path,
    region: str = "",
    format_name: str = "standard",
) -> dict[str, Any] | None:
    """Return current format/block context for (region, format) or None."""
    path = Path(rules_db_path)
    if not path.is_file():
        return None
    reg = (region or "").strip()
    fmt = (format_name or "standard").strip().lower()
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT region, format_name, block_rotation_active, effective_at, source_id, source_reference, notice_id, notes, updated_at
                   FROM official_format_context
                   WHERE region = ? AND format_name = ?
                   ORDER BY effective_at DESC
                   LIMIT 1""",
                (reg, fmt),
            ).fetchone()
            if row:
                return dict(row)
    except Exception:
        pass
    return None


def block_rotation_active_for(
    rules_db_path: Path,
    region: str = "",
    format_name: str = "standard",
) -> bool:
    """True if block rotation is marked active for (region, format)."""
    ctx = get_current_format_context(rules_db_path, region=region, format_name=format_name)
    if not ctx:
        return False
    return bool(ctx.get("block_rotation_active"))


def insert_legality_history(
    rules_db_path: Path,
    card_code: str,
    format_name: str,
    legality_state: str,
    *,
    region: str = "",
    effective_start: str = "",
    effective_end: str = "",
    source_id: str = "",
    source_reference: str = "",
    notice_id: str = "",
    is_current: int = 0,
    is_upcoming: int = 0,
    notes: str = "",
) -> bool:
    """Insert or replace one row in official_legality_history. Ensures schema. Returns True if written."""
    path = Path(rules_db_path)
    _ensure_rules_db(path)
    code = (card_code or "").strip().upper()
    fmt = (format_name or "standard").strip().lower()
    reg = (region or "").strip()
    state = (legality_state or "").strip().lower()
    eff_start = (effective_start or "").strip()
    if not code or not state:
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with closing(_conn(path)) as conn:
            conn.execute(
                """INSERT INTO official_legality_history
                   (card_code, format_name, region, legality_state, effective_start, effective_end,
                    source_id, source_reference, notice_id, is_current, is_upcoming, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(card_code, format_name, region, effective_start) DO UPDATE SET
                    legality_state = excluded.legality_state,
                    effective_end = excluded.effective_end,
                    source_id = excluded.source_id,
                    source_reference = excluded.source_reference,
                    notice_id = excluded.notice_id,
                    is_current = excluded.is_current,
                    is_upcoming = excluded.is_upcoming,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at""",
                (
                    code,
                    fmt,
                    reg,
                    state,
                    eff_start,
                    (effective_end or "").strip(),
                    (source_id or "").strip(),
                    (source_reference or "").strip(),
                    (notice_id or "").strip(),
                    1 if is_current else 0,
                    1 if is_upcoming else 0,
                    (notes or "").strip(),
                    now,
                ),
            )
            conn.commit()
        return True
    except Exception:
        return False


def insert_rule_notice(
    rules_db_path: Path,
    notice_id: str,
    *,
    title: str = "",
    source_id: str = "",
    source_url: str = "",
    source_reference: str = "",
    region: str = "",
    format_name: str = "standard",
    notice_type: str = NOTICE_TYPE_OTHER,
    published_at: str = "",
    effective_at: str = "",
    status: str = STATUS_CURRENT,
    summary: str = "",
    payload_json: str = "{}",
) -> bool:
    """Insert or replace one official_rule_notices row. Ensures schema. Returns True if written."""
    path = Path(rules_db_path)
    _ensure_rules_db(path)
    nid = (notice_id or "").strip()
    if not nid:
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with closing(_conn(path)) as conn:
            conn.execute(
                """INSERT INTO official_rule_notices
                   (notice_id, title, source_id, source_url, source_reference, region, format_name,
                    notice_type, published_at, effective_at, status, summary, payload_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(notice_id) DO UPDATE SET
                    title = excluded.title,
                    source_id = excluded.source_id,
                    source_url = excluded.source_url,
                    source_reference = excluded.source_reference,
                    region = excluded.region,
                    format_name = excluded.format_name,
                    notice_type = excluded.notice_type,
                    published_at = excluded.published_at,
                    effective_at = excluded.effective_at,
                    status = excluded.status,
                    summary = excluded.summary,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at""",
                (
                    nid,
                    (title or "").strip(),
                    (source_id or "").strip(),
                    (source_url or "").strip(),
                    (source_reference or "").strip(),
                    (region or "").strip(),
                    (format_name or "standard").strip().lower(),
                    (notice_type or NOTICE_TYPE_OTHER).strip().lower(),
                    (published_at or "").strip(),
                    (effective_at or "").strip(),
                    (status or STATUS_CURRENT).strip().lower(),
                    (summary or "").strip(),
                    payload_json if payload_json else "{}",
                    now,
                ),
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_official_rules_summary(rules_db_path: Path) -> dict[str, Any]:
    """Lightweight summary for Dev/operator: counts of current/upcoming notices and upcoming legality rows."""
    path = Path(rules_db_path)
    if not path.is_file():
        return {"current_notices": 0, "upcoming_notices": 0, "upcoming_legality_count": 0}
    try:
        current = len(list_notices(path, status=STATUS_CURRENT))
        upcoming = len(list_notices(path, status=STATUS_UPCOMING))
        with closing(_conn(path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM official_legality_history WHERE is_upcoming = 1",
            ).fetchone()
        upcoming_legality = int(row[0]) if row else 0
        return {
            "current_notices": current,
            "upcoming_notices": upcoming,
            "upcoming_legality_count": upcoming_legality,
        }
    except Exception:
        return {"current_notices": 0, "upcoming_notices": 0, "upcoming_legality_count": 0}


def insert_format_context(
    rules_db_path: Path,
    *,
    region: str = "",
    format_name: str = "standard",
    block_rotation_active: int = 0,
    effective_at: str = "",
    source_id: str = "",
    source_reference: str = "",
    notice_id: str = "",
    notes: str = "",
) -> bool:
    """Insert one row into official_format_context. Ensures schema. Returns True if written."""
    path = Path(rules_db_path)
    _ensure_rules_db(path)
    fmt = (format_name or "standard").strip().lower()
    reg = (region or "").strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with closing(_conn(path)) as conn:
            conn.execute(
                """INSERT INTO official_format_context
                   (region, format_name, block_rotation_active, effective_at, source_id, source_reference, notice_id, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reg,
                    fmt,
                    1 if block_rotation_active else 0,
                    (effective_at or "").strip(),
                    (source_id or "").strip(),
                    (source_reference or "").strip(),
                    (notice_id or "").strip(),
                    (notes or "").strip(),
                    now,
                ),
            )
            conn.commit()
        return True
    except Exception:
        return False


def list_notices(
    rules_db_path: Path,
    status: str | None = None,
    format_name: str | None = None,
) -> list[dict[str, Any]]:
    """List official_rule_notices, optionally filtered by status and/or format_name."""
    path = Path(rules_db_path)
    if not path.is_file():
        return []
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            if status and format_name:
                rows = conn.execute(
                    "SELECT * FROM official_rule_notices WHERE status = ? AND format_name = ? ORDER BY effective_at DESC",
                    (status.strip().lower(), format_name.strip().lower()),
                ).fetchall()
            elif status:
                rows = conn.execute(
                    "SELECT * FROM official_rule_notices WHERE status = ? ORDER BY effective_at DESC",
                    (status.strip().lower(),),
                ).fetchall()
            elif format_name:
                rows = conn.execute(
                    "SELECT * FROM official_rule_notices WHERE format_name = ? ORDER BY effective_at DESC",
                    (format_name.strip().lower(),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM official_rule_notices ORDER BY effective_at DESC").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Official rulings / Q&A (card-specific and general)
# ---------------------------------------------------------------------------

def insert_card_ruling(
    rules_db_path: Path,
    *,
    ruling_id: str = "",
    card_code: str | None = None,
    topic_key: str = "",
    question_text: str = "",
    ruling_text: str = "",
    normalized_summary: str = "",
    source_id: str = "",
    source_type: str = RULING_SOURCE_OTHER_OFFICIAL,
    source_title: str = "",
    source_url: str = "",
    source_reference: str = "",
    source_anchor: str = "",
    published_at: str = "",
    effective_at: str = "",
    status: str = STATUS_CURRENT,
    tags: str = "",
) -> bool:
    """Insert or replace one official_card_rulings row (by ruling_id if given). Returns True if written."""
    path = Path(rules_db_path)
    _ensure_rules_db(path)
    rid = (ruling_id or "").strip()
    if not rid:
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = (card_code or "").strip().upper() or None
    stype = (source_type or RULING_SOURCE_OTHER_OFFICIAL).strip().lower()
    if stype not in RULING_SOURCE_TYPES:
        stype = RULING_SOURCE_OTHER_OFFICIAL
    try:
        with closing(_conn(path)) as conn:
            params = (
                rid,
                card,
                (topic_key or "").strip(),
                (question_text or "").strip(),
                (ruling_text or "").strip(),
                (normalized_summary or "").strip(),
                (source_id or "").strip(),
                stype,
                (source_title or "").strip(),
                (source_url or "").strip(),
                (source_reference or "").strip(),
                (source_anchor or "").strip(),
                (published_at or "").strip(),
                (effective_at or "").strip(),
                (status or STATUS_CURRENT).strip().lower(),
                (tags or "").strip(),
                now,
            )
            conn.execute(
                """INSERT INTO official_card_rulings
                   (ruling_id, card_code, topic_key, question_text, ruling_text, normalized_summary,
                    source_id, source_type, source_title, source_url, source_reference, source_anchor,
                    published_at, effective_at, status, tags, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                params,
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        # ruling_id already exists; update by ruling_id (no ON CONFLICT on partial index in older SQLite)
        try:
            with closing(_conn(path)) as conn:
                conn.execute(
                    """UPDATE official_card_rulings SET
                        card_code = ?, topic_key = ?, question_text = ?, ruling_text = ?, normalized_summary = ?,
                        source_id = ?, source_type = ?, source_title = ?, source_url = ?, source_reference = ?, source_anchor = ?,
                        published_at = ?, effective_at = ?, status = ?, tags = ?, updated_at = ?
                       WHERE ruling_id = ?""",
                    (
                        card,
                        (topic_key or "").strip(),
                        (question_text or "").strip(),
                        (ruling_text or "").strip(),
                        (normalized_summary or "").strip(),
                        (source_id or "").strip(),
                        stype,
                        (source_title or "").strip(),
                        (source_url or "").strip(),
                        (source_reference or "").strip(),
                        (source_anchor or "").strip(),
                        (published_at or "").strip(),
                        (effective_at or "").strip(),
                        (status or STATUS_CURRENT).strip().lower(),
                        (tags or "").strip(),
                        now,
                        rid,
                    ),
                )
                conn.commit()
            return True
        except Exception:
            return False
    except Exception:
        return False


def get_current_rulings_for_card(
    rules_db_path: Path,
    card_code: str,
    *,
    status: str = STATUS_CURRENT,
) -> list[dict[str, Any]]:
    """Return current (or given status) official rulings for a card. Card-specific only."""
    path = Path(rules_db_path)
    if not path.is_file():
        return []
    code = (card_code or "").strip().upper()
    if not code:
        return []
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM official_card_rulings
                   WHERE card_code = ? AND status = ?
                   ORDER BY effective_at DESC, id DESC""",
                (code, status.strip().lower()),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_rulings_for_topic(
    rules_db_path: Path,
    topic_key: str,
    *,
    status: str = STATUS_CURRENT,
    card_code: str | None = None,
) -> list[dict[str, Any]]:
    """Return official rulings for a topic (and optionally a card). General or card-specific."""
    path = Path(rules_db_path)
    if not path.is_file():
        return []
    topic = (topic_key or "").strip()
    if not topic:
        return []
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            if card_code:
                code = (card_code or "").strip().upper()
                rows = conn.execute(
                    """SELECT * FROM official_card_rulings
                       WHERE topic_key = ? AND (card_code = ? OR card_code IS NULL) AND status = ?
                       ORDER BY card_code IS NOT NULL DESC, effective_at DESC, id DESC""",
                    (topic, code, status.strip().lower()),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM official_card_rulings
                       WHERE topic_key = ? AND status = ?
                       ORDER BY effective_at DESC, id DESC""",
                    (topic, status.strip().lower()),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def search_official_rulings(
    rules_db_path: Path,
    *,
    card_code: str | None = None,
    query: str | None = None,
    tags: str | None = None,
    topic_key: str | None = None,
    status: str = STATUS_CURRENT,
    source_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search official rulings by card, text query (question/ruling/summary), tags, topic, status, source_type."""
    path = Path(rules_db_path)
    if not path.is_file():
        return []
    conditions: list[str] = []
    params: list[Any] = []
    conditions.append("status = ?")
    params.append(status.strip().lower())
    if card_code:
        conditions.append("(card_code = ? OR card_code IS NULL)")
        params.append((card_code or "").strip().upper())
    if topic_key:
        conditions.append("topic_key = ?")
        params.append((topic_key or "").strip())
    if tags:
        conditions.append("(tags LIKE ? OR tags = ?)")
        tag_val = (tags or "").strip()
        params.append(f"%{tag_val}%")
        params.append(tag_val)
    if source_type:
        conditions.append("source_type = ?")
        params.append((source_type or "").strip().lower())
    where = " AND ".join(conditions)
    try:
        with closing(_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""SELECT * FROM official_card_rulings WHERE {where} ORDER BY effective_at DESC, id DESC LIMIT ?""",
                (*params, limit),
            ).fetchall()
            result = [dict(r) for r in rows]
    except Exception:
        return []
    if query:
        q = (query or "").strip().lower()
        if q:
            result = [
                r for r in result
                if q in (r.get("question_text") or "").lower()
                or q in (r.get("ruling_text") or "").lower()
                or q in (r.get("normalized_summary") or "").lower()
                or q in (r.get("topic_key") or "").lower()
            ]
    return result


def get_best_official_ruling_match(
    rules_db_path: Path,
    *,
    card_code: str | None = None,
    topic_key: str | None = None,
    query: str | None = None,
    prefer_card_specific: bool = True,
    status: str = STATUS_CURRENT,
) -> dict[str, Any] | None:
    """Return the single best-matching current ruling: card-specific first, then topic, then text match."""
    path = Path(rules_db_path)
    if not path.is_file():
        return None
    candidates = search_official_rulings(
        path,
        card_code=card_code,
        topic_key=topic_key,
        query=query,
        status=status,
        limit=20,
    )
    if not candidates:
        return None
    if prefer_card_specific and card_code:
        code = (card_code or "").strip().upper()
        for r in candidates:
            if (r.get("card_code") or "").strip().upper() == code:
                return r
    return candidates[0] if candidates else None


def format_source_citation(ruling_row: dict[str, Any]) -> dict[str, Any]:
    """Build a source-backed citation dict for UI/insight display. Safe for missing keys."""
    return {
        "source_title": (ruling_row.get("source_title") or "").strip() or "Official ruling",
        "source_type": (ruling_row.get("source_type") or "").strip() or RULING_SOURCE_OTHER_OFFICIAL,
        "source_reference": (ruling_row.get("source_reference") or "").strip(),
        "source_url": (ruling_row.get("source_url") or "").strip(),
        "source_anchor": (ruling_row.get("source_anchor") or "").strip(),
    }


def get_rulings_for_card_with_citations(
    rules_db_path: Path,
    card_code: str,
    *,
    status: str = STATUS_CURRENT,
) -> list[dict[str, Any]]:
    """Return current rulings for a card, each with a 'citation' key for UI/insight display (source-backed)."""
    rulings = get_current_rulings_for_card(rules_db_path, card_code, status=status)
    out: list[dict[str, Any]] = []
    for r in rulings:
        row = dict(r)
        row["citation"] = format_source_citation(r)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Staged ingestion (official-source payloads only; no scraping)
# ---------------------------------------------------------------------------

def ingest_notice_json(
    rules_db_path: Path,
    payload: dict[str, Any],
    *,
    notice_id: str | None = None,
) -> bool:
    """Ingest one official rule notice from a JSON-like dict. Returns True if written.
    Expects keys: notice_id (or pass), title, source_id, source_url?, source_reference?, region?, format_name?,
    notice_type?, published_at?, effective_at?, status?, summary?, payload_json?.
    """
    nid = (notice_id or payload.get("notice_id") or "").strip()
    if not nid:
        return False
    return insert_rule_notice(
        rules_db_path,
        nid,
        title=str(payload.get("title") or ""),
        source_id=str(payload.get("source_id") or ""),
        source_url=str(payload.get("source_url") or ""),
        source_reference=str(payload.get("source_reference") or ""),
        region=str(payload.get("region") or ""),
        format_name=str(payload.get("format_name") or "standard").strip().lower(),
        notice_type=str(payload.get("notice_type") or NOTICE_TYPE_OTHER).strip().lower(),
        published_at=str(payload.get("published_at") or ""),
        effective_at=str(payload.get("effective_at") or ""),
        status=str(payload.get("status") or STATUS_CURRENT).strip().lower(),
        summary=str(payload.get("summary") or ""),
        payload_json=json.dumps(payload.get("payload") or payload) if isinstance(payload.get("payload"), dict) else (str(payload.get("payload_json") or "{}")),
    )


def ingest_legality_row(
    rules_db_path: Path,
    card_code: str,
    format_name: str,
    legality_state: str,
    *,
    region: str = "",
    effective_date: str = "",
    source_id: str = "",
    source_reference: str = "",
    notice_id: str = "",
    notes: str = "",
) -> bool:
    """Ingest one card legality row into official_legality_history. Sets is_current/is_upcoming from effective_date.
    If effective_date is in the future → is_upcoming=1, is_current=0. Else → is_current=1, is_upcoming=0.
    Returns True if written.
    """
    effective_at = (effective_date or "").strip()
    now_effective = is_effective_now(effective_at)
    return insert_legality_history(
        rules_db_path,
        card_code,
        format_name,
        legality_state,
        region=region,
        effective_start=effective_at or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        effective_end="",
        source_id=source_id,
        source_reference=source_reference,
        notice_id=notice_id,
        is_current=1 if now_effective else 0,
        is_upcoming=0 if now_effective else 1,
        notes=notes,
    )
