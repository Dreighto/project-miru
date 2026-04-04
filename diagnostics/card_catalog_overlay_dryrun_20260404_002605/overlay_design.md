# Overlay design (dry-run)

## Strategy

**Recommended: versioned sidecar CSV** (e.g. `printing_market_map_overlay_high_confidence.csv` checked in or shipped beside the catalog).

### Why CSV is safest for this repo

1. **No schema migration** — `card_catalog.db` stays unchanged until operators explicitly promote rows into `printing_market_map`.
2. **Full audit trail** — Git-friendly diff of proposed `(printing_id, market_product_id)` pairs, scores, and reasons.
3. **Explicit opt-in** — PM or a future importer loads the overlay only when configured; default behavior unchanged.
4. **Easy rollback** — Remove or replace the file; no table rebuilds.

### Alternatives

| Approach | Pros | Cons |
|----------|------|------|
| **Sidecar SQLite** (`overlay.db`) | Indexed lookups, can hold multiple tiers | Second file to deploy/sync; less visible in review |
| **New overlay table in main DB** | Single connection, SQL joins | Requires migration and write access; higher risk if applied prematurely |

For a **first cleanup pass** after human review, importing CSV rows into real `printing_market_map` (append-only, idempotent `INSERT OR IGNORE`) is usually the production end state; the CSV remains the source of truth for *what* was approved.

## Input bundle

Latest candidate export used: **`card_catalog_map_candidates_20260404_001525`**

## Semantics

- `printing_id` in a real map row = **`card_variants.id`**.
- `market_product_id` in a real map row = **`market_products.id`** (internal PK).
