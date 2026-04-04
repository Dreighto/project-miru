-- Migration m001: Deck Builder tables
-- Target database : miru_user_decks.db  (new file, created on first run)
-- Applied by      : tools/miru_migrate_db.py --target user_decks
--
-- All statements are idempotent (CREATE ... IF NOT EXISTS).
-- Run this migration at any time; running it twice is safe.
--
-- See docs/miru_db_schema.md §3 for full field reference and design rationale.

-- ────────────────────────────────────────────────────────────────────────────
-- PRAGMA
-- ────────────────────────────────────────────────────────────────────────────

-- WAL mode is required from day one: the AI server and the Deck Builder
-- frontend will read concurrently while the API writes.
PRAGMA journal_mode = WAL;

-- ────────────────────────────────────────────────────────────────────────────
-- TABLE: user_decks
-- ────────────────────────────────────────────────────────────────────────────
-- One row per user-created deck. The deck_uid is stable across version bumps
-- and is used in URLs (/deck/<deck_uid>).
--
-- deck_uid format: "ud-" + 8 lowercase hex chars  (secrets.token_hex(4))
-- Example: "ud-a3f29c1e"
-- Generated in application code, never by this migration.

CREATE TABLE IF NOT EXISTS user_decks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    leader_code     TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code (type=Leader).
    -- Not a SQL foreign key — the catalog lives in a separate file.
    -- Application code enforces validity.

    format_code     TEXT    NOT NULL DEFAULT 'standard',
    -- Allowed values: 'standard', 'block', 'unlimited'

    archetype_hint  TEXT    NOT NULL DEFAULT '',
    -- Optional user-supplied label, e.g. "aggro", "straw-hat-rush"

    notes           TEXT    NOT NULL DEFAULT '',
    -- Free-form deck notes; visible only to the owner

    is_public       INTEGER NOT NULL DEFAULT 0,
    -- 0 = private (default), 1 = publicly viewable without auth

    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    -- Both columns: Unix timestamp (seconds since epoch).
    -- Use int(time.time()) in Python.

    version         INTEGER NOT NULL DEFAULT 1
    -- Increments on every successful edit.
    -- Always equals MAX(user_deck_versions.version) for this deck_uid.
);

-- ────────────────────────────────────────────────────────────────────────────
-- TABLE: user_deck_cards
-- ────────────────────────────────────────────────────────────────────────────
-- One row per card per deck. Quantity stored in the row; not as multiple rows.

CREATE TABLE IF NOT EXISTS user_deck_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid    TEXT    NOT NULL REFERENCES user_decks(deck_uid) ON DELETE CASCADE,
    card_code   TEXT    NOT NULL,
    -- Soft reference to cards.canonical_code in card_catalog.db

    quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity BETWEEN 1 AND 4),
    -- Standard One Piece TCG: max 4 copies per card.
    -- Leader card is NOT stored here (it is in user_decks.leader_code).

    section     TEXT    NOT NULL DEFAULT 'main',
    -- 'main' only in this pass. Future values: 'side', 'reserve'.

    UNIQUE(deck_uid, card_code)
);

-- ────────────────────────────────────────────────────────────────────────────
-- TABLE: user_deck_versions
-- ────────────────────────────────────────────────────────────────────────────
-- Append-only version history. A snapshot is written on every successful edit.
-- Enables rollback without re-querying user_deck_cards.

CREATE TABLE IF NOT EXISTS user_deck_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid        TEXT    NOT NULL REFERENCES user_decks(deck_uid) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    -- Matches user_decks.version at the time of this snapshot.

    snapshot_json   TEXT    NOT NULL DEFAULT '{}',
    -- Full deck state at this version. JSON schema:
    -- {
    --   "leader_code": "OP01-001",
    --   "format_code": "standard",
    --   "cards": [
    --     {"card_code": "OP01-002", "quantity": 4, "section": "main"},
    --     ...
    --   ]
    -- }

    change_summary  TEXT    NOT NULL DEFAULT '',
    -- Optional human-readable description of this version's changes.
    -- Example: "Added 2x OP01-007, removed 1x OP01-003"

    created_at      INTEGER NOT NULL,
    -- Unix timestamp of when this version was created.

    UNIQUE(deck_uid, version)
);

-- ────────────────────────────────────────────────────────────────────────────
-- INDEXES
-- ────────────────────────────────────────────────────────────────────────────

-- List decks by leader or recency (most common dashboard query)
CREATE INDEX IF NOT EXISTS idx_user_decks_leader
    ON user_decks(leader_code, updated_at DESC);

-- Find all cards in a deck (most common card-list read)
CREATE INDEX IF NOT EXISTS idx_user_deck_cards_deck
    ON user_deck_cards(deck_uid);

-- "Which decks use this card?" (deck builder card search)
CREATE INDEX IF NOT EXISTS idx_user_deck_cards_card
    ON user_deck_cards(card_code);

-- Version history for a specific deck
CREATE INDEX IF NOT EXISTS idx_user_deck_versions_deck
    ON user_deck_versions(deck_uid, version DESC);
