# Candidate mapping summary (missing_printing_market_map)

**Generated (UTC):** 20260404_001525
**Database:** `D:\dev\tcg-watcher-worktree\data\card_catalog.db` (read-only)

## Scope

- All `card_variants` with **no** `printing_market_map` row where `printing_id = card_variants.id`
  (dominant reconciliation failure bucket).

Rows examined: **3099**

## Candidate counts

| Tier | Count |
|------|------:|
| High confidence (unique, strong score, `needs_manual_review=0`) | 1037 |
| Medium confidence (`match_confidence=medium`) | 501 |
| Low confidence (`match_confidence=low`) | 1490 |
| Manual review CSV (medium/low and/or `needs_manual_review=1` when a candidate pk exists) | 1991 |
| No safe candidate (`none` or empty `candidate_market_product_pk`) | 71 |

## Top mismatch / gate causes (heuristic counters)

| Cause | Count |
|-------|------:|
| `tight_top_two_scores` | 1491 |
| `no_or_weak_candidate_pool` | 71 |
| `empty_pool_after_index` | 71 |
| `confidence_gate` | 71 |

## Fields most often present in winning `match_reasons` (signal frequency)

| Signal prefix | Times in best-row reasons |
|---------------|---------------------------:|
| `market_number_eq_canonical_or_extracted` | 3028 |
| `source_tcgcsv` | 3028 |
| `card_name_substring_product` | 2932 |
| `set_code_match` | 2918 |
| `rarity_match` | 2670 |
| `image_basename_number_agrees` | 2487 |
| `parallel_variant_no_parallel_market` | 1221 |
| `alt_art_signal` | 448 |
| `parallel_token_in_market_fields` | 197 |
| `set_code_mismatch` | 110 |
| `parallel_index_loose_in_name` | 30 |
| `name_token_overlap` | 19 |

## Clustering (see `cluster_breakdown.csv`)

Dimensions included: variant source, derived variant family from `variant_key`/`is_base`, card rarity, `cards.set_code`,
`card_variants.release_set_code`, `cards.distribution_source`, and `match_confidence`. Strong skew in any bucket
usually indicates a systematic ingest or matcher gap for that slice.

## `tools/rebuild_market_tables.py` (read-only review)

`tools/rebuild_market_tables.py` calls `match_card_variant` then `insert_printing_market_map(printing_id=int(match['printing_id']), market_product_id=market_product_fk)` where `market_product_fk` is the return value of `upsert_market_product` (i.e. **`market_products.id`**). `match_card_variant` resolves **`card_variants.id`** as `printing_id` (see `miru_ai/workers/tcgcsv_fetcher.py`). The diagnostic JOIN `cv.id = pmm.printing_id` matches this script.

**Filtering / exclusion:** The script only processes TCGCSV groups listed in `group_set_mapping.json` with `confidence == "high"` and with both `products.json` and `prices.json` present under `data/tcgcsv/{group_id}/`. Any set/group not in that manifest path never gets products/maps from this tool, which can leave large `missing_printing_market_map` populations even when `market_products` rows exist from other ingest paths. `match_card_variant` also fails closed on variant families it does not recognize, ambiguous multi-row matches, or when `print_id` / `variant_key` / `release_set_code` patterns do not satisfy its SQL filters.

(Read-only scan: file present, 11453 chars.)


## Method (conservative)

- Index `market_products` by `(market_set_code, market_number)` and by `market_number` alone.
- For each unresolved variant, derive `derived_number_key` from `canonical_code` or `print_id` (or image basename).
- Score candidates with weighted signals (exact number, set match, rarity, variant-family hints vs `market_variant_label` / product name).
- Penalize set mismatches and obvious family mismatches (e.g. base vs parallel product).
- **Ambiguity:** small gap between top two scores forces manual review or `low` confidence.
- **No OCR**; image path used only for filename code hints.

## Outputs

- `missing_printing_market_map_candidates.csv` — full capped export (3099 rows)
- `high_confidence_candidates.csv`
- `manual_review_candidates.csv`
- `unresolved_no_candidate.csv`
- `cluster_breakdown.csv`
- `safe_join_keys.md`
