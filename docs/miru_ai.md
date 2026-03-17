# Miru AI Card Intelligence

`Miru AI` is a local, deterministic card-intelligence layer for Project Miru. It is intentionally narrow: it parses One Piece card and product names, understands common set families, normalizes variants, and can build a small observed catalog from the repo's local watcher data.

## What Miru AI knows today

- One Piece card code structure such as `OP11-067`, `EB03-062`, `ST10-001`, and `P-093`
- Set-family inference for booster, extra booster, starter deck, premium booster, and promo cards
- Named set profiles for common modern English One Piece releases including `OP01` through `OP11`, `EB01` through `EB03`, `PRB01`, and promo cards
- Variant markers such as alternate art, foil, manga, illustration box, judge, serialized, and winner
- Lightweight card traits present in the text itself, including colors, basic card type labels, and simple rarity markers
- Observed catalog generation from local `data/prices.json` records
- A SQLite-first Miru Verified Intelligence Loop sidecar with a Tier 1 official snapshot adapter for source-backed dossier storage
- A tightly scoped official export refresh path that normalizes local exports into Miru snapshots and updates stored dossiers safely
- A small dossier-query helper for practical verified question-style answers from stored card profiles

## What Miru AI does not know yet

- Full card text, effects, rulings, restrictions, bans, or tournament legality for the whole game corpus
- Complete card databases for every One Piece printing and language
- Image OCR or visual card recognition
- Cross-game intelligence beyond One Piece
- Broad live syncing from official sources in this repo lane
- Long-term historical fact versioning beyond the current refresh reports

## Files in this lane

- `dashboard/miru_card_intel.py`: shared parsing and knowledge engine
- `dashboard/miru_intel_models.py`: verified-loop dossier model
- `dashboard/miru_intel_trust.py`: source trust tiers
- `dashboard/miru_intel_adapters.py`: official snapshot, fixture, and placeholder adapters
- `dashboard/miru_intel_db.py`: SQLite storage for verified facts, citations, and refresh reports
- `dashboard/miru_intel_pipeline.py`: controlled enrichment runner
- `dashboard/miru_snapshot_refresh.py`: official export normalization and refresh reporting
- `tools/miru_ai.py`: main CLI entry point
- `tools/miru_verified_loop.py`: verified-loop sidecar runner
- `tools/miru_refresh_official_snapshot.py`: local official refresh CLI
- `tests/test_miru_card_intel.py`: parser tests
- `tests/test_miru_verified_intel.py`: verified-loop and official adapter tests
- `tests/test_miru_snapshot_refresh.py`: official refresh and regression tests

## Design notes

- The parser remains rules-based so behavior is stable and easy to test.
- The verified loop is additive and sidecar-only in this pass.
- The first Tier 1 path is a local official-cardlist-style snapshot, plus a local export-to-snapshot refresh step, not a broad live fetch system.
- Unknowns stay unknown, and conflicting values are recorded instead of silently collapsed.
- Runtime integration into the dashboard remains intentionally deferred.
