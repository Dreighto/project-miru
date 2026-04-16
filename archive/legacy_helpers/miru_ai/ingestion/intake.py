"""
miru_intake.py

Single-command orchestrator for the full Miru card-intake workflow:

  1. Validate CSV rows with DB guardrails (BLOCK on bad data)
  2. Convert CSV -> intermediate export JSON
  3. Refresh the official dossier snapshot
  4. Queue verify_official_fields tasks via refresh_from_source
  5. Print a clean per-step summary

Usage:
    python -m tools.miru_intake <input.csv> [options]

Options:
    --set-code CODE          setCode override for all rows
    --set-name NAME          setName override for all rows
    --db-path PATH           Dossier DB for guardrail comparison
                             (default: data/miru_dossiers.db)
    --snapshot-output PATH   Where to write the updated snapshot JSON
                             (default: data/official_cardlist_snapshot.json)
    --export-output PATH     Intermediate export JSON path
                             (default: data/<set_code>_intake_export.json)
    --dry-run                Validate only; no files written, no tasks queued
    --strict                 Treat WARN-level issues as BLOCKs
    --no-queue               Validate + convert + refresh, but skip task queueing
    --tsv                    Treat input as tab-separated
    --snapshot-date DATE     snapshotTakenAt override (YYYY-MM-DD HH:MM:SS)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header(n: int, total: int, label: str) -> None:
    print(f"\n[{n}/{total}] {label}")


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _warn(msg: str) -> None:
    print(f"  WARN  {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"  ERROR  {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Orchestrate the full Miru card-intake workflow in one command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_path", help="Path to the CSV or TSV input file.")
    parser.add_argument(
        "--set-code", default="", metavar="CODE",
        help="setCode override for all rows (e.g. EB04).",
    )
    parser.add_argument(
        "--set-name", default="", metavar="NAME",
        help="setName override for all rows.",
    )
    parser.add_argument(
        "--db-path",
        default=str(ROOT / "data" / "miru_dossiers.db"),
        metavar="PATH",
        help="Dossier DB path for guardrail comparison (default: data/miru_dossiers.db).",
    )
    parser.add_argument(
        "--snapshot-output", default="", metavar="PATH",
        help="Path to write the updated snapshot JSON "
             "(default: data/official_cardlist_snapshot.json).",
    )
    parser.add_argument(
        "--export-output", default="", metavar="PATH",
        help="Intermediate export JSON path "
             "(default: data/<set_code>_intake_export.json).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate only. No files written, no tasks queued.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat WARN-level issues as BLOCKs (abort instead of continuing).",
    )
    parser.add_argument(
        "--no-queue", action="store_true",
        help="Skip the refresh_from_source queue step after snapshot refresh.",
    )
    parser.add_argument(
        "--tsv", action="store_true",
        help="Treat input as tab-separated regardless of file extension.",
    )
    parser.add_argument(
        "--snapshot-date", default="", metavar="DATE",
        help="snapshotTakenAt override (YYYY-MM-DD HH:MM:SS).",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    input_path = Path(args.input_path)
    if not input_path.exists():
        _err(f"input file not found: {input_path}")
        return 1

    # DB path — disable guardrails gracefully if file missing
    db_path: Optional[Path] = Path(args.db_path)
    if not db_path.exists():
        _warn(f"db-path not found ({db_path}); DB guardrails disabled.")
        db_path = None

    delimiter = "\t" if (args.tsv or input_path.suffix.lower() == ".tsv") else ","

    # Resolve output paths
    set_code_slug = args.set_code.strip().lower() or "intake"
    export_output = (
        Path(args.export_output)
        if args.export_output
        else ROOT / "data" / f"{set_code_slug}_intake_export.json"
    )
    snapshot_output = (
        Path(args.snapshot_output)
        if args.snapshot_output
        else ROOT / "data" / "official_cardlist_snapshot.json"
    )

    total_steps = 2 if args.dry_run else (3 if args.no_queue else 4)

    # -------------------------------------------------------------------------
    # Step 1: Validate
    # -------------------------------------------------------------------------
    _header(1, total_steps, "Validating CSV...")

    from tools.miru_import_card_csv import convert, validate_rows, _print_report

    try:
        payload = convert(
            input_path,
            delimiter=delimiter,
            set_code_override=args.set_code,
            set_name_override=args.set_name,
            snapshot_date=args.snapshot_date,
        )
    except ValueError as exc:
        _err(str(exc))
        return 1

    rows = payload["rows"]
    total_rows = len(rows)
    report = validate_rows(rows, db_path=db_path)

    if report.issues:
        _print_report(report, total_rows=total_rows)
    else:
        _ok(f"{total_rows} row(s) — no issues found.")

    # Decide whether to stop
    effective_blocks = (
        report.block_count + report.warn_count  # --strict: warns count as blocks
        if args.strict
        else report.block_count
    )

    if effective_blocks > 0:
        detail = (
            f"{report.block_count} block(s)"
            if not args.strict
            else f"{report.block_count} block(s) + {report.warn_count} warning(s) treated as blocks (--strict)"
        )
        print(f"\nINTAKE STOPPED: {detail}. Fix the CSV and re-run.", file=sys.stderr)
        return 1

    # -------------------------------------------------------------------------
    # Dry-run exits here
    # -------------------------------------------------------------------------
    if args.dry_run:
        print(
            f"\nDry-run complete. {total_rows} row(s) validated"
            f" ({report.warn_count} warning(s), 0 blocks). No files written."
        )
        return 0

    # -------------------------------------------------------------------------
    # Step 2: Write export JSON
    # -------------------------------------------------------------------------
    _header(2, total_steps, f"Writing export JSON -> {export_output}")

    try:
        export_output.parent.mkdir(parents=True, exist_ok=True)
        export_output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _ok(f"{total_rows} row(s) written.")
    except OSError as exc:
        _err(f"could not write export JSON: {exc}")
        return 1

    # -------------------------------------------------------------------------
    # Step 3: Refresh official snapshot
    # -------------------------------------------------------------------------
    _header(3, total_steps, f"Refreshing snapshot -> {snapshot_output}")

    dossier_db_path = db_path or ROOT / "data" / "miru_dossiers.db"

    try:
        from shared.intel.db import MiruIntelRepository
        from shared.intel.snapshot_refresh import OfficialSnapshotRefresher

        repo = MiruIntelRepository(str(dossier_db_path))
        refresher = OfficialSnapshotRefresher(repo)
        refresh_result = refresher.refresh_from_export_path(
            export_output,
            snapshot_output_path=snapshot_output,
        )
        cards_processed = len(refresh_result.get("results") or [])
        _ok(f"{cards_processed} card(s) processed.")
    except Exception as exc:
        _err(f"snapshot refresh failed: {exc}")
        return 1

    # -------------------------------------------------------------------------
    # --no-queue exits here
    # -------------------------------------------------------------------------
    if args.no_queue:
        print(
            f"\nIntake complete (--no-queue). "
            f"{total_rows} row(s) converted, {cards_processed} card(s) refreshed. "
            f"Tasks not queued."
        )
        if report.warn_count:
            print(f"Note: {report.warn_count} warning(s) were accepted.")
        return 0

    # -------------------------------------------------------------------------
    # Step 4: Queue verification tasks via refresh_from_source
    # -------------------------------------------------------------------------
    _header(4, total_steps, "Queuing verification tasks...")

    queued = 0
    try:
        from tools.miru_learning_engine import (
            MiruLearningEngine,
            DEFAULT_QUEUE_DB_PATH,
            DEFAULT_STATUS_DB_PATH,
            DEFAULT_KNOWLEDGE_CACHE_PATH,
            DEFAULT_CATALOG_DB_PATH,
            DEFAULT_IMAGE_DEST_ROOT,
        )

        engine = MiruLearningEngine(
            queue_db_path=DEFAULT_QUEUE_DB_PATH,
            status_db_path=DEFAULT_STATUS_DB_PATH,
            dossier_db_path=dossier_db_path,
            knowledge_cache_path=DEFAULT_KNOWLEDGE_CACHE_PATH,
            catalog_db_path=DEFAULT_CATALOG_DB_PATH,
            image_dest_root=DEFAULT_IMAGE_DEST_ROOT,
        )
        queue_result = engine.run_once(
            task_type="refresh_from_source",
            source_id="official-cardlist",
            task_payload={"snapshot_path": str(snapshot_output)},
        )
        queued = int(queue_result.get("queued_tasks", 0))
        _ok(f"{queued} verify_official_fields task(s) queued.")
    except Exception as exc:
        _warn(f"queue step failed: {exc}")
        _warn("Intake data is safe. Run refresh_from_source manually to retry queueing:")
        _warn(
            f"  python -m tools.miru_learning_engine --mode once "
            f"--task refresh_from_source --source official-cardlist "
            f"--snapshot-path {snapshot_output}"
        )
        print(f"\nIntake complete (queue step failed). {total_rows} row(s) refreshed.")
        return 0

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print(
        f"\nIntake complete. "
        f"{total_rows} row(s) converted, "
        f"{cards_processed} card(s) refreshed, "
        f"{queued} verification task(s) queued."
    )
    if report.warn_count:
        print(f"Note: {report.warn_count} warning(s) were accepted. Review with --dry-run if needed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
