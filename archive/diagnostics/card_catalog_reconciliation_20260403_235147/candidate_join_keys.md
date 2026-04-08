# Candidate join keys (card_catalog.db)

Observed in this schema (see `PRAGMA table_info` / prior probe exports).

## Strong / safe

- **`card_variants.id` → `printing_market_map.printing_id`** — This is the active bridge used in
  repo tooling (`JOIN card_variants cv ON cv.id = pmm.printing_id`). Treat as **canonical** for automation.
- **`printing_market_map.market_product_id` → `market_products.id`** — INTEGER internal PK on
  `market_products`; **not** the TEXT `market_products.market_product_id` (external TCG id).
- **`market_prices.market_product_fk` → `market_products.id`** — Standard price attachment.

## Moderately safe (with validation)

- **`cards.id` → `card_variants.card_id`** — Core catalog structure.
- **`tcgplayer_product_id` on `card_variants`** — Shortcut to TCGPlayer product id when populated;
  compare against `market_products.market_product_id` (TEXT) only after normalizing types.

## Risky / ambiguous

- **`card_variants.print_id`** — Human-readable code; **not** the same as `printing_market_map.printing_id`
  in this database (type and semantics differ). Do not assume `print_id` joins to `printing_market_map`
  without a documented mapping rule.
- **`market_products.market_product_id` (TEXT)** — External id; join via `market_products.id` from the map.
- **`canonical_code` / set+number** — Good for human reconciliation; risk of collisions or format drift across sources.
- **`market_number` / “ext number”** — Useful for matching within a set; weak alone across sets.
- **Variant family fields** — Not present on `card_variants` in the probed schema; `variant_key` /
  `variant_label` are the local discriminantors.
- **Image paths (`image_path`, `image_url`, `image_assets.local_path`)** — Great for diagnostics;
  filenames alone are risky as unique keys (duplicates, moves).

## `image_assets`

`image_assets.printing_id` aligns with **`card_variants.id`** in the same way as `printing_market_map.printing_id`.
