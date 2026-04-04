from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.intel.adapters import (
    MiruKnowledgeCacheAdapter,
    OfficialCardListSnapshotAdapter,
    PlaceholderAdapter,
)
from shared.intel.db import MiruIntelRepository
from shared.intel.pipeline import MiruEnrichmentRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the additive Miru verified intelligence loop against local official-style sources.")
    parser.add_argument("codes", nargs="*", help="One or more canonical card codes, such as OP01-001")
    parser.add_argument("--db-path", default=str(ROOT / "data" / "miru_dossiers.db"), help="SQLite path for the Miru intelligence sidecar database")
    parser.add_argument("--official-snapshot", default="", help="Optional local official-cardlist-style JSON snapshot")
    parser.add_argument("--knowledge-cache", default=str(ROOT / "data" / "miru_ai_onepiece_knowledge.json"), help="Local Miru knowledge cache JSON used to bootstrap dossier coverage")
    parser.add_argument("--all-from-cache", action="store_true", help="Enrich every card code present in the Miru knowledge cache")
    parser.add_argument("--placeholder-only", action="store_true", help="Use only the explicit placeholder adapter")
    parser.add_argument("--resume", action="store_true", help="Resume an existing run id instead of creating a new one")
    parser.add_argument("--run-id", default="", help="Optional run id to resume or label this enrichment pass")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    target_codes = [str(code).strip().upper() for code in args.codes if str(code).strip()]
    repository = MiruIntelRepository(args.db_path)

    if args.placeholder_only:
        adapters = [PlaceholderAdapter()]
    elif args.official_snapshot:
        adapters = [OfficialCardListSnapshotAdapter.from_path(args.official_snapshot), PlaceholderAdapter()]
    else:
        knowledge_adapter = MiruKnowledgeCacheAdapter.from_path(args.knowledge_cache)
        adapters = [knowledge_adapter]
        if args.all_from_cache:
            target_codes = knowledge_adapter.list_card_codes()

    if not target_codes:
        parser.error("Provide one or more card codes, or use --all-from-cache to bootstrap from the knowledge cache.")

    runner = MiruEnrichmentRunner(repository, adapters)
    result = runner.run_batch(target_codes, run_id=args.run_id or None, resume=args.resume)
    print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
