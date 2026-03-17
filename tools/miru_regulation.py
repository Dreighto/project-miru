#!/usr/bin/env python
"""Regulation intelligence: card legality state from official/snapshot sources only.

No fabricated banlist data. Legality claims require an official-source-backed record.
Ready for future UI legality/banned badges; backend/data model only.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Source IDs that count as official for legality (e.g. Bandai rules / banlist ingestion).
# Add when an official ingestion path exists; no fabrication.
OFFICIAL_LEGALITY_SOURCE_IDS: frozenset[str] = frozenset({"official", "bandai_rules", "official_banlist"})

# Legality states we store (prefer unknown over guessed).
LEGALITY_LEGAL = "legal"
LEGALITY_BANNED = "banned"
LEGALITY_RESTRICTED = "restricted"
LEGALITY_ROTATED = "rotated"
LEGALITY_UNKNOWN = "unknown"
LEGALITY_STATES = (LEGALITY_LEGAL, LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED, LEGALITY_UNKNOWN)

# For target selection: exclude these from Standard target pool when we have a record.
ILLEGAL_IN_STANDARD_STATES = (LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED)


def _catalog_conn(path: Path):
    return sqlite3.connect(path)


def get_legality_state(
    catalog_path: Path,
    card_code: str,
    format_name: str = "standard",
) -> dict[str, Any] | None:
    """Return one legality record for (card_code, format) or None if no record."""
    code = (card_code or "").strip().upper()
    fmt = (format_name or "standard").strip().lower()
    if not code:
        return None
    path = Path(catalog_path)
    if not path.is_file():
        return None
    try:
        with closing(_catalog_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                "FROM miru_card_legality WHERE card_code = ? AND format = ?",
                (code, fmt),
            ).fetchone()
            if not row:
                return None
            return dict(row)
    except Exception:
        return None


def get_legality_state_batch(
    catalog_path: Path,
    card_codes: list[str],
    format_name: str = "standard",
) -> dict[str, dict[str, Any]]:
    """Return map card_code -> legality record for codes that have a row. No record = not in map."""
    path = Path(catalog_path)
    if not path.is_file() or not card_codes:
        return {}
    fmt = (format_name or "standard").strip().lower()
    codes = [str(c or "").strip().upper() for c in card_codes if str(c or "").strip()]
    if not codes:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with closing(_catalog_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            for code in codes:
                row = conn.execute(
                    "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                    "FROM miru_card_legality WHERE card_code = ? AND format = ?",
                    (code, fmt),
                ).fetchone()
                if row:
                    out[code] = dict(row)
    except Exception:
        pass
    return out


def save_legality_state(
    catalog_path: Path,
    card_code: str,
    format_name: str,
    legality_state: str,
    *,
    effective_date: str = "",
    source_id: str = "",
    source_reference: str = "",
    last_checked_at: str = "",
    notes: str = "",
) -> bool:
    """Upsert one legality row. Returns True if written. Caller must ensure catalog schema exists.
    Write-side validation: non-official sources cannot write a claimable state (legal/banned/restricted/rotated);
    if source_id is not in OFFICIAL_LEGALITY_SOURCE_IDS and state is not unknown, the write is refused and returns False.
    """
    code = (card_code or "").strip().upper()
    fmt = (format_name or "standard").strip().lower()
    state = (legality_state or LEGALITY_UNKNOWN).strip().lower()
    if state not in LEGALITY_STATES:
        state = LEGALITY_UNKNOWN
    if not code:
        return False
    sid = (source_id or "").strip()
    is_official = sid in OFFICIAL_LEGALITY_SOURCE_IDS
    if not is_official and state != LEGALITY_UNKNOWN:
        return False
    path = Path(catalog_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_catalog_conn(path)) as conn:
            conn.execute(
                """
                INSERT INTO miru_card_legality
                (card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (card_code, format) DO UPDATE SET
                    legality_state = excluded.legality_state,
                    effective_date = excluded.effective_date,
                    source_id = excluded.source_id,
                    source_reference = excluded.source_reference,
                    last_checked_at = excluded.last_checked_at,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (code, fmt, state, effective_date or "", source_id or "", source_reference or "", last_checked_at or "", notes or ""),
            )
            conn.commit()
        return True
    except Exception:
        return False


def list_legality_records(
    catalog_path: Path,
    format_name: str | None = None,
    legality_state: str | None = None,
) -> list[dict[str, Any]]:
    """List legality rows, optionally filtered by format and/or legality_state."""
    path = Path(catalog_path)
    if not path.is_file():
        return []
    try:
        with closing(_catalog_conn(path)) as conn:
            conn.row_factory = sqlite3.Row
            if format_name is not None and legality_state is not None:
                rows = conn.execute(
                    "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                    "FROM miru_card_legality WHERE format = ? AND legality_state = ? ORDER BY card_code",
                    (format_name.strip().lower(), legality_state.strip().lower()),
                ).fetchall()
            elif format_name is not None:
                rows = conn.execute(
                    "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                    "FROM miru_card_legality WHERE format = ? ORDER BY card_code",
                    (format_name.strip().lower(),),
                ).fetchall()
            elif legality_state is not None:
                rows = conn.execute(
                    "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                    "FROM miru_card_legality WHERE legality_state = ? ORDER BY card_code, format",
                    (legality_state.strip().lower(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT card_code, format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes, updated_at "
                    "FROM miru_card_legality ORDER BY card_code, format"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def is_legal_for_format(catalog_path: Path, card_code: str, format_name: str = "standard") -> bool | None:
    """
    True = official record says legal; False = official record says illegal for the format; None = no valid official claim.
    Returns None when there is no record or when the record is not from an official legality source (enforces
    "do not claim legality without official-source-backed record" centrally).
    """
    rec = get_legality_state(catalog_path, card_code, format_name)
    if not rec:
        return None
    sid = (rec.get("source_id") or "").strip()
    if sid not in OFFICIAL_LEGALITY_SOURCE_IDS:
        return None
    state = (rec.get("legality_state") or "").strip().lower()
    if state == LEGALITY_LEGAL:
        return True
    if state in ILLEGAL_IN_STANDARD_STATES:
        return False
    return None
