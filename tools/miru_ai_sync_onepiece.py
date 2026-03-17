from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.miru_card_intel import build_observed_catalog, load_prices_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Miru AI One Piece catalog snapshot")
    parser.add_argument(
        "--prices",
        default="data/prices.json",
        help="Path to watcher prices JSON",
    )
    parser.add_argument(
        "--output",
        default="data/miru_onepiece_catalog.json",
        help="Path to write the generated catalog JSON",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Also print the generated catalog to stdout",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    records = load_prices_records(Path(args.prices))
    catalog = build_observed_catalog(records)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")

    if args.stdout:
        print(json.dumps(catalog, indent=2, sort_keys=True))

    print(
        f"Wrote {len(catalog['cards'])} cards across {len(catalog['sets'])} sets to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

