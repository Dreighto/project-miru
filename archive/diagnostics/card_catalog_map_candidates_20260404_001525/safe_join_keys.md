# Safe join keys (future repair pass)

## Safest (schema-aligned)

- **`printing_market_map.printing_id` = `card_variants.id`** — internal printing/variant row.
- **`printing_market_map.market_product_id` = `market_products.id`** — internal PK, not the external TEXT id.

## Strong structured matching (composite)

- **`market_products.market_set_code` + `market_products.market_number`** together — aligns with TCGCSV extended
  "Number" + mapped set code from `group_set_mapping.json` in rebuild tooling.
- Add **`market_variant_label` / product name tokens** when multiple `market_products` share the same set+number
  (e.g. parallels).

## Moderately safe

- **`cards.canonical_code`** vs **`market_products.market_number`** when formats align (e.g. `EB01-001`).
- **`card_variants.release_set_code`** or **`cards.set_code`** vs **`market_products.market_set_code`** when both populated.

## Risky alone

- **`card_variants.print_id`** — human-readable; do not equate to `printing_market_map.printing_id`.
- **`market_products.market_product_id` (TEXT)** — external TCG id; fine for display, not the FK column for the map table.
- **`card_variants.tcgplayer_product_id`** — largely empty in this DB; do not rely on as primary key path.
- **Image filename only** — helpful hint, not sufficient alone.
- **Name-only fuzzy match** without set+number — high collision risk.

## Example safe composite patterns

1. `canonical_code == market_number` AND `set_code == market_set_code` AND variant family consistent with `market_variant_label`.
2. Parallel: same as (1) plus parallel index / "Parallel N" consistency in market name or label.
3. Base: same as (1) with `is_base=1` and normal/empty market variant label.
