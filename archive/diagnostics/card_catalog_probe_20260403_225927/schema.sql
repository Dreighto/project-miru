-- index: idx_card_relationships_card_id
CREATE INDEX idx_card_relationships_card_id
    ON card_relationships(card_id);

-- index: idx_card_relationships_related_entity
CREATE INDEX idx_card_relationships_related_entity
    ON card_relationships(related_entity);

-- index: idx_card_relationships_type
CREATE INDEX idx_card_relationships_type
    ON card_relationships(relationship_type);

-- index: idx_cards_card_name
CREATE INDEX idx_cards_card_name ON cards(card_name);

-- index: idx_cards_set_code
CREATE INDEX idx_cards_set_code ON cards(set_code);

-- index: idx_image_assets_asset_type
CREATE INDEX idx_image_assets_asset_type ON image_assets(asset_type);

-- index: idx_image_assets_printing_id
CREATE INDEX idx_image_assets_printing_id ON image_assets(printing_id);

-- index: idx_image_variant_analysis_review
CREATE INDEX idx_image_variant_analysis_review ON image_variant_analysis(review_status, sp_marker_detected);

-- index: idx_market_prices_captured_at
CREATE INDEX idx_market_prices_captured_at ON market_prices(captured_at);

-- index: idx_market_prices_product_fk
CREATE INDEX idx_market_prices_product_fk ON market_prices(market_product_fk);

-- index: idx_market_prices_subtype
CREATE INDEX idx_market_prices_subtype ON market_prices(subtype_name);

-- index: idx_market_products_group_id
CREATE INDEX idx_market_products_group_id ON market_products(market_group_id);

-- index: idx_market_products_market_number
CREATE INDEX idx_market_products_market_number ON market_products(market_number);

-- index: idx_market_products_product_name
CREATE INDEX idx_market_products_product_name ON market_products(product_name);

-- index: idx_market_products_variant_label
CREATE INDEX idx_market_products_variant_label ON market_products(market_variant_label);

-- index: idx_miru_action_history_created_at
CREATE INDEX idx_miru_action_history_created_at ON miru_action_history(created_at DESC, action_id);

-- index: idx_miru_action_history_target
CREATE INDEX idx_miru_action_history_target ON miru_action_history(target_type, target_id, created_at DESC);

-- index: idx_miru_card_insights_card_id
CREATE INDEX idx_miru_card_insights_card_id ON miru_card_insights(card_id, confidence DESC, updated_at DESC);

-- index: idx_miru_card_legality_format_state
CREATE INDEX idx_miru_card_legality_format_state ON miru_card_legality(format, legality_state);

-- index: idx_miru_publication_batch_items_batch
CREATE INDEX idx_miru_publication_batch_items_batch ON miru_publication_batch_items(batch_id, status, updated_at DESC);

-- index: idx_miru_publication_batch_items_target
CREATE INDEX idx_miru_publication_batch_items_target ON miru_publication_batch_items(target_id, updated_at DESC);

-- index: idx_miru_publication_batches_status
CREATE INDEX idx_miru_publication_batches_status ON miru_publication_batches(batch_status, updated_at DESC);

-- index: idx_miru_publication_stage_batch
CREATE INDEX idx_miru_publication_stage_batch ON miru_publication_stage(batch_id, stage_state, updated_at DESC);

-- index: idx_miru_publication_stage_state
CREATE INDEX idx_miru_publication_stage_state ON miru_publication_stage(stage_state, updated_at DESC, target_id);

-- index: idx_miru_review_queue_status
CREATE INDEX idx_miru_review_queue_status ON miru_review_queue(status, updated_at DESC, queue_type);

-- index: idx_miru_review_queue_target
CREATE INDEX idx_miru_review_queue_target ON miru_review_queue(target_type, target_id, updated_at DESC);

-- index: idx_miru_validations_verified_at
CREATE INDEX idx_miru_validations_verified_at ON miru_validations(verified_at);

-- index: idx_official_legality_history_card_format
CREATE INDEX idx_official_legality_history_card_format
    ON official_legality_history(card_code, format_name, region);

-- index: idx_official_legality_history_current
CREATE INDEX idx_official_legality_history_current
    ON official_legality_history(card_code, format_name, region, is_current) WHERE is_current = 1;

-- index: idx_official_legality_history_upcoming
CREATE INDEX idx_official_legality_history_upcoming
    ON official_legality_history(card_code, format_name, region, is_upcoming) WHERE is_upcoming = 1;

-- index: idx_official_rule_notices_effective
CREATE INDEX idx_official_rule_notices_effective
    ON official_rule_notices(effective_at, status);

-- index: idx_official_rule_notices_format_status
CREATE INDEX idx_official_rule_notices_format_status
    ON official_rule_notices(format_name, status);

-- index: idx_osr_canonical_card_id
CREATE INDEX idx_osr_canonical_card_id ON official_source_refs(canonical_card_id);

-- index: idx_osr_printing_id
CREATE INDEX idx_osr_printing_id ON official_source_refs(printing_id);

-- index: idx_osr_source_type
CREATE INDEX idx_osr_source_type ON official_source_refs(source_type);

-- index: idx_perception_batch
CREATE INDEX idx_perception_batch
    ON miru_perception_ledger(batch_id);

-- index: idx_perception_card_code
CREATE INDEX idx_perception_card_code
    ON miru_perception_ledger(resolved_card_code);

-- index: idx_perception_dead_end
CREATE INDEX idx_perception_dead_end
    ON miru_perception_ledger(resolution_dead_end);

-- index: idx_perception_fields_discrepancy
CREATE INDEX idx_perception_fields_discrepancy
    ON miru_perception_ledger_fields(discrepancy_id);

-- index: idx_perception_fields_identity_critical
CREATE INDEX idx_perception_fields_identity_critical
    ON miru_perception_ledger_fields(is_identity_critical);

-- index: idx_perception_image_hash
CREATE INDEX idx_perception_image_hash
    ON miru_perception_ledger(image_hash);

-- index: idx_perception_known_variant
CREATE INDEX idx_perception_known_variant
    ON miru_perception_ledger(known_variant);

-- index: idx_perception_recurrence_dead_end
CREATE INDEX idx_perception_recurrence_dead_end
    ON miru_perception_ledger_recurrence(resolution_dead_end);

-- index: idx_perception_recurrence_dead_end_flag
CREATE INDEX idx_perception_recurrence_dead_end_flag
    ON miru_perception_ledger_recurrence(
        resolver_confidence_ever_reached_medium_or_high
    );

-- index: idx_perception_recurrence_key
CREATE INDEX idx_perception_recurrence_key
    ON miru_perception_ledger_recurrence(card_code, field_name, discrepancy_category);

-- index: idx_perception_status
CREATE INDEX idx_perception_status
    ON miru_perception_ledger(review_status, discrepancy_category);

-- index: idx_perception_suppression
CREATE INDEX idx_perception_suppression
    ON miru_perception_ledger(suppression_active);

-- index: idx_perception_variant_classification
CREATE INDEX idx_perception_variant_classification
    ON miru_perception_ledger(variant_classification);

-- index: idx_perception_variant_risk
CREATE INDEX idx_perception_variant_risk
    ON miru_perception_ledger(variant_risk_score);

-- index: idx_pmm_confidence
CREATE INDEX idx_pmm_confidence ON printing_market_map(mapping_confidence);

-- index: idx_pmm_market_product_id
CREATE INDEX idx_pmm_market_product_id ON printing_market_map(market_product_id);

-- index: idx_pmm_printing_id
CREATE INDEX idx_pmm_printing_id ON printing_market_map(printing_id);

-- index: idx_variant_index_active
CREATE INDEX idx_variant_index_active
    ON miru_variant_index(is_active);

-- index: idx_variant_index_base_code
CREATE INDEX idx_variant_index_base_code
    ON miru_variant_index(base_card_code);

-- index: idx_variant_index_category
CREATE INDEX idx_variant_index_category
    ON miru_variant_index(variant_category);

-- index: idx_variant_index_subtype
CREATE INDEX idx_variant_index_subtype
    ON miru_variant_index(variant_subtype);

-- index: idx_variant_index_variant_code
CREATE INDEX idx_variant_index_variant_code
    ON miru_variant_index(variant_card_code);

-- index: idx_variants_card_id
CREATE INDEX idx_variants_card_id ON card_variants(card_id);

-- index: idx_variants_variant_key
CREATE INDEX idx_variants_variant_key ON card_variants(variant_key);

-- table: bandai_cardlist_scrape
CREATE TABLE bandai_cardlist_scrape (
    bandai_series_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    rarities_json TEXT NOT NULL,
    error TEXT,
    scraped_at TEXT NOT NULL
);

-- table: card_intelligence
CREATE TABLE card_intelligence (
            card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            role_label TEXT NOT NULL DEFAULT '',
            role_summary TEXT NOT NULL DEFAULT '',
            deck_usage_summary TEXT NOT NULL DEFAULT '',
            price_value REAL,
            price_currency TEXT NOT NULL DEFAULT '',
            price_source TEXT NOT NULL DEFAULT '',
            price_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        , meta_relevance_score REAL, top_leaders_json TEXT NOT NULL DEFAULT '[]', rulings_summary TEXT NOT NULL DEFAULT '', price_trend_note TEXT NOT NULL DEFAULT '', confidence_score REAL, source_summary TEXT NOT NULL DEFAULT '', last_verified_at TEXT NOT NULL DEFAULT '', legality_state TEXT NOT NULL DEFAULT '', legality_note TEXT NOT NULL DEFAULT '', rulings_sources_json TEXT NOT NULL DEFAULT '[]', usage_profile_json TEXT NOT NULL DEFAULT '{}', section_confidence_json TEXT NOT NULL DEFAULT '{}', source_agreement_json TEXT NOT NULL DEFAULT '{}', projection_sections_json TEXT NOT NULL DEFAULT '[]', projection_source_updated_at TEXT NOT NULL DEFAULT '', last_sync_reason TEXT NOT NULL DEFAULT '', last_sync_mode TEXT NOT NULL DEFAULT '', last_priority_score REAL, last_priority_context_json TEXT NOT NULL DEFAULT '{}', publication_readiness TEXT NOT NULL DEFAULT '', publication_guardrail TEXT NOT NULL DEFAULT '', publication_rationale TEXT NOT NULL DEFAULT '', publication_updated_at TEXT NOT NULL DEFAULT '', approval_state TEXT NOT NULL DEFAULT '', promotion_state TEXT NOT NULL DEFAULT '', promotion_rationale TEXT NOT NULL DEFAULT '', promotion_updated_at TEXT NOT NULL DEFAULT '', publication_candidate_score REAL, publication_candidate_score_band TEXT NOT NULL DEFAULT '', publication_candidate_profile TEXT NOT NULL DEFAULT '', publication_candidate_reasons_json TEXT NOT NULL DEFAULT '[]', publication_candidate_risks_json TEXT NOT NULL DEFAULT '[]', publication_candidate_updated_at TEXT NOT NULL DEFAULT '', publish_status TEXT NOT NULL DEFAULT '', publish_reasons_json TEXT NOT NULL DEFAULT '[]', publish_risks_json TEXT NOT NULL DEFAULT '[]', publish_payload_json TEXT NOT NULL DEFAULT '{}', publish_updated_at TEXT NOT NULL DEFAULT '', dossier_gap_class TEXT NOT NULL DEFAULT '', dossier_gap_tags_json TEXT NOT NULL DEFAULT '[]', coverage_value_score REAL, coverage_value_band TEXT NOT NULL DEFAULT '', coverage_gap_summary TEXT NOT NULL DEFAULT '', revalidation_status TEXT NOT NULL DEFAULT '', revalidation_reason TEXT NOT NULL DEFAULT '', revalidation_priority_score REAL, revalidation_priority_bucket TEXT NOT NULL DEFAULT '', revalidation_updated_at TEXT NOT NULL DEFAULT '');

-- table: card_relationships
CREATE TABLE "card_relationships" (
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL,
    related_entity TEXT NOT NULL,
    related_entity_type TEXT NOT NULL CHECK(
        related_entity_type IN ('card', 'leader', 'archetype', 'package')
    ),
    relationship_type TEXT NOT NULL CHECK(
        relationship_type IN (
            'supports_leader',
            'frequently_appears_with',
            'searches_target',
            'protects_combo_piece',
            'enables_tempo_swing',
            'payoff_for_setup',
            'overlaps_archetype_package',
            'budget_alternative',
            'enables_cost_reduction',
            'provides_recursion',
            'provides_draw',
            'provides_removal',
            'provides_don_acceleration',
            'provides_life_recovery',
            'enables_finisher'
        )
    ),
    evidence_source TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK(
        confidence IN ('low', 'medium', 'high', 'verified')
    ),
    status TEXT NOT NULL DEFAULT 'inferred' CHECK(
        status IN ('inferred', 'corroborated', 'verified', 'rejected')
    ),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(card_id, related_entity, relationship_type)
);

-- table: card_roles
CREATE TABLE card_roles (
    card_id TEXT NOT NULL,
    role TEXT NOT NULL,
    role_confidence TEXT NOT NULL CHECK(
        role_confidence IN ('low', 'medium', 'high', 'verified')
    ),
    evidence TEXT NOT NULL,
    classification_source TEXT NOT NULL DEFAULT 'text_analysis',
    status TEXT NOT NULL DEFAULT 'inferred' CHECK(
        status IN ('inferred', 'corroborated', 'verified', 'rejected')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (card_id, role)
);

-- table: card_variants
CREATE TABLE card_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            variant_key TEXT NOT NULL,
            variant_label TEXT NOT NULL DEFAULT '',
            print_id TEXT NOT NULL DEFAULT '',
            release_set_code TEXT NOT NULL DEFAULT '',
            release_set_name TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local-catalog',
            is_base INTEGER NOT NULL DEFAULT 0,
            is_alt INTEGER NOT NULL DEFAULT 0,
            is_sp INTEGER NOT NULL DEFAULT 0,
            has_variant_evidence INTEGER NOT NULL DEFAULT 0, is_tr INTEGER NOT NULL DEFAULT 0, is_manga_rare INTEGER NOT NULL DEFAULT 0, is_golden_manga_rare INTEGER NOT NULL DEFAULT 0, is_promo INTEGER NOT NULL DEFAULT 0, is_serialized INTEGER NOT NULL DEFAULT 0, is_illustration_rare INTEGER NOT NULL DEFAULT 0, official_provenance TEXT, distribution_product_key TEXT, updated_at TEXT, tcgplayer_product_id INTEGER, tcgplayer_market_price REAL, tcgplayer_mid_price REAL, tcgplayer_low_price REAL, tcgplayer_price_updated_at TEXT,
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
            UNIQUE(card_id, variant_key, print_id)
        );

-- table: cards
CREATE TABLE cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_code TEXT NOT NULL UNIQUE,
            set_code TEXT NOT NULL DEFAULT '',
            card_number TEXT NOT NULL DEFAULT '',
            set_name TEXT NOT NULL DEFAULT '',
            card_name TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '',
            card_type TEXT NOT NULL DEFAULT '',
            cost INTEGER,
            power TEXT NOT NULL DEFAULT '',
            counter TEXT NOT NULL DEFAULT '',
            attribute TEXT NOT NULL DEFAULT '',
            traits TEXT NOT NULL DEFAULT '',
            life TEXT NOT NULL DEFAULT '',
            block_icon TEXT NOT NULL DEFAULT '',
            effect_text TEXT NOT NULL DEFAULT '',
            trigger_text TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]'
        , base_card_id TEXT NOT NULL DEFAULT '', is_variant INTEGER NOT NULL DEFAULT 0, variant_category TEXT NOT NULL DEFAULT '', variant_subtype TEXT NOT NULL DEFAULT '', stamp_type TEXT NOT NULL DEFAULT '', stamp_event_name TEXT NOT NULL DEFAULT '', stamp_placement TEXT NOT NULL DEFAULT '', distribution_source TEXT NOT NULL DEFAULT '', distribution_event TEXT NOT NULL DEFAULT '', is_serialized INTEGER NOT NULL DEFAULT 0, serial_number INTEGER, print_run INTEGER, is_premium_variant INTEGER NOT NULL DEFAULT 0, variant_meta_json TEXT NOT NULL DEFAULT '{}');

-- table: image_assets
CREATE TABLE image_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printing_id INTEGER NOT NULL,
    asset_type TEXT NOT NULL DEFAULT 'card_scan',
    local_path TEXT NOT NULL,
    source_label TEXT DEFAULT 'local_library',
    source_url TEXT,
    width INTEGER,
    height INTEGER,
    checksum TEXT,
    has_sample_watermark INTEGER DEFAULT 0,
    image_confidence TEXT DEFAULT 'UNVERIFIED',
    is_primary INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    FOREIGN KEY (printing_id) REFERENCES card_variants(id),
    UNIQUE(printing_id, local_path)
);

-- table: image_variant_analysis
CREATE TABLE image_variant_analysis (
                canonical_code TEXT NOT NULL PRIMARY KEY,
                image_path TEXT NOT NULL,
                sp_marker_detected INTEGER,
                parallel_marker_detected INTEGER,
                analysis_confidence TEXT NOT NULL DEFAULT 'high',
                raw_vision_response TEXT NOT NULL DEFAULT '',
                analysis_timestamp TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                operator_decision TEXT DEFAULT NULL
            );

-- table: market_prices
CREATE TABLE market_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_product_fk INTEGER NOT NULL,
    source_name TEXT NOT NULL DEFAULT 'tcgcsv',
    captured_at TEXT NOT NULL,
    currency TEXT DEFAULT 'USD',
    subtype_name TEXT,
    low_price REAL,
    mid_price REAL,
    high_price REAL,
    market_price REAL,
    direct_low_price REAL,
    listed_median_price REAL,
    sales_volume INTEGER,
    available_quantity INTEGER,
    raw_payload_json TEXT,
    FOREIGN KEY (market_product_fk) REFERENCES market_products(id),
    UNIQUE(market_product_fk, captured_at, subtype_name)
);

-- table: market_products
CREATE TABLE market_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL DEFAULT 'tcgcsv',
    market_product_id TEXT NOT NULL,
    market_group_id TEXT,
    market_category_id TEXT,
    product_name TEXT NOT NULL,
    clean_product_name TEXT,
    market_variant_label TEXT,
    market_set_name TEXT,
    market_set_code TEXT,
    market_number TEXT,
    rarity_market TEXT,
    subtype_support INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    url TEXT,
    image_url TEXT,
    raw_payload_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE(source_name, market_product_id)
);

-- table: miru_action_history
CREATE TABLE miru_action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id TEXT NOT NULL,
                action_title TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                execution_status TEXT NOT NULL DEFAULT '',
                eligibility TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.0,
                rationale TEXT NOT NULL DEFAULT '',
                sync_reason TEXT NOT NULL DEFAULT '',
                priority_score REAL,
                priority_bucket TEXT NOT NULL DEFAULT '',
                publication_readiness TEXT NOT NULL DEFAULT '',
                worker_hint TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

-- table: miru_card_insights
CREATE TABLE miru_card_insights (
            card_id TEXT NOT NULL,
            insight_type TEXT NOT NULL,
            insight_text TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, quality_tier TEXT NOT NULL DEFAULT '', source_ref TEXT NOT NULL DEFAULT '', leader_code TEXT NOT NULL DEFAULT '', generated_at INTEGER NOT NULL DEFAULT 0, used_sections_json TEXT NOT NULL DEFAULT '[]', sync_reason TEXT NOT NULL DEFAULT '', source_updated_at TEXT NOT NULL DEFAULT '', approval_state TEXT NOT NULL DEFAULT '', is_upcoming INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(card_id, insight_type),
            FOREIGN KEY(card_id) REFERENCES cards(canonical_code) ON DELETE CASCADE
        );

-- table: miru_card_legality
CREATE TABLE miru_card_legality (
            card_code TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'standard',
            legality_state TEXT NOT NULL DEFAULT 'unknown',
            effective_date TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            last_checked_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (card_code, format)
        );

-- table: miru_card_usage
CREATE TABLE miru_card_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_code TEXT NOT NULL,
            archetype_key TEXT NOT NULL DEFAULT '',
            usage_count INTEGER NOT NULL DEFAULT 0,
            format_name TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '',
            period_label TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(card_code, archetype_key, source_kind, period_label)
        );

-- table: miru_deck_archetypes
CREATE TABLE miru_deck_archetypes (
            archetype_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT '',
            representative_leader_code TEXT NOT NULL DEFAULT '',
            confidence_score REAL NOT NULL DEFAULT 0.0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

-- table: miru_meta_events
CREATE TABLE miru_meta_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            event_name TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT '',
            event_date TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

-- table: miru_perception_ledger
CREATE TABLE miru_perception_ledger (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    discrepancy_id              TEXT NOT NULL UNIQUE,
    batch_id                    TEXT NOT NULL DEFAULT '',
    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    image_ref                   TEXT NOT NULL DEFAULT '',
    ocr_engine                  TEXT NOT NULL DEFAULT '',
    ocr_run_id                  TEXT NOT NULL DEFAULT '',
    ocr_confidence              REAL NOT NULL DEFAULT 0.0,

    resolved_card_code          TEXT NOT NULL DEFAULT '',
    resolved_record_source      TEXT NOT NULL DEFAULT '',
    resolver_confidence         TEXT NOT NULL DEFAULT 'low',

    overall_status              TEXT NOT NULL DEFAULT 'open',
    discrepancy_category        TEXT NOT NULL DEFAULT '',
    severity                    TEXT NOT NULL DEFAULT 'low',

    recommended_action          TEXT NOT NULL DEFAULT '',

    review_status               TEXT NOT NULL DEFAULT 'open',
    resolution_category         TEXT NOT NULL DEFAULT '',
    reviewed_at                 TEXT NOT NULL DEFAULT '',
    reviewer                    TEXT NOT NULL DEFAULT '',
    final_disposition           TEXT NOT NULL DEFAULT '',
    notes                       TEXT NOT NULL DEFAULT '',

    suppression_active          INTEGER NOT NULL DEFAULT 0,

    recurrence_count            INTEGER NOT NULL DEFAULT 1,
    first_seen_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    resolution_dead_end         INTEGER NOT NULL DEFAULT 0,
    dead_end_flagged_at         TEXT NOT NULL DEFAULT '',

    reconciliation_eligible     INTEGER NOT NULL DEFAULT 0,
    last_reconciliation_attempt TEXT NOT NULL DEFAULT '',

    patch_candidate_state       TEXT NOT NULL DEFAULT '',
    patch_candidate_created_at  TEXT NOT NULL DEFAULT '',
    patch_candidate_updated_at  TEXT NOT NULL DEFAULT ''
, original_candidate_card_code TEXT NOT NULL DEFAULT '', image_source_ref TEXT NOT NULL DEFAULT '', source_context TEXT NOT NULL DEFAULT '', variant_risk_score REAL NOT NULL DEFAULT 0.0, variant_risk_signals_json TEXT NOT NULL DEFAULT '[]', known_variant INTEGER NOT NULL DEFAULT 0, variant_index_hit INTEGER NOT NULL DEFAULT 0, variant_classification TEXT NOT NULL DEFAULT '', variant_base_card_code TEXT NOT NULL DEFAULT '', variant_resolution_source TEXT NOT NULL DEFAULT '', resolver_confidence_history_json TEXT NOT NULL DEFAULT '[]', token_spend REAL, latency REAL, tier_used INTEGER NOT NULL DEFAULT 0, cache_hit INTEGER NOT NULL DEFAULT 0, image_hash TEXT NOT NULL DEFAULT '', crop_hash TEXT NOT NULL DEFAULT '', ocr_pipeline_version TEXT NOT NULL DEFAULT '', crop_version TEXT NOT NULL DEFAULT '');

-- table: miru_perception_ledger_fields
CREATE TABLE miru_perception_ledger_fields (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discrepancy_id      TEXT NOT NULL,
    field_name          TEXT NOT NULL,
    local_value         TEXT NOT NULL DEFAULT '',
    ocr_value           TEXT NOT NULL DEFAULT '',
    match_state         TEXT NOT NULL DEFAULT '',
    field_confidence    REAL NOT NULL DEFAULT 0.0,
    field_notes         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, normalized_local_value TEXT NOT NULL DEFAULT '', normalized_observed_value TEXT NOT NULL DEFAULT '', is_identity_critical INTEGER NOT NULL DEFAULT 0, normalization_applied TEXT NOT NULL DEFAULT '',

    FOREIGN KEY (discrepancy_id)
        REFERENCES miru_perception_ledger(discrepancy_id)
        ON DELETE CASCADE
);

-- table: miru_perception_ledger_recurrence
CREATE TABLE miru_perception_ledger_recurrence (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code               TEXT NOT NULL,
    field_name              TEXT NOT NULL,
    discrepancy_category    TEXT NOT NULL,
    recurrence_count        INTEGER NOT NULL DEFAULT 1,
    avg_ocr_confidence      REAL NOT NULL DEFAULT 0.0,
    highest_resolver_confidence TEXT NOT NULL DEFAULT 'low',
    first_seen_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    escalation_eligible     INTEGER NOT NULL DEFAULT 0,
    resolution_dead_end     INTEGER NOT NULL DEFAULT 0,
    dead_end_flagged_at     TEXT NOT NULL DEFAULT '', resolver_confidence_ever_reached_medium_or_high INTEGER NOT NULL DEFAULT 0, resolver_confidence_history_json TEXT NOT NULL DEFAULT '[]',

    UNIQUE(card_code, field_name, discrepancy_category)
);

-- table: miru_perception_ledger_summary
CREATE TABLE miru_perception_ledger_summary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT NOT NULL DEFAULT '',
    card_code           TEXT NOT NULL DEFAULT '',
    field_name          TEXT NOT NULL DEFAULT '',
    mismatch_type       TEXT NOT NULL DEFAULT 'cosmetic',
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    first_seen_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- table: miru_publication_batch_items
CREATE TABLE miru_publication_batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                stage_state TEXT NOT NULL DEFAULT '',
                readiness_state TEXT NOT NULL DEFAULT '',
                approval_state TEXT NOT NULL DEFAULT '',
                promotion_state TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.0,
                rationale TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                removed_at TEXT NOT NULL DEFAULT '', candidate_score REAL NOT NULL DEFAULT 0.0, candidate_score_band TEXT NOT NULL DEFAULT '', candidate_profile TEXT NOT NULL DEFAULT '',
                UNIQUE(batch_id, item_key)
            );

-- table: miru_publication_batches
CREATE TABLE miru_publication_batches (
                batch_id TEXT PRIMARY KEY,
                batch_status TEXT NOT NULL DEFAULT 'draft',
                batch_title TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                summary_text TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                member_count INTEGER NOT NULL DEFAULT 0,
                ready_member_count INTEGER NOT NULL DEFAULT 0,
                review_member_count INTEGER NOT NULL DEFAULT 0,
                blocked_member_count INTEGER NOT NULL DEFAULT 0,
                deferred_member_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                archived_at TEXT NOT NULL DEFAULT ''
            , batch_quality_score REAL NOT NULL DEFAULT 0.0, batch_quality_band TEXT NOT NULL DEFAULT '', batch_profile TEXT NOT NULL DEFAULT '', strongest_reasons_json TEXT NOT NULL DEFAULT '[]', unresolved_risks_json TEXT NOT NULL DEFAULT '[]', recommended_next_step TEXT NOT NULL DEFAULT '', batch_publish_status TEXT NOT NULL DEFAULT '', batch_publish_reasons_json TEXT NOT NULL DEFAULT '[]', batch_publish_risks_json TEXT NOT NULL DEFAULT '[]', batch_publish_payload_json TEXT NOT NULL DEFAULT '{}', batch_publish_updated_at TEXT NOT NULL DEFAULT '');

-- table: miru_publication_stage
CREATE TABLE miru_publication_stage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL UNIQUE,
                target_type TEXT NOT NULL DEFAULT 'card',
                target_id TEXT NOT NULL DEFAULT '',
                readiness_state TEXT NOT NULL DEFAULT '',
                approval_state TEXT NOT NULL DEFAULT '',
                promotion_state TEXT NOT NULL DEFAULT '',
                stage_state TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.0,
                risk_level TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                summary_text TEXT NOT NULL DEFAULT '',
                supporting_sections_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                batch_id TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                decision_source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                removed_at TEXT NOT NULL DEFAULT ''
            , candidate_score REAL NOT NULL DEFAULT 0.0, candidate_score_band TEXT NOT NULL DEFAULT '', candidate_profile TEXT NOT NULL DEFAULT '', candidate_score_reasons_json TEXT NOT NULL DEFAULT '[]', candidate_risk_factors_json TEXT NOT NULL DEFAULT '[]');

-- table: miru_review_queue
CREATE TABLE miru_review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL UNIQUE,
                queue_type TEXT NOT NULL DEFAULT 'publication_readiness',
                target_type TEXT NOT NULL DEFAULT 'card',
                target_id TEXT NOT NULL DEFAULT '',
                readiness_state TEXT NOT NULL DEFAULT '',
                review_reason TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.0,
                risk_level TEXT NOT NULL DEFAULT '',
                recommended_next_step TEXT NOT NULL DEFAULT '',
                summary_text TEXT NOT NULL DEFAULT '',
                supporting_sections_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                resolution_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT NOT NULL DEFAULT ''
            , approval_state TEXT NOT NULL DEFAULT '', promotion_state TEXT NOT NULL DEFAULT '', approval_note TEXT NOT NULL DEFAULT '', decision_source TEXT NOT NULL DEFAULT '', approval_updated_at TEXT NOT NULL DEFAULT '');

-- table: miru_sync_metadata
CREATE TABLE miru_sync_metadata (
            sync_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

-- table: miru_validations
CREATE TABLE miru_validations (
            card_code TEXT PRIMARY KEY,
            confidence REAL NOT NULL DEFAULT 0.0,
            task_type TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]',
            winning_source_json TEXT NOT NULL DEFAULT '{}',
            rejected_sources_json TEXT NOT NULL DEFAULT '[]',
            validated_fields_json TEXT NOT NULL DEFAULT '[]',
            conflict_summary_json TEXT NOT NULL DEFAULT '{}',
            confidence_reason TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(card_code) REFERENCES cards(canonical_code) ON DELETE CASCADE
        );

-- table: miru_variant_index
CREATE TABLE miru_variant_index (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_card_code       TEXT NOT NULL,
    base_card_code          TEXT NOT NULL,
    variant_category        TEXT NOT NULL DEFAULT '',
    variant_subtype         TEXT NOT NULL DEFAULT '',
    detection_signals_json  TEXT NOT NULL DEFAULT '{}',
    source                  TEXT NOT NULL DEFAULT '',
    confidence              TEXT NOT NULL DEFAULT 'high',
    is_active               INTEGER NOT NULL DEFAULT 1,
    verified_at             TEXT NOT NULL DEFAULT '',
    verified_by             TEXT NOT NULL DEFAULT '',
    notes                   TEXT NOT NULL DEFAULT '',
    image_fingerprint_ref   TEXT NOT NULL DEFAULT '',
    layout_template_ref     TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(variant_card_code, variant_subtype)
);

-- table: official_legality_history
CREATE TABLE official_legality_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    format_name TEXT NOT NULL DEFAULT 'standard',
    region TEXT NOT NULL DEFAULT '',
    legality_state TEXT NOT NULL,
    effective_start TEXT NOT NULL DEFAULT '',
    effective_end TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    notice_id TEXT NOT NULL DEFAULT '',
    is_current INTEGER NOT NULL DEFAULT 0,
    is_upcoming INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, format_name, region, effective_start)
);

-- table: official_rule_notices
CREATE TABLE official_rule_notices (
    notice_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    format_name TEXT NOT NULL DEFAULT 'standard',
    notice_type TEXT NOT NULL DEFAULT 'other',
    published_at TEXT NOT NULL DEFAULT '',
    effective_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'current',
    summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);

-- table: official_source_refs
CREATE TABLE official_source_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_card_id INTEGER,
    printing_id INTEGER,
    source_type TEXT,
    source_url TEXT,
    source_label TEXT,
    extracted_field TEXT,
    extracted_value TEXT,
    verified_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (canonical_card_id) REFERENCES cards(id),
    FOREIGN KEY (printing_id) REFERENCES card_variants(id)
);

-- table: printing_market_map
CREATE TABLE printing_market_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printing_id INTEGER NOT NULL,
    market_product_id INTEGER NOT NULL,
    mapping_confidence TEXT NOT NULL DEFAULT 'UNVERIFIED',
    mapping_method TEXT,
    mapping_notes TEXT,
    is_preferred INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    FOREIGN KEY (printing_id) REFERENCES card_variants(id),
    FOREIGN KEY (market_product_id) REFERENCES market_products(id),
    UNIQUE(printing_id, market_product_id)
);

-- table: sets
CREATE TABLE sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_code TEXT NOT NULL UNIQUE,
            set_name TEXT NOT NULL DEFAULT '',
            series_code_display TEXT NOT NULL DEFAULT '',
            series_id TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]'
        );

-- table: sqlite_sequence
CREATE TABLE sqlite_sequence(name,seq);

-- table: tcgplayer_products
CREATE TABLE tcgplayer_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER UNIQUE NOT NULL,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        clean_name TEXT,
        card_code TEXT,
        parsed_variant_key TEXT,
        card_variant_id INTEGER,
        market_price REAL,
        mid_price REAL,
        low_price REAL,
        price_updated_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT
    );
