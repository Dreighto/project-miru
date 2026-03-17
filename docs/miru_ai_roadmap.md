# Miru AI Roadmap

## Near term

1. Expand official-card snapshot coverage beyond the initial sample fixture and validate more real card shapes.
2. Compare multiple official refresh snapshots over time and decide when longer-term fact history needs dedicated time-series storage.
3. Add richer variant reasoning so Miru can distinguish reprint foil, treasure rare, manga, judge, serial, anniversary, and event prize variants when a trusted source exposes them.
4. Add more verified-loop fixtures and tests drawn from real official card-list exports.
5. Keep the shared parser and verified loop deterministic, source-honest, and auditable.

## Mid term

1. Add a tightly scoped live official fetch mode that feeds the existing export-to-snapshot normalization path instead of creating a separate ingestion system.
2. Expand dossier coverage for traits, costs, power, counters, life, and other structured official card fields when the source exposes them.
3. Add stale-snapshot detection and optional scheduled refresh orchestration on top of the current refresh reports.
4. Reuse the verified-loop SQLite dossiers in carefully bounded runtime read paths only after compatibility coverage is proven.
5. Expand dossier-query helpers into a broader verified answer layer after more official snapshot coverage exists.

## Longer term

1. Add OCR-assisted parsing from screenshots or scans.
2. Layer in rules text understanding and simple gameplay Q&A backed by a verifiable local corpus.
3. Extend Miru AI to other TCGs with per-game adapters while keeping the verified-loop storage model reusable.
4. Add semantic retrieval on top of stored facts and citations without replacing the source-backed SQLite foundation.

## Guardrails

- Keep the verified loop additive and sidecar-only until a dedicated runtime integration pass is approved.
- Prefer official snapshots and narrow trusted adapters over broad scraping.
- Record unknowns explicitly so downstream consumers can distinguish missing facts from negative facts.
- Record conflicts instead of silently picking a winner when sources disagree.
