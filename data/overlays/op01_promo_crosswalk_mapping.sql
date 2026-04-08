-- OP01 promo keyword-crosswalk mismatch-resolution overlay (printing_market_map)
-- Generated: 2026-04-07
-- Source: user-sqlite-ro-snapshot verification (read-only) before authoring
-- Schema: id, printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at
--
-- Population (fail-closed):
--   promo_keyword_crosswalk: OP01, variant_key = promo, exactly one market_products row
--     where market_number = cards.canonical_code AND product_name contains a keyword
--     that semantically matches the variant_label. Neither printing_id nor
--     market_product_id is referenced by any existing printing_market_map row.
--     No competing variant or market product claims the same keyword space.
--
-- Keyword crosswalk pairs:
--   "1St Anniversary Alt" ↔ "(English Version 1st Anniversary Set)" — 5 rows
--   "Wanted"              ↔ "(Wanted Poster)"                       — 1 row
--   "S N"                 ↔ "[Serial Number]"                       — 1 row
--   "2Nd Anniversary Alt" ↔ "(Japanese Version 2nd Anniversary Set)"— 1 row
--
-- Collision checks (all pass):
--   - 0 of 8 printing_ids exist in printing_market_map
--   - 0 of 8 market_product_ids exist in printing_market_map
--   - 0 competing unmapped variants share the same keyword space per code
--   - 0 additional market products match the same keyword per code
--
-- MCP row counts: 5 anniversary + 1 wanted + 1 serial + 1 2nd-anniversary = 8
-- Depends on: op01_parallel_sp_mapping.sql (21 rows) — no ID overlap
-- DO NOT EXECUTE without operator approval

-- OP01-004 | promo "1St Anniversary Alt" | mp:1236 | Usopp (English Version 1st Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (720, 1236, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "1St Anniversary Alt" ↔ "English Version 1st Anniversary Set" for OP01-004; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 720 AND market_product_id = 1236

-- OP01-013 | promo "1St Anniversary Alt" | mp:1237 | Sanji (English Version 1st Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (758, 1237, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "1St Anniversary Alt" ↔ "English Version 1st Anniversary Set" for OP01-013; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 758 AND market_product_id = 1237

-- OP01-016 | promo "1St Anniversary Alt" | mp:1238 | Nami (English Version 1st Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (779, 1238, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "1St Anniversary Alt" ↔ "English Version 1st Anniversary Set" for OP01-016; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 779 AND market_product_id = 1238

-- OP01-017 | promo "1St Anniversary Alt" | mp:1243 | Nico Robin (English Version 1st Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (798, 1243, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "1St Anniversary Alt" ↔ "English Version 1st Anniversary Set" for OP01-017; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 798 AND market_product_id = 1243

-- OP01-025 | promo "1St Anniversary Alt" | mp:1240 | Roronoa Zoro (English Version 1st Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (836, 1240, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "1St Anniversary Alt" ↔ "English Version 1st Anniversary Set" for OP01-025; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 836 AND market_product_id = 1240

-- OP01-030 | promo "2Nd Anniversary Alt" | mp:1457 | In Two Years!! At the Sabaody Archipelago!! (One Piece Japanese Version 2nd Anniversary Set)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (859, 1457, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "2Nd Anniversary Alt" ↔ "Japanese Version 2nd Anniversary Set" for OP01-030; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 859 AND market_product_id = 1457

-- OP01-051 | promo "Wanted" | mp:2143 | Eustass"Captain"Kid (Wanted Poster)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (950, 2143, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "Wanted" ↔ "Wanted Poster" for OP01-051; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 950 AND market_product_id = 2143

-- OP01-120 | promo "S N" | mp:928 | Shanks (Championship 2023) [Serial Number]
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (1164, 928, 'HIGH', 'promo_keyword_crosswalk', 'OP01 overlay-2: promo; "S N" ↔ "Serial Number" for OP01-120; method=promo_keyword_crosswalk', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 1164 AND market_product_id = 928

-- ROLLBACK BLOCK (run all to reverse this overlay)
DELETE FROM printing_market_map WHERE printing_id = 720 AND market_product_id = 1236;
DELETE FROM printing_market_map WHERE printing_id = 758 AND market_product_id = 1237;
DELETE FROM printing_market_map WHERE printing_id = 779 AND market_product_id = 1238;
DELETE FROM printing_market_map WHERE printing_id = 798 AND market_product_id = 1243;
DELETE FROM printing_market_map WHERE printing_id = 836 AND market_product_id = 1240;
DELETE FROM printing_market_map WHERE printing_id = 859 AND market_product_id = 1457;
DELETE FROM printing_market_map WHERE printing_id = 950 AND market_product_id = 2143;
DELETE FROM printing_market_map WHERE printing_id = 1164 AND market_product_id = 928;
