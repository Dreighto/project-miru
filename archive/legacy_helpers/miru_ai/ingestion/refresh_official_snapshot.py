from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.intel.db import MiruIntelRepository
from shared.intel.snapshot_refresh import OfficialSnapshotRefresher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh Miru's local official snapshot sidecar from a local official export file.")
    parser.add_argument("input_path", help="Local official export JSON file to normalize and refresh")
    parser.add_argument("--db-path", default=str(ROOT / "data" / "miru_dossiers.db"), help="SQLite path for the Miru intelligence sidecar database")
    parser.add_argument("--snapshot-output", default="", help="Optional path to write the normalized snapshot JSON")
    parser.add_argument("--resume", action="store_true", help="Resume an existing refresh run")
    parser.add_argument("--run-id", default="", help="Optional refresh run id")
    parser.add_argument("--notes", default="", help="Optional notes to store on the refresh run")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repository = MiruIntelRepository(args.db_path)
    refresher = OfficialSnapshotRefresher(repository)
    result = refresher.refresh_from_export_path(
        args.input_path,
        snapshot_output_path=args.snapshot_output or None,
        run_id=args.run_id or None,
        resume=args.resume,
        notes=args.notes,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
