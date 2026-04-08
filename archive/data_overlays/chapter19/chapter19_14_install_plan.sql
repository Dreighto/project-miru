-- ===================================================================
-- Chapter 19.14 — Schema-First Controlled Install Plan
-- Generated: 2026-04-05 03:43:07 UTC
-- ===================================================================
--
-- Source artifacts:
--   chapter19_11_install_plan.csv   (16-row reviewed batch)
--   chapter19_12_price_hydration.csv (hydrated prices)
--   chapter19_10_candidates.csv      (original candidate mapping)
--
-- Target table: printing_market_map
-- Schema:
--   id                INTEGER   PK AUTOINCREMENT
--   printing_id       INTEGER   NOT NULL
--   market_product_id INTEGER   NOT NULL
--   mapping_confidence TEXT      NOT NULL  DEFAULT 'UNVERIFIED'
--   mapping_method     TEXT
--   mapping_notes      TEXT
--   is_preferred       INTEGER   DEFAULT 1
--   created_at         TEXT      DEFAULT datetime('now')
--   updated_at         TEXT
-- Unique constraint: UNIQUE(printing_id, market_product_id)
--
-- CLEAR_TO_INSTALL: 16
-- Total rows in batch: 16
--
-- !!! DO NOT EXECUTE WITHOUT OPERATOR APPROVAL !!!
-- ===================================================================

BEGIN TRANSACTION;

-- OP15-001 | printing_id=6149 -> mp_id=5455 | Krieg (OP15-001) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6149, 5455, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-001 -> mp:5455 (Krieg (OP15-001) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6149 AND market_product_id = 5455;

-- OP15-008 | printing_id=6160 -> mp_id=5466 | Krieg (OP15-008) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6160, 5466, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-008 -> mp:5466 (Krieg (OP15-008) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6160 AND market_product_id = 5466;

-- OP15-022 | printing_id=6175 -> mp_id=5481 | Brook (OP15-022) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6175, 5481, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-022 -> mp:5481 (Brook (OP15-022) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6175 AND market_product_id = 5481;

-- OP15-032 | printing_id=6187 -> mp_id=5493 | Brook (OP15-032) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6187, 5493, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-032 -> mp:5493 (Brook (OP15-032) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6187 AND market_product_id = 5493;

-- OP15-039 | printing_id=6195 -> mp_id=5501 | Rebecca (OP15-039) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6195, 5501, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-039 -> mp:5501 (Rebecca (OP15-039) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6195 AND market_product_id = 5501;

-- OP15-047 | printing_id=6205 -> mp_id=5511 | Sanji (OP15-047) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6205, 5511, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-047 -> mp:5511 (Sanji (OP15-047) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6205 AND market_product_id = 5511;

-- OP15-053 | printing_id=6212 -> mp_id=5518 | Rebecca (OP15-053) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6212, 5518, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-053 -> mp:5518 (Rebecca (OP15-053) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6212 AND market_product_id = 5518;

-- OP15-058 | printing_id=6218 -> mp_id=5524 | Enel (OP15-058) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6218, 5524, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-058 -> mp:5524 (Enel (OP15-058) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6218 AND market_product_id = 5524;

-- OP15-060 | printing_id=6221 -> mp_id=5527 | Enel (OP15-060) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6221, 5527, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-060 -> mp:5527 (Enel (OP15-060) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6221 AND market_product_id = 5527;

-- OP15-086 | printing_id=6251 -> mp_id=5557 | Nami (OP15-086) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6251, 5557, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-086 -> mp:5557 (Nami (OP15-086) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6251 AND market_product_id = 5557;

-- OP15-092 | printing_id=6258 -> mp_id=5564 | Monkey.D.Luffy (OP15-092) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6258, 5564, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-092 -> mp:5564 (Monkey.D.Luffy (OP15-092) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6258 AND market_product_id = 5564;

-- OP15-098 | printing_id=6265 -> mp_id=5571 | Monkey.D.Luffy (OP15-098) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6265, 5571, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-098 -> mp:5571 (Monkey.D.Luffy (OP15-098) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6265 AND market_product_id = 5571;

-- OP15-109 | printing_id=6277 -> mp_id=5583 | Nico Robin (OP15-109) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6277, 5583, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-109 -> mp:5583 (Nico Robin (OP15-109) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6277 AND market_product_id = 5583;

-- OP15-113 | printing_id=6282 -> mp_id=5588 | Roronoa Zoro (OP15-113) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6282, 5588, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-113 -> mp:5588 (Roronoa Zoro (OP15-113) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6282 AND market_product_id = 5588;

-- OP15-118 | printing_id=6290 -> mp_id=5596 | Enel (OP15-118) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6290, 5596, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-118 -> mp:5596 (Enel (OP15-118) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6290 AND market_product_id = 5596;

-- OP15-119 | printing_id=6293 -> mp_id=5599 | Monkey.D.Luffy (OP15-119) (Alternate Art) | Foil
INSERT INTO printing_market_map (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes, is_preferred, created_at, updated_at) VALUES (6293, 5599, 'HIGH', 'chapter19_batch2_install', 'chapter19.14 reviewed OP15 alt-art batch install; OP15-119 -> mp:5599 (Monkey.D.Luffy (OP15-119) (Alternate Art)); subtype=Foil', 1, datetime('now'), datetime('now'));
-- rollback: DELETE FROM printing_market_map WHERE printing_id = 6293 AND market_product_id = 5599;

COMMIT;

-- ===================================================================
-- GROUPED ROLLBACK BLOCK
-- Use this block to reverse all installs from this chapter.
-- ===================================================================
-- BEGIN TRANSACTION;
-- DELETE FROM printing_market_map WHERE printing_id = 6149 AND market_product_id = 5455;
-- DELETE FROM printing_market_map WHERE printing_id = 6160 AND market_product_id = 5466;
-- DELETE FROM printing_market_map WHERE printing_id = 6175 AND market_product_id = 5481;
-- DELETE FROM printing_market_map WHERE printing_id = 6187 AND market_product_id = 5493;
-- DELETE FROM printing_market_map WHERE printing_id = 6195 AND market_product_id = 5501;
-- DELETE FROM printing_market_map WHERE printing_id = 6205 AND market_product_id = 5511;
-- DELETE FROM printing_market_map WHERE printing_id = 6212 AND market_product_id = 5518;
-- DELETE FROM printing_market_map WHERE printing_id = 6218 AND market_product_id = 5524;
-- DELETE FROM printing_market_map WHERE printing_id = 6221 AND market_product_id = 5527;
-- DELETE FROM printing_market_map WHERE printing_id = 6251 AND market_product_id = 5557;
-- DELETE FROM printing_market_map WHERE printing_id = 6258 AND market_product_id = 5564;
-- DELETE FROM printing_market_map WHERE printing_id = 6265 AND market_product_id = 5571;
-- DELETE FROM printing_market_map WHERE printing_id = 6277 AND market_product_id = 5583;
-- DELETE FROM printing_market_map WHERE printing_id = 6282 AND market_product_id = 5588;
-- DELETE FROM printing_market_map WHERE printing_id = 6290 AND market_product_id = 5596;
-- DELETE FROM printing_market_map WHERE printing_id = 6293 AND market_product_id = 5599;
-- COMMIT;
