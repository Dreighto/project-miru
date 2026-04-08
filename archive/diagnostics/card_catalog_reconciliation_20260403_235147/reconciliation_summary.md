# Card catalog reconciliation summary

**Generated (UTC):** 20260403_235147
**Database:** `D:\dev\tcg-watcher-worktree\data\card_catalog.db`
**Read-only URI:** `file:...?mode=ro`

## Interpretation note (linkage)

In this worktree schema, `printing_market_map.printing_id` joins to **`card_variants.id`**
(internal variant / printing row), not to the human-readable `card_variants.print_id` string.
Tools such as `tools/rebuild_market_tables.py` use `JOIN card_variants cv ON cv.id = pmm.printing_id`.

The `print_id` column is still exported for operator diagnostics; empty `print_id` is flagged as
`missing_print_id` even when a `printing_market_map` row exists keyed by `card_variants.id`.

## Row counts (variant-level)

| Bucket | Count |
|--------|------:|
| Total `card_variants` | 5413 |
| Unresolved (no chain price row with non-null coalesced price **and** no tcgplayer column price) | 3100 |
| Unresolved with empty `print_id` | 0 |
| Unresolved with no `printing_market_map` for `cv.id` | 3099 |
| Unresolved with map but no resolvable `market_products` row | 0 |
| Unresolved with product but no `market_prices` row with any price column set | 1 |
| Resolved **only** via tcgplayer columns (no chain price) | 0 |
| Image present (`image_path`/`image_url` or `image_assets` by `printing_id=cv.id`) but no price | 3100 |
| `tcgplayer_product_id` null/0 (all variants) | 5413 |

## Dominant `failure_stage` (unresolved subset)

**missing_printing_market_map** — 3099 rows.

Stage definitions:

- `missing_print_id` — `print_id` column empty (after trim).
- `missing_printing_market_map` — no `printing_market_map` row with `printing_id = card_variants.id`.
- `missing_market_product` — map exists but internal FK does not resolve to a `market_products` row.
- `missing_market_price` — product exists (via map) but no `market_prices` row with a non-null coalesced price.
- `missing_tcgplayer_product_id_only` — used when **price exists** (chain or column) but tcgplayer id empty; these are **not** counted as price-unresolved.
- `other` — price-unresolved but none of the above (investigate manually).

Price usability: chain side uses non-null `COALESCE(market_price, mid_price, low_price, high_price, direct_low_price, listed_median_price)` on **any** joined price row for the mapped product; variant side uses non-null `COALESCE(tcgplayer_market_price, tcgplayer_mid_price, tcgplayer_low_price)`.

## Top 10 failure patterns (unresolved)

Patterns encode: image flag, has map, has product, has chain price, tcgplayer id present, failure_stage.

| Pattern | Count |
|---------|------:|
| `img=1|pmm=0|mp=0|mpr=0|tcgpid=0|stage=missing_printing_market_map` | 3099 |
| `img=1|pmm=1|mp=1|mpr=0|tcgpid=0|stage=missing_market_price` | 1 |

## Which linkage step fails most often (unresolved)

Among unresolved rows, the staged classifier above attributes the first failing step.
The highest-frequency `failure_stage` is listed above; raw counts are in `failure_stage_counts.csv`.

## Missing price attribution (summary)

See bucket table: missing prices are primarily explained by **`failure_stage`** distribution
(`failure_stage_counts.csv`). Empty `print_id` is common metadata debt even when internal
`printing_id` mapping uses `card_variants.id`.

## Exports

| File | Description |
|------|-------------|
| `unresolved_variants_full.csv` | Up to 2000 price-unresolved variants |
| `image_without_price_detailed.csv` | Variants with image signals but no price |
| `missing_tcgplayer_product_id_detailed.csv` | `tcgplayer_product_id` null/0 (up to 2000) |
| `failure_stage_counts.csv` | Aggregated stages for unresolved |
| `likely_recoverable_rows.csv` | Map+product OK but price missing, or chain price OK but tcg id missing |
| `candidate_join_keys.md` | Join key notes |
| `reconciliation_summary.md` | This file |

## Verification

- Script: `diagnostics/export_card_catalog_reconciliation.py`
- DB opened read-only; no migrations or updates performed.
- `failure_stage_counts.csv` counts **price-unresolved** variants only (excludes rows that already have chain or tcgplayer column prices).
