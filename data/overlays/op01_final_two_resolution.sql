-- OP01 elimination-match mismatch-resolution overlay (printing_market_map)
-- Generated: 2026-04-07
-- Source: user-sqlite-ro-snapshot verification (read-only) before authoring
-- Schema: id, printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at
--
-- Population (fail-closed, then cleared by exhaustive elimination):
--   elimination_match: OP01, exactly 2 card_variants per canonical code (base + alt),
--     exactly 2 market_products per canonical code (base product + event product),
--     base variant already mapped to base market product, leaving exactly one unmapped
--     variant and one orphan market product. Neither printing_id nor market_product_id
--     is referenced by any existing printing_market_map row or any prior overlay.
--
-- Semantic alignment rationale:
--   - "alt" describes the art treatment (alternate artwork vs base)
--   - "(Promotion Pack 2023)" / "(Store Championship Participation Pack)" describes
--     the distribution channel through which the alternate art was released
--   - These are orthogonal properties of the same physical card, not competing claims
--   - The base market product is separately listed and already mapped to the base variant
--   - Character names match: Ms. All Sunday = Ms. All Sunday, Hitokiri Kamazo = Hitokiri Kamazo
--
-- Collision checks (all pass):
--   - 0 of 2 printing_ids exist in printing_market_map
--   - 0 of 2 market_product_ids exist in printing_market_map
--   - 0 of 4 IDs appear in overlay-1 (op01_parallel_sp_mapping.sql, 21 rows)
--   - 0 of 4 IDs appear in overlay-2 (op01_promo_crosswalk_mapping.sql, 8 rows)
--   - 0 competing unmapped variants exist for either canonical code
--   - 0 competing orphan market products exist for either canonical code
--
-- MCP row counts: 2
-- Depends on: overlay-1 (21 rows) + overlay-2 (8 rows) — no ID overlap
-- DO NOT EXECUTE without operator approval

-- OP01-079 | alt "Alt" | mp:773 | Ms. All Sunday (Promotion Pack 2023)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (1053, 773, 'HIGH', 'elimination_match', 'OP01 overlay-3: alt; sole unmapped variant + sole orphan market product for OP01-079 after base-to-base locked; character identity confirmed; method=elimination_match', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 1053 AND market_product_id = 773

-- OP01-108 | alt "Alt" | mp:860 | Hitokiri Kamazo (Store Championship Participation Pack)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (1128, 860, 'HIGH', 'elimination_match', 'OP01 overlay-3: alt; sole unmapped variant + sole orphan market product for OP01-108 after base-to-base locked; character identity confirmed; method=elimination_match', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 1128 AND market_product_id = 860

-- ROLLBACK BLOCK (run all to reverse this overlay)
DELETE FROM printing_market_map WHERE printing_id = 1053 AND market_product_id = 773;
DELETE FROM printing_market_map WHERE printing_id = 1128 AND market_product_id = 860;
