# Implementation notes (future, conservative)

## Preferred lookup order for PM price resolution

1. **Real `printing_market_map`** — if a row exists for `card_variants.id`, use existing join path (authoritative once committed).
2. **Approved overlay** — if no real map row, consult an explicitly configured overlay source (CSV path or small SQLite sidecar) keyed by `printing_id` → `market_products.id`, then join `market_prices` the same way.
3. **Fail closed** — if neither applies, return empty price chain (current safe behavior).

This preserves production data while allowing staged rollout.

## Likely touchpoints (if approved later)

- `pm/app.py` — `get_card_price_chain(db_path, printing_id)` currently joins only `printing_market_map` → `market_products` → `market_prices`. A minimal change is a **fallback** after the main query returns empty: resolve `printing_id` through an in-memory dict loaded from the overlay CSV at startup or on first use.
- Optional: shared helper module for “resolve printing → market_product_pk” used by PM and any dashboard duplicate logic.
- **Not recommended:** running `tools/rebuild_market_tables.py` for this pass (destructive full wipe of market tables).

## Promotion workflow (operators)

1. Review `medium_review_queue.csv` and spot-check `high_confidence` rows.
2. Approve subset → append to `printing_market_map` via controlled script (separate task) or maintain overlay CSV until bulk insert is scheduled.
3. Re-run reconciliation export to confirm reduced `missing_printing_market_map` bucket.
