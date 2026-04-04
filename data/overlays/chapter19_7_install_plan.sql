-- Chapter 19.7 Install Plan
-- Generated: 2026-04-04T07:29:30
-- Generated from: D:\dev\tcg-watcher-worktree\data\overlays\chapter19_6_alt_sibling_candidates.csv
-- Read-only verification snapshot taken before plan generation
-- printing_market_map schema: id, printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at
-- Scope: printing_market_map only ? 10 rows scoped
-- CLEAR_TO_INSTALL count: 10
-- DO NOT EXECUTE without operator approval
-- Rollback statements follow each INSERT

-- OP15-002 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Lucy (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6151, 5457, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6151 AND market_product_id = 5457

-- OP15-003 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Alvida (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6153, 5459, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6153 AND market_product_id = 5459

-- OP15-007 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Gin (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6158, 5464, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6158 AND market_product_id = 5464

-- OP15-025 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Kuro (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6179, 5485, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6179 AND market_product_id = 5485

-- OP15-046 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Sabo (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6203, 5509, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6203 AND market_product_id = 5509

-- OP15-061 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Ohm (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6223, 5529, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6223 AND market_product_id = 5529

-- OP15-077 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Lightning Dragon (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6240, 5546, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6240 AND market_product_id = 5546

-- OP15-078 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Mamaragan (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6242, 5548, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6242 AND market_product_id = 5548

-- OP15-114 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Wyper (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6284, 5590, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6284 AND market_product_id = 5590

-- OP15-116 | is_parallel=0;is_sp=0;is_tr=0;is_alt=1;is_manga_rare=0;is_anniversary=0 | Gum-Gum Golden Rifle (Alternate Art)
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6287, 5593, 'HIGH', 'chapter19_overlay', 'chapter19.7 planned install from chapter19_6 alt sibling selection', 1, datetime('now'), datetime('now'));
-- ROLLBACK: DELETE FROM printing_market_map WHERE printing_id = 6287 AND market_product_id = 5593

-- ROLLBACK BLOCK (run all to fully reverse install)
DELETE FROM printing_market_map WHERE printing_id = 6151 AND market_product_id = 5457;
DELETE FROM printing_market_map WHERE printing_id = 6153 AND market_product_id = 5459;
DELETE FROM printing_market_map WHERE printing_id = 6158 AND market_product_id = 5464;
DELETE FROM printing_market_map WHERE printing_id = 6179 AND market_product_id = 5485;
DELETE FROM printing_market_map WHERE printing_id = 6203 AND market_product_id = 5509;
DELETE FROM printing_market_map WHERE printing_id = 6223 AND market_product_id = 5529;
DELETE FROM printing_market_map WHERE printing_id = 6240 AND market_product_id = 5546;
DELETE FROM printing_market_map WHERE printing_id = 6242 AND market_product_id = 5548;
DELETE FROM printing_market_map WHERE printing_id = 6284 AND market_product_id = 5590;
DELETE FROM printing_market_map WHERE printing_id = 6287 AND market_product_id = 5593;
