# Overlay effect summary (simulated)

**Input:** `card_catalog_map_candidates_20260404_001525` → `high_confidence_candidates.csv`  
**Simulation:** Read-only `card_catalog.db`; for each overlay target `market_products.id`, check latest `market_prices` row by `captured_at` (ties: first returned).  
**Usable price:** any of schema columns market_price, mid_price, low_price, high_price, direct_low_price, listed_median_price is non-null on that row.

## Counts

| Metric | Count |
|--------|------:|
| High-confidence overlay rows examined | 1037 |
| Would gain price coverage via overlay (`has_price_via_overlay=1`) | 1036 |
| Would **not** resolve to a usable price with overlay | 1 |
| — of those: no `market_prices` row for candidate product PK | 1 |
| — of those: price row exists but all tracked price columns null | 0 |

## Note resolution breakdown (simulation)

| Note | Count |
|------|------:|
| `at_least_one_price_column_non_null` | 1036 |
| `no_market_prices_row_for_product_pk` | 1 |

## Products pointed to by overlay but lacking usable price (distinct PKs)

**1** distinct `market_products.id` values (see simulation `notes` and `has_price_via_overlay=0`).

## Caveats

- PM today may pick a different `market_prices` row (e.g. subtype ordering in `get_card_price_chain`); this simulation uses **latest `captured_at` only**.
- Overlay does not create prices; it only exposes existing `market_prices` through a synthetic map path.
