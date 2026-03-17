-- Migration m003: card_catalog.db extensions
-- Target database : card_catalog.db  (must already exist)
-- Applied by      : tools/miru_migrate_db.py --target catalog
--
-- All statements are idempotent (CREATE ... IF NOT EXISTS).
-- card_catalog.db must already exist (created by miru_project_sync.py).
--
-- NOTE: The three new columns on miru_card_insights (source_ref, leader_code,
-- generated_at) are NOT applied here. They are applied by miru_project_sync.py
-- via _ensure_column() calls — which already owns the card_catalog.db schema
-- lifecycle. See docs/miru_db_schema.md §5 for details.
--
-- See docs/miru_db_schema.md §5–6 for full field reference and design rationale.

-- ────────────────────────────────────────────────────────────────────────────
-- TABLE: miru_leader_insights
-- ────────────────────────────────────────────────────────────────────────────
-- Leader-level insights parallel miru_card_insights at the leader granularity.
-- Cards have per-card insights; leaders have per-leader insights of different
-- types (overview, playstyle, meta, variant, budget).
--
-- Written by: miru_bundle_leader_insight.py and future synthesis tools
-- Read by:    dashboard leader page, AI server (/api/miru/insight/<leader_code>)
--
-- The PRIMARY KEY is (leader_code, insight_type) — the same pattern as
-- miru_card_insights(card_id, insight_type). Upsert with INSERT OR REPLACE.

CREATE TABLE IF NOT EXISTS miru_leader_insights (
    leader_code     TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader).
    -- Not a SQL foreign key — enforced in application code.

    insight_type    TEXT    NOT NULL,
    -- Allowed values (extensible — document new types here before use):
    --   'overview'  — general leader identity summary
    --   'playstyle' — how the deck plays: aggro / control / tempo / combo
    --   'meta'      — current meta standing and tier assessment
    --   'variant'   — explanation of archetype branches for this leader
    --   'budget'    — budget path notes and progression advice

    insight_text    TEXT    NOT NULL DEFAULT '',
    -- The generated insight prose. May be empty while awaiting generation.

    confidence      REAL    NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- Confidence score from 0.0 (no data) to 1.0 (fully evidenced).
    -- Same scale as miru_card_insights.confidence.

    quality_tier    TEXT    NOT NULL DEFAULT '',
    -- Four-tier quality classification matching miru_card_insights:
    --   'generic'     — template-level text, no card-specific data
    --   'contextual'  — card/leader data incorporated but no deck signal
    --   'strategic'   — deck signal incorporated
    --   'evidenced'   — deck signal + tournament placement data
    -- Governed by docs/miru_insight_upgrade_policy.md

    source_ref      TEXT    NOT NULL DEFAULT '',
    -- What data produced this insight. Examples:
    --   "archetype_profiles:OP01-001:2026-03-16"
    --   "leader_card_signals:OP01-001:standard"
    -- Used for traceability and cache invalidation.

    generated_at    INTEGER NOT NULL DEFAULT 0,
    -- Unix timestamp of initial generation.
    -- Distinct from updated_at — generated_at never changes after first write.

    updated_at      INTEGER NOT NULL DEFAULT 0,
    -- Unix timestamp of last update (different from generated_at if upgraded).

    PRIMARY KEY (leader_code, insight_type)
);

-- ────────────────────────────────────────────────────────────────────────────
-- TABLE: miru_meta_placements
-- ────────────────────────────────────────────────────────────────────────────
-- Child of the existing miru_meta_events table. Each row is one leader's
-- performance record within one event.
--
-- Written by: miru_import_decklist.py or a future event import tool
-- Read by:    dashboard leader page meta signals panel, AI server meta queries
--
-- deck_snapshot_json is always stored even when deck_uid is set — immutability
-- is more important than normalization here. Event placements must not change
-- if the referenced deck is later edited or deleted.

CREATE TABLE IF NOT EXISTS miru_meta_placements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    event_key           TEXT    NOT NULL REFERENCES miru_meta_events(event_key) ON DELETE CASCADE,
    -- Hard FK to miru_meta_events.event_key (same database — FK is safe here).
    -- ON DELETE CASCADE: removing an event removes all its placements.

    leader_code         TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader).

    placement_rank      INTEGER,
    -- 1 = 1st place, 2 = 2nd, etc.
    -- NULL if unranked or placement is unknown.

    deck_uid            TEXT    NOT NULL DEFAULT '',
    -- Optional: links to miru_user_decks.user_decks.deck_uid if this deck
    -- was imported to the Deck Builder. Empty string if not in the system.
    -- Cross-database soft reference — no SQL FK.

    deck_snapshot_json  TEXT    NOT NULL DEFAULT '{}',
    -- JSON snapshot of the decklist at time of event. Always stored.
    -- Schema:
    -- {
    --   "leader_code": "OP01-001",
    --   "cards": [{"card_code": "OP01-002", "quantity": 4}, ...]
    -- }

    player_handle       TEXT    NOT NULL DEFAULT '',
    -- Anonymized or public player identifier.
    -- Empty string if unknown or not disclosed.

    sample_size         INTEGER NOT NULL DEFAULT 1,
    -- Number of decks represented by this row. Usually 1.
    -- Set > 1 for aggregated records:
    --   e.g. "15 Luffy decks reached top cut at this event"

    notes               TEXT    NOT NULL DEFAULT '',
    -- Optional free-form annotation for this placement.

    created_at          INTEGER NOT NULL
    -- Unix timestamp of when this row was inserted.
);

-- ────────────────────────────────────────────────────────────────────────────
-- INDEXES
-- ────────────────────────────────────────────────────────────────────────────

-- Leader page: load all insights for a leader, ordered by confidence
CREATE INDEX IF NOT EXISTS idx_miru_leader_insights_leader
    ON miru_leader_insights(leader_code, confidence DESC);

-- Meta signals: filter placements by event (event detail view)
CREATE INDEX IF NOT EXISTS idx_miru_meta_placements_event
    ON miru_meta_placements(event_key, placement_rank ASC);

-- Meta signals: filter placements by leader (leader page meta panel)
CREATE INDEX IF NOT EXISTS idx_miru_meta_placements_leader
    ON miru_meta_placements(leader_code, placement_rank ASC);
