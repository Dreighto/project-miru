#!/usr/bin/env python
"""Shared overlap check for Miru worktree: snapshot vs meta-bearing catalog codes.

Single source of truth for overlap/blocker logic. Used by run_worktree_overlap_growth
and run_worktree_worker so autonomy can skip useless cycles when snapshot coverage
is insufficient.

Standard-format targeting (April 2026): Block Number system — Blocks ②–⑤ legal.
Booster 1–4 = Block ① (OP01–OP04); Booster 5–8 = Block ② (OP05–OP08); etc.
Target selection prefers Standard-legal set prefixes unless format is extra/legacy.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

# Standard legality (April 2026–March 2027): set prefixes for Blocks ②–⑤.
# Block ① = OP01–OP04; Block ② = OP05–OP08; Block ③ = OP09–OP12; extend as needed.
# Data-driven: update when regulation changes; no fabricated legality.
STANDARD_LEGAL_SET_PREFIXES: tuple[str, ...] = (
    "OP05", "OP06", "OP07", "OP08", "OP09", "OP10", "OP11", "OP12",
)
# Optional: specific card codes that remain legal in Standard (e.g. same-card-number exceptions).
# Leave empty or populate from an official source; no fabrication.
STANDARD_EXCEPTION_CARD_CODES: frozenset[str] = frozenset()

# Card code pattern: optional prefix (e.g. OP, EB), digits, hyphen, number (e.g. OP05-067).
_SET_PREFIX_RE = re.compile(r"^([A-Z]+\d+)-", re.IGNORECASE)


def _get_set_prefix(card_code: str) -> str:
    """Extract set prefix from card code (e.g. OP05-067 -> OP05). Empty if not parseable."""
    code = (card_code or "").strip().upper()
    if not code:
        return ""
    match = _SET_PREFIX_RE.match(code)
    if match:
        return match.group(1).upper()
    if "-" in code:
        return code.split("-", 1)[0].strip().upper()
    return ""


def _is_standard_legal(card_code: str) -> bool:
    """True if card is in a Standard-legal set (Blocks ②–⑤) or on the exception list."""
    code = (card_code or "").strip().upper()
    if code in STANDARD_EXCEPTION_CARD_CODES:
        return True
    prefix = _get_set_prefix(code)
    return prefix in STANDARD_LEGAL_SET_PREFIXES


def _filter_illegal_for_standard(catalog_path: Path, card_codes: list[str]) -> list[str]:
    """Drop codes that have an official legality record stating banned/restricted/rotated for standard."""
    if not card_codes:
        return []
    try:
        from tools.miru_regulation import get_legality_state_batch, ILLEGAL_IN_STANDARD_STATES
    except ImportError:
        return list(card_codes)
    path = Path(catalog_path)
    if not path.is_file():
        return list(card_codes)
    batch = get_legality_state_batch(path, card_codes, "standard")
    illegal = {c for c, rec in batch.items() if (rec.get("legality_state") or "").strip().lower() in ILLEGAL_IN_STANDARD_STATES}
    return [c for c in card_codes if c not in illegal]


def meta_bearing_codes(catalog_path: Path) -> set[str]:
    """Card codes that have at least one row in card_intelligence (canonical_code from cards)."""
    if not catalog_path.is_file():
        return set()
    try:
        conn = sqlite3.connect(catalog_path)
        rows = conn.execute("SELECT card_id FROM card_intelligence").fetchall()
        codes = set()
        for (cid,) in rows:
            r = conn.execute("SELECT canonical_code FROM cards WHERE id = ?", (cid,)).fetchone()
            if r and r[0]:
                codes.add(str(r[0]).strip().upper())
        conn.close()
        return codes
    except Exception:
        return set()


def _norm_snapshot_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    return code


def snapshot_codes(snapshot_path: Path) -> set[str]:
    """
    Card codes present in a worktree snapshot JSON.

    Supported shapes:
    - Card-list snapshots: top-level ``cards[]`` with ``card_code`` (community_cardlist,
      onepiece_cardgame_dev, etc.).
    - Limitless tournament snapshot: ``meta_summary.leader_usage`` keys (leader card codes)
      and ``tournaments[].results[].leader_code`` (non-empty values only).
    All sources are merged and deduplicated.
    """
    if not snapshot_path.is_file():
        return set()
    try:
        with open(snapshot_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return set()
    if not isinstance(data, dict):
        return set()

    out: set[str] = set()

    cards = data.get("cards") or []
    if isinstance(cards, list):
        for c in cards:
            if not isinstance(c, dict):
                continue
            code = _norm_snapshot_code(c.get("card_code"))
            if code:
                out.add(code)

    meta = data.get("meta_summary")
    if isinstance(meta, dict):
        leader_usage = meta.get("leader_usage")
        if isinstance(leader_usage, dict):
            for key in leader_usage.keys():
                code = _norm_snapshot_code(key)
                if code:
                    out.add(code)

    tournaments = data.get("tournaments")
    if isinstance(tournaments, list):
        for t in tournaments:
            if not isinstance(t, dict):
                continue
            results = t.get("results") or []
            if not isinstance(results, list):
                continue
            for row in results:
                if not isinstance(row, dict):
                    continue
                code = _norm_snapshot_code(row.get("leader_code"))
                if code:
                    out.add(code)

    return out


def compute_overlap(
    snapshot_path: Path,
    catalog_path: Path,
    *,
    sample_size: int = 20,
) -> dict[str, Any]:
    """
    Compute overlap between snapshot card codes and meta-bearing catalog codes.

    Returns a dict with:
      overlap_count: number of codes in both snapshot and meta-bearing set
      overlap_codes: full sorted list of overlapping codes (for growth loop)
      meta_bearing_count: total meta-bearing codes in catalog
      snapshot_card_count: total card codes in snapshot
      blocker: human-readable blocker message when overlap_count == 0, else None
      sample_meta_bearing_codes: sample of meta-bearing codes (for reports)
      exact_snapshot_needed: guidance when overlap == 0, else None
      snapshot_path / catalog_path: normalized paths as strings
    """
    meta_codes = meta_bearing_codes(catalog_path)
    snap_codes = snapshot_codes(snapshot_path)
    overlap = meta_codes & snap_codes
    overlap_list = sorted(overlap)

    result: dict[str, Any] = {
        "meta_bearing_count": len(meta_codes),
        "snapshot_card_count": len(snap_codes),
        "overlap_count": len(overlap),
        "overlap_codes": overlap_list,
        "snapshot_path": str(snapshot_path.resolve()),
        "catalog_path": str(catalog_path.resolve()),
    }

    if not overlap:
        result["blocker"] = (
            "Snapshot does not contain any of the meta-bearing catalog codes."
        )
        result["sample_meta_bearing_codes"] = sorted(meta_codes)[:sample_size]
        result["exact_snapshot_needed"] = (
            "A card-list JSON (same schema as community_cardlist) that includes at least some of "
            "sample_meta_bearing_codes (e.g. OP01-002, OP01-004, ...). Place it at "
            "data/snapshots/community_cardlist.json (replace/expand) or data/snapshots/official_cardlist.json, "
            "then re-run this script."
        )
    else:
        result["blocker"] = None
        result["sample_meta_bearing_codes"] = sorted(meta_codes)[:sample_size]
        result["exact_snapshot_needed"] = None

    return result


def good_next_learning_targets(
    catalog_path: Path,
    dossier_db_path: Path,
    *,
    limit: int = 20,
    max_sources: int = 1,
    format: str = "standard",
) -> list[str]:
    """
    Highest-value targets for expanding dossier coverage: meta-bearing cards with no or
    limited sources. Ordered by zero-source first, then one-source.

    When format="standard" (default): prefer Standard-legal cards (Blocks ②–⑤ set
    prefixes or exception list) so targeting follows current Standard regulation.
    When format is "extra", "legacy", or "all": no format filter; all meta-bearing
    targets are ranked by source count only (legacy behavior).
    Lightweight; no new subsystem.
    """
    meta_codes = meta_bearing_codes(catalog_path)
    if not meta_codes:
        return []
    if not dossier_db_path.is_file():
        candidates = sorted(meta_codes)
        if format == "standard":
            standard = [c for c in candidates if _is_standard_legal(c)]
            other = [c for c in candidates if c not in standard]
            candidates = sorted(standard) + sorted(other)
        return candidates[:limit] if limit > 0 else candidates
    try:
        conn = sqlite3.connect(dossier_db_path)
        rows = conn.execute(
            """
            SELECT card_code, COUNT(DISTINCT source_id) AS n
            FROM learning_dossier_sources
            WHERE TRIM(COALESCE(card_code, '')) != ''
            GROUP BY card_code
            """
        ).fetchall()
        conn.close()
        source_count = {str(r[0] or "").strip().upper(): int(r[1] or 0) for r in rows if r[0]}
    except Exception:
        source_count = {}
    zero = [c for c in meta_codes if source_count.get(c, 0) == 0]
    one = [c for c in meta_codes if source_count.get(c, 0) == 1]
    if format == "standard":
        zero_standard = sorted(c for c in zero if _is_standard_legal(c))
        zero_other = sorted(c for c in zero if not _is_standard_legal(c))
        one_standard = sorted(c for c in one if _is_standard_legal(c))
        one_other = sorted(c for c in one if not _is_standard_legal(c))
        targets = zero_standard + zero_other + one_standard + one_other
        # Legality-aware: drop cards known banned/restricted/rotated in Standard when we have a record
        targets = _filter_illegal_for_standard(catalog_path, targets)
    else:
        targets = sorted(zero) + sorted(one)
    if limit > 0:
        targets = targets[:limit]
    return targets
