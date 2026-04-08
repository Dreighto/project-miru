# Card Catalog Diagnostic Summary

**Generated:** 20260403_225927
**DB:** `D:\dev\tcg-watcher-worktree\data\card_catalog.db`
**DB size:** 47,869,952 bytes
**Tables:** 33 total, 28 relevant

## Source-of-Truth Candidates

| Category | Tables |
|----------|--------|
| Card identity | `bandai_cardlist_scrape` (51 rows), `card_intelligence` (2497 rows), `card_relationships` (61679 rows), `card_roles` (3466 rows), `cards` (2497 rows), `miru_card_insights` (242 rows), `miru_card_legality` (3 rows), `miru_card_usage` (0 rows) |
| Variants / prints | `card_variants` (5413 rows), `image_variant_analysis` (1951 rows), `miru_variant_index` (51 rows) |
| Prices / market | `market_prices` (5163 rows), `market_products` (5301 rows), `printing_market_map` (4581 rows), `tcgplayer_products` (154 rows) |
| Images / assets | `image_assets` (0 rows), `image_variant_analysis` (1951 rows) |
| Sources / provenance | `official_source_refs` (0 rows) |
| Mappings / TCG IDs | `tcgplayer_products` (154 rows) |

## Observed Join Keys

`canonical_code`, `card_code`, `card_id`, `group_id`, `image_path`, `image_url`, `print_id`, `product_id`, `set_code`, `source`, `source_id`, `variant_key`

## Key Linkage Chain

```
cards.id
  -> card_variants.card_id  (1:N, variant_key differentiates)
     -> card_variants.print_id
        -> printing_market_map.printing_id  (bridge to market)
           -> printing_market_map.market_product_id
              -> market_products.market_product_id
                 -> market_prices.market_product_fk = market_products.id
     -> card_variants.tcgplayer_product_id  (direct shortcut to TCGplayer)
```

## Optional Diagnostics

- `missing_price_candidates.csv`: 500 rows
- `image_without_price_candidates.csv`: 500 rows
- `duplicate_mapping_candidates.csv`: 0 rows (query ran clean, no results)

## Risk Areas

_Review the sample CSVs and optional diagnostic outputs for:_
- Duplicate `ext_product_id` or `canonical_code` values across tables
- Multiple tables serving similar mapping roles (e.g. both `market_products` and a separate price table)
- Variants with images but no market linkage
- Cards present in `cards` but absent from price/market tables
- `printing_market_map` uses `printing_id` (= `card_variants.print_id`) NOT `card_id`
- `market_products` has NO direct FK to `cards` — linkage is only through variant print_id or tcgplayer_product_id
