#!/usr/bin/env python
"""Batch runner for local card image variant classification (Claude Vision).

Usage:
  python -m tools.run_image_variant_classification
  python -m tools.run_image_variant_classification --card_codes OP01-001 EB02-028

Skips codes listed in data/verified_variant_mappings.json (variant_canonical_code).
Skips codes that already have a row in image_variant_analysis.

Use ``--force`` (with ``--card_codes`` or default backlog) to bypass the verified-mappings
skip and to replace any existing analysis + ``image_variant_sp`` queue row for that code.
Operator / verification only; does not change governance or auto-publish behavior.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from tools.miru_image_variant_classifier import (
    analyze_one_card,
    load_verified_variant_codes,
    normalize_canonical_code,
    resolve_image_path,
)
from tools.miru_project_sync import (
    DEFAULT_PROJECT_DB_PATH,
    connect_catalog_db,
    ensure_catalog_sync_schema,
)

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent

logger = logging.getLogger("miru.image_variant.runner")


def _map_variant_subtype(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw == "sp":
        return "sp"
    if raw == "tr":
        return "tr"
    if raw and "alt" in raw:
        return "alt"
    return "base"


def _catalog_codes_needing_analysis(
    project_db: Path, verified_skip: set[str], *, force: bool = False
) -> list[str]:
    ensure_catalog_sync_schema(project_db)
    conn = connect_catalog_db(project_db)
    try:
        analyzed_rows = conn.execute(
            "SELECT canonical_code FROM image_variant_analysis"
        ).fetchall()
        analyzed = {
            str(r["canonical_code"] or "").strip().upper() for r in analyzed_rows
        }
        rows = conn.execute(
            "SELECT canonical_code, variant_subtype FROM cards ORDER BY canonical_code ASC"
        ).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for row in rows:
        code = str(row["canonical_code"] or "").strip().upper()
        if not code:
            continue
        if code in verified_skip and not force:
            continue
        if code in analyzed and not force:
            continue
        variant_type = _map_variant_subtype(
            row["variant_subtype"] if "variant_subtype" in row.keys() else None
        )
        if resolve_image_path(code, variant_type=variant_type) is None:
            continue
        out.append(code)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Image-based SP / parallel marker classification."
    )
    parser.add_argument(
        "--card_codes",
        nargs="*",
        default=[],
        help="Specific canonical codes (space-separated). Default: all catalog cards with a local image and no analysis row.",
    )
    parser.add_argument(
        "--catalog-db",
        type=Path,
        default=Path(DEFAULT_PROJECT_DB_PATH),
        help="Path to card_catalog.db",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass verified_variant_mappings skip; replace existing analysis row and image_variant_sp queue row (testing).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    project_db = args.catalog_db.resolve()
    verified_skip = load_verified_variant_codes(PROJECT_ROOT)

    if args.card_codes:
        codes = [normalize_canonical_code(c) for c in args.card_codes if str(c).strip()]
        codes = [c for c in codes if c]
    else:
        codes = _catalog_codes_needing_analysis(
            project_db, verified_skip, force=args.force
        )

    conn = connect_catalog_db(project_db)
    try:
        rows = conn.execute(
            "SELECT canonical_code, variant_subtype FROM cards WHERE canonical_code IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    subtype_by_code = {
        str(r["canonical_code"] or "")
        .strip()
        .upper(): _map_variant_subtype(
            r["variant_subtype"] if "variant_subtype" in r.keys() else None
        )
        for r in rows
        if str(r["canonical_code"] or "").strip()
    }

    stats = {
        "processed": 0,
        "skipped_image_unavailable": 0,
        "skipped_verified_mapping": 0,
        "skipped_exists": 0,
        "inconclusive": 0,
        "api_error": 0,
        "written": 0,
        "flagged_for_review": 0,
    }

    for code in codes:
        if code in verified_skip and not args.force:
            logger.info("skip verified_variant_mappings canonical_code=%s", code)
            stats["skipped_verified_mapping"] += 1
            continue
        stats["processed"] += 1
        variant_type = subtype_by_code.get(code, "base")
        res = analyze_one_card(
            code,
            project_db_path=project_db,
            force=args.force,
            variant_type=variant_type,
        )
        st = str(res.get("status") or "")
        if st == "IMAGE_UNAVAILABLE":
            stats["skipped_image_unavailable"] += 1
        elif st == "INCONCLUSIVE":
            stats["inconclusive"] += 1
        elif st == "API_ERROR":
            stats["api_error"] += 1
        elif st == "SKIPPED_EXISTS":
            stats["skipped_exists"] += 1
        elif st == "written":
            stats["written"] += 1
            if res.get("flagged_for_review"):
                stats["flagged_for_review"] += 1
        else:
            logger.warning("unexpected status %s for %s", st, code)

    print(
        json.dumps(
            {
                "ok": True,
                "stats": stats,
                "codes_in_run": len(codes),
                "force": bool(args.force),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
