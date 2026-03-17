# Project Miru Database Schema

This document is the authoritative schema specification for Project Miru's database layer. It covers existing tables (for reference), the additions required for the Deck Builder, Leader Hub intelligence, Miru Insight, and Meta Signal features, and the migration strategy for applying each change safely.

Implementation agents must read this document before writing any database code. When this document conflicts with existing code, treat the discrepancy as a bug to resolve — do not silently deviate.

---

## 1. Summary

### Database inventory

| File | Location | Access pattern | Owner |
|---|---|---|---|
| `card_catalog.db` | `./data/` | Read-write (tools), read-only (dashboard) | `miru_project_sync.py` |
| `miru_deck_intel.db` | `./data/` | Read-write (pipeline tools), read-only (AI server) | `miru_import_decklist.py` and pipeline tools |
| `miru_user_decks.db` | `./data/` | Read-write (AI server, future API) | **NEW** |

`card_catalog.db` is the shared intelligence store — both pipeline tools and the dashboard depend on it. It is never truncated and never schema-migrated destructively. New tables and columns are always additive.

`miru_deck_intel.db` is the pipeline intelligence store — tournament decks, signals, archetypes. The dashboard and AI server read from it. Pipeline tools write to it.

`miru_user_decks.db` is the user-content store — decks created through the Deck Builder. It is isolated from AI-generated pipeline data so it can be independently backed up, inspected, and reset without touching intelligence data.

### What this document adds

| Feature | New tables | Target database |
|---|---|---|
| Deck Builder | `user_decks`, `user_deck_cards`, `user_deck_versions` | `miru_user_decks.db` (new) |
| Leader Hub intelligence | `leader_profiles`, `leader_budget_builds` | `miru_deck_intel.db` |
| Miru Insight (leader-level) | `miru_leader_insights` | `card_catalog.db` |
| Miru Insight (card-level extensions) | 3 new columns on `miru_card_insights` | `card_catalog.db` |
| Meta signal placements | `miru_meta_placements` | `card_catalog.db` |

### What this document does NOT change

- `cards`, `card_variants`, `sets` — read-only catalog, never modified
- `miru_validations` — validation pipeline, no new columns needed
- `decklists`, `deck_entries`, `deck_sources` — tournament import pipeline, untouched
- `leader_card_signals`, `leader_cost_curves`, `leader_trait_signals`, `archetype_profiles*` — existing signal tables, untouched
- `miru_meta_events` — existing event header table, untouched (only adding its child table)

---

## 2. Existing Schema Reference

### `card_catalog.db`

```sql
-- Card catalog (read-only from dashboard)
cards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_code    TEXT    NOT NULL UNIQUE,     -- e.g. "OP01-001"
    set_code          TEXT    NOT NULL DEFAULT '',
    card_number       TEXT    NOT NULL DEFAULT '',
    set_name          TEXT    NOT NULL DEFAULT '',
    card_name         TEXT    NOT NULL DEFAULT '',
    rarity            TEXT    NOT NULL DEFAULT '',
    color             TEXT    NOT NULL DEFAULT '',
    card_type         TEXT    NOT NULL DEFAULT '',
    cost              INTEGER,
    power             TEXT    NOT NULL DEFAULT '',
    counter           TEXT    NOT NULL DEFAULT '',
    attribute         TEXT    NOT NULL DEFAULT '',
    traits            TEXT    NOT NULL DEFAULT '',
    life              TEXT    NOT NULL DEFAULT '',
    block_icon        TEXT    NOT NULL DEFAULT '',
    effect_text       TEXT    NOT NULL DEFAULT '',
    trigger_text      TEXT    NOT NULL DEFAULT '',
    aliases_json      TEXT    NOT NULL DEFAULT '[]',
    sources_json      TEXT    NOT NULL DEFAULT '[]'
);

card_variants (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id                 INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    variant_key             TEXT    NOT NULL,
    variant_label           TEXT    NOT NULL DEFAULT '',
    print_id                TEXT    NOT NULL DEFAULT '',
    release_set_code        TEXT    NOT NULL DEFAULT '',
    release_set_name        TEXT    NOT NULL DEFAULT '',
    image_path              TEXT    NOT NULL DEFAULT '',
    image_url               TEXT    NOT NULL DEFAULT '',
    source                  TEXT    NOT NULL DEFAULT 'local-catalog',
    is_base                 INTEGER NOT NULL DEFAULT 0,
    is_alt                  INTEGER NOT NULL DEFAULT 0,
    is_sp                   INTEGER NOT NULL DEFAULT 0,
    has_variant_evidence    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(card_id, variant_key, print_id)
);

-- Miru intelligence (AI-generated, tooling writes, dashboard reads)
miru_card_insights (
    card_id         TEXT    NOT NULL REFERENCES cards(canonical_code),
    insight_type    TEXT    NOT NULL,
    insight_text    TEXT    NOT NULL DEFAULT '',
    confidence      REAL    NOT NULL DEFAULT 0.0,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    quality_tier    TEXT    NOT NULL DEFAULT '',  -- added via _ensure_column
    PRIMARY KEY (card_id, insight_type)
);

miru_meta_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key       TEXT    NOT NULL UNIQUE,
    event_name      TEXT    NOT NULL DEFAULT '',
    format_name     TEXT    NOT NULL DEFAULT '',
    event_date      TEXT    NOT NULL DEFAULT '',
    source_url      TEXT    NOT NULL DEFAULT '',
    source_kind     TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### `miru_deck_intel.db`

```sql
-- Tournament deck pipeline
decklists (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL UNIQUE,
    leader_code     TEXT    NOT NULL,
    format_code     TEXT    NOT NULL DEFAULT '',
    source_id       INTEGER REFERENCES deck_sources(id),
    placement       TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL
);

deck_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL REFERENCES decklists(deck_uid),
    card_code       TEXT    NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(deck_uid, card_code)
);

-- Leader signal tables (computed by pipeline)
leader_card_signals (
    leader_code     TEXT    NOT NULL,
    card_code       TEXT    NOT NULL,
    format_code     TEXT    NOT NULL DEFAULT '',
    deck_count      INTEGER NOT NULL DEFAULT 0,
    total_copies    INTEGER NOT NULL DEFAULT 0,
    usage_percent   REAL    NOT NULL DEFAULT 0.0,
    avg_copies      REAL    NOT NULL DEFAULT 0.0,
    role_label      TEXT    NOT NULL DEFAULT '',  -- 'core', 'flex', 'tech'
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (leader_code, card_code, format_code)
);
```

---

## 3. New Schema: Deck Builder (`miru_user_decks.db`)

This is a brand-new database file. It holds only user-created deck content. It has no dependency on `card_catalog.db` or `miru_deck_intel.db` at the SQL level — card codes are stored as plain text references, not enforced foreign keys, because the catalog is in a separate file.

### `user_decks`

```sql
CREATE TABLE IF NOT EXISTS user_decks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL UNIQUE,
    -- Format: "ud-" + 8 lowercase hex chars. Example: "ud-a3f29c1e"
    -- Generated: "ud-" + secrets.token_hex(4) at creation time.
    -- Stable across version bumps. Used in URLs: /deck/<deck_uid>

    name            TEXT    NOT NULL,
    leader_code     TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader)
    -- Not a foreign key — cross-file enforcement done in application code

    format_code     TEXT    NOT NULL DEFAULT 'standard',
    -- Allowed values: 'standard', 'block', 'unlimited'
    -- Matches format_code used in miru_deck_intel.db

    archetype_hint  TEXT    NOT NULL DEFAULT '',
    -- Optional user label: e.g. "aggro", "control", "straw-hat-rush"

    notes           TEXT    NOT NULL DEFAULT '',
    -- Free-form deck notes, visible to owner

    is_public       INTEGER NOT NULL DEFAULT 0,
    -- 0 = private (default), 1 = public (viewable without auth)

    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    -- Both: Unix timestamp (seconds since epoch). Use int(time.time()).

    version         INTEGER NOT NULL DEFAULT 1
    -- Increments on every successful edit. Matches the latest user_deck_versions.version.
);
```

### `user_deck_cards`

```sql
CREATE TABLE IF NOT EXISTS user_deck_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid    TEXT    NOT NULL REFERENCES user_decks(deck_uid) ON DELETE CASCADE,
    card_code   TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code

    quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity BETWEEN 1 AND 4),
    -- Standard One Piece TCG: max 4 copies per card, 1 leader

    section     TEXT    NOT NULL DEFAULT 'main',
    -- 'main' only in this pass. Future: 'side', 'reserve'

    UNIQUE(deck_uid, card_code)
    -- One row per card per deck. Quantity stored in the row, not as multiple rows.
);
```

### `user_deck_versions`

```sql
CREATE TABLE IF NOT EXISTS user_deck_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL REFERENCES user_decks(deck_uid) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    -- Matches user_decks.version at the time of this snapshot

    snapshot_json   TEXT    NOT NULL DEFAULT '{}',
    -- Full deck state at this version. Schema:
    -- {
    --   "leader_code": "OP01-001",
    --   "format_code": "standard",
    --   "cards": [{"card_code": "OP01-002", "quantity": 4, "section": "main"}, ...]
    -- }
    -- Allows rollback without re-querying user_deck_cards.

    change_summary  TEXT    NOT NULL DEFAULT '',
    -- Optional: "Added 2x OP01-007, removed 1x OP01-003"

    created_at      INTEGER NOT NULL,
    -- Unix timestamp of when this version was created

    UNIQUE(deck_uid, version)
);
```

### Relationships

```
user_decks ──< user_deck_cards   (1 deck has many card entries)
user_decks ──< user_deck_versions (1 deck has many version snapshots)
```

---

## 4. New Schema: Leader Hub Intelligence (`miru_deck_intel.db` additions)

These tables extend the existing pipeline database. They are written by pipeline tools and read by the AI server and dashboard.

### `leader_profiles`

Summary-level metadata per leader, computed from the existing signal tables. This is a cache/aggregate row — the pipeline recomputes it from `leader_card_signals`, `archetype_profiles`, and `decklists`.

```sql
CREATE TABLE IF NOT EXISTS leader_profiles (
    leader_code         TEXT    PRIMARY KEY,
    -- Soft reference to cards.canonical_code (type=Leader)

    decks_sampled       INTEGER NOT NULL DEFAULT 0,
    -- COUNT(DISTINCT deck_uid) from decklists WHERE leader_code = this

    confidence_label    TEXT    NOT NULL DEFAULT 'low',
    -- Derived from decks_sampled:
    --   'low'    : decks_sampled < 5
    --   'medium' : 5 <= decks_sampled <= 14
    --   'strong' : decks_sampled >= 15
    -- Thresholds match miru_archetype_preview.py constants.

    archetype_count     INTEGER NOT NULL DEFAULT 0,
    -- COUNT(DISTINCT archetype_id) from archetype_profiles WHERE leader_code = this

    dominant_archetype_id TEXT  NOT NULL DEFAULT '',
    -- The archetype_id with the highest deck_count or coverage for this leader

    identity_tags_json  TEXT    NOT NULL DEFAULT '[]',
    -- JSON array of string tags: ["aggro", "straw-hat", "rush", "red"]
    -- Written by pipeline based on trait signals and cost curve shape.

    playstyle_summary   TEXT    NOT NULL DEFAULT '',
    -- Short Miru-generated prose summary (1–3 sentences).
    -- Written by miru_bundle_leader_insight.py or a future synthesis step.

    updated_at          INTEGER NOT NULL
    -- Unix timestamp of last pipeline recompute
);
```

### `leader_budget_builds`

Structured budget build entries generated by the intelligence pipeline, not by users. Each build specifies a cost tier and a recommended card set.

```sql
CREATE TABLE IF NOT EXISTS leader_budget_builds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    leader_code     TEXT    NOT NULL,
    format_code     TEXT    NOT NULL DEFAULT '',

    budget_tier     TEXT    NOT NULL DEFAULT 'budget',
    -- Allowed values: 'budget', 'mid', 'premium'
    -- One row per (leader_code, format_code, budget_tier) — see UNIQUE below.

    name            TEXT    NOT NULL,
    -- Display name: e.g. "Budget Luffy Aggro", "Mid-Range Red Luffy"

    description     TEXT    NOT NULL DEFAULT '',
    -- 1–3 sentence rationale for this build tier

    est_cost_usd    REAL,
    -- Estimated market value. NULL if unknown.

    core_cards_json TEXT    NOT NULL DEFAULT '[]',
    -- JSON array: [{"card_code": "OP01-001", "quantity": 4, "role": "core"}, ...]

    flex_cards_json TEXT    NOT NULL DEFAULT '[]',
    -- JSON array: [{"card_code": "OP01-060", "quantity": 2, "role": "flex",
    --               "budget_alt": "OP02-011"}, ...]
    -- flex entries may include a "budget_alt" card_code as a cheaper substitute.

    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,

    UNIQUE(leader_code, format_code, budget_tier)
);
```

### Relationships

```
decklists          ──> leader_profiles     (aggregated by pipeline)
archetype_profiles ──> leader_profiles     (aggregated by pipeline)
leader_card_signals──> leader_budget_builds (source for core/flex card selection)
leader_profiles    ──< leader_budget_builds (1 leader has 0–3 budget tiers)
```

---

## 5. New Schema: Miru Insight (`card_catalog.db` additions)

### `miru_leader_insights` (new table)

Leader-level insights parallel `miru_card_insights` at the leader granularity. Cards have per-card insights; leaders have per-leader insights of different types.

```sql
CREATE TABLE IF NOT EXISTS miru_leader_insights (
    leader_code     TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader)

    insight_type    TEXT    NOT NULL,
    -- Allowed values:
    --   'overview'   — general leader identity summary
    --   'playstyle'  — how the deck plays (aggro/control/tempo/combo)
    --   'meta'       — current meta standing
    --   'variant'    — explanation of archetype branches
    --   'budget'     — budget path notes
    -- This list may grow. New types must be documented here.

    insight_text    TEXT    NOT NULL DEFAULT '',
    confidence      REAL    NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0.0 AND 1.0),

    quality_tier    TEXT    NOT NULL DEFAULT '',
    -- Same 4-tier system as miru_card_insights:
    -- 'generic', 'contextual', 'strategic', 'evidenced'
    -- Governed by docs/miru_insight_upgrade_policy.md

    source_ref      TEXT    NOT NULL DEFAULT '',
    -- What data produced this insight. Examples:
    --   "archetype_profiles:OP01-001:2026-03-16"
    --   "leader_card_signals:OP01-001:standard"

    generated_at    INTEGER NOT NULL DEFAULT 0,
    -- Unix timestamp of when this insight was generated

    updated_at      INTEGER NOT NULL DEFAULT 0,
    -- Unix timestamp of last update (different from generated_at if upgraded)

    PRIMARY KEY (leader_code, insight_type)
);
```

### New columns on `miru_card_insights`

These columns do not exist yet. They are applied via `_ensure_column()` in `miru_project_sync.py` — the same safe additive pattern used for `quality_tier`.

```sql
-- Apply via _ensure_column():
ALTER TABLE miru_card_insights ADD COLUMN source_ref      TEXT NOT NULL DEFAULT '';
ALTER TABLE miru_card_insights ADD COLUMN leader_code     TEXT NOT NULL DEFAULT '';
ALTER TABLE miru_card_insights ADD COLUMN generated_at    INTEGER NOT NULL DEFAULT 0;
```

Column definitions:
- `source_ref` — what data produced this insight (mirrors `miru_leader_insights.source_ref`)
- `leader_code` — optional leader context: this insight is specifically for card X in the context of leader Y. Empty string means card-universal.
- `generated_at` — Unix timestamp of initial generation, separate from `updated_at` (ISO string, existing column)

---

## 6. New Schema: Meta Signal Storage (`card_catalog.db` addition)

### `miru_meta_placements` (new table)

Child of the existing `miru_meta_events` table. Each row is one leader's placement within one event.

```sql
CREATE TABLE IF NOT EXISTS miru_meta_placements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    event_key           TEXT    NOT NULL REFERENCES miru_meta_events(event_key) ON DELETE CASCADE,
    -- Hard reference to miru_meta_events.event_key

    leader_code         TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader)

    placement_rank      INTEGER,
    -- 1 = 1st place, 2 = 2nd, etc. NULL if unranked or unknown.

    deck_uid            TEXT    NOT NULL DEFAULT '',
    -- Optional: links to user_decks.deck_uid if this deck was imported to Deck Builder
    -- Empty string if deck is not in the system

    deck_snapshot_json  TEXT    NOT NULL DEFAULT '{}',
    -- JSON snapshot of the decklist at time of event. Schema:
    -- {"leader_code": "OP01-001", "cards": [{"card_code": "OP01-002", "quantity": 4}, ...]}
    -- Always stored here even if deck_uid is set, for immutability.

    player_handle       TEXT    NOT NULL DEFAULT '',
    -- Anonymized or public player identifier. Empty if unknown.

    sample_size         INTEGER NOT NULL DEFAULT 1,
    -- Number of decks represented by this entry. Usually 1.
    -- Set > 1 for aggregated records (e.g. "15 Luffy decks top-cut this event").

    notes               TEXT    NOT NULL DEFAULT '',
    created_at          INTEGER NOT NULL
    -- Unix timestamp
);
```

### Relationships

```
miru_meta_events ──< miru_meta_placements (1 event has many placements)
miru_meta_placements.deck_uid ──> user_decks.deck_uid (soft, optional)
```

---

## 7. Index Recommendations

### `miru_user_decks.db`

```sql
-- Primary access pattern: list decks by leader or by recency
CREATE INDEX IF NOT EXISTS idx_user_decks_leader
    ON user_decks(leader_code, updated_at DESC);

-- Access pattern: find all cards in a deck (most common read)
CREATE INDEX IF NOT EXISTS idx_user_deck_cards_deck
    ON user_deck_cards(deck_uid);

-- Access pattern: "which decks use this card?" (deck builder search)
CREATE INDEX IF NOT EXISTS idx_user_deck_cards_card
    ON user_deck_cards(card_code);

-- Access pattern: version history for a deck
CREATE INDEX IF NOT EXISTS idx_user_deck_versions_deck
    ON user_deck_versions(deck_uid, version DESC);
```

### `miru_deck_intel.db` additions

```sql
-- Primary access: leader page loads leader_profiles by code
CREATE INDEX IF NOT EXISTS idx_leader_profiles_confidence
    ON leader_profiles(confidence_label);
-- Note: leader_code is PRIMARY KEY, no additional index needed for exact lookup.

-- Access pattern: list budget builds for a leader
CREATE INDEX IF NOT EXISTS idx_leader_budget_leader
    ON leader_budget_builds(leader_code, format_code);
```

### `card_catalog.db` additions

```sql
-- Access pattern: leader page loads all insights for a leader
CREATE INDEX IF NOT EXISTS idx_miru_leader_insights_leader
    ON miru_leader_insights(leader_code, confidence DESC);

-- Access pattern: meta placements filtered by event
CREATE INDEX IF NOT EXISTS idx_miru_meta_placements_event
    ON miru_meta_placements(event_key, placement_rank ASC);

-- Access pattern: meta placements filtered by leader (meta signal queries)
CREATE INDEX IF NOT EXISTS idx_miru_meta_placements_leader
    ON miru_meta_placements(leader_code, placement_rank ASC);
```

### Existing indexes already in place (do not recreate)

```sql
-- Already exists in card_catalog.db:
idx_cards_set_code             ON cards(set_code)
idx_cards_card_name            ON cards(card_name)
idx_variants_card_id           ON card_variants(card_id)
idx_miru_card_insights_card_id ON miru_card_insights(card_id, confidence DESC, updated_at DESC)

-- Already exists in miru_deck_intel.db:
idx_decklists_leader           ON decklists(leader_code)
idx_deck_entries_card          ON deck_entries(card_code)
idx_deck_entries_deck          ON deck_entries(deck_uid)
```

---

## 8. Migration Strategy

### Principles

1. **Additive only** — no existing table is altered destructively. No columns are renamed or dropped. No data is moved.
2. **Idempotent** — every statement uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Running the migration twice is safe.
3. **Column additions** use `_ensure_column()` from `miru_project_sync.py` — the existing helper already handles this safely.
4. **No transactions across databases** — SQLite does not support cross-file transactions. Each migration script opens one database at a time.
5. **WAL mode** — new databases are opened in WAL mode for concurrent read access. Existing databases already use WAL (check with `PRAGMA journal_mode`).

### Migration files

Located at `tools/migrations/`. Applied by `tools/miru_migrate_db.py`.

| File | Target database | Contents |
|---|---|---|
| `m001_user_decks.sql` | `miru_user_decks.db` | `user_decks`, `user_deck_cards`, `user_deck_versions` + indexes |
| `m002_leader_hub.sql` | `miru_deck_intel.db` | `leader_profiles`, `leader_budget_builds` + indexes |
| `m003_catalog_extensions.sql` | `card_catalog.db` | `miru_leader_insights`, `miru_meta_placements` + indexes |

Column extensions to `miru_card_insights` (`source_ref`, `leader_code`, `generated_at`) are applied by `miru_project_sync.py`'s `ensure_catalog_schema()` via `_ensure_column()` calls — not by a migration file — because that function already owns the `card_catalog.db` schema lifecycle.

### Application order

```
Step 1:  python -m tools.miru_migrate_db --target user_decks
         Creates miru_user_decks.db if missing. Safe to run at any time.

Step 2:  python -m tools.miru_migrate_db --target deck_intel
         Adds leader_profiles and leader_budget_builds to miru_deck_intel.db.
         Requires miru_deck_intel.db to exist (created by miru_import_decklist.py).

Step 3:  python -m tools.miru_migrate_db --target catalog
         Adds miru_leader_insights and miru_meta_placements to card_catalog.db.
         Requires card_catalog.db to exist (created by miru_project_sync.py).

Step 4:  python -m tools.miru_project_sync  (existing tool — extended with new _ensure_column calls)
         Adds source_ref, leader_code, generated_at columns to miru_card_insights.
         This step is already idempotent.
```

### Rollback

Because all changes are additive:
- New tables can be dropped with `DROP TABLE IF EXISTS <name>` to undo.
- New columns on `miru_card_insights` cannot be dropped in older SQLite versions without rebuilding the table — but they have safe defaults and do not break existing queries.
- `miru_user_decks.db` can be deleted entirely with no impact on any other system.

---

## 9. Complete Field Reference

### `user_decks`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `deck_uid` | TEXT | NOT NULL UNIQUE | URL-safe ID: `"ud-" + 8 hex chars` |
| `name` | TEXT | NOT NULL | User-assigned deck name |
| `leader_code` | TEXT | NOT NULL | Leader card code (e.g. `"OP01-001"`) |
| `format_code` | TEXT | NOT NULL DEFAULT `'standard'` | `'standard'`, `'block'`, `'unlimited'` |
| `archetype_hint` | TEXT | NOT NULL DEFAULT `''` | Optional user archetype tag |
| `notes` | TEXT | NOT NULL DEFAULT `''` | Free-form notes |
| `is_public` | INTEGER | NOT NULL DEFAULT `0` | `0`=private, `1`=public |
| `created_at` | INTEGER | NOT NULL | Unix epoch timestamp |
| `updated_at` | INTEGER | NOT NULL | Unix epoch timestamp |
| `version` | INTEGER | NOT NULL DEFAULT `1` | Version counter |

### `user_deck_cards`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `deck_uid` | TEXT | NOT NULL FK→`user_decks` CASCADE | Parent deck |
| `card_code` | TEXT | NOT NULL | Card code (e.g. `"OP01-002"`) |
| `quantity` | INTEGER | NOT NULL DEFAULT `1` CHECK `1–4` | Copies in deck |
| `section` | TEXT | NOT NULL DEFAULT `'main'` | `'main'` only in this pass |

### `user_deck_versions`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `deck_uid` | TEXT | NOT NULL FK→`user_decks` CASCADE | Parent deck |
| `version` | INTEGER | NOT NULL | Version number |
| `snapshot_json` | TEXT | NOT NULL DEFAULT `'{}'` | Full deck state JSON |
| `change_summary` | TEXT | NOT NULL DEFAULT `''` | Human-readable change note |
| `created_at` | INTEGER | NOT NULL | Unix epoch timestamp |

### `leader_profiles`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `leader_code` | TEXT | PRIMARY KEY | Leader card code |
| `decks_sampled` | INTEGER | NOT NULL DEFAULT `0` | Total distinct decklists sampled |
| `confidence_label` | TEXT | NOT NULL DEFAULT `'low'` | `'low'`, `'medium'`, `'strong'` |
| `archetype_count` | INTEGER | NOT NULL DEFAULT `0` | Number of distinct archetypes |
| `dominant_archetype_id` | TEXT | NOT NULL DEFAULT `''` | Most common archetype |
| `identity_tags_json` | TEXT | NOT NULL DEFAULT `'[]'` | JSON string array of tags |
| `playstyle_summary` | TEXT | NOT NULL DEFAULT `''` | Miru prose summary |
| `updated_at` | INTEGER | NOT NULL | Unix epoch timestamp |

### `leader_budget_builds`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `leader_code` | TEXT | NOT NULL | Leader card code |
| `format_code` | TEXT | NOT NULL DEFAULT `''` | Format context |
| `budget_tier` | TEXT | NOT NULL DEFAULT `'budget'` | `'budget'`, `'mid'`, `'premium'` |
| `name` | TEXT | NOT NULL | Display name |
| `description` | TEXT | NOT NULL DEFAULT `''` | Build rationale |
| `est_cost_usd` | REAL | — | Estimated market cost, NULL if unknown |
| `core_cards_json` | TEXT | NOT NULL DEFAULT `'[]'` | JSON array of core card entries |
| `flex_cards_json` | TEXT | NOT NULL DEFAULT `'[]'` | JSON array of flex card entries |
| `created_at` | INTEGER | NOT NULL | Unix epoch timestamp |
| `updated_at` | INTEGER | NOT NULL | Unix epoch timestamp |

### `miru_leader_insights`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `leader_code` | TEXT | NOT NULL | Leader card code |
| `insight_type` | TEXT | NOT NULL | `'overview'`, `'playstyle'`, `'meta'`, `'variant'`, `'budget'` |
| `insight_text` | TEXT | NOT NULL DEFAULT `''` | Miru insight prose |
| `confidence` | REAL | NOT NULL DEFAULT `0.0` CHECK `0.0–1.0` | Confidence score |
| `quality_tier` | TEXT | NOT NULL DEFAULT `''` | Same 4-tier system as `miru_card_insights` |
| `source_ref` | TEXT | NOT NULL DEFAULT `''` | Data provenance reference |
| `generated_at` | INTEGER | NOT NULL DEFAULT `0` | Unix epoch: first generation |
| `updated_at` | INTEGER | NOT NULL DEFAULT `0` | Unix epoch: last update |

### `miru_meta_placements`

| Column | Type | Constraint | Description |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | Internal row ID |
| `event_key` | TEXT | NOT NULL FK→`miru_meta_events` CASCADE | Parent event |
| `leader_code` | TEXT | NOT NULL | Leader card code |
| `placement_rank` | INTEGER | — | Finish position; NULL if unranked |
| `deck_uid` | TEXT | NOT NULL DEFAULT `''` | Optional link to user deck |
| `deck_snapshot_json` | TEXT | NOT NULL DEFAULT `'{}'` | Immutable deck snapshot at event time |
| `player_handle` | TEXT | NOT NULL DEFAULT `''` | Anonymized player name |
| `sample_size` | INTEGER | NOT NULL DEFAULT `1` | Decks represented by this row |
| `notes` | TEXT | NOT NULL DEFAULT `''` | Free-form notes |
| `created_at` | INTEGER | NOT NULL | Unix epoch timestamp |

### `miru_card_insights` new columns (additive)

| Column | Type | Added via | Description |
|---|---|---|---|
| `source_ref` | TEXT NOT NULL DEFAULT `''` | `_ensure_column()` | Data provenance |
| `leader_code` | TEXT NOT NULL DEFAULT `''` | `_ensure_column()` | Optional leader context |
| `generated_at` | INTEGER NOT NULL DEFAULT `0` | `_ensure_column()` | First generation timestamp |

---

## 10. Future Scalability Notes

### SQLite capacity

SQLite handles single-writer workloads up to millions of rows comfortably with WAL mode. For Project Miru's expected scale (thousands of decks, thousands of insights), SQLite is the correct choice. Do not migrate to PostgreSQL preemptively.

WAL mode is required on `miru_user_decks.db` from day one because the AI server and the Deck Builder frontend will both read from it concurrently. Apply with `PRAGMA journal_mode = WAL` at connection time.

### Schema growth patterns

- **New insight types**: Add to the `insight_type` column's allowed values list in this document. No schema change needed — the column is open TEXT.
- **New budget tiers**: Same pattern — extend the `budget_tier` check list in this document.
- **New format codes**: `format_code` is open TEXT everywhere. No schema change needed.
- **Deck side-board**: Add `section = 'side'` to `user_deck_cards`. No schema change needed (column already exists with `'main'` default).
- **Deck sharing / collaboration**: Add a `user_deck_shares` table with `(deck_uid, shared_with_handle, permission_level)`. `user_decks.is_public` covers public sharing already.
- **Tag taxonomy**: `identity_tags_json` and `archetype_hint` are open strings. If a formal tag taxonomy is needed later, add a `leader_tags` table as a normalized child of `leader_profiles`. No existing data is lost.
- **Multiple leaders per deck** (future format): `user_decks` has one `leader_code`. If multi-leader formats emerge, add a `user_deck_leaders` table rather than changing the column — preserves backward compatibility for all existing single-leader decks.

### JSON column discipline

JSON-in-TEXT columns (`snapshot_json`, `core_cards_json`, `flex_cards_json`, `identity_tags_json`) are used sparingly and only where:
1. The data is a variable-length list with no cross-table query need, and
2. The whole value is always read or written atomically.

Do not use JSON columns for data that needs to be filtered, aggregated, or joined in SQL. If a JSON column starts being queried with `json_extract()` frequently, migrate its contents to a proper normalized table.

### Concurrent writes

`miru_user_decks.db` is the only database that will receive concurrent writes (multiple users saving decks simultaneously). WAL mode handles this. Do not use `BEGIN EXCLUSIVE` transactions in the write path — use default `DEFERRED` transactions and let SQLite's WAL handle concurrency.

For `card_catalog.db` and `miru_deck_intel.db`, writes come only from single-process pipeline tools. Concurrent write protection is not needed.

### Path to read replicas

If the site ever needs read replicas (e.g., Tailscale multi-device access where each device serves its own traffic), the SQLite files can be replicated via:
- `rsync` + WAL checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`)
- Litestream (continuous SQLite replication to S3/object storage)

No schema changes are needed for either approach. Do not add application-level replication logic to database code.
