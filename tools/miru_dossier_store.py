from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from shared.intel.identity_truth_locks import reject_forbidden_identity_name

try:
    from tools.miru_insight_voice import build_insight_display_list, build_single_voice_insight
except ImportError:
    build_insight_display_list = None  # optional Phase 16 voice layer
    build_single_voice_insight = None

try:
    from tools.miru_source_agreement import compute_card_source_agreement
except ImportError:
    compute_card_source_agreement = None  # optional compute-on-read agreement layer

try:
    from tools.miru_preflight_safety import (
        AFFECTED_SURFACE_BANNED_INDICATOR,
        AFFECTED_SURFACE_IMAGE,
        AFFECTED_SURFACE_LEADER_HUB,
        AFFECTED_SURFACE_LIBRARY,
        AFFECTED_SURFACE_MODAL_INSIGHT,
        AFFECTED_SURFACE_RULINGS_SECTION,
        CONFLICT_CONFIDENCE_CAP,
        CONFIDENCE_CATEGORY_CARD_FACT,
        CONFIDENCE_CATEGORY_IMAGE,
        CONFIDENCE_CATEGORY_LEGALITY,
        CONFIDENCE_CATEGORY_META,
        CONFIDENCE_CATEGORY_PRICE,
        CONFIDENCE_CATEGORY_RULING,
        CONFIDENCE_CATEGORY_TRANSLATION,
        RELEASE_STATUS_PRERELEASE as PFLIGHT_PRERELEASE,
        WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND,
        WEAK_SIGNAL_NO_STRONG_META_SIGNAL,
        WEAK_SIGNAL_STILL_VERIFYING,
        WEAK_SIGNAL_TOO_EARLY_TO_CALL,
        affected_surfaces_for_insight,
        confidence_by_category_schema,
        conflict_reason_to_block_reason,
    )
except ImportError:
    PFLIGHT_PRERELEASE = "prerelease"
    CONFLICT_CONFIDENCE_CAP = 0.5
    AFFECTED_SURFACE_LIBRARY = "library"
    AFFECTED_SURFACE_MODAL_INSIGHT = "modal_insight"
    AFFECTED_SURFACE_BANNED_INDICATOR = "banned_indicator"
    AFFECTED_SURFACE_RULINGS_SECTION = "rulings_section"
    AFFECTED_SURFACE_IMAGE = "image"
    AFFECTED_SURFACE_LEADER_HUB = "leader_hub"
    CONFIDENCE_CATEGORY_CARD_FACT = "card_fact"
    CONFIDENCE_CATEGORY_IMAGE = "image"
    CONFIDENCE_CATEGORY_TRANSLATION = "translation"
    CONFIDENCE_CATEGORY_META = "meta"
    CONFIDENCE_CATEGORY_PRICE = "price"
    CONFIDENCE_CATEGORY_RULING = "ruling"
    CONFIDENCE_CATEGORY_LEGALITY = "legality"
    WEAK_SIGNAL_STILL_VERIFYING = "still_verifying"
    WEAK_SIGNAL_TOO_EARLY_TO_CALL = "too_early_to_call"
    WEAK_SIGNAL_NO_STRONG_META_SIGNAL = "no_strong_meta_signal"
    WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND = "no_official_ruling_found"

    def confidence_by_category_schema() -> dict:
        return {}
    def conflict_reason_to_block_reason(_: str) -> str:
        return "conflict_detected"
    def affected_surfaces_for_insight(**kwargs: Any) -> list:
        return []

try:
    from tools.miru_budget_guardrails import content_hash_jp
except ImportError:
    import hashlib
    def content_hash_jp(card_name_jp: str, effect_text_jp: str) -> str:
        raw = f"{str(card_name_jp or '').strip()}\n{str(effect_text_jp or '').strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


# ---------------------------------------------------------------------------
# Phase 15 – Publication compliance: source policy (fail closed)
# ---------------------------------------------------------------------------

SOURCE_POLICY_BLOCKED_PREFIXES = (
    "paywalled_",
    "login_gated_",
    "prohibited_",
    "unknown_",
    "anonymous_",
    "anon_",
    "speculation_",
    "unverified_",
    "restricted_",
)

SOURCE_POLICY_PERMITTED_PREFIXES = (
    "official_",
    "tournament_",
    "public_",
    "permitted_",
    "trusted_",
    "reputable_",
)

# Phase 19 – Japanese card intake, verified pipeline, image quality
VERIFICATION_STAGE_DISCOVERED = "discovered"
VERIFICATION_STAGE_VERIFIED = "verified"
VERIFICATION_STAGE_PUBLISH_ELIGIBLE = "publish_eligible"
VERIFICATION_STAGES = (VERIFICATION_STAGE_DISCOVERED, VERIFICATION_STAGE_VERIFIED, VERIFICATION_STAGE_PUBLISH_ELIGIBLE)

IMAGE_QUALITY_CLEAN = "clean"
IMAGE_QUALITY_CLEAR_SAMPLE = "clear_sample"
IMAGE_QUALITY_ACCEPTABLE = "acceptable"
IMAGE_QUALITY_ORDER = (IMAGE_QUALITY_CLEAN, IMAGE_QUALITY_CLEAR_SAMPLE, IMAGE_QUALITY_ACCEPTABLE)  # best first

WATERMARK_NONE = "none"
WATERMARK_SAMPLE = "sample"

REPLACEMENT_PRIORITY_HIGH = "high"
REPLACEMENT_PRIORITY_MEDIUM = "medium"
REPLACEMENT_PRIORITY_LOW = "low"

RELEASE_STATUS_PRERELEASE = "prerelease"
RELEASE_STATUS_RELEASED = "released"

# One Piece TCG official card size 63x88 mm
ASPECT_RATIO_OPTCG_W = 63
ASPECT_RATIO_OPTCG_H = 88
ASPECT_RATIO_OPTCG = ASPECT_RATIO_OPTCG_W / ASPECT_RATIO_OPTCG_H  # ~0.716


def source_policy_status(source_id: str) -> str:
    """Return 'permitted', 'blocked', or 'unknown' for a source_id.

    Unknown is treated as blocked in publication audit (fail closed).
    """
    sid = str(source_id or "").strip().lower()
    if not sid:
        return "unknown"
    for prefix in SOURCE_POLICY_BLOCKED_PREFIXES:
        if sid.startswith(prefix):
            return "blocked"
    for prefix in SOURCE_POLICY_PERMITTED_PREFIXES:
        if sid.startswith(prefix):
            return "permitted"
    return "unknown"


def connect_dossier_db(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(Path(path), timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


DOSSIER_SCHEMA_VERSION = "2026-03-stability-1"
DOSSIER_SCHEMA_REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "card_identity": {
        "translation_source_hash": "translation_source_hash TEXT NOT NULL DEFAULT ''",
    },
    "card_usage": {
        "leader_code": "leader_code TEXT NOT NULL DEFAULT ''",
        "archetype_label": "archetype_label TEXT NOT NULL DEFAULT ''",
        "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
    },
    "leader_links": {
        "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
    },
}


DOSSIER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL UNIQUE,
    canonical_code TEXT NOT NULL DEFAULT '',
    card_name TEXT NOT NULL DEFAULT '',
    set_code TEXT NOT NULL DEFAULT '',
    set_name TEXT NOT NULL DEFAULT '',
    rarity TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    card_type TEXT NOT NULL DEFAULT '',
    cost TEXT NOT NULL DEFAULT '',
    power TEXT NOT NULL DEFAULT '',
    counter TEXT NOT NULL DEFAULT '',
    life TEXT NOT NULL DEFAULT '',
    attribute TEXT NOT NULL DEFAULT '',
    traits_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    verification_state TEXT NOT NULL DEFAULT 'placeholder',
    answer_state TEXT NOT NULL DEFAULT 'no_evidence',
    source_summary TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dossier_cards_set_code ON cards(set_code, rarity);
CREATE INDEX IF NOT EXISTS idx_dossier_cards_updated_at ON cards(updated_at DESC);

CREATE TABLE IF NOT EXISTS card_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    effect_type TEXT NOT NULL DEFAULT '',
    effect_text TEXT NOT NULL DEFAULT '',
    parsed_payload_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    source_count INTEGER NOT NULL DEFAULT 0,
    primary_source_id TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, effect_type)
);
CREATE INDEX IF NOT EXISTS idx_card_effects_card ON card_effects(card_code, effect_type);

CREATE TABLE IF NOT EXISTS card_rulings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    ruling_text TEXT NOT NULL DEFAULT '',
    ruling_context TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'pending',
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, ruling_text, source_id)
);
CREATE INDEX IF NOT EXISTS idx_card_rulings_card ON card_rulings(card_code, updated_at DESC);

CREATE TABLE IF NOT EXISTS card_rulings_intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    ruling_key TEXT NOT NULL,
    ruling_text TEXT NOT NULL DEFAULT '',
    ruling_topic TEXT NOT NULL DEFAULT '',
    interaction_context TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    evidence_posture TEXT NOT NULL DEFAULT 'no_ruling_evidence_found',
    freshness_at TEXT NOT NULL DEFAULT '',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, ruling_key)
);
CREATE INDEX IF NOT EXISTS idx_card_rulings_intel_card ON card_rulings_intel(card_code, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_card_rulings_intel_topic ON card_rulings_intel(card_code, ruling_topic, evidence_posture);

CREATE TABLE IF NOT EXISTS card_synergy_intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    related_card_code TEXT NOT NULL,
    leader_code TEXT NOT NULL DEFAULT '',
    archetype_label TEXT NOT NULL DEFAULT '',
    relationship_type TEXT NOT NULL DEFAULT 'recurring_pair',
    support_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    evidence_posture TEXT NOT NULL DEFAULT 'no_synergy_evidence_found',
    freshness_at TEXT NOT NULL DEFAULT '',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    relationship_summary TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, related_card_code, leader_code, archetype_label)
);
CREATE INDEX IF NOT EXISTS idx_card_synergy_intel_card ON card_synergy_intel(card_code, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_card_synergy_intel_leader ON card_synergy_intel(leader_code, archetype_label, evidence_posture);
CREATE INDEX IF NOT EXISTS idx_card_synergy_intel_pair ON card_synergy_intel(card_code, related_card_code, evidence_posture);

CREATE TABLE IF NOT EXISTS card_lore_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL UNIQUE,
    lore_text TEXT NOT NULL DEFAULT '',
    lore_source TEXT NOT NULL DEFAULT '',
    lore_posture TEXT NOT NULL DEFAULT 'no_lore_available',
    freshness_at TEXT NOT NULL DEFAULT '',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_lore_context_card ON card_lore_context(card_code);

CREATE TABLE IF NOT EXISTS card_publication_audit (
    card_code TEXT NOT NULL PRIMARY KEY,
    audit_timestamp TEXT NOT NULL DEFAULT '',
    overall_publish_allowed INTEGER NOT NULL DEFAULT 0,
    publication_block_reasons_json TEXT NOT NULL DEFAULT '[]',
    layer_status_json TEXT NOT NULL DEFAULT '{}',
    source_policy_status TEXT NOT NULL DEFAULT 'unknown',
    provenance_status TEXT NOT NULL DEFAULT 'unknown',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_publication_audit_timestamp ON card_publication_audit(audit_timestamp DESC);

-- Phase 17: Rules, rulings, banlist, format intelligence
-- Phase 17.5: freshness/reverification metadata
CREATE TABLE IF NOT EXISTS card_banlist (
    card_code TEXT NOT NULL,
    format_name TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL DEFAULT 'legal',
    ban_date TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT NOT NULL DEFAULT '',
    next_review_at TEXT NOT NULL DEFAULT '',
    freshness_status TEXT NOT NULL DEFAULT 'current',
    stale_after_days INTEGER NOT NULL DEFAULT 90,
    UNIQUE(card_code, format_name)
);
CREATE INDEX IF NOT EXISTS idx_card_banlist_card ON card_banlist(card_code);
CREATE INDEX IF NOT EXISTS idx_card_banlist_status ON card_banlist(status, format_name);

CREATE TABLE IF NOT EXISTS card_ruling_explanations (
    card_code TEXT NOT NULL,
    ruling_key TEXT NOT NULL,
    official_ruling_text TEXT NOT NULL DEFAULT '',
    plain_language_explanation TEXT NOT NULL DEFAULT '',
    gameplay_example TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT NOT NULL DEFAULT '',
    next_review_at TEXT NOT NULL DEFAULT '',
    freshness_status TEXT NOT NULL DEFAULT 'current',
    UNIQUE(card_code, ruling_key)
);
CREATE INDEX IF NOT EXISTS idx_card_ruling_explanations_card ON card_ruling_explanations(card_code);

CREATE TABLE IF NOT EXISTS card_upcoming_rule_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effective_date TEXT NOT NULL DEFAULT '',
    announcement_source TEXT NOT NULL DEFAULT '',
    affected_cards_json TEXT NOT NULL DEFAULT '[]',
    format_name TEXT NOT NULL DEFAULT 'standard',
    change_summary TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT NOT NULL DEFAULT '',
    next_review_at TEXT NOT NULL DEFAULT '',
    freshness_status TEXT NOT NULL DEFAULT 'current',
    change_status TEXT NOT NULL DEFAULT 'upcoming',
    stale_after_days INTEGER NOT NULL DEFAULT 180
);
CREATE INDEX IF NOT EXISTS idx_card_upcoming_rule_changes_date ON card_upcoming_rule_changes(effective_date);

-- Phase 18: Publish layer – stable, compliance-approved, ready-to-display intelligence only
CREATE TABLE IF NOT EXISTS card_published_insight (
    card_code TEXT NOT NULL PRIMARY KEY,
    publish_allowed INTEGER NOT NULL DEFAULT 0,
    publish_status TEXT NOT NULL DEFAULT 'withheld',
    published_at TEXT NOT NULL DEFAULT '',
    publish_timestamp TEXT NOT NULL DEFAULT '',
    last_audit_timestamp TEXT NOT NULL DEFAULT '',
    publication_block_reasons_json TEXT NOT NULL DEFAULT '[]',
    published_payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_published_insight_status ON card_published_insight(publish_status, publish_allowed);

-- Phase 19: Japanese card intake, verified card pipeline, master image
CREATE TABLE IF NOT EXISTS card_identity (
    card_code TEXT NOT NULL PRIMARY KEY,
    card_name_jp TEXT NOT NULL DEFAULT '',
    card_name_en TEXT NOT NULL DEFAULT '',
    effect_text_jp TEXT NOT NULL DEFAULT '',
    effect_text_en TEXT NOT NULL DEFAULT '',
    trigger_text TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    card_type TEXT NOT NULL DEFAULT '',
    cost TEXT NOT NULL DEFAULT '',
    power TEXT NOT NULL DEFAULT '',
    counter TEXT NOT NULL DEFAULT '',
    life TEXT NOT NULL DEFAULT '',
    rarity TEXT NOT NULL DEFAULT '',
    set_code TEXT NOT NULL DEFAULT '',
    set_name TEXT NOT NULL DEFAULT '',
    block_icon TEXT NOT NULL DEFAULT '',
    release_status TEXT NOT NULL DEFAULT 'released',
    verification_stage TEXT NOT NULL DEFAULT 'discovered',
    translated_text_en TEXT NOT NULL DEFAULT '',
    translation_confidence REAL NOT NULL DEFAULT 0.0,
    source_id TEXT NOT NULL DEFAULT '',
    source_provenance_json TEXT NOT NULL DEFAULT '{}',
    image_source TEXT NOT NULL DEFAULT '',
    translation_source_hash TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_identity_stage ON card_identity(verification_stage);
CREATE INDEX IF NOT EXISTS idx_card_identity_translation_hash ON card_identity(translation_source_hash);
CREATE INDEX IF NOT EXISTS idx_card_identity_release ON card_identity(release_status);

CREATE TABLE IF NOT EXISTS card_master_images (
    card_code TEXT NOT NULL PRIMARY KEY,
    master_image_path TEXT NOT NULL DEFAULT '',
    master_image_url TEXT NOT NULL DEFAULT '',
    thumbnail_path TEXT NOT NULL DEFAULT '',
    full_size_modal_path TEXT NOT NULL DEFAULT '',
    image_quality TEXT NOT NULL DEFAULT 'acceptable',
    watermark_status TEXT NOT NULL DEFAULT 'none',
    replacement_priority TEXT NOT NULL DEFAULT 'medium',
    image_source_url TEXT NOT NULL DEFAULT '',
    image_verified INTEGER NOT NULL DEFAULT 0,
    aspect_ratio_preserved INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_master_images_quality ON card_master_images(image_quality, replacement_priority);

-- Preflight: conflict flags (contradictory facts, legality, translation, etc.)
CREATE TABLE IF NOT EXISTS card_conflict_flags (
    card_code TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (card_code, conflict_type)
);
CREATE INDEX IF NOT EXISTS idx_card_conflict_flags_card ON card_conflict_flags(card_code);

CREATE TABLE IF NOT EXISTS card_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    variant_key TEXT NOT NULL DEFAULT '',
    variant_label TEXT NOT NULL DEFAULT '',
    print_label TEXT NOT NULL DEFAULT '',
    finish TEXT NOT NULL DEFAULT '',
    promo_flag INTEGER NOT NULL DEFAULT 0,
    alt_art_flag INTEGER NOT NULL DEFAULT 0,
    parallel_flag INTEGER NOT NULL DEFAULT 0,
    language_code TEXT NOT NULL DEFAULT 'en',
    image_path TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    print_profile_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, variant_key, language_code, finish)
);
CREATE INDEX IF NOT EXISTS idx_card_variants_card ON card_variants(card_code, variant_key);

CREATE TABLE IF NOT EXISTS card_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_reference TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    trust_score REAL NOT NULL DEFAULT 0.0,
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, source_id, source_reference)
);
CREATE INDEX IF NOT EXISTS idx_card_sources_card ON card_sources(card_code, updated_at DESC);

CREATE TABLE IF NOT EXISTS card_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_type TEXT NOT NULL DEFAULT '',
    fact_value_json TEXT NOT NULL DEFAULT '{}',
    fact_value_text TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'candidate',
    verification_state TEXT NOT NULL DEFAULT 'pending',
    primary_source_id TEXT NOT NULL DEFAULT '',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, fact_key)
);
CREATE INDEX IF NOT EXISTS idx_card_facts_card ON card_facts(card_code, fact_type, status);

CREATE TABLE IF NOT EXISTS answer_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    fragment_key TEXT NOT NULL,
    fragment_type TEXT NOT NULL DEFAULT '',
    answer_text TEXT NOT NULL DEFAULT '',
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'pending',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, fragment_key)
);
CREATE INDEX IF NOT EXISTS idx_answer_fragments_card ON answer_fragments(card_code, fragment_type);

CREATE TABLE IF NOT EXISTS miru_schema_metadata (
    component TEXT NOT NULL PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS card_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    usage_type TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    leader_code TEXT NOT NULL DEFAULT '',
    leader_name TEXT NOT NULL DEFAULT '',
    archetype_label TEXT NOT NULL DEFAULT '',
    role_classification TEXT NOT NULL DEFAULT '',
    support_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'tentative',
    primary_source_id TEXT NOT NULL DEFAULT '',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    freshness_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, usage_type)
);
CREATE INDEX IF NOT EXISTS idx_card_usage_card ON card_usage(card_code, confidence DESC, support_count DESC);
CREATE INDEX IF NOT EXISTS idx_card_usage_leader ON card_usage(leader_code, card_code);
CREATE INDEX IF NOT EXISTS idx_card_usage_archetype ON card_usage(archetype_label, card_code);
CREATE TABLE IF NOT EXISTS card_market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, market_type)
);
CREATE TABLE IF NOT EXISTS leader_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    linked_card_code TEXT NOT NULL DEFAULT '',
    link_type TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    leader_name TEXT NOT NULL DEFAULT '',
    archetype_label TEXT NOT NULL DEFAULT '',
    role_classification TEXT NOT NULL DEFAULT '',
    support_count INTEGER NOT NULL DEFAULT 0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    status TEXT NOT NULL DEFAULT 'tentative',
    primary_source_id TEXT NOT NULL DEFAULT '',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    freshness_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, linked_card_code, link_type)
);
CREATE INDEX IF NOT EXISTS idx_leader_links_card ON leader_links(card_code, confidence DESC, support_count DESC);
CREATE INDEX IF NOT EXISTS idx_leader_links_leader ON leader_links(linked_card_code, card_code);

CREATE TABLE IF NOT EXISTS leader_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    leader_code TEXT NOT NULL UNIQUE,
    leader_name TEXT NOT NULL DEFAULT '',
    archetype_labels_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    evidence_posture TEXT NOT NULL DEFAULT 'no_leader_evidence_found',
    support_count INTEGER NOT NULL DEFAULT 0,
    linked_card_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    core_count INTEGER NOT NULL DEFAULT 0,
    flex_count INTEGER NOT NULL DEFAULT 0,
    tech_count INTEGER NOT NULL DEFAULT 0,
    staple_count INTEGER NOT NULL DEFAULT 0,
    freshness_at TEXT NOT NULL DEFAULT '',
    citation_payload_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leader_intelligence_confidence ON leader_intelligence(confidence DESC, freshness_at DESC);

CREATE TABLE IF NOT EXISTS card_strategy_intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL,
    leader_code TEXT NOT NULL DEFAULT '',
    role_label TEXT NOT NULL DEFAULT '',
    role_purpose TEXT NOT NULL DEFAULT '',
    synergy_tags_json TEXT NOT NULL DEFAULT '[]',
    game_plan_relevance TEXT NOT NULL DEFAULT '',
    strategy_rationale TEXT NOT NULL DEFAULT '',
    evidence_posture TEXT NOT NULL DEFAULT 'no_strategy_evidence_found',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    support_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    freshness_at TEXT NOT NULL DEFAULT '',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(card_code, leader_code, role_label)
);
CREATE INDEX IF NOT EXISTS idx_card_strategy_card ON card_strategy_intel(card_code, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_card_strategy_leader ON card_strategy_intel(leader_code, role_label, confidence DESC);

CREATE TABLE IF NOT EXISTS card_meta_intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code TEXT NOT NULL UNIQUE,
    trend_label TEXT NOT NULL DEFAULT 'unknown',
    meta_posture TEXT NOT NULL DEFAULT 'no_meta_evidence_found',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    support_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    leader_count INTEGER NOT NULL DEFAULT 0,
    archetype_count INTEGER NOT NULL DEFAULT 0,
    recency_score REAL NOT NULL DEFAULT 0.0,
    first_seen_at TEXT NOT NULL DEFAULT '',
    freshness_at TEXT NOT NULL DEFAULT '',
    evidence_window_days INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_card_meta_code ON card_meta_intel(card_code);
CREATE INDEX IF NOT EXISTS idx_card_meta_posture ON card_meta_intel(meta_posture, confidence DESC);

CREATE TABLE IF NOT EXISTS leader_meta_intel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    leader_code TEXT NOT NULL UNIQUE,
    leader_name TEXT NOT NULL DEFAULT '',
    trend_label TEXT NOT NULL DEFAULT 'unknown',
    meta_posture TEXT NOT NULL DEFAULT 'no_meta_evidence_found',
    confidence REAL NOT NULL DEFAULT 0.0,
    confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
    support_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    linked_card_count INTEGER NOT NULL DEFAULT 0,
    archetype_count INTEGER NOT NULL DEFAULT 0,
    recency_score REAL NOT NULL DEFAULT 0.0,
    first_seen_at TEXT NOT NULL DEFAULT '',
    freshness_at TEXT NOT NULL DEFAULT '',
    evidence_window_days INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leader_meta_code ON leader_meta_intel(leader_code);
CREATE INDEX IF NOT EXISTS idx_leader_meta_posture ON leader_meta_intel(meta_posture, confidence DESC);
"""


def ensure_dossier_schema(db_path: Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_dossier_db(path)) as conn:
        _ensure_existing_dossier_table_columns(
            conn,
            DOSSIER_SCHEMA_REQUIRED_COLUMNS,
        )
        _execute_schema_script_permissive(conn, DOSSIER_SCHEMA_SQL)
        _ensure_table_columns(
            conn,
            "card_usage",
            {
                "leader_code": "leader_code TEXT NOT NULL DEFAULT ''",
                "leader_name": "leader_name TEXT NOT NULL DEFAULT ''",
                "archetype_label": "archetype_label TEXT NOT NULL DEFAULT ''",
                "role_classification": "role_classification TEXT NOT NULL DEFAULT ''",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "status": "status TEXT NOT NULL DEFAULT 'tentative'",
                "primary_source_id": "primary_source_id TEXT NOT NULL DEFAULT ''",
                "source_ids_json": "source_ids_json TEXT NOT NULL DEFAULT '[]'",
                "citation_payload_json": "citation_payload_json TEXT NOT NULL DEFAULT '{}'",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "leader_links",
            {
                "leader_name": "leader_name TEXT NOT NULL DEFAULT ''",
                "archetype_label": "archetype_label TEXT NOT NULL DEFAULT ''",
                "role_classification": "role_classification TEXT NOT NULL DEFAULT ''",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "status": "status TEXT NOT NULL DEFAULT 'tentative'",
                "primary_source_id": "primary_source_id TEXT NOT NULL DEFAULT ''",
                "source_ids_json": "source_ids_json TEXT NOT NULL DEFAULT '[]'",
                "citation_payload_json": "citation_payload_json TEXT NOT NULL DEFAULT '{}'",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "leader_intelligence",
            {
                "leader_name": "leader_name TEXT NOT NULL DEFAULT ''",
                "archetype_labels_json": "archetype_labels_json TEXT NOT NULL DEFAULT '[]'",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "evidence_posture": "evidence_posture TEXT NOT NULL DEFAULT 'no_leader_evidence_found'",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "linked_card_count": "linked_card_count INTEGER NOT NULL DEFAULT 0",
                "source_count": "source_count INTEGER NOT NULL DEFAULT 0",
                "core_count": "core_count INTEGER NOT NULL DEFAULT 0",
                "flex_count": "flex_count INTEGER NOT NULL DEFAULT 0",
                "tech_count": "tech_count INTEGER NOT NULL DEFAULT 0",
                "staple_count": "staple_count INTEGER NOT NULL DEFAULT 0",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "citation_payload_json": "citation_payload_json TEXT NOT NULL DEFAULT '{}'",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_strategy_intel",
            {
                "role_purpose": "role_purpose TEXT NOT NULL DEFAULT ''",
                "synergy_tags_json": "synergy_tags_json TEXT NOT NULL DEFAULT '[]'",
                "game_plan_relevance": "game_plan_relevance TEXT NOT NULL DEFAULT ''",
                "strategy_rationale": "strategy_rationale TEXT NOT NULL DEFAULT ''",
                "evidence_posture": "evidence_posture TEXT NOT NULL DEFAULT 'no_strategy_evidence_found'",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "source_count": "source_count INTEGER NOT NULL DEFAULT 0",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_meta_intel",
            {
                "trend_label": "trend_label TEXT NOT NULL DEFAULT 'unknown'",
                "meta_posture": "meta_posture TEXT NOT NULL DEFAULT 'no_meta_evidence_found'",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "source_count": "source_count INTEGER NOT NULL DEFAULT 0",
                "leader_count": "leader_count INTEGER NOT NULL DEFAULT 0",
                "archetype_count": "archetype_count INTEGER NOT NULL DEFAULT 0",
                "recency_score": "recency_score REAL NOT NULL DEFAULT 0.0",
                "first_seen_at": "first_seen_at TEXT NOT NULL DEFAULT ''",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "evidence_window_days": "evidence_window_days INTEGER NOT NULL DEFAULT 0",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "leader_meta_intel",
            {
                "leader_name": "leader_name TEXT NOT NULL DEFAULT ''",
                "trend_label": "trend_label TEXT NOT NULL DEFAULT 'unknown'",
                "meta_posture": "meta_posture TEXT NOT NULL DEFAULT 'no_meta_evidence_found'",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "source_count": "source_count INTEGER NOT NULL DEFAULT 0",
                "linked_card_count": "linked_card_count INTEGER NOT NULL DEFAULT 0",
                "archetype_count": "archetype_count INTEGER NOT NULL DEFAULT 0",
                "recency_score": "recency_score REAL NOT NULL DEFAULT 0.0",
                "first_seen_at": "first_seen_at TEXT NOT NULL DEFAULT ''",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "evidence_window_days": "evidence_window_days INTEGER NOT NULL DEFAULT 0",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_rulings_intel",
            {
                "ruling_key": "ruling_key TEXT NOT NULL DEFAULT ''",
                "ruling_text": "ruling_text TEXT NOT NULL DEFAULT ''",
                "ruling_topic": "ruling_topic TEXT NOT NULL DEFAULT ''",
                "interaction_context": "interaction_context TEXT NOT NULL DEFAULT ''",
                "source_id": "source_id TEXT NOT NULL DEFAULT ''",
                "source_reference": "source_reference TEXT NOT NULL DEFAULT ''",
                "source_url": "source_url TEXT NOT NULL DEFAULT ''",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "evidence_posture": "evidence_posture TEXT NOT NULL DEFAULT 'no_ruling_evidence_found'",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_synergy_intel",
            {
                "card_code": "card_code TEXT NOT NULL DEFAULT ''",
                "related_card_code": "related_card_code TEXT NOT NULL DEFAULT ''",
                "leader_code": "leader_code TEXT NOT NULL DEFAULT ''",
                "archetype_label": "archetype_label TEXT NOT NULL DEFAULT ''",
                "relationship_type": "relationship_type TEXT NOT NULL DEFAULT 'recurring_pair'",
                "support_count": "support_count INTEGER NOT NULL DEFAULT 0",
                "source_count": "source_count INTEGER NOT NULL DEFAULT 0",
                "confidence": "confidence REAL NOT NULL DEFAULT 0.0",
                "confidence_label": "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'",
                "evidence_posture": "evidence_posture TEXT NOT NULL DEFAULT 'no_synergy_evidence_found'",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "relationship_summary": "relationship_summary TEXT NOT NULL DEFAULT ''",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_lore_context",
            {
                "lore_text": "lore_text TEXT NOT NULL DEFAULT ''",
                "lore_source": "lore_source TEXT NOT NULL DEFAULT ''",
                "lore_posture": "lore_posture TEXT NOT NULL DEFAULT 'no_lore_available'",
                "freshness_at": "freshness_at TEXT NOT NULL DEFAULT ''",
                "provenance_json": "provenance_json TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        _ensure_table_columns(
            conn,
            "card_publication_audit",
            {
                "audit_timestamp": "audit_timestamp TEXT NOT NULL DEFAULT ''",
                "overall_publish_allowed": "overall_publish_allowed INTEGER NOT NULL DEFAULT 0",
                "publication_block_reasons_json": "publication_block_reasons_json TEXT NOT NULL DEFAULT '[]'",
                "layer_status_json": "layer_status_json TEXT NOT NULL DEFAULT '{}'",
                "source_policy_status": "source_policy_status TEXT NOT NULL DEFAULT 'unknown'",
                "provenance_status": "provenance_status TEXT NOT NULL DEFAULT 'unknown'",
                "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
            },
        )
        # Phase 17.5: freshness/reverification columns (migrate existing Phase 17 tables)
        _ensure_table_columns(
            conn,
            "card_banlist",
            {
                "fetched_at": "fetched_at TEXT NOT NULL DEFAULT ''",
                "last_verified_at": "last_verified_at TEXT NOT NULL DEFAULT ''",
                "next_review_at": "next_review_at TEXT NOT NULL DEFAULT ''",
                "freshness_status": "freshness_status TEXT NOT NULL DEFAULT 'current'",
                "stale_after_days": "stale_after_days INTEGER NOT NULL DEFAULT 90",
            },
        )
        _ensure_table_columns(
            conn,
            "card_ruling_explanations",
            {
                "last_verified_at": "last_verified_at TEXT NOT NULL DEFAULT ''",
                "next_review_at": "next_review_at TEXT NOT NULL DEFAULT ''",
                "freshness_status": "freshness_status TEXT NOT NULL DEFAULT 'current'",
            },
        )
        _ensure_table_columns(
            conn,
            "card_upcoming_rule_changes",
            {
                "last_verified_at": "last_verified_at TEXT NOT NULL DEFAULT ''",
                "next_review_at": "next_review_at TEXT NOT NULL DEFAULT ''",
                "freshness_status": "freshness_status TEXT NOT NULL DEFAULT 'current'",
                "change_status": "change_status TEXT NOT NULL DEFAULT 'upcoming'",
                "stale_after_days": "stale_after_days INTEGER NOT NULL DEFAULT 180",
            },
        )
        _ensure_table_columns(
            conn,
            "card_identity",
            DOSSIER_SCHEMA_REQUIRED_COLUMNS["card_identity"],
        )
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_card_identity_translation_hash ON card_identity(translation_source_hash)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_card_banlist_next_review ON card_banlist(next_review_at)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_card_ruling_explanations_next_review ON card_ruling_explanations(next_review_at)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_card_upcoming_rule_changes_status ON card_upcoming_rule_changes(change_status, effective_date)")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            INSERT INTO miru_schema_metadata (component, schema_version, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(component) DO UPDATE SET
                schema_version = excluded.schema_version,
                updated_at = excluded.updated_at
            """,
            ("miru_dossier_store", DOSSIER_SCHEMA_VERSION, utc_timestamp()),
        )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_existing_dossier_table_columns(
    conn: sqlite3.Connection,
    table_column_specs: dict[str, dict[str, str]],
) -> None:
    for table_name, column_specs in table_column_specs.items():
        if not _table_exists(conn, table_name):
            continue
        _ensure_table_columns(conn, table_name, column_specs)


def _missing_required_columns(
    conn: sqlite3.Connection,
    table_column_specs: dict[str, dict[str, str]],
) -> list[str]:
    missing: list[str] = []
    for table_name, column_specs in table_column_specs.items():
        if not _table_exists(conn, table_name):
            missing.extend(f"{table_name}.{column_name}" for column_name in column_specs)
            continue
        existing = _table_columns(conn, table_name)
        for column_name in column_specs:
            if column_name not in existing:
                missing.append(f"{table_name}.{column_name}")
    return missing


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _ensure_table_columns(
    conn: sqlite3.Connection,
    table_name: str,
    column_specs: dict[str, str],
) -> None:
    existing = _table_columns(conn, table_name)
    for column_name, column_spec in column_specs.items():
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_spec}")


def _json_load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _execute_schema_script_permissive(conn: sqlite3.Connection, script: str) -> None:
    statements = [statement.strip() for statement in str(script or "").split(";") if statement.strip()]
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such column" in message or "duplicate column name" in message:
                continue
            raise


def _cards_table_identity_mode(conn: sqlite3.Connection) -> str:
    """Detect ``cards`` row shape for snapshot upserts.

    - ``dossier``: Miru dossier store layout (``card_code`` PK).
    - ``card_catalog``: Project Miru library / ``ensure_catalog_sync_schema`` layout
      (``canonical_code`` plus ``card_number``, ``aliases_json``, …) — not legacy
      ``canonical_code``-only projection tables.
    - ``unsupported``: another ``cards`` shape; skip snapshot writes to avoid
      corrupting unrelated schemas.
    """
    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards' LIMIT 1"
            ).fetchone()
            is None
        ):
            return "unknown"
    except sqlite3.Error:
        return "unknown"
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "card_code" in cols:
        return "dossier"
    if (
        "canonical_code" in cols
        and "card_number" in cols
        and "aliases_json" in cols
        and "sources_json" in cols
        and "trigger_text" in cols
    ):
        return "card_catalog"
    return "unsupported"


def _coerce_catalog_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fact_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "").strip()


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        try:
            return datetime.fromisoformat(f"{text[:-1]}+00:00")
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, list):
        raw = values
    elif isinstance(values, tuple):
        raw = list(values)
    elif isinstance(values, str):
        text = values.strip()
        raw = [part.strip() for part in text.split("|")] if "|" in text else [text]
    else:
        raw = []
    cleaned: list[str] = []
    for item in raw:
        text = _clean_text(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _latest_timestamp(values: list[str]) -> str:
    latest = ""
    latest_dt: datetime | None = None
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        parsed = _parse_utc_timestamp(text)
        if parsed is None:
            if not latest:
                latest = text
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest = text
    return latest


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        if value not in (None, "", [], {}, ()):
            return value
    return ""


class MiruDossierStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._price_index_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}

    @staticmethod
    def confidence_label(confidence: float) -> str:
        score = float(confidence or 0.0)
        if score >= 0.9:
            return "verified_fact"
        if score >= 0.7:
            return "high_confidence"
        if score >= 0.45:
            return "likely_inference"
        if score > 0.0:
            return "incomplete_data"
        return "no_evidence"

    @staticmethod
    def build_usage_type(
        *,
        leader_code: str = "",
        archetype_label: str = "",
        role_classification: str = "",
    ) -> str:
        leader = str(leader_code or "").strip().upper() or "unknown"
        archetype = str(archetype_label or "").strip().lower().replace(" ", "-") or "general"
        role = str(role_classification or "").strip().lower().replace(" ", "-") or "unspecified"
        return f"{leader}|{archetype}|{role}"

    def ensure_schema(self) -> None:
        ensure_dossier_schema(self.db_path)

    def inspect_summary(self) -> dict[str, Any]:
        status = {
            "path": str(self.db_path),
            "exists": self.db_path.is_file(),
            "openable": False,
            "usable": False,
            "schema_ok": False,
            "schema_version": "",
            "required_schema_version": DOSSIER_SCHEMA_VERSION,
            "schema_issues": [],
            "dossiers_created": 0,
            "verified_dossiers": 0,
            "variant_records": 0,
            "image_coverage_cards": 0,
            "error": "",
        }
        if not status["exists"]:
            status["error"] = "Miru dossier database does not exist yet."
            return status
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                status["openable"] = True
                metadata_row = conn.execute(
                    "SELECT schema_version FROM miru_schema_metadata WHERE component = ?",
                    ("miru_dossier_store",),
                ).fetchone()
                if metadata_row is not None:
                    status["schema_version"] = str(metadata_row[0] or "")
                missing_columns = _missing_required_columns(conn, DOSSIER_SCHEMA_REQUIRED_COLUMNS)
                status["schema_issues"] = list(missing_columns)
                status["schema_ok"] = not missing_columns
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "cards" in tables:
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
                    status["dossiers_created"] = int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
                    if "verification_state" in columns:
                        status["verified_dossiers"] = int(
                            conn.execute(
                                "SELECT COUNT(*) FROM cards WHERE lower(coalesce(verification_state, '')) = 'verified'"
                            ).fetchone()[0]
                        )
                    elif "overall_state" in columns:
                        status["verified_dossiers"] = int(
                            conn.execute(
                                "SELECT COUNT(*) FROM cards WHERE lower(coalesce(overall_state, '')) = 'verified'"
                            ).fetchone()[0]
                        )
                    if "card_variants" in tables:
                        status["variant_records"] = int(conn.execute("SELECT COUNT(*) FROM card_variants").fetchone()[0])
                        status["image_coverage_cards"] = int(
                            conn.execute(
                                "SELECT COUNT(DISTINCT card_code) FROM card_variants WHERE trim(coalesce(image_path, '')) != '' OR trim(coalesce(image_url, '')) != ''"
                            ).fetchone()[0]
                        )
                elif "dossiers" in tables:
                    status["dossiers_created"] = int(conn.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0])
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(dossiers)").fetchall()}
                    for candidate in ("overall_state", "verification_state", "status"):
                        if candidate in columns:
                            status["verified_dossiers"] = int(
                                conn.execute(
                                    f"SELECT COUNT(*) FROM dossiers WHERE lower(coalesce({candidate}, '')) = 'verified'"
                                ).fetchone()[0]
                            )
                            break
                else:
                    status["error"] = "Miru dossier database opened, but no recognized dossier tables were found."
                    return status
                status["usable"] = status["dossiers_created"] > 0 or "cards" in tables or "dossiers" in tables
                if not status["schema_ok"]:
                    missing_text = ", ".join(str(item) for item in status["schema_issues"][:8])
                    status["error"] = (
                        "Schema upgrade required for Miru dossier database. Missing columns: "
                        + missing_text
                    )
                if not status["usable"] and not status["error"]:
                    status["error"] = "Miru dossier database opened, but contains no dossier rows yet."
        except sqlite3.Error as exc:
            status["error"] = f"{exc.__class__.__name__}: {exc}"
        return status

    def upsert_card_source(self, *, card_code: str, source_id: str, source_type: str = "", source_url: str = "", source_reference: str = "", fetched_at: str = "", trust_level: str = "unknown", trust_score: float = 0.0, citation_payload: dict[str, Any] | None = None, notes: str = "") -> None:
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_sources (card_code, source_id, source_type, source_url, source_reference, fetched_at, trust_level, trust_score, citation_payload_json, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, source_id, source_reference) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_url = excluded.source_url,
                    fetched_at = excluded.fetched_at,
                    trust_level = excluded.trust_level,
                    trust_score = excluded.trust_score,
                    citation_payload_json = excluded.citation_payload_json,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (str(card_code or "").strip().upper(), str(source_id or "").strip().lower(), str(source_type or "").strip(), str(source_url or "").strip(), str(source_reference or "").strip(), str(fetched_at or "").strip(), str(trust_level or "unknown").strip(), float(trust_score or 0.0), json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True), str(notes or "").strip(), now),
            )

    def upsert_card_snapshot(self, *, card_code: str, canonical_code: str = "", facts: dict[str, Any] | None = None, confidence: float = 0.0, verification_state: str = "placeholder", source_summary: str = "") -> None:
        now = utc_timestamp()
        payload = dict(facts or {})
        resolved_confidence = float(confidence or 0.0)
        resolved_code = str(canonical_code or card_code or "").strip().upper()
        reject_forbidden_identity_name(
            resolved_code, str(payload.get("card_name") or "").strip()
        )
        with closing(connect_dossier_db(self.db_path)) as conn:
            mode = _cards_table_identity_mode(conn)
            if mode == "unsupported":
                return
            if mode == "card_catalog":
                traits_raw = payload.get("traits")
                if isinstance(traits_raw, list):
                    traits_text = " / ".join(str(x).strip() for x in traits_raw if str(x).strip())
                else:
                    traits_text = str(traits_raw or "").strip()
                cost_val = _coerce_catalog_int(payload.get("cost"))
                conn.execute(
                    """
                    INSERT INTO cards (
                        canonical_code, set_code, card_number, set_name, card_name, rarity, color,
                        card_type, cost, power, counter, attribute, traits, life, block_icon,
                        effect_text, trigger_text, aliases_json, sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_code) DO UPDATE SET
                        set_code = excluded.set_code,
                        card_number = excluded.card_number,
                        set_name = excluded.set_name,
                        card_name = excluded.card_name,
                        rarity = excluded.rarity,
                        color = excluded.color,
                        card_type = excluded.card_type,
                        cost = excluded.cost,
                        power = excluded.power,
                        counter = excluded.counter,
                        attribute = excluded.attribute,
                        traits = excluded.traits,
                        life = excluded.life,
                        block_icon = excluded.block_icon,
                        effect_text = excluded.effect_text,
                        trigger_text = excluded.trigger_text,
                        aliases_json = excluded.aliases_json,
                        sources_json = excluded.sources_json
                    """,
                    (
                        resolved_code,
                        str(payload.get("set_code") or "").strip().upper(),
                        str(payload.get("card_number") or "").strip(),
                        str(payload.get("set_name") or "").strip(),
                        str(payload.get("card_name") or "").strip(),
                        str(payload.get("rarity") or "").strip(),
                        str(payload.get("color") or "").strip(),
                        str(payload.get("card_type") or "").strip(),
                        cost_val,
                        _fact_text(payload.get("power")),
                        _fact_text(payload.get("counter")),
                        str(payload.get("attribute") or "").strip(),
                        traits_text,
                        _fact_text(payload.get("life")),
                        "",
                        str(payload.get("effect_text") or "").strip(),
                        str(payload.get("trigger_text") or "").strip(),
                        "[]",
                        "[]",
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO cards (card_code, canonical_code, card_name, set_code, set_name, rarity, color, card_type, cost, power, counter, life, attribute, traits_json, confidence, confidence_label, verification_state, answer_state, source_summary, updated_at, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    canonical_code = excluded.canonical_code,
                    card_name = excluded.card_name,
                    set_code = excluded.set_code,
                    set_name = excluded.set_name,
                    rarity = excluded.rarity,
                    color = excluded.color,
                    card_type = excluded.card_type,
                    cost = excluded.cost,
                    power = excluded.power,
                    counter = excluded.counter,
                    life = excluded.life,
                    attribute = excluded.attribute,
                    traits_json = excluded.traits_json,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    verification_state = excluded.verification_state,
                    answer_state = excluded.answer_state,
                    source_summary = excluded.source_summary,
                    updated_at = excluded.updated_at,
                    verified_at = excluded.verified_at
                """,
                (str(card_code or "").strip().upper(), str(canonical_code or card_code or "").strip().upper(), str(payload.get("card_name") or "").strip(), str(payload.get("set_code") or "").strip().upper(), str(payload.get("set_name") or "").strip(), str(payload.get("rarity") or "").strip(), str(payload.get("color") or "").strip(), str(payload.get("card_type") or "").strip(), _fact_text(payload.get("cost")), _fact_text(payload.get("power")), _fact_text(payload.get("counter")), _fact_text(payload.get("life")), str(payload.get("attribute") or "").strip(), json.dumps(list(payload.get("traits") or []), ensure_ascii=False, sort_keys=True), resolved_confidence, self.confidence_label(resolved_confidence), str(verification_state or "placeholder").strip(), self.confidence_label(resolved_confidence), str(source_summary or "").strip(), now, now if str(verification_state or "").strip().lower() == "verified" else ""),
            )

    def upsert_card_fact(self, *, card_code: str, fact_key: str, fact_type: str, fact_value: Any, confidence: float, status: str, verification_state: str, primary_source_id: str = "", source_ids: list[str] | None = None, citation_payload: dict[str, Any] | None = None, provenance: dict[str, Any] | None = None) -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        with closing(connect_dossier_db(self.db_path)) as conn:
            fact_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(card_facts)").fetchall()
            }
            if "card_code" not in fact_cols:
                # Legacy or alternate ``card_facts`` layout (e.g. ``card_id``); skip
                # to avoid corrupting unrelated schemas.
                return
            conn.execute(
                """
                INSERT INTO card_facts (card_code, fact_key, fact_type, fact_value_json, fact_value_text, confidence, confidence_label, status, verification_state, primary_source_id, source_ids_json, citation_payload_json, provenance_json, updated_at, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, fact_key) DO UPDATE SET
                    fact_value_json = excluded.fact_value_json,
                    fact_value_text = excluded.fact_value_text,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    status = excluded.status,
                    verification_state = excluded.verification_state,
                    primary_source_id = excluded.primary_source_id,
                    source_ids_json = excluded.source_ids_json,
                    citation_payload_json = excluded.citation_payload_json,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at,
                    verified_at = excluded.verified_at
                """,
                (str(card_code or "").strip().upper(), str(fact_key or "").strip(), str(fact_type or "").strip(), json.dumps(fact_value, ensure_ascii=False, sort_keys=True), _fact_text(fact_value), resolved_confidence, self.confidence_label(resolved_confidence), str(status or "candidate").strip(), str(verification_state or "pending").strip(), str(primary_source_id or "").strip().lower(), json.dumps(list(source_ids or []), ensure_ascii=False, sort_keys=True), json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True), json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True), now, now if str(status or "").strip().lower() == "verified" else ""),
            )

    def upsert_card_effect(self, *, card_code: str, effect_type: str, effect_text: str, confidence: float, primary_source_id: str = "", source_reference: str = "", source_count: int = 0, status: str = "verified", parsed_payload: dict[str, Any] | None = None) -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_effects (card_code, effect_type, effect_text, parsed_payload_json, confidence, confidence_label, source_count, primary_source_id, source_reference, status, updated_at, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, effect_type) DO UPDATE SET
                    effect_text = excluded.effect_text,
                    parsed_payload_json = excluded.parsed_payload_json,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    source_count = excluded.source_count,
                    primary_source_id = excluded.primary_source_id,
                    source_reference = excluded.source_reference,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    verified_at = excluded.verified_at
                """,
                (str(card_code or "").strip().upper(), str(effect_type or "").strip(), str(effect_text or "").strip(), json.dumps(dict(parsed_payload or {}), ensure_ascii=False, sort_keys=True), resolved_confidence, self.confidence_label(resolved_confidence), int(source_count or 0), str(primary_source_id or "").strip().lower(), str(source_reference or "").strip(), str(status or "verified").strip(), now, now if str(status or "").strip().lower() == "verified" else ""),
            )

    def upsert_card_variant(self, *, card_code: str, variant_key: str = "", variant_label: str = "", print_label: str = "", finish: str = "", promo_flag: bool = False, alt_art_flag: bool = False, parallel_flag: bool = False, language_code: str = "en", image_path: str = "", image_url: str = "", print_profile: dict[str, Any] | None = None, confidence: float = 0.0, status: str = "verified") -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        with closing(connect_dossier_db(self.db_path)) as conn:
            var_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(card_variants)").fetchall()
            }
            if "card_code" not in var_cols:
                # Catalog / legacy layouts use ``card_id`` instead of ``card_code``.
                return
            conn.execute(
                """
                INSERT INTO card_variants (card_code, variant_key, variant_label, print_label, finish, promo_flag, alt_art_flag, parallel_flag, language_code, image_path, image_url, print_profile_json, confidence, confidence_label, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, variant_key, language_code, finish) DO UPDATE SET
                    variant_label = excluded.variant_label,
                    print_label = excluded.print_label,
                    promo_flag = excluded.promo_flag,
                    alt_art_flag = excluded.alt_art_flag,
                    parallel_flag = excluded.parallel_flag,
                    image_path = excluded.image_path,
                    image_url = excluded.image_url,
                    print_profile_json = excluded.print_profile_json,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (str(card_code or "").strip().upper(), str(variant_key or "").strip(), str(variant_label or "").strip(), str(print_label or "").strip(), str(finish or "").strip(), 1 if promo_flag else 0, 1 if alt_art_flag else 0, 1 if parallel_flag else 0, str(language_code or "en").strip().lower(), str(image_path or "").strip(), str(image_url or "").strip(), json.dumps(dict(print_profile or {}), ensure_ascii=False, sort_keys=True), resolved_confidence, self.confidence_label(resolved_confidence), str(status or "verified").strip(), now),
            )

    def upsert_answer_fragment(self, *, card_code: str, fragment_key: str, fragment_type: str, answer_text: str, confidence_label: str, status: str, provenance: dict[str, Any] | None = None) -> None:
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO answer_fragments (card_code, fragment_key, fragment_type, answer_text, confidence_label, status, provenance_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, fragment_key) DO UPDATE SET
                    fragment_type = excluded.fragment_type,
                    answer_text = excluded.answer_text,
                    confidence_label = excluded.confidence_label,
                    status = excluded.status,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (str(card_code or "").strip().upper(), str(fragment_key or "").strip(), str(fragment_type or "").strip(), str(answer_text or "").strip(), str(confidence_label or "no_evidence").strip(), str(status or "pending").strip(), json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True), now),
            )

    def upsert_card_usage(
        self,
        *,
        card_code: str,
        leader_code: str = "",
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        support_count: int = 0,
        confidence: float = 0.0,
        status: str = "tentative",
        primary_source_id: str = "",
        source_ids: list[str] | None = None,
        citation_payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        freshness_at: str = "",
    ) -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        usage_type = self.build_usage_type(
            leader_code=leader_code,
            archetype_label=archetype_label,
            role_classification=role_classification,
        )
        payload = {
            "leader_code": str(leader_code or "").strip().upper(),
            "leader_name": str(leader_name or "").strip(),
            "archetype_label": str(archetype_label or "").strip(),
            "role_classification": str(role_classification or "").strip().lower(),
            "support_count": int(support_count or 0),
        }
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_usage (
                    card_code,
                    usage_type,
                    payload_json,
                    leader_code,
                    leader_name,
                    archetype_label,
                    role_classification,
                    support_count,
                    confidence,
                    confidence_label,
                    status,
                    primary_source_id,
                    source_ids_json,
                    citation_payload_json,
                    provenance_json,
                    freshness_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, usage_type) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    leader_name = excluded.leader_name,
                    support_count = excluded.support_count,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    status = excluded.status,
                    primary_source_id = excluded.primary_source_id,
                    source_ids_json = excluded.source_ids_json,
                    citation_payload_json = excluded.citation_payload_json,
                    provenance_json = excluded.provenance_json,
                    freshness_at = excluded.freshness_at,
                    updated_at = excluded.updated_at
                """,
                (
                    str(card_code or "").strip().upper(),
                    usage_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    payload["leader_code"],
                    payload["leader_name"],
                    payload["archetype_label"],
                    payload["role_classification"],
                    payload["support_count"],
                    resolved_confidence,
                    self.confidence_label(resolved_confidence),
                    str(status or "tentative").strip(),
                    str(primary_source_id or "").strip().lower(),
                    json.dumps(list(source_ids or []), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    str(freshness_at or "").strip(),
                    now,
                ),
            )

    def upsert_leader_link(
        self,
        *,
        card_code: str,
        leader_code: str,
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        support_count: int = 0,
        confidence: float = 0.0,
        status: str = "tentative",
        primary_source_id: str = "",
        source_ids: list[str] | None = None,
        citation_payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        freshness_at: str = "",
        link_type: str = "usage_association",
    ) -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO leader_links (
                    card_code,
                    linked_card_code,
                    link_type,
                    confidence,
                    leader_name,
                    archetype_label,
                    role_classification,
                    support_count,
                    confidence_label,
                    status,
                    primary_source_id,
                    source_ids_json,
                    citation_payload_json,
                    provenance_json,
                    freshness_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, linked_card_code, link_type) DO UPDATE SET
                    confidence = excluded.confidence,
                    leader_name = excluded.leader_name,
                    archetype_label = excluded.archetype_label,
                    role_classification = excluded.role_classification,
                    support_count = excluded.support_count,
                    confidence_label = excluded.confidence_label,
                    status = excluded.status,
                    primary_source_id = excluded.primary_source_id,
                    source_ids_json = excluded.source_ids_json,
                    citation_payload_json = excluded.citation_payload_json,
                    provenance_json = excluded.provenance_json,
                    freshness_at = excluded.freshness_at,
                    updated_at = excluded.updated_at
                """,
                (
                    str(card_code or "").strip().upper(),
                    str(leader_code or "").strip().upper(),
                    str(link_type or "usage_association").strip(),
                    resolved_confidence,
                    str(leader_name or "").strip(),
                    str(archetype_label or "").strip(),
                    str(role_classification or "").strip().lower(),
                    int(support_count or 0),
                    self.confidence_label(resolved_confidence),
                    str(status or "tentative").strip(),
                    str(primary_source_id or "").strip().lower(),
                    json.dumps(list(source_ids or []), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    str(freshness_at or "").strip(),
                    now,
                ),
            )

    def upsert_leader_intelligence(
        self,
        *,
        leader_code: str,
        leader_name: str = "",
        archetype_labels: list[str] | None = None,
        confidence: float = 0.0,
        evidence_posture: str = "no_leader_evidence_found",
        support_count: int = 0,
        linked_card_count: int = 0,
        source_count: int = 0,
        role_counts: dict[str, int] | None = None,
        freshness_at: str = "",
        citation_payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        now = utc_timestamp()
        resolved_confidence = float(confidence or 0.0)
        counts = dict(role_counts or {})
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO leader_intelligence (
                    leader_code,
                    leader_name,
                    archetype_labels_json,
                    confidence,
                    confidence_label,
                    evidence_posture,
                    support_count,
                    linked_card_count,
                    source_count,
                    core_count,
                    flex_count,
                    tech_count,
                    staple_count,
                    freshness_at,
                    citation_payload_json,
                    provenance_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(leader_code) DO UPDATE SET
                    leader_name = excluded.leader_name,
                    archetype_labels_json = excluded.archetype_labels_json,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    evidence_posture = excluded.evidence_posture,
                    support_count = excluded.support_count,
                    linked_card_count = excluded.linked_card_count,
                    source_count = excluded.source_count,
                    core_count = excluded.core_count,
                    flex_count = excluded.flex_count,
                    tech_count = excluded.tech_count,
                    staple_count = excluded.staple_count,
                    freshness_at = excluded.freshness_at,
                    citation_payload_json = excluded.citation_payload_json,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(leader_code or "").strip().upper(),
                    str(leader_name or "").strip(),
                    json.dumps(list(archetype_labels or []), ensure_ascii=False, sort_keys=True),
                    resolved_confidence,
                    self.confidence_label(resolved_confidence),
                    str(evidence_posture or "no_leader_evidence_found").strip(),
                    int(support_count or 0),
                    int(linked_card_count or 0),
                    int(source_count or 0),
                    int(counts.get("core") or 0),
                    int(counts.get("flex") or 0),
                    int(counts.get("tech") or 0),
                    int(counts.get("staple") or 0),
                    str(freshness_at or "").strip(),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

    def fetch_card_snapshot(self, card_code: str) -> dict[str, Any] | None:
        cc = str(card_code or "").strip().upper()
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                mode = _cards_table_identity_mode(conn)
                if mode == "card_catalog":
                    row = conn.execute(
                        "SELECT * FROM cards WHERE canonical_code = ? LIMIT 1",
                        (cc,),
                    ).fetchone()
                elif mode == "dossier":
                    row = conn.execute("SELECT * FROM cards WHERE card_code = ? LIMIT 1", (cc,)).fetchone()
                else:
                    return None
        except sqlite3.Error:
            return None
        if row is None:
            return None
        item = {key: row[key] for key in row.keys()}
        if "traits_json" in item:
            item["traits"] = _json_load(item.get("traits_json") or "[]", [])
        elif "traits" in item and isinstance(item.get("traits"), str):
            item["traits"] = [p.strip() for p in str(item.get("traits") or "").split("/") if p.strip()]
        return item

    def fetch_card_facts(self, card_code: str) -> list[dict[str, Any]]:
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute("SELECT * FROM card_facts WHERE card_code = ? ORDER BY fact_type ASC", (str(card_code or "").strip().upper(),)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["fact_value"] = _json_load(item.get("fact_value_json") or "{}", item.get("fact_value_text") or "")
            item["source_ids"] = _json_load(item.get("source_ids_json") or "[]", [])
            item["citation_payload"] = _json_load(item.get("citation_payload_json") or "{}", {})
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def fetch_verified_facts(self, card_code: str, *, fact_type: str = "") -> list[dict[str, Any]]:
        facts = self.fetch_card_facts(card_code)
        allowed_statuses = {"accepted", "corroborated"}
        results = [
            item for item in facts
            if str(item.get("status") or "").strip().lower() in allowed_statuses
        ]
        if str(fact_type or "").strip():
            results = [item for item in results if str(item.get("fact_type") or "") == str(fact_type or "").strip()]
        return results

    def fetch_card_effects(self, card_code: str, *, effect_type: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM card_effects WHERE card_code = ?"
        params: list[Any] = [str(card_code or "").strip().upper()]
        if str(effect_type or "").strip():
            query += " AND effect_type = ?"
            params.append(str(effect_type or "").strip())
        query += " ORDER BY effect_type ASC"
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["parsed_payload"] = _json_load(item.get("parsed_payload_json") or "{}", {})
            results.append(item)
        return results

    def fetch_effect_text(self, card_code: str, *, effect_type: str = "effect_text") -> str:
        effects = self.fetch_card_effects(card_code, effect_type=effect_type)
        if not effects:
            return ""
        return str(effects[0].get("effect_text") or "").strip()

    def fetch_answer_fragments(self, card_code: str) -> list[dict[str, Any]]:
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute("SELECT * FROM answer_fragments WHERE card_code = ? ORDER BY fragment_key ASC", (str(card_code or "").strip().upper(),)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def fetch_card_usage(
        self,
        card_code: str,
        *,
        leader_code: str = "",
        archetype_label: str = "",
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM card_usage WHERE card_code = ?"
        params: list[Any] = [str(card_code or "").strip().upper()]
        if str(leader_code or "").strip():
            query += " AND leader_code = ?"
            params.append(str(leader_code or "").strip().upper())
        if str(archetype_label or "").strip():
            query += " AND archetype_label = ?"
            params.append(str(archetype_label or "").strip())
        query += " ORDER BY confidence DESC, support_count DESC, archetype_label ASC, role_classification ASC"
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["payload"] = _json_load(item.get("payload_json") or "{}", {})
            item["source_ids"] = _json_load(item.get("source_ids_json") or "[]", [])
            item["citation_payload"] = _json_load(item.get("citation_payload_json") or "{}", {})
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def fetch_leader_links(self, card_code: str) -> list[dict[str, Any]]:
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM leader_links
                    WHERE card_code = ?
                    ORDER BY confidence DESC, support_count DESC, linked_card_code ASC
                    """,
                    (str(card_code or "").strip().upper(),),
                ).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["source_ids"] = _json_load(item.get("source_ids_json") or "[]", [])
            item["citation_payload"] = _json_load(item.get("citation_payload_json") or "{}", {})
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def fetch_leader_intelligence(self, leader_code: str) -> dict[str, Any] | None:
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                row = conn.execute(
                    "SELECT * FROM leader_intelligence WHERE leader_code = ? LIMIT 1",
                    (str(leader_code or "").strip().upper(),),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        item = {key: row[key] for key in row.keys()}
        item["archetype_labels"] = _json_load(item.get("archetype_labels_json") or "[]", [])
        item["citation_payload"] = _json_load(item.get("citation_payload_json") or "{}", {})
        item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
        return item

    def fetch_usage_by_leader(
        self,
        leader_code: str,
        *,
        role_classification: str = "",
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM card_usage WHERE leader_code = ?"
        params: list[Any] = [str(leader_code or "").strip().upper()]
        if str(role_classification or "").strip():
            query += " AND role_classification = ?"
            params.append(str(role_classification or "").strip().lower())
        query += " ORDER BY confidence DESC, support_count DESC, card_code ASC"
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["payload"] = _json_load(item.get("payload_json") or "{}", {})
            item["source_ids"] = _json_load(item.get("source_ids_json") or "[]", [])
            item["citation_payload"] = _json_load(item.get("citation_payload_json") or "{}", {})
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def build_leader_posture(self, leader_code: str) -> dict[str, Any]:
        leader = self.fetch_leader_intelligence(leader_code)
        if leader is None:
            return {
                "leader_code": str(leader_code or "").strip().upper(),
                "confidence_label": "no_evidence",
                "evidence_posture": "no_leader_evidence_found",
                "caution_note": "Miru does not have enough verified leader-pattern evidence yet.",
                "reassurance_note": "It can wait for stronger list coverage instead of forcing a leader conclusion.",
            }
        return {
            "leader_code": str(leader.get("leader_code") or "").strip().upper(),
            "leader_name": str(leader.get("leader_name") or "").strip(),
            "confidence_label": str(leader.get("confidence_label") or "no_evidence"),
            "evidence_posture": str(leader.get("evidence_posture") or "no_leader_evidence_found"),
            "caution_note": str((leader.get("provenance") or {}).get("caution_note") or ""),
            "reassurance_note": str((leader.get("provenance") or {}).get("reassurance_note") or ""),
            "support_count": int(leader.get("support_count") or 0),
            "linked_card_count": int(leader.get("linked_card_count") or 0),
            "source_count": int(leader.get("source_count") or 0),
            "freshness_at": str(leader.get("freshness_at") or "").strip(),
        }

    def build_usage_posture(self, card_code: str) -> dict[str, Any]:
        usage_rows = self.fetch_card_usage(card_code)
        if not usage_rows:
            return {
                "card_code": str(card_code or "").strip().upper(),
                "confidence_label": "no_evidence",
                "evidence_posture": "no_usage_evidence_found",
                "caution_note": "Miru does not have enough verified usage evidence for this card yet.",
                "reassurance_note": "It can wait for stronger decklist or tournament coverage instead of overcalling the meta.",
                "leader_code": "",
                "leader_name": "",
                "archetype_label": "",
                "role_classification": "",
            }
        top_row = usage_rows[0]
        top_confidence = float(top_row.get("confidence") or 0.0)
        support_count = int(top_row.get("support_count") or 0)
        status = str(top_row.get("status") or "").strip().lower()
        provenance = dict(top_row.get("provenance") or {})
        source_ids = list(top_row.get("source_ids") or [])
        source_count = int(
            provenance.get("source_count")
            or len(source_ids)
            or (1 if str(top_row.get("primary_source_id") or "").strip() else 0)
        )
        one_source_only = bool(provenance.get("one_source_only")) or (source_count == 1)
        stale_evidence = bool(provenance.get("stale_evidence"))
        conflicting_usage = bool(provenance.get("conflicting_usage_signals"))
        evidence_record_count = int(provenance.get("evidence_record_count") or support_count or len(usage_rows))
        freshness_at = str(top_row.get("freshness_at") or provenance.get("latest_seen_at") or "").strip()
        if stale_evidence:
            evidence_posture = "stale_usage_evidence"
            caution_note = "Miru's stored usage evidence looks stale, so it should not present this pattern as current meta certainty."
            reassurance_note = "It can still share the historical pattern carefully while waiting for fresher verified decklist coverage."
        elif conflicting_usage:
            evidence_posture = "partial_usage_evidence"
            caution_note = "Current usage signals are split across nearby patterns, so Miru should avoid overstating a single leader or archetype link."
            reassurance_note = "It can keep the usage summary narrow until stronger list coverage separates the pattern more clearly."
        elif status in {"accepted", "corroborated"} and top_confidence >= 0.82 and support_count >= 3 and source_count >= 2 and not one_source_only:
            evidence_posture = "verified_usage"
            caution_note = ""
            reassurance_note = "Miru can talk about this usage pattern as verified current usage, while keeping it distinct from card truth."
        elif top_confidence >= 0.5 or support_count >= 1:
            evidence_posture = "partial_usage_evidence"
            caution_note = "Usage evidence exists, but coverage is still limited."
            if one_source_only:
                caution_note = "Usage evidence currently comes from a narrow source slice, so Miru should keep the pattern provisional."
            reassurance_note = "Miru can describe the current pattern carefully without overstating staple or meta status."
        else:
            evidence_posture = "incomplete_usage_evidence"
            caution_note = "Usage evidence is still too thin for a strong deck-role claim."
            reassurance_note = "Miru can stay cautious and avoid calling this a staple until coverage improves."
        return {
            "card_code": str(card_code or "").strip().upper(),
            "confidence_label": str(top_row.get("confidence_label") or self.confidence_label(top_confidence)),
            "evidence_posture": evidence_posture,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
            "leader_code": str(top_row.get("leader_code") or "").strip().upper(),
            "leader_name": str(top_row.get("leader_name") or "").strip(),
            "archetype_label": str(top_row.get("archetype_label") or "").strip(),
            "role_classification": str(top_row.get("role_classification") or "").strip(),
            "support_count": support_count,
            "source_count": source_count,
            "evidence_record_count": evidence_record_count,
            "freshness_at": freshness_at,
        }

    # ------------------------------------------------------------------
    # Meta intelligence storage and read helpers
    # ------------------------------------------------------------------

    def upsert_card_meta_intel(
        self,
        *,
        card_code: str,
        trend_label: str = "unknown",
        meta_posture: str = "no_meta_evidence_found",
        confidence: float = 0.0,
        support_count: int = 0,
        source_count: int = 0,
        leader_count: int = 0,
        archetype_count: int = 0,
        recency_score: float = 0.0,
        first_seen_at: str = "",
        freshness_at: str = "",
        evidence_window_days: int = 0,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        now = utc_timestamp()
        resolved_code = str(card_code or "").strip().upper()
        resolved_confidence = round(max(min(float(confidence or 0.0), 0.98), 0.0), 4)
        resolved_recency = round(max(min(float(recency_score or 0.0), 1.0), 0.0), 4)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_meta_intel (
                    card_code, trend_label, meta_posture, confidence, confidence_label,
                    support_count, source_count, leader_count, archetype_count,
                    recency_score, first_seen_at, freshness_at, evidence_window_days,
                    provenance_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    trend_label = excluded.trend_label,
                    meta_posture = excluded.meta_posture,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    support_count = excluded.support_count,
                    source_count = excluded.source_count,
                    leader_count = excluded.leader_count,
                    archetype_count = excluded.archetype_count,
                    recency_score = excluded.recency_score,
                    first_seen_at = excluded.first_seen_at,
                    freshness_at = excluded.freshness_at,
                    evidence_window_days = excluded.evidence_window_days,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_code,
                    str(trend_label or "unknown").strip(),
                    str(meta_posture or "no_meta_evidence_found").strip(),
                    resolved_confidence,
                    self.confidence_label(resolved_confidence),
                    max(int(support_count or 0), 0),
                    max(int(source_count or 0), 0),
                    max(int(leader_count or 0), 0),
                    max(int(archetype_count or 0), 0),
                    resolved_recency,
                    str(first_seen_at or "").strip(),
                    str(freshness_at or "").strip(),
                    max(int(evidence_window_days or 0), 0),
                    json.dumps(dict(provenance or {}), ensure_ascii=True, sort_keys=True),
                    now,
                ),
            )

    def fetch_card_meta_intel(self, card_code: str) -> dict[str, Any] | None:
        resolved_code = str(card_code or "").strip().upper()
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                row = conn.execute(
                    "SELECT * FROM card_meta_intel WHERE card_code = ? LIMIT 1",
                    (resolved_code,),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        item = {key: row[key] for key in row.keys()}
        item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
        return item

    def upsert_leader_meta_intel(
        self,
        *,
        leader_code: str,
        leader_name: str = "",
        trend_label: str = "unknown",
        meta_posture: str = "no_meta_evidence_found",
        confidence: float = 0.0,
        support_count: int = 0,
        source_count: int = 0,
        linked_card_count: int = 0,
        archetype_count: int = 0,
        recency_score: float = 0.0,
        first_seen_at: str = "",
        freshness_at: str = "",
        evidence_window_days: int = 0,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        now = utc_timestamp()
        resolved_leader = str(leader_code or "").strip().upper()
        resolved_confidence = round(max(min(float(confidence or 0.0), 0.98), 0.0), 4)
        resolved_recency = round(max(min(float(recency_score or 0.0), 1.0), 0.0), 4)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO leader_meta_intel (
                    leader_code, leader_name, trend_label, meta_posture,
                    confidence, confidence_label, support_count, source_count,
                    linked_card_count, archetype_count, recency_score,
                    first_seen_at, freshness_at, evidence_window_days,
                    provenance_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(leader_code) DO UPDATE SET
                    leader_name = excluded.leader_name,
                    trend_label = excluded.trend_label,
                    meta_posture = excluded.meta_posture,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    support_count = excluded.support_count,
                    source_count = excluded.source_count,
                    linked_card_count = excluded.linked_card_count,
                    archetype_count = excluded.archetype_count,
                    recency_score = excluded.recency_score,
                    first_seen_at = excluded.first_seen_at,
                    freshness_at = excluded.freshness_at,
                    evidence_window_days = excluded.evidence_window_days,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_leader,
                    str(leader_name or "").strip(),
                    str(trend_label or "unknown").strip(),
                    str(meta_posture or "no_meta_evidence_found").strip(),
                    resolved_confidence,
                    self.confidence_label(resolved_confidence),
                    max(int(support_count or 0), 0),
                    max(int(source_count or 0), 0),
                    max(int(linked_card_count or 0), 0),
                    max(int(archetype_count or 0), 0),
                    resolved_recency,
                    str(first_seen_at or "").strip(),
                    str(freshness_at or "").strip(),
                    max(int(evidence_window_days or 0), 0),
                    json.dumps(dict(provenance or {}), ensure_ascii=True, sort_keys=True),
                    now,
                ),
            )

    def fetch_leader_meta_intel(self, leader_code: str) -> dict[str, Any] | None:
        resolved_leader = str(leader_code or "").strip().upper()
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                row = conn.execute(
                    "SELECT * FROM leader_meta_intel WHERE leader_code = ? LIMIT 1",
                    (resolved_leader,),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        item = {key: row[key] for key in row.keys()}
        item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
        return item

    def build_card_meta_posture(self, card_code: str) -> dict[str, Any]:
        resolved_code = str(card_code or "").strip().upper()
        record = self.fetch_card_meta_intel(resolved_code)
        if record is None:
            return {
                "card_code": resolved_code,
                "meta_posture": "no_meta_evidence_found",
                "trend_label": "unknown",
                "confidence_label": "no_evidence",
                "caution_note": "Miru does not have verified meta evidence for this card yet.",
                "reassurance_note": "It can wait for broader verified usage coverage before describing a meta pattern.",
                "support_count": 0,
                "source_count": 0,
            }
        posture = str(record.get("meta_posture") or "no_meta_evidence_found")
        trend_label = str(record.get("trend_label") or "unknown")
        confidence_label = str(record.get("confidence_label") or "no_evidence")
        provenance = dict(record.get("provenance") or {})
        caution_note = str(provenance.get("caution_note") or "")
        if posture == "verified_meta_pattern":
            caution_note = caution_note or ""
            reassurance_note = "Miru can describe this card's meta presence from stored verified usage evidence without speculating."
        elif posture == "emerging_meta_pattern":
            caution_note = caution_note or "This pattern is still recent and coverage may not reflect full meta adoption."
            reassurance_note = "Miru can describe it as an emerging pattern while keeping the window of evidence visible."
        elif posture == "partial_meta_evidence":
            caution_note = caution_note or "Meta evidence exists, but coverage is still limited in scope or source diversity."
            reassurance_note = "Miru can describe the current signal carefully without overstating meta importance."
        elif posture == "stale_meta_evidence":
            caution_note = caution_note or "Meta evidence for this card looks stale and may not reflect the current environment."
            reassurance_note = "It can keep the historical picture available while waiting for fresher verified coverage."
        else:
            caution_note = caution_note or "Meta evidence is still too thin for any strong pattern claim."
            reassurance_note = "Miru can stay cautious and avoid meta claims until coverage improves."
        return {
            "card_code": resolved_code,
            "meta_posture": posture,
            "trend_label": trend_label,
            "confidence_label": confidence_label,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
            "support_count": int(record.get("support_count") or 0),
            "source_count": int(record.get("source_count") or 0),
            "leader_count": int(record.get("leader_count") or 0),
            "archetype_count": int(record.get("archetype_count") or 0),
            "recency_score": float(record.get("recency_score") or 0.0),
            "freshness_at": str(record.get("freshness_at") or "").strip(),
        }

    def build_leader_meta_posture(self, leader_code: str) -> dict[str, Any]:
        resolved_leader = str(leader_code or "").strip().upper()
        record = self.fetch_leader_meta_intel(resolved_leader)
        if record is None:
            return {
                "leader_code": resolved_leader,
                "meta_posture": "no_meta_evidence_found",
                "trend_label": "unknown",
                "confidence_label": "no_evidence",
                "caution_note": "Miru does not have verified meta evidence for this leader yet.",
                "reassurance_note": "It can wait for broader verified decklist coverage before describing a leader meta pattern.",
                "support_count": 0,
                "source_count": 0,
            }
        posture = str(record.get("meta_posture") or "no_meta_evidence_found")
        trend_label = str(record.get("trend_label") or "unknown")
        confidence_label = str(record.get("confidence_label") or "no_evidence")
        provenance = dict(record.get("provenance") or {})
        caution_note = str(provenance.get("caution_note") or "")
        if posture == "verified_meta_pattern":
            caution_note = caution_note or ""
            reassurance_note = "Miru can describe this leader's meta standing from stored verified usage evidence."
        elif posture == "emerging_meta_pattern":
            caution_note = caution_note or "This leader pattern is still recent; coverage may not reflect full meta adoption."
            reassurance_note = "Miru can describe it as an emerging pattern while keeping the evidence window visible."
        elif posture == "partial_meta_evidence":
            caution_note = caution_note or "Leader meta evidence exists, but coverage is still narrow in scope or diversity."
            reassurance_note = "Miru can describe the current signal carefully without overstating the leader's meta standing."
        elif posture == "stale_meta_evidence":
            caution_note = caution_note or "Leader meta evidence looks stale and may not reflect the current environment."
            reassurance_note = "It can keep the historical picture available while waiting for fresher verified coverage."
        else:
            caution_note = caution_note or "Leader meta evidence is still too thin for any strong pattern claim."
            reassurance_note = "Miru can stay cautious and wait for more verified decklist coverage."
        return {
            "leader_code": resolved_leader,
            "leader_name": str(record.get("leader_name") or "").strip(),
            "meta_posture": posture,
            "trend_label": trend_label,
            "confidence_label": confidence_label,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
            "support_count": int(record.get("support_count") or 0),
            "source_count": int(record.get("source_count") or 0),
            "linked_card_count": int(record.get("linked_card_count") or 0),
            "archetype_count": int(record.get("archetype_count") or 0),
            "recency_score": float(record.get("recency_score") or 0.0),
            "freshness_at": str(record.get("freshness_at") or "").strip(),
        }

    def upsert_card_strategy_intel(
        self,
        *,
        card_code: str,
        leader_code: str = "",
        role_label: str = "",
        role_purpose: str = "",
        synergy_tags: list[str] | None = None,
        game_plan_relevance: str = "",
        strategy_rationale: str = "",
        evidence_posture: str = "no_strategy_evidence_found",
        confidence: float = 0.0,
        support_count: int = 0,
        source_count: int = 0,
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        now = utc_timestamp()
        resolved_code = str(card_code or "").strip().upper()
        resolved_leader = str(leader_code or "").strip().upper()
        resolved_role = str(role_label or "").strip().lower()
        resolved_confidence = round(max(min(float(confidence or 0.0), 0.98), 0.0), 4)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_strategy_intel (
                    card_code, leader_code, role_label, role_purpose,
                    synergy_tags_json, game_plan_relevance, strategy_rationale,
                    evidence_posture, confidence, confidence_label,
                    support_count, source_count, freshness_at,
                    provenance_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, leader_code, role_label) DO UPDATE SET
                    role_purpose = excluded.role_purpose,
                    synergy_tags_json = excluded.synergy_tags_json,
                    game_plan_relevance = excluded.game_plan_relevance,
                    strategy_rationale = excluded.strategy_rationale,
                    evidence_posture = excluded.evidence_posture,
                    confidence = excluded.confidence,
                    confidence_label = excluded.confidence_label,
                    support_count = excluded.support_count,
                    source_count = excluded.source_count,
                    freshness_at = excluded.freshness_at,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_code,
                    resolved_leader,
                    resolved_role,
                    str(role_purpose or "").strip(),
                    json.dumps(list(synergy_tags or []), ensure_ascii=True),
                    str(game_plan_relevance or "").strip(),
                    str(strategy_rationale or "").strip(),
                    str(evidence_posture or "no_strategy_evidence_found").strip(),
                    resolved_confidence,
                    self.confidence_label(resolved_confidence),
                    max(int(support_count or 0), 0),
                    max(int(source_count or 0), 0),
                    str(freshness_at or "").strip(),
                    json.dumps(dict(provenance or {}), ensure_ascii=True, sort_keys=True),
                    now,
                ),
            )

    def fetch_card_strategy_intel(
        self,
        card_code: str,
        *,
        leader_code: str = "",
    ) -> list[dict[str, Any]]:
        resolved_code = str(card_code or "").strip().upper()
        resolved_leader = str(leader_code or "").strip().upper()
        query = "SELECT * FROM card_strategy_intel WHERE card_code = ?"
        params: list[Any] = [resolved_code]
        if resolved_leader:
            query += " AND leader_code = ?"
            params.append(resolved_leader)
        query += " ORDER BY confidence DESC, leader_code ASC, role_label ASC"
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["synergy_tags"] = _json_load(item.get("synergy_tags_json") or "[]", [])
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def fetch_strategy_by_leader(
        self,
        leader_code: str,
        *,
        role_label: str = "",
    ) -> list[dict[str, Any]]:
        resolved_leader = str(leader_code or "").strip().upper()
        query = "SELECT * FROM card_strategy_intel WHERE leader_code = ?"
        params: list[Any] = [resolved_leader]
        if str(role_label or "").strip():
            query += " AND role_label = ?"
            params.append(str(role_label or "").strip().lower())
        query += " ORDER BY role_label ASC, confidence DESC, card_code ASC"
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.Error:
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["synergy_tags"] = _json_load(item.get("synergy_tags_json") or "[]", [])
            item["provenance"] = _json_load(item.get("provenance_json") or "{}", {})
            results.append(item)
        return results

    def build_strategy_posture(self, card_code: str, *, leader_code: str = "") -> dict[str, Any]:
        rows = self.fetch_card_strategy_intel(card_code, leader_code=leader_code)
        resolved_code = str(card_code or "").strip().upper()
        if not rows:
            return {
                "card_code": resolved_code,
                "leader_code": str(leader_code or "").strip().upper(),
                "evidence_posture": "no_strategy_evidence_found",
                "confidence_label": "no_evidence",
                "caution_note": "Miru does not have verified strategy evidence for this card yet.",
                "reassurance_note": "It can wait for stronger usage and role coverage instead of speculating about strategy.",
                "role_label": "",
                "support_count": 0,
                "source_count": 0,
            }
        top = rows[0]
        posture = str(top.get("evidence_posture") or "no_strategy_evidence_found").strip()
        confidence_label = str(top.get("confidence_label") or "no_evidence").strip()
        support_count = int(top.get("support_count") or 0)
        source_count = int(top.get("source_count") or 0)
        provenance = dict(top.get("provenance") or {})
        if posture == "verified_strategy_pattern":
            caution_note = ""
            reassurance_note = "Miru can describe this strategy pattern from stored verified usage evidence without speculating."
        elif posture == "partial_strategy_pattern":
            caution_note = str(provenance.get("caution_note") or "Strategy evidence exists, but coverage is still limited.")
            reassurance_note = "Miru can describe the current pattern carefully without overstating the card's strategic importance."
        elif posture == "stale_strategy_evidence":
            caution_note = "Miru's strategy evidence for this card looks stale and should not be presented as current meta certainty."
            reassurance_note = "It can keep the historical pattern available while waiting for fresher verified coverage."
        else:
            caution_note = "Strategy evidence is still too thin for a strong role or synergy claim."
            reassurance_note = "Miru can stay cautious and avoid calling this a key piece until coverage improves."
        return {
            "card_code": resolved_code,
            "leader_code": str(top.get("leader_code") or "").strip().upper(),
            "evidence_posture": posture,
            "confidence_label": confidence_label,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
            "role_label": str(top.get("role_label") or "").strip(),
            "role_purpose": str(top.get("role_purpose") or "").strip(),
            "synergy_tags": list(top.get("synergy_tags") or []),
            "support_count": support_count,
            "source_count": source_count,
            "freshness_at": str(top.get("freshness_at") or "").strip(),
        }

    def build_strategy_context(self, card_code: str, *, leader_code: str = "") -> dict[str, Any]:
        strategy_records = self.fetch_card_strategy_intel(card_code, leader_code=leader_code)
        strategy_posture = self.build_strategy_posture(card_code, leader_code=leader_code)
        return {
            "card_code": str(card_code or "").strip().upper(),
            "leader_code": str(leader_code or "").strip().upper(),
            "strategy_records": strategy_records,
            "strategy_posture": strategy_posture,
        }

    def build_answer_posture(self, card_code: str) -> dict[str, Any]:
        snapshot = self.fetch_card_snapshot(card_code)
        if snapshot is None:
            return {
                "card_code": str(card_code or "").strip().upper(),
                "confidence_label": "no_evidence",
                "evidence_posture": "no_evidence_found",
                "caution_note": "Miru does not have verified dossier evidence for this card yet.",
                "reassurance_note": "It can stay cautious and wait for stronger verified sources.",
            }
        confidence_label = str(snapshot.get("confidence_label") or "no_evidence")
        if confidence_label == "verified_fact":
            posture = "verified_fact"
            caution = ""
            reassurance = "Miru can answer from stored verified dossier facts."
        elif confidence_label in {"high_confidence", "likely_inference"}:
            posture = "partial_evidence"
            caution = "Some details may still need stronger corroboration."
            reassurance = "Miru can answer carefully and keep uncertainty visible."
        else:
            posture = "incomplete_evidence"
            caution = "Verified support is still incomplete for this card."
            reassurance = "Miru can fall back to cautious wording instead of guessing."
        return {
            "card_code": str(card_code or "").strip().upper(),
            "confidence_label": confidence_label,
            "evidence_posture": posture,
            "caution_note": caution,
            "reassurance_note": reassurance,
        }

    def build_answer_context(self, card_code: str) -> dict[str, Any]:
        snapshot = self.fetch_card_snapshot(card_code)
        return {
            "card_code": str(card_code or "").strip().upper(),
            "snapshot": snapshot,
            "facts": self.fetch_verified_facts(card_code),
            "effects": self.fetch_card_effects(card_code),
            "answer_fragments": self.fetch_answer_fragments(card_code),
            "answer_posture": self.build_answer_posture(card_code),
            "has_verified_snapshot": bool(snapshot and str(snapshot.get("verification_state") or "").strip().lower() == "verified"),
        }

    def build_usage_context(self, card_code: str) -> dict[str, Any]:
        return {
            "card_code": str(card_code or "").strip().upper(),
            "usage_records": self.fetch_card_usage(card_code),
            "leader_links": self.fetch_leader_links(card_code),
            "usage_posture": self.build_usage_posture(card_code),
        }

    def build_leader_context(self, leader_code: str) -> dict[str, Any]:
        resolved_leader = str(leader_code or "").strip().upper()
        cards = self.fetch_usage_by_leader(resolved_leader)
        cards_by_role: dict[str, list[dict[str, Any]]] = {}
        for card in cards:
            role = str(card.get("role_classification") or "unspecified").strip().lower()
            cards_by_role.setdefault(role, []).append(card)
        strategy_records = self.fetch_strategy_by_leader(resolved_leader)
        return {
            "leader_code": resolved_leader,
            "leader": self.fetch_leader_intelligence(resolved_leader),
            "cards": cards,
            "cards_by_role": cards_by_role,
            "strategy_records": strategy_records,
            "leader_posture": self.build_leader_posture(resolved_leader),
        }

    # ------------------------------------------------------------------
    # Unified intelligence summary read paths (deliverable #6 – Phase 11)
    # No new DB writes; stitches existing stored structures for a clean
    # future UI read path.  All reads are indexed; no heavy aggregation.
    # ------------------------------------------------------------------

    def build_card_intelligence_summary(
        self,
        card_code: str,
        *,
        leader_code: str = "",
    ) -> dict[str, Any]:
        """Return a compact, UI-ready intelligence summary for a card.

        Combines identity, usage posture, strategy posture, meta posture,
        and top leader association into one flat dict so a future Project
        Miru UI page can render without additional server-side computation.
        All sub-reads are lightweight indexed lookups on stored structures.
        """
        resolved_code = str(card_code or "").strip().upper()

        # Identity
        snapshot = self.fetch_card_snapshot(resolved_code)
        identity: dict[str, Any] = {}
        if snapshot:
            identity = {
                "card_name": str(snapshot.get("card_name") or "").strip(),
                "set_code": str(snapshot.get("set_code") or "").strip(),
                "set_name": str(snapshot.get("set_name") or "").strip(),
                "rarity": str(snapshot.get("rarity") or "").strip(),
                "color": str(snapshot.get("color") or "").strip(),
                "card_type": str(snapshot.get("card_type") or "").strip(),
                "verification_state": str(snapshot.get("verification_state") or "").strip(),
            }

        # Posture layers
        usage_posture = self.build_usage_posture(resolved_code)
        strategy_posture = self.build_strategy_posture(resolved_code, leader_code=leader_code)
        meta_posture = self.build_card_meta_posture(resolved_code)

        # Top leader link
        leader_links = self.fetch_leader_links(resolved_code)
        top_leader: dict[str, Any] = {}
        if leader_links:
            top = leader_links[0]
            top_leader = {
                "leader_code": str(top.get("card_code") or top.get("linked_card_code") or "").strip().upper(),
                "leader_name": str(top.get("leader_name") or "").strip(),
                "archetype_label": str(top.get("archetype_label") or "").strip(),
                "role_classification": str(top.get("role_classification") or "").strip(),
                "confidence": float(top.get("confidence") or 0.0),
            }

        # Strategy signals
        strategy_records = self.fetch_card_strategy_intel(resolved_code, leader_code=leader_code)
        role_label = str(strategy_posture.get("role_label") or "").strip()
        role_purpose = str(strategy_posture.get("role_purpose") or "").strip()
        synergy_tags = list(strategy_posture.get("synergy_tags") or [])

        # Overall confidence: lowest among layers that have evidence
        layer_confidences = [
            float(usage_posture.get("confidence_label") == "verified_fact") * 0.95,
        ]
        meta_record = self.fetch_card_meta_intel(resolved_code)
        if meta_record:
            layer_confidences.append(float(meta_record.get("confidence") or 0.0))
        if strategy_records:
            layer_confidences.append(float(strategy_records[0].get("confidence") or 0.0))
        overall_confidence = round(sum(layer_confidences) / len(layer_confidences), 3) if layer_confidences else 0.0

        # Rulings posture (Phase 12)
        ruling_posture = self.build_ruling_posture(resolved_code)
        ruling_records_raw = self.fetch_card_rulings_intel(resolved_code, limit=5)
        ruling_records = [
            {
                "ruling_text": str(r.get("ruling_text") or "").strip(),
                "ruling_topic": str(r.get("ruling_topic") or "").strip(),
                "evidence_posture": str(r.get("evidence_posture") or "").strip(),
                "confidence": float(r.get("confidence") or 0.0),
                "freshness_at": str(r.get("freshness_at") or "").strip(),
            }
            for r in ruling_records_raw
        ]

        # Synergy posture (Phase 13)
        synergy_posture = self.build_synergy_posture(resolved_code)
        synergy_records_raw = self.fetch_card_synergy_intel(resolved_code, limit=5)
        synergy_records = [
            {
                "related_card_code": str(r.get("related_card_code") or "").strip().upper(),
                "leader_code": str(r.get("leader_code") or "").strip().upper(),
                "relationship_type": str(r.get("relationship_type") or "").strip(),
                "confidence": float(r.get("confidence") or 0.0),
                "evidence_posture": str(r.get("evidence_posture") or "").strip(),
                "relationship_summary": str(r.get("relationship_summary") or "").strip(),
            }
            for r in synergy_records_raw
        ]

        # Freshness: most recent across all layers (including rulings + synergy)
        freshness_candidates = [
            str(usage_posture.get("freshness_at") or "").strip(),
            str(meta_posture.get("freshness_at") or "").strip(),
            str(strategy_posture.get("freshness_at") or "").strip(),
            str(ruling_posture.get("freshness_at") or "").strip(),
            str(synergy_posture.get("freshness_at") or "").strip(),
        ]
        freshness_at = max((f for f in freshness_candidates if f), default="")

        # Evidence breadth
        breadth: dict[str, Any] = {
            "leader_count": int(meta_record.get("leader_count") or 0) if meta_record else 0,
            "archetype_count": int(meta_record.get("archetype_count") or 0) if meta_record else 0,
            "source_count": int(meta_record.get("source_count") or 0) if meta_record else 0,
            "support_count": int(meta_record.get("support_count") or 0) if meta_record else 0,
            "ruling_count": int(ruling_posture.get("ruling_count") or 0),
            "synergy_count": int(synergy_posture.get("synergy_count") or 0),
        }

        return {
            "card_code": resolved_code,
            "identity": identity,
            "usage_posture": usage_posture,
            "strategy_posture": strategy_posture,
            "meta_posture": meta_posture,
            "ruling_posture": ruling_posture,
            "ruling_records": ruling_records,
            "synergy_posture": synergy_posture,
            "synergy_records": synergy_records,
            "top_leader": top_leader,
            "role_label": role_label,
            "role_purpose": role_purpose,
            "synergy_tags": synergy_tags,
            "trend_label": str(meta_posture.get("trend_label") or "unknown"),
            "overall_confidence": overall_confidence,
            "freshness_at": freshness_at,
            "evidence_breadth": breadth,
        }

    def build_leader_intelligence_summary(self, leader_code: str) -> dict[str, Any]:
        """Return a compact, UI-ready intelligence summary for a leader.

        Combines leader pattern intelligence, meta posture, role-grouped
        cards, strategy records, and archetype labels into one flat dict
        for a future Project Miru UI page.  All sub-reads are indexed
        lookups on stored structures; no heavy aggregation at read time.
        """
        resolved_leader = str(leader_code or "").strip().upper()

        # Leader base intelligence
        leader_intel = self.fetch_leader_intelligence(resolved_leader)
        leader_name = str((leader_intel or {}).get("leader_name") or "").strip()
        archetype_labels = list((leader_intel or {}).get("archetype_labels") or [])

        # Posture layers
        leader_posture = self.build_leader_posture(resolved_leader)
        meta_posture = self.build_leader_meta_posture(resolved_leader)

        # Cards grouped by role
        usage_cards = self.fetch_usage_by_leader(resolved_leader)
        cards_by_role: dict[str, list[dict[str, Any]]] = {}
        for card in usage_cards:
            role = str(card.get("role_classification") or "unspecified").strip().lower()
            entry = {
                "card_code": str(card.get("card_code") or "").strip().upper(),
                "confidence": float(card.get("confidence") or 0.0),
                "support_count": int(card.get("support_count") or 0),
                "archetype_label": str(card.get("archetype_label") or "").strip(),
            }
            cards_by_role.setdefault(role, []).append(entry)

        # Top strategy records per role (limit 5 per role for summary)
        strategy_records_raw = self.fetch_strategy_by_leader(resolved_leader)
        strategy_by_role: dict[str, list[dict[str, Any]]] = {}
        for rec in strategy_records_raw:
            role = str(rec.get("role_label") or "unspecified").strip().lower()
            entry = {
                "card_code": str(rec.get("card_code") or "").strip().upper(),
                "role_purpose": str(rec.get("role_purpose") or "").strip(),
                "synergy_tags": list(rec.get("synergy_tags") or []),
                "evidence_posture": str(rec.get("evidence_posture") or "").strip(),
                "confidence": float(rec.get("confidence") or 0.0),
            }
            bucket = strategy_by_role.setdefault(role, [])
            if len(bucket) < 5:
                bucket.append(entry)

        # Meta record for breadth/freshness
        meta_record = self.fetch_leader_meta_intel(resolved_leader)

        # Overall confidence: average of leader_intel confidence and meta confidence
        confidence_values = []
        if leader_intel:
            confidence_values.append(float(leader_intel.get("confidence") or 0.0))
        if meta_record:
            confidence_values.append(float(meta_record.get("confidence") or 0.0))
        overall_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0

        freshness_candidates = [
            str(leader_posture.get("freshness_at") or "").strip(),
            str(meta_posture.get("freshness_at") or "").strip(),
        ]
        freshness_at = max((f for f in freshness_candidates if f), default="")

        breadth: dict[str, Any] = {
            "linked_card_count": int(
                (leader_intel or {}).get("linked_card_count")
                or (meta_record or {}).get("linked_card_count")
                or 0
            ),
            "archetype_count": int(
                (meta_record or {}).get("archetype_count")
                or (leader_intel or {}).get("tech_count", 0)
                or 0
            ),
            "source_count": int(
                (leader_intel or {}).get("source_count")
                or (meta_record or {}).get("source_count")
                or 0
            ),
            "support_count": int(
                (leader_intel or {}).get("support_count")
                or (meta_record or {}).get("support_count")
                or 0
            ),
        }

        # Synergy highlights for this leader (Phase 13) – top pairs only
        synergy_records_raw = self.fetch_synergy_by_leader(resolved_leader, limit=10)
        synergy_highlights = [
            {
                "card_code": str(r.get("card_code") or "").strip().upper(),
                "related_card_code": str(r.get("related_card_code") or "").strip().upper(),
                "relationship_type": str(r.get("relationship_type") or "").strip(),
                "confidence": float(r.get("confidence") or 0.0),
                "evidence_posture": str(r.get("evidence_posture") or "").strip(),
            }
            for r in synergy_records_raw
        ]

        return {
            "leader_code": resolved_leader,
            "leader_name": leader_name,
            "archetype_labels": archetype_labels,
            "leader_posture": leader_posture,
            "meta_posture": meta_posture,
            "cards_by_role": cards_by_role,
            "strategy_by_role": strategy_by_role,
            "synergy_highlights": synergy_highlights,
            "trend_label": str(meta_posture.get("trend_label") or "unknown"),
            "overall_confidence": overall_confidence,
            "freshness_at": freshness_at,
            "evidence_breadth": breadth,
        }

    # ------------------------------------------------------------------
    # Phase 12 – Rulings Intelligence
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_ruling_posture_and_label(
        *,
        confidence: float,
        source_count: int,
        days_old: int,
    ) -> tuple[str, str]:
        """Return (evidence_posture, confidence_label) for a rulings record.

        Failsafe rules (conservative, in priority order):
        1. No evidence → no_ruling_evidence_found
        2. Stale evidence (>365 days) → stale_ruling_evidence regardless of confidence
        3. Single source only → at most partial_ruling_evidence even if confidence is high
        4. Low confidence (<0.55) → incomplete_ruling_evidence
        5. Moderate confidence (<0.75) → partial_ruling_evidence
        6. Strong, multi-source, fresh → verified_ruling
        """
        if confidence <= 0.0 or source_count < 1:
            return "no_ruling_evidence_found", "no_evidence"

        if days_old > 365:
            return "stale_ruling_evidence", "low_confidence"

        if source_count < 2:
            # Single source: cap at partial even if confidence is good
            if confidence >= 0.55:
                return "partial_ruling_evidence", "partial_confidence"
            return "incomplete_ruling_evidence", "low_confidence"

        if confidence < 0.55:
            return "incomplete_ruling_evidence", "low_confidence"

        if confidence < 0.75:
            return "partial_ruling_evidence", "partial_confidence"

        return "verified_ruling", "high_confidence"

    def upsert_card_ruling_intel(
        self,
        *,
        card_code: str,
        ruling_key: str,
        ruling_text: str,
        ruling_topic: str = "",
        interaction_context: str = "",
        source_id: str,
        source_reference: str = "",
        source_url: str = "",
        confidence: float,
        evidence_posture: str,
        confidence_label: str,
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        """Write or update one ruling intelligence record (idempotent by ruling_key)."""
        resolved = str(card_code or "").strip().upper()
        key = str(ruling_key or "").strip()
        if not resolved or not key:
            return
        now = utc_timestamp()
        provenance_json = json.dumps(provenance or {})
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_rulings_intel
                    (card_code, ruling_key, ruling_text, ruling_topic, interaction_context,
                     source_id, source_reference, source_url, confidence, confidence_label,
                     evidence_posture, freshness_at, provenance_json, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, ruling_key) DO UPDATE SET
                    ruling_text        = excluded.ruling_text,
                    ruling_topic       = excluded.ruling_topic,
                    interaction_context = excluded.interaction_context,
                    source_id          = excluded.source_id,
                    source_reference   = excluded.source_reference,
                    source_url         = excluded.source_url,
                    confidence         = excluded.confidence,
                    confidence_label   = excluded.confidence_label,
                    evidence_posture   = excluded.evidence_posture,
                    freshness_at       = excluded.freshness_at,
                    provenance_json    = excluded.provenance_json,
                    updated_at         = excluded.updated_at
                """,
                (
                    resolved, key,
                    str(ruling_text or "").strip(),
                    str(ruling_topic or "").strip(),
                    str(interaction_context or "").strip(),
                    str(source_id or "").strip(),
                    str(source_reference or "").strip(),
                    str(source_url or "").strip(),
                    float(confidence),
                    str(confidence_label or "no_evidence").strip(),
                    str(evidence_posture or "no_ruling_evidence_found").strip(),
                    str(freshness_at or now).strip(),
                    provenance_json,
                    now,
                ),
            )

    def fetch_card_rulings_intel(
        self,
        card_code: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return stored ruling intelligence records for a card, ordered by confidence desc."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT ruling_key, ruling_text, ruling_topic, interaction_context,
                       source_id, source_reference, source_url,
                       confidence, confidence_label, evidence_posture,
                       freshness_at, provenance_json, updated_at
                FROM card_rulings_intel
                WHERE card_code = ?
                ORDER BY confidence DESC, updated_at DESC
                LIMIT ?
                """,
                (resolved, limit),
            ).fetchall()
        columns = [
            "ruling_key", "ruling_text", "ruling_topic", "interaction_context",
            "source_id", "source_reference", "source_url",
            "confidence", "confidence_label", "evidence_posture",
            "freshness_at", "provenance_json", "updated_at",
        ]
        result = []
        for row in rows:
            rec: dict[str, Any] = dict(zip(columns, row))
            try:
                rec["provenance"] = json.loads(rec.get("provenance_json") or "{}")
            except (ValueError, TypeError):
                rec["provenance"] = {}
            result.append(rec)
        return result

    def build_ruling_posture(self, card_code: str) -> dict[str, Any]:
        """Return a structured posture dict for a card's rulings intelligence.

        Follows the same caution/reassurance convention as other posture builders.
        Safe empty posture returned when no records exist.
        """
        resolved = str(card_code or "").strip().upper()
        records = self.fetch_card_rulings_intel(resolved)
        if not records:
            return {
                "evidence_posture": "no_ruling_evidence_found",
                "confidence_label": "no_evidence",
                "ruling_count": 0,
                "source_count": 0,
                "freshness_at": "",
                "caution_note": (
                    "No ruling evidence has been verified for this card yet. "
                    "Miru cannot confirm interaction behavior without a credible source."
                ),
                "reassurance_note": (
                    "When verified ruling evidence is available, Miru will surface it here."
                ),
            }

        # Aggregate signals across all stored rulings
        source_ids: set[str] = set()
        freshness_candidates: list[str] = []
        best_confidence = 0.0
        weakest_posture = "verified_ruling"

        posture_rank = {
            "verified_ruling": 6,
            "partial_ruling_evidence": 4,
            "incomplete_ruling_evidence": 2,
            "stale_ruling_evidence": 1,
            "no_ruling_evidence_found": 0,
        }

        for rec in records:
            sid = str(rec.get("source_id") or "").strip()
            if sid:
                source_ids.add(sid)
            fa = str(rec.get("freshness_at") or "").strip()
            if fa:
                freshness_candidates.append(fa)
            conf = float(rec.get("confidence") or 0.0)
            if conf > best_confidence:
                best_confidence = conf
            rp = str(rec.get("evidence_posture") or "no_ruling_evidence_found")
            if posture_rank.get(rp, 0) < posture_rank.get(weakest_posture, 6):
                weakest_posture = rp

        freshness_at = max(freshness_candidates, default="")

        # Single-source failsafe: aggregate can never be "verified_ruling" from one source
        if len(source_ids) < 2 and weakest_posture == "verified_ruling":
            weakest_posture = "partial_ruling_evidence"

        label_map = {
            "verified_ruling": "high_confidence",
            "partial_ruling_evidence": "partial_confidence",
            "incomplete_ruling_evidence": "low_confidence",
            "stale_ruling_evidence": "low_confidence",
            "no_ruling_evidence_found": "no_evidence",
        }
        confidence_label = label_map.get(weakest_posture, "no_evidence")

        caution_note = ""
        reassurance_note = ""
        if weakest_posture == "verified_ruling":
            reassurance_note = (
                "Ruling evidence appears strong and multi-sourced. "
                "Miru's confidence in this interaction behavior is high."
            )
        elif weakest_posture == "partial_ruling_evidence":
            caution_note = (
                "Ruling evidence is present but limited to a single source or "
                "moderate confidence. Treat with appropriate caution."
            )
            reassurance_note = (
                "Miru will upgrade this ruling posture as additional verified "
                "sources are found."
            )
        elif weakest_posture == "stale_ruling_evidence":
            caution_note = (
                "Ruling evidence exists but is older than one year. Rules or "
                "rulings may have changed; verify against current official sources."
            )
        else:
            caution_note = (
                "Ruling evidence is sparse or incomplete. "
                "Miru cannot confirm interaction behavior with confidence."
            )
            reassurance_note = (
                "Miru stores only verified ruling records and will not surface "
                "speculation as a ruling."
            )

        return {
            "evidence_posture": weakest_posture,
            "confidence_label": confidence_label,
            "ruling_count": len(records),
            "source_count": len(source_ids),
            "freshness_at": freshness_at,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
        }

    def build_ruling_context(self, card_code: str) -> dict[str, Any]:
        """Return posture + top ruling records for a card (lightweight, indexed read)."""
        resolved = str(card_code or "").strip().upper()
        posture = self.build_ruling_posture(resolved)
        records = self.fetch_card_rulings_intel(resolved, limit=10)
        compact_records = [
            {
                "ruling_text": str(r.get("ruling_text") or "").strip(),
                "ruling_topic": str(r.get("ruling_topic") or "").strip(),
                "interaction_context": str(r.get("interaction_context") or "").strip(),
                "source_id": str(r.get("source_id") or "").strip(),
                "evidence_posture": str(r.get("evidence_posture") or "").strip(),
                "confidence": float(r.get("confidence") or 0.0),
                "freshness_at": str(r.get("freshness_at") or "").strip(),
            }
            for r in records
        ]
        return {
            "card_code": resolved,
            "ruling_posture": posture,
            "ruling_records": compact_records,
        }

    # ------------------------------------------------------------------
    # Phase 17 – Rules, rulings, banlist, format intelligence
    # ------------------------------------------------------------------

    def upsert_banlist_status(
        self,
        *,
        card_code: str,
        format_name: str = "standard",
        status: str,
        ban_date: str = "",
        reason: str = "",
        source_id: str,
        fetched_at: str = "",
        last_verified_at: str = "",
        next_review_at: str = "",
        stale_after_days: int = 90,
    ) -> None:
        """Write or update banlist status for a card in a format (idempotent). Phase 17.5: sets freshness metadata."""
        resolved = str(card_code or "").strip().upper()
        fmt = str(format_name or "standard").strip().lower()
        st = str(status or "legal").strip().lower()
        if st not in ("legal", "banned", "restricted", "format_restricted"):
            st = "legal"
        now = utc_timestamp()
        fetched = str(fetched_at or now).strip()[:19]
        verified = str(last_verified_at or now).strip()[:19]
        if not verified and fetched:
            verified = fetched[:10] + " 00:00:00" if len(fetched) >= 10 else fetched
        stale_d = max(1, int(stale_after_days or 90))
        next_review = str(next_review_at or "").strip()[:10]
        if not next_review:
            next_review = (datetime.now(timezone.utc) + timedelta(days=stale_d)).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_banlist
                    (card_code, format_name, status, ban_date, reason, source_id, updated_at,
                     fetched_at, last_verified_at, next_review_at, freshness_status, stale_after_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?)
                ON CONFLICT(card_code, format_name) DO UPDATE SET
                    status = excluded.status,
                    ban_date = excluded.ban_date,
                    reason = excluded.reason,
                    source_id = excluded.source_id,
                    updated_at = excluded.updated_at,
                    fetched_at = excluded.fetched_at,
                    last_verified_at = excluded.last_verified_at,
                    next_review_at = excluded.next_review_at,
                    freshness_status = 'current',
                    stale_after_days = excluded.stale_after_days
                """,
                (
                    resolved,
                    fmt,
                    st,
                    str(ban_date or "").strip()[:10],
                    str(reason or "").strip(),
                    str(source_id or "").strip(),
                    now,
                    fetched,
                    verified,
                    next_review,
                    stale_d,
                ),
            )

    def fetch_banlist_status(
        self,
        card_code: str,
        *,
        format_name: str = "standard",
    ) -> dict[str, Any]:
        """Return banlist record for card in format, or legal-by-default. Phase 17.5: includes freshness metadata and computes stale."""
        resolved = str(card_code or "").strip().upper()
        fmt = str(format_name or "standard").strip().lower()
        default = {
            "card_code": resolved or "",
            "format_name": fmt,
            "status": "legal",
            "ban_date": "",
            "reason": "",
            "source_id": "",
            "updated_at": "",
            "fetched_at": "",
            "last_verified_at": "",
            "next_review_at": "",
            "freshness_status": "current",
            "stale_after_days": 90,
        }
        if not resolved:
            return default
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT card_code, format_name, status, ban_date, reason, source_id, updated_at,
                       fetched_at, last_verified_at, next_review_at, freshness_status, stale_after_days
                FROM card_banlist
                WHERE card_code = ? AND format_name = ?
                """,
                (resolved, fmt),
            ).fetchone()
        if not row:
            default["card_code"] = resolved
            return default
        out = {
            "card_code": row[0],
            "format_name": row[1],
            "status": str(row[2] or "legal").strip(),
            "ban_date": str(row[3] or "").strip(),
            "reason": str(row[4] or "").strip(),
            "source_id": str(row[5] or "").strip(),
            "updated_at": str(row[6] or "").strip(),
            "fetched_at": str(row[7] or "").strip(),
            "last_verified_at": str(row[8] or "").strip(),
            "next_review_at": str(row[9] or "").strip(),
            "freshness_status": str(row[10] or "current").strip(),
            "stale_after_days": int(row[11] or 90),
        }
        # Phase 17.5: if no explicit freshness columns yet, compute from updated_at
        ref_date = out.get("last_verified_at") or out.get("updated_at") or ""
        if ref_date:
            try:
                ref_ts = ref_date[:10] if len(ref_date) >= 10 else ref_date
                ref_d = datetime.strptime(ref_ts, "%Y-%m-%d").date()
                age_days = (datetime.now(timezone.utc).date() - ref_d).days
                if age_days > out.get("stale_after_days", 90):
                    out["freshness_status"] = "stale"
            except (ValueError, TypeError):
                pass
        return out

    def upsert_ruling_explanation(
        self,
        *,
        card_code: str,
        ruling_key: str,
        official_ruling_text: str,
        plain_language_explanation: str = "",
        gameplay_example: str = "",
        source_id: str,
        last_verified_at: str = "",
        next_review_at: str = "",
    ) -> None:
        """Store or update beginner-friendly explanation for a ruling (idempotent by ruling_key). Phase 17.5: optional freshness."""
        resolved = str(card_code or "").strip().upper()
        key = str(ruling_key or "").strip()
        if not resolved or not key:
            return
        now = utc_timestamp()
        verified = str(last_verified_at or now).strip()[:19]
        review = str(next_review_at or "").strip()
        if not review:
            review = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_ruling_explanations
                    (card_code, ruling_key, official_ruling_text, plain_language_explanation, gameplay_example, source_id, updated_at,
                     last_verified_at, next_review_at, freshness_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'current')
                ON CONFLICT(card_code, ruling_key) DO UPDATE SET
                    official_ruling_text = excluded.official_ruling_text,
                    plain_language_explanation = excluded.plain_language_explanation,
                    gameplay_example = excluded.gameplay_example,
                    source_id = excluded.source_id,
                    updated_at = excluded.updated_at,
                    last_verified_at = excluded.last_verified_at,
                    next_review_at = excluded.next_review_at,
                    freshness_status = 'current'
                """,
                (
                    resolved,
                    key,
                    str(official_ruling_text or "").strip(),
                    str(plain_language_explanation or "").strip(),
                    str(gameplay_example or "").strip(),
                    str(source_id or "").strip(),
                    now,
                    verified,
                    review,
                ),
            )

    def fetch_ruling_explanations(
        self,
        card_code: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return stored ruling explanations for a card (official + plain language + example)."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT ruling_key, official_ruling_text, plain_language_explanation, gameplay_example, source_id, updated_at
                FROM card_ruling_explanations
                WHERE card_code = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (resolved, limit),
            ).fetchall()
        columns = ["ruling_key", "official_ruling_text", "plain_language_explanation", "gameplay_example", "source_id", "updated_at"]
        return [dict(zip(columns, row)) for row in rows]

    def fetch_upcoming_rule_changes_for_card(
        self,
        card_code: str,
        *,
        format_name: str = "standard",
    ) -> list[dict[str, Any]]:
        """Return upcoming rule changes that affect this card (effective_date in future)."""
        resolved = str(card_code or "").strip().upper()
        fmt = str(format_name or "standard").strip().lower()
        if not resolved:
            return []
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT id, effective_date, announcement_source, affected_cards_json, format_name, change_summary, source_id, updated_at
                FROM card_upcoming_rule_changes
                WHERE format_name = ? AND effective_date > ? AND (change_status = 'upcoming' OR change_status = '')
                ORDER BY effective_date ASC
                """,
                (fmt, now_iso),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                affected = json.loads(row[3] or "[]")
            except (TypeError, json.JSONDecodeError):
                affected = []
            if not isinstance(affected, list):
                affected = []
            affects_this = "*" in affected or resolved in (str(c or "").strip().upper() for c in affected)
            if affects_this:
                out.append({
                    "id": row[0],
                    "effective_date": str(row[1] or "").strip(),
                    "announcement_source": str(row[2] or "").strip(),
                    "affected_cards": affected if isinstance(affected, list) else [],
                    "format_name": str(row[4] or "").strip(),
                    "change_summary": str(row[5] or "").strip(),
                    "source_id": str(row[6] or "").strip(),
                    "updated_at": str(row[7] or "").strip(),
                })
        return out

    def upsert_upcoming_rule_change(
        self,
        *,
        effective_date: str,
        format_name: str = "standard",
        announcement_source: str = "",
        affected_cards_json: str | list[str] = "[]",
        change_summary: str = "",
        source_id: str = "",
    ) -> int | None:
        """Insert or update an upcoming rule change. affected_cards can be list or 'all'. Returns id."""
        fmt = str(format_name or "standard").strip().lower()
        eff = str(effective_date or "").strip()[:10]
        if not eff:
            return None
        if isinstance(affected_cards_json, list):
            payload = json.dumps(affected_cards_json)
        else:
            payload = str(affected_cards_json or "[]")
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            cur = conn.execute(
                """
                INSERT INTO card_upcoming_rule_changes
                    (effective_date, format_name, announcement_source, affected_cards_json, change_summary, source_id, updated_at,
                     last_verified_at, next_review_at, freshness_status, change_status, stale_after_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', 'upcoming', 180)
                """,
                (eff, fmt, str(announcement_source or "").strip(), payload, str(change_summary or "").strip(), str(source_id or "").strip(), now, now, now),
            )
            return cur.lastrowid

    def mark_upcoming_change_status(self, change_id: int, change_status: str) -> None:
        """Mark an upcoming rule change as current, expired, or superseded (Phase 17.5)."""
        st = str(change_status or "").strip().lower()
        if st not in ("upcoming", "current", "expired", "superseded"):
            return
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE card_upcoming_rule_changes
                SET change_status = ?, updated_at = ?, last_verified_at = ?
                WHERE id = ?
                """,
                (st, now, now, change_id),
            )

    def find_due_banlist_rechecks(
        self,
        *,
        stale_after_days: int = 90,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return banlist records due for reverification (next_review_at <= today or missing, or stale). Worker-side (Phase 17.5)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT card_code, format_name, status, updated_at, next_review_at, last_verified_at, freshness_status, stale_after_days
                FROM card_banlist
                WHERE next_review_at <= ? OR next_review_at = '' OR freshness_status = 'stale' OR freshness_status = 'needs_review'
                ORDER BY next_review_at ASC, updated_at ASC
                LIMIT ?
                """,
                (today, limit),
            ).fetchall()
        cols = ["card_code", "format_name", "status", "updated_at", "next_review_at", "last_verified_at", "freshness_status", "stale_after_days"]
        return [dict(zip(cols, row)) for row in rows]

    def find_due_ruling_rechecks(
        self,
        *,
        stale_after_days: int = 365,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return ruling intel records (card_code + ruling_key) due for reverification (freshness_at old or missing). Worker-side (Phase 17.5)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_after_days)).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT card_code, ruling_key, freshness_at, updated_at
                FROM card_rulings_intel
                WHERE freshness_at < ? OR freshness_at = ''
                ORDER BY freshness_at ASC, updated_at ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        cols = ["card_code", "ruling_key", "freshness_at", "updated_at"]
        return [dict(zip(cols, row)) for row in rows]

    def find_due_format_change_rechecks(
        self,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return upcoming rule changes due for review: effective_date in the past (mark current/expired) or within 30 days. Worker-side (Phase 17.5)."""
        near = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT id, effective_date, format_name, change_summary, change_status, last_verified_at
                FROM card_upcoming_rule_changes
                WHERE change_status = 'upcoming' AND effective_date <= ?
                ORDER BY effective_date ASC
                LIMIT ?
                """,
                (near, limit),
            ).fetchall()
        cols = ["id", "effective_date", "format_name", "change_summary", "change_status", "last_verified_at"]
        return [dict(zip(cols, row)) for row in rows]

    # ------------------------------------------------------------------
    # Phase 13 – Synergy Intelligence
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_synergy_posture_and_label(
        *,
        confidence: float,
        source_count: int,
        support_count: int,
        days_old: int,
    ) -> tuple[str, str]:
        """Return (evidence_posture, confidence_label) for a synergy record.

        Failsafe rules (conservative, in priority order):
        1. No evidence → no_synergy_evidence_found
        2. Stale evidence (>180 days) → stale_synergy_evidence
        3. Single source only → at most partial_synergy_pattern
        4. Thin support (support_count < 3) → incomplete_synergy_evidence
        5. Low confidence (<0.55) → incomplete_synergy_evidence
        6. Moderate confidence (<0.75) → partial_synergy_pattern
        7. Strong, multi-source, adequate support → verified_synergy_pattern
        """
        if confidence <= 0.0 or source_count < 1:
            return "no_synergy_evidence_found", "no_evidence"

        if days_old > 180:
            return "stale_synergy_evidence", "low_confidence"

        # Thin support: too few co-appearance observations to claim a real pattern
        if support_count < 3:
            return "incomplete_synergy_evidence", "low_confidence"

        # Single source: cap at partial even if confidence is high
        if source_count < 2:
            if confidence >= 0.55:
                return "partial_synergy_pattern", "partial_confidence"
            return "incomplete_synergy_evidence", "low_confidence"

        if confidence < 0.55:
            return "incomplete_synergy_evidence", "low_confidence"

        if confidence < 0.75:
            return "partial_synergy_pattern", "partial_confidence"

        return "verified_synergy_pattern", "high_confidence"

    def upsert_card_synergy_intel(
        self,
        *,
        card_code: str,
        related_card_code: str,
        leader_code: str = "",
        archetype_label: str = "",
        relationship_type: str = "recurring_pair",
        support_count: int = 0,
        source_count: int = 0,
        confidence: float = 0.0,
        confidence_label: str = "no_evidence",
        evidence_posture: str = "no_synergy_evidence_found",
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
        relationship_summary: str = "",
    ) -> None:
        """Write or update one synergy record (idempotent by card_code+related+leader+archetype)."""
        card = str(card_code or "").strip().upper()
        related = str(related_card_code or "").strip().upper()
        if not card or not related or card == related:
            return
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_synergy_intel
                    (card_code, related_card_code, leader_code, archetype_label,
                     relationship_type, support_count, source_count, confidence,
                     confidence_label, evidence_posture, freshness_at,
                     provenance_json, relationship_summary, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, related_card_code, leader_code, archetype_label) DO UPDATE SET
                    relationship_type   = excluded.relationship_type,
                    support_count       = excluded.support_count,
                    source_count        = excluded.source_count,
                    confidence          = excluded.confidence,
                    confidence_label    = excluded.confidence_label,
                    evidence_posture    = excluded.evidence_posture,
                    freshness_at        = excluded.freshness_at,
                    provenance_json     = excluded.provenance_json,
                    relationship_summary = excluded.relationship_summary,
                    updated_at          = excluded.updated_at
                """,
                (
                    card, related,
                    str(leader_code or "").strip().upper(),
                    str(archetype_label or "").strip().lower(),
                    str(relationship_type or "recurring_pair").strip(),
                    int(support_count), int(source_count),
                    float(confidence),
                    str(confidence_label or "no_evidence").strip(),
                    str(evidence_posture or "no_synergy_evidence_found").strip(),
                    str(freshness_at or now).strip(),
                    json.dumps(provenance or {}),
                    str(relationship_summary or "").strip(),
                    now,
                ),
            )

    def fetch_card_synergy_intel(
        self,
        card_code: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return synergy records for a card, ordered by confidence DESC."""
        card = str(card_code or "").strip().upper()
        if not card:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT related_card_code, leader_code, archetype_label,
                       relationship_type, support_count, source_count,
                       confidence, confidence_label, evidence_posture,
                       freshness_at, relationship_summary, provenance_json
                FROM card_synergy_intel
                WHERE card_code = ?
                ORDER BY confidence DESC, support_count DESC
                LIMIT ?
                """,
                (card, limit),
            ).fetchall()
        cols = [
            "related_card_code", "leader_code", "archetype_label",
            "relationship_type", "support_count", "source_count",
            "confidence", "confidence_label", "evidence_posture",
            "freshness_at", "relationship_summary", "provenance_json",
        ]
        out = []
        for row in rows:
            rec: dict[str, Any] = dict(zip(cols, row))
            try:
                rec["provenance"] = json.loads(rec.get("provenance_json") or "{}")
            except (ValueError, TypeError):
                rec["provenance"] = {}
            out.append(rec)
        return out

    def fetch_synergy_by_leader(
        self,
        leader_code: str,
        *,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """Return synergy records for a leader context, ordered by confidence DESC."""
        leader = str(leader_code or "").strip().upper()
        if not leader:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT card_code, related_card_code, archetype_label,
                       relationship_type, support_count, source_count,
                       confidence, confidence_label, evidence_posture,
                       freshness_at, relationship_summary
                FROM card_synergy_intel
                WHERE leader_code = ?
                ORDER BY confidence DESC, support_count DESC
                LIMIT ?
                """,
                (leader, limit),
            ).fetchall()
        cols = [
            "card_code", "related_card_code", "archetype_label",
            "relationship_type", "support_count", "source_count",
            "confidence", "confidence_label", "evidence_posture",
            "freshness_at", "relationship_summary",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def build_synergy_posture(self, card_code: str) -> dict[str, Any]:
        """Return a structured synergy posture dict with caution/reassurance notes."""
        card = str(card_code or "").strip().upper()
        records = self.fetch_card_synergy_intel(card)
        if not records:
            return {
                "evidence_posture": "no_synergy_evidence_found",
                "confidence_label": "no_evidence",
                "synergy_count": 0,
                "partner_count": 0,
                "source_count": 0,
                "freshness_at": "",
                "caution_note": (
                    "No synergy evidence has been verified for this card yet. "
                    "Miru derives synergy only from verified usage patterns, never from card text alone."
                ),
                "reassurance_note": (
                    "When recurring co-appearance patterns are found in verified sources, "
                    "Miru will surface them here."
                ),
            }

        posture_rank = {
            "verified_synergy_pattern": 5,
            "partial_synergy_pattern": 3,
            "incomplete_synergy_evidence": 2,
            "stale_synergy_evidence": 1,
            "no_synergy_evidence_found": 0,
        }
        partners: set[str] = set()
        source_ids: set[str] = set()
        freshness_candidates: list[str] = []
        weakest_posture = "verified_synergy_pattern"
        best_confidence = 0.0

        for rec in records:
            partners.add(str(rec.get("related_card_code") or ""))
            prov = rec.get("provenance") or {}
            for sid in list(prov.get("source_ids") or []):
                if sid:
                    source_ids.add(str(sid))
            fa = str(rec.get("freshness_at") or "").strip()
            if fa:
                freshness_candidates.append(fa)
            conf = float(rec.get("confidence") or 0.0)
            if conf > best_confidence:
                best_confidence = conf
            rp = str(rec.get("evidence_posture") or "no_synergy_evidence_found")
            if posture_rank.get(rp, 0) < posture_rank.get(weakest_posture, 5):
                weakest_posture = rp

        freshness_at = max(freshness_candidates, default="")
        label_map = {
            "verified_synergy_pattern": "high_confidence",
            "partial_synergy_pattern": "partial_confidence",
            "incomplete_synergy_evidence": "low_confidence",
            "stale_synergy_evidence": "low_confidence",
            "no_synergy_evidence_found": "no_evidence",
        }
        confidence_label = label_map.get(weakest_posture, "no_evidence")

        caution_note = ""
        reassurance_note = ""
        if weakest_posture == "verified_synergy_pattern":
            reassurance_note = (
                "Synergy patterns are backed by recurring co-appearance evidence "
                "across multiple verified sources."
            )
        elif weakest_posture == "partial_synergy_pattern":
            caution_note = (
                "Synergy evidence is present but limited to a single source or "
                "a small number of observations. Treat relationships with appropriate caution."
            )
            reassurance_note = (
                "Miru will strengthen these synergy records as more verified data is observed."
            )
        elif weakest_posture == "stale_synergy_evidence":
            caution_note = (
                "Synergy evidence exists but is older than 180 days. "
                "Meta shifts may have changed how these cards interact in practice."
            )
        else:
            caution_note = (
                "Synergy evidence is sparse or incomplete. "
                "These relationships should be considered tentative."
            )
            reassurance_note = (
                "Miru only stores synergy relationships backed by verified usage evidence."
            )

        return {
            "evidence_posture": weakest_posture,
            "confidence_label": confidence_label,
            "synergy_count": len(records),
            "partner_count": len(partners),
            "source_count": len(source_ids),
            "freshness_at": freshness_at,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
        }

    def build_synergy_context(self, card_code: str) -> dict[str, Any]:
        """Return synergy posture + top synergy records in one lightweight call."""
        card = str(card_code or "").strip().upper()
        posture = self.build_synergy_posture(card)
        records = self.fetch_card_synergy_intel(card, limit=10)
        compact = [
            {
                "related_card_code": str(r.get("related_card_code") or "").strip().upper(),
                "leader_code": str(r.get("leader_code") or "").strip().upper(),
                "archetype_label": str(r.get("archetype_label") or "").strip(),
                "relationship_type": str(r.get("relationship_type") or "").strip(),
                "support_count": int(r.get("support_count") or 0),
                "confidence": float(r.get("confidence") or 0.0),
                "evidence_posture": str(r.get("evidence_posture") or "").strip(),
                "relationship_summary": str(r.get("relationship_summary") or "").strip(),
                "freshness_at": str(r.get("freshness_at") or "").strip(),
            }
            for r in records
        ]
        return {
            "card_code": card,
            "synergy_posture": posture,
            "synergy_records": compact,
        }

    # ------------------------------------------------------------------
    # Phase 14 – Integrated Insight Model + Lore Foundation
    # ------------------------------------------------------------------

    # ── Lore context storage ──────────────────────────────────────────

    @staticmethod
    def _safe_empty_lore() -> dict[str, Any]:
        return {
            "lore_text": "",
            "lore_source": "",
            "lore_posture": "no_lore_available",
            "freshness_at": "",
        }

    def upsert_card_lore_context(
        self,
        *,
        card_code: str,
        lore_text: str,
        lore_source: str,
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        """Store verified lore context for a card (idempotent, one record per card).

        Caller must only supply lore from permitted, credible sources.
        Lore content is separate from gameplay truth and must never affect
        meta, strategy, or ruling conclusions.
        """
        card = str(card_code or "").strip().upper()
        text = str(lore_text or "").strip()
        source = str(lore_source or "").strip()
        if not card or not text or not source:
            return
        # Governance: reject obviously unverifiable sources
        disallowed = ("anonymous", "unknown", "unverified", "speculation", "fan_wiki")
        if any(source.lower().startswith(d) for d in disallowed):
            return
        now = utc_timestamp()
        lore_posture = "verified_lore" if text and source else "no_lore_available"
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_lore_context
                    (card_code, lore_text, lore_source, lore_posture, freshness_at, provenance_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    lore_text       = excluded.lore_text,
                    lore_source     = excluded.lore_source,
                    lore_posture    = excluded.lore_posture,
                    freshness_at    = excluded.freshness_at,
                    provenance_json = excluded.provenance_json,
                    updated_at      = excluded.updated_at
                """,
                (
                    card, text, source, lore_posture,
                    str(freshness_at or now).strip(),
                    json.dumps(provenance or {}),
                    now,
                ),
            )

    def fetch_card_lore_context(self, card_code: str) -> dict[str, Any]:
        """Return stored lore context for a card; safe-empty if none exists."""
        card = str(card_code or "").strip().upper()
        if not card:
            return self._safe_empty_lore()
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                "SELECT lore_text, lore_source, lore_posture, freshness_at "
                "FROM card_lore_context WHERE card_code = ?",
                (card,),
            ).fetchone()
        if not row:
            return self._safe_empty_lore()
        return {
            "lore_text": str(row[0] or "").strip(),
            "lore_source": str(row[1] or "").strip(),
            "lore_posture": str(row[2] or "no_lore_available").strip(),
            "freshness_at": str(row[3] or "").strip(),
        }

    # ── Integrated insight helpers ────────────────────────────────────

    @staticmethod
    def _posture_strength(posture: str) -> int:
        """Rank an evidence posture string 0–5 for primary insight selection.

        Higher values represent stronger, more verified evidence.
        Enrichment layers (lore, gameplay tip) are never passed here;
        only structured intelligence postures are ranked.
        """
        p = str(posture or "").lower()
        if p.startswith("verified_"):
            return 5
        if p.startswith("partial_") or p == "emerging_meta_pattern":
            return 3
        if p.startswith("incomplete_") or p.startswith("stale_"):
            return 1
        return 0  # no_*_evidence_found or unrecognised

    @staticmethod
    def _compute_insight_readiness(
        posture_map: dict[str, str],
    ) -> tuple[bool, str]:
        """Return (insight_ready, insight_availability) from a posture map.

        insight_availability values:
            "verified"  – at least one layer has verified evidence
            "partial"   – at least one layer has partial/emerging evidence
            "minimal"   – only incomplete or stale evidence across all layers
            "none"      – all layers have no evidence (card is not insight-ready)

        Failsafe: a card is not considered insight-ready if every structured
        layer returns no evidence.  Enrichment layers (tip, lore) cannot
        contribute to readiness.
        """
        strengths = [
            MiruDossierStore._posture_strength(p)
            for p in posture_map.values()
        ]
        max_strength = max(strengths, default=0)
        if max_strength >= 5:
            return True, "verified"
        if max_strength >= 3:
            return True, "partial"
        if max_strength >= 1:
            return True, "minimal"
        return False, "none"

    @staticmethod
    def _build_insight_section(
        insight_type: str,
        *,
        evidence_posture: str,
        confidence_label: str,
        summary: str,
        freshness_at: str = "",
        priority: int = 0,
    ) -> dict[str, Any]:
        """Build one insight section dict for the integrated model."""
        strength = MiruDossierStore._posture_strength(evidence_posture)
        return {
            "insight_type": insight_type,
            "evidence_posture": evidence_posture,
            "confidence_label": confidence_label,
            "summary": str(summary or "").strip(),
            "freshness_at": str(freshness_at or "").strip(),
            "posture_strength": strength,
            "priority": priority,
        }

    @staticmethod
    def _derive_gameplay_tip(
        *,
        strategy_posture: dict[str, Any],
        ruling_records: list[dict[str, Any]],
        role_purpose: str,
        synergy_tags: list[str],
    ) -> dict[str, Any]:
        """Derive a gameplay tip from existing verified intelligence layers only.

        Tip is derived (in priority order) from:
        1. strategy_posture.role_purpose → practical role description
        2. first ruling record → notable interaction note
        3. synergy_tags → brief synergy note if present

        No tip is generated from card text alone, from speculation, or
        from unverified claims.  The safe default is no_tip_available.
        """
        strategy_ep = str(strategy_posture.get("evidence_posture") or "").lower()
        is_verified_or_partial_strategy = (
            strategy_ep.startswith("verified_") or strategy_ep.startswith("partial_")
        )

        # 1. Role purpose from strategy intel
        if is_verified_or_partial_strategy and role_purpose:
            tip_text = f"In verified lists I'm usually playing this to {role_purpose}."
            tags = [str(t) for t in synergy_tags if t][:3]
            if tags:
                tip_text += f" Watch: {', '.join(tags)}."
            return {
                "tip_text": tip_text,
                "evidence_basis": "strategy_intel",
                "tip_posture": "evidence_based_tip",
            }

        # 2. First ruling record (clarifier)
        for rec in ruling_records:
            ruling_ep = str(rec.get("evidence_posture") or "").lower()
            if ruling_ep.startswith("verified_") or ruling_ep.startswith("partial_"):
                text = str(rec.get("ruling_text") or "").strip()
                if text:
                    tip_text = text[:120] + ("…" if len(text) > 120 else "")
                    return {
                        "tip_text": tip_text,
                        "evidence_basis": "ruling_intel",
                        "tip_posture": "evidence_based_tip",
                    }

        # 3. Synergy tags alone (weak but evidence-backed)
        tags = [str(t) for t in synergy_tags if t][:3]
        if tags and is_verified_or_partial_strategy:
            return {
                "tip_text": f"Verified patterns I watch here: {', '.join(tags)}.",
                "evidence_basis": "strategy_intel",
                "tip_posture": "evidence_based_tip",
            }

        return {
            "tip_text": "",
            "evidence_basis": "none",
            "tip_posture": "no_tip_available",
        }

    def build_integrated_card_insight(
        self,
        card_code: str,
        *,
        leader_code: str = "",
    ) -> dict[str, Any]:
        """Return the unified Miru Insights object for a card.

        Combines all verified intelligence layers into one structured read.
        Uses existing summary builders as the foundation; no additional
        heavy aggregation is performed at request time.

        The primary insight is the layer with the strongest posture (verified
        > partial > incomplete/stale > none).  Enrichment layers (gameplay
        tip, lore) are optional and can never become the primary insight.

        Failsafe: insight_ready is False if every structured layer returns
        no evidence.  A card cannot appear "insight ready" on the strength
        of a single thin layer.
        """
        resolved = str(card_code or "").strip().upper()

        # ── Foundation: use the Phase 11/13 summary builder ─────────
        summary = self.build_card_intelligence_summary(resolved, leader_code=leader_code)

        usage_p = summary.get("usage_posture") or {}
        strategy_p = summary.get("strategy_posture") or {}
        meta_p = summary.get("meta_posture") or {}
        ruling_p = summary.get("ruling_posture") or {}
        synergy_p = summary.get("synergy_posture") or {}

        # ── Posture map ──────────────────────────────────────────────
        # Price (Phase 16.5): no price intel yet; future phases can set price_posture/summary
        price_p = summary.get("price_posture") or {}
        posture_map = {
            "usage": str(usage_p.get("evidence_posture") or "no_usage_evidence_found"),
            "strategy": str(strategy_p.get("evidence_posture") or "no_strategy_evidence_found"),
            "meta": str(meta_p.get("evidence_posture") or "no_meta_evidence_found"),
            "ruling": str(ruling_p.get("evidence_posture") or "no_ruling_evidence_found"),
            "synergy": str(synergy_p.get("evidence_posture") or "no_synergy_evidence_found"),
            "price": str(price_p.get("evidence_posture") or "no_price_evidence_found"),
        }

        # ── Readiness ────────────────────────────────────────────────
        insight_ready, insight_availability = self._compute_insight_readiness(posture_map)

        # ── Build insight sections (only structured layers compete for primary) ──
        usage_leader = str(usage_p.get("leader_name") or summary.get("top_leader", {}).get("leader_name") or "").strip()
        usage_archetype = str(usage_p.get("archetype_label") or "").strip()
        usage_summary = (
            f"Verified usage data exists for this card"
            + (f" in {usage_archetype} decks" if usage_archetype else "")
            + (f" (leader: {usage_leader})" if usage_leader else "")
            + "."
            if posture_map["usage"] not in ("no_usage_evidence_found", "")
            else ""
        )

        role_label = str(summary.get("role_label") or strategy_p.get("role_label") or "").strip()
        role_purpose = str(summary.get("role_purpose") or strategy_p.get("role_purpose") or "").strip()
        strategy_summary = (
            f"Strategy role: {role_label}" + (f" — {role_purpose}" if role_purpose else "") + "."
            if role_label
            else (
                "Partial strategy evidence is available for this card."
                if posture_map["strategy"] not in ("no_strategy_evidence_found", "")
                else ""
            )
        )

        trend_label = str(summary.get("trend_label") or meta_p.get("trend_label") or "unknown").strip()
        meta_summary = (
            f"Meta trend: {trend_label}."
            if posture_map["meta"] not in ("no_meta_evidence_found", "")
            else ""
        )

        ruling_records = list(summary.get("ruling_records") or [])
        first_ruling_text = ""
        if ruling_records:
            raw = str(ruling_records[0].get("ruling_text") or "").strip()
            first_ruling_text = raw[:160] + ("…" if len(raw) > 160 else "")
        ruling_summary = (
            f"Ruling available: {first_ruling_text}"
            if first_ruling_text
            else (
                "Ruling evidence exists for this card."
                if posture_map["ruling"] not in ("no_ruling_evidence_found", "")
                else ""
            )
        )

        synergy_records = list(summary.get("synergy_records") or [])
        top_partners = [str(r.get("related_card_code") or "") for r in synergy_records[:3] if r.get("related_card_code")]
        synergy_summary = (
            f"Frequently pairs with: {', '.join(top_partners)}."
            if top_partners
            else (
                "Synergy pattern evidence exists for this card."
                if posture_map["synergy"] not in ("no_synergy_evidence_found", "")
                else ""
            )
        )

        # Price (Phase 16.5): future price intel can set price_summary; filler filtered by voice
        price_summary = str(price_p.get("price_summary") or "").strip()
        if not price_summary and posture_map["price"] not in ("no_price_evidence_found", ""):
            price_summary = "Price data exists for this card."

        sections: list[dict[str, Any]] = [
            self._build_insight_section(
                "usage",
                evidence_posture=posture_map["usage"],
                confidence_label=str(usage_p.get("confidence_label") or "no_evidence"),
                summary=usage_summary,
                freshness_at=str(usage_p.get("freshness_at") or ""),
                priority=5,
            ),
            self._build_insight_section(
                "strategy",
                evidence_posture=posture_map["strategy"],
                confidence_label=str(strategy_p.get("confidence_label") or "no_evidence"),
                summary=strategy_summary,
                freshness_at=str(strategy_p.get("freshness_at") or ""),
                priority=4,
            ),
            self._build_insight_section(
                "meta",
                evidence_posture=posture_map["meta"],
                confidence_label=str(meta_p.get("confidence_label") or "no_evidence"),
                summary=meta_summary,
                freshness_at=str(meta_p.get("freshness_at") or ""),
                priority=3,
            ),
            self._build_insight_section(
                "ruling",
                evidence_posture=posture_map["ruling"],
                confidence_label=str(ruling_p.get("confidence_label") or "no_evidence"),
                summary=ruling_summary,
                freshness_at=str(ruling_p.get("freshness_at") or ""),
                priority=2,
            ),
            self._build_insight_section(
                "synergy",
                evidence_posture=posture_map["synergy"],
                confidence_label=str(synergy_p.get("confidence_label") or "no_evidence"),
                summary=synergy_summary,
                freshness_at=str(synergy_p.get("freshness_at") or ""),
                priority=1,
            ),
            self._build_insight_section(
                "price",
                evidence_posture=posture_map["price"],
                confidence_label=str(price_p.get("confidence_label") or "no_evidence"),
                summary=price_summary,
                freshness_at=str(price_p.get("freshness_at") or ""),
                priority=2,
            ),
        ]

        # Sections that have real evidence
        available = [s for s in sections if s["posture_strength"] > 0]

        # ── Primary insight (failsafe: highest-ranked layer only) ─────
        # Sort by posture_strength DESC, then priority DESC as tiebreaker
        ranked = sorted(available, key=lambda s: (s["posture_strength"], s["priority"]), reverse=True)
        primary_section = ranked[0] if ranked else {}
        additional_sections = ranked[1:] if len(ranked) > 1 else []

        # Remove internal bookkeeping fields before returning
        def _public_section(s: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in s.items() if k not in ("posture_strength", "priority")}

        primary_insight = _public_section(primary_section) if primary_section else {}
        additional_insights = [_public_section(s) for s in additional_sections]

        # ── Phase 16: Miru voice display (first-person, short, player language) ─
        voice_context = {
            "leader_name": usage_leader,
            "usage_leader": usage_leader,
            "archetype_label": usage_archetype,
            "usage_archetype": usage_archetype,
            "role_label": role_label,
            "role_purpose": role_purpose,
            "trend_label": trend_label,
            "first_ruling_text": first_ruling_text,
            "top_partners": top_partners,
            "price_signal_type": str(price_p.get("price_signal_type") or "").strip(),
            "no_ruling_found": not bool(ruling_records),
        }
        ranked_sections = [primary_section] + additional_sections if primary_section else []
        if build_insight_display_list and ranked_sections:
            display_list = build_insight_display_list(ranked_sections, voice_context)
            primary_insight_display = display_list[0] if display_list else None
            additional_insight_displays = display_list[1:] if len(display_list) > 1 else []
        else:
            primary_insight_display = None
            additional_insight_displays = []

        # ── Gameplay tip (never primary; derived from verified intel) ─
        synergy_tags = list(summary.get("synergy_tags") or [])
        gameplay_tip = self._derive_gameplay_tip(
            strategy_posture=strategy_p,
            ruling_records=ruling_records,
            role_purpose=role_purpose,
            synergy_tags=synergy_tags,
        )

        # ── Lore context (safe-empty by default; never affects posture) ─
        lore_context = self.fetch_card_lore_context(resolved)

        # ── Publication eligibility (Phase 15): precomputed audit only ─
        eligibility = self.get_publication_eligibility(resolved)
        publish_allowed = bool(eligibility.get("publish_allowed"))
        publication_block_reasons = list(eligibility.get("publication_block_reasons") or [])

        # ── Preflight: prerelease caution, weak-signal states, affected surfaces, confidence by category ─
        prerelease_disclaimer = self.get_prerelease_disclaimer(resolved)
        is_prerelease = bool(prerelease_disclaimer.get("is_prerelease"))
        known_conflicts = self.get_known_conflicts(resolved)
        overall_conf = float(summary.get("overall_confidence") or 0.0)
        if is_prerelease and overall_conf > 0.7:
            overall_conf = min(overall_conf, 0.7)
        if known_conflicts and overall_conf > CONFLICT_CONFIDENCE_CAP:
            overall_conf = min(overall_conf, CONFLICT_CONFIDENCE_CAP)
        weak_signal_states: list[str] = []
        if not ruling_records:
            weak_signal_states.append(WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND)
        if is_prerelease:
            weak_signal_states.append(WEAK_SIGNAL_TOO_EARLY_TO_CALL)
        if not insight_ready and not available:
            weak_signal_states.append(WEAK_SIGNAL_STILL_VERIFYING)
        if posture_map.get("meta") in ("no_meta_evidence_found", ""):
            weak_signal_states.append(WEAK_SIGNAL_NO_STRONG_META_SIGNAL)
        banlist_status_val = self.fetch_banlist_status(resolved)
        ruling_explanations_val = self.fetch_ruling_explanations(resolved)
        has_rulings = bool(ruling_explanations_val and len(ruling_explanations_val) > 0)
        has_master_img = self.get_card_master_image(resolved) is not None
        has_leader_intel = bool(leader_code and self.fetch_leader_intelligence(leader_code)) if hasattr(self, "fetch_leader_intelligence") else False
        affected_surfaces_list = (
            affected_surfaces_for_insight(
                insight_ready=insight_ready,
                publish_eligible=publish_allowed,
                banlist_banned=bool(banlist_status_val and str(banlist_status_val.get("status") or "").strip().lower() == "banned"),
                has_ruling_explanations=has_rulings,
                has_master_image=has_master_img,
                has_leader_intel=has_leader_intel,
            )
            if callable(affected_surfaces_for_insight)
            else []
        )
        confidence_by_cat = self.get_card_confidence_by_category(resolved)

        return {
            "card_code": resolved,
            "insight_ready": insight_ready,
            "insight_availability": insight_availability,
            "available_section_count": len(available),
            "primary_insight_type": str(primary_insight.get("insight_type") or ""),
            "overall_confidence": overall_conf,
            "freshness_at": str(summary.get("freshness_at") or ""),
            "identity": dict(summary.get("identity") or {}),
            "posture_summary": posture_map,
            "primary_insight": primary_insight,
            "additional_insights": additional_insights,
            "primary_insight_display": primary_insight_display,
            "additional_insight_displays": additional_insight_displays,
            "gameplay_tip": gameplay_tip,
            "lore_context": lore_context,
            "usage_posture": usage_p,
            "strategy_posture": strategy_p,
            "meta_posture": meta_p,
            "ruling_posture": ruling_p,
            "synergy_posture": synergy_p,
            "evidence_breadth": dict(summary.get("evidence_breadth") or {}),
            "publication_eligible": publish_allowed,
            "publication_block_reasons": publication_block_reasons,
            "banlist_status": banlist_status_val,
            "ruling_explanations": ruling_explanations_val,
            "upcoming_rule_changes": self.fetch_upcoming_rule_changes_for_card(resolved),
            "no_ruling_found_message": "I couldn't find an official ruling for this interaction yet." if not ruling_records else None,
            "prerelease_caution": is_prerelease,
            "prerelease_disclaimer": prerelease_disclaimer,
            "weak_signal_states": weak_signal_states,
            "conflict_detected": bool(known_conflicts),
            "conflict_types": list(known_conflicts),
            "affected_surfaces": affected_surfaces_list,
            "confidence_by_category": confidence_by_cat,
        }

    # ------------------------------------------------------------------
    # Phase 15 – Publication compliance gate
    # ------------------------------------------------------------------

    def upsert_publication_audit(
        self,
        *,
        card_code: str,
        audit_timestamp: str,
        overall_publish_allowed: bool,
        publication_block_reasons: list[str],
        layer_status: dict[str, str],
        source_policy_status: str,
        provenance_status: str,
    ) -> None:
        """Write or replace the publication audit result for a card (idempotent)."""
        card = str(card_code or "").strip().upper()
        if not card:
            return
        now = utc_timestamp()
        reasons_json = json.dumps(list(publication_block_reasons))
        layer_json = json.dumps(dict(layer_status))
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_publication_audit
                    (card_code, audit_timestamp, overall_publish_allowed,
                     publication_block_reasons_json, layer_status_json,
                     source_policy_status, provenance_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    audit_timestamp = excluded.audit_timestamp,
                    overall_publish_allowed = excluded.overall_publish_allowed,
                    publication_block_reasons_json = excluded.publication_block_reasons_json,
                    layer_status_json = excluded.layer_status_json,
                    source_policy_status = excluded.source_policy_status,
                    provenance_status = excluded.provenance_status,
                    updated_at = excluded.updated_at
                """,
                (
                    card,
                    str(audit_timestamp or now),
                    1 if overall_publish_allowed else 0,
                    reasons_json,
                    layer_json,
                    str(source_policy_status or "unknown").strip(),
                    str(provenance_status or "unknown").strip(),
                    now,
                ),
            )

    def _posture_meets_publish_threshold(self, posture: str) -> bool:
        """Return True if posture (or confidence_label) is strong enough for publication."""
        p = str(posture or "").lower()
        if p.startswith("verified_") or p.startswith("partial_") or p == "emerging_meta_pattern":
            return True
        if p in ("verified_fact", "high_confidence", "partial_confidence"):
            return True
        return False

    def run_publication_audit(self, card_code: str) -> dict[str, Any]:
        """Run a self-audit for publication eligibility (worker-side / precomputed).

        Evaluates usage, strategy, meta, ruling, synergy, and lore layers.
        Fails closed: no audit record → not publishable; prohibited source → not publishable;
        missing required provenance → layer blocked.

        Returns a result dict with audit outcome and block reasons.
        """
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return {"audit_done": False, "reason": "missing_card_code"}

        now = utc_timestamp()
        block_reasons: list[str] = []
        layer_status: dict[str, str] = {}
        any_prohibited_source = False
        any_permitted_source = False
        provenance_ok = True

        # ── Usage ─────────────────────────────────────────────────────
        usage_rows = self.fetch_card_usage(resolved)
        if not usage_rows:
            layer_status["usage"] = "no_data"
        else:
            row = usage_rows[0]
            source_ids = list(row.get("source_ids") or [])
            prov = row.get("provenance") or {}
            for sid in list(prov.get("source_ids") or []):
                if sid and sid not in source_ids:
                    source_ids.append(sid)
            if not source_ids:
                source_ids = [str(row.get("primary_source_id") or "").strip()] if row.get("primary_source_id") else []
            if not source_ids:
                layer_status["usage"] = "blocked"
                block_reasons.append("usage_provenance_incomplete")
                provenance_ok = False
            else:
                has_permitted = False
                for sid in source_ids:
                    sid = str(sid or "").strip().lower()
                    if not sid:
                        continue
                    st = source_policy_status(sid)
                    if st == "blocked":
                        any_prohibited_source = True
                        layer_status["usage"] = "blocked"
                        if "prohibited_source_usage" not in block_reasons:
                            block_reasons.append("prohibited_source_usage")
                    elif st == "permitted":
                        any_permitted_source = True
                        has_permitted = True
                if layer_status.get("usage") != "blocked" and not has_permitted:
                    layer_status["usage"] = "blocked"
                    if "usage_source_policy_unknown" not in block_reasons:
                        block_reasons.append("usage_source_policy_unknown")
                elif layer_status.get("usage") != "blocked":
                    posture = str(row.get("evidence_posture") or row.get("confidence_label") or "").strip()
                    if self._posture_meets_publish_threshold(posture):
                        layer_status["usage"] = "approved"
                    else:
                        layer_status["usage"] = "withheld"
        if "usage" not in layer_status:
            layer_status["usage"] = "no_data"

        # ── Strategy ──────────────────────────────────────────────────
        strategy_rows = self.fetch_card_strategy_intel(resolved)
        if not strategy_rows:
            layer_status["strategy"] = "no_data"
        else:
            row = strategy_rows[0]
            prov = row.get("provenance") or {}
            source_ids = list(prov.get("source_ids") or [])
            if not source_ids and row.get("source_count", 0) > 0:
                layer_status["strategy"] = "blocked"
                if "strategy_provenance_incomplete" not in block_reasons:
                    block_reasons.append("strategy_provenance_incomplete")
                    provenance_ok = False
            else:
                has_permitted = False
                for sid in source_ids:
                    st = source_policy_status(str(sid or ""))
                    if st == "blocked":
                        any_prohibited_source = True
                        layer_status["strategy"] = "blocked"
                        if "prohibited_source_strategy" not in block_reasons:
                            block_reasons.append("prohibited_source_strategy")
                    elif st == "permitted":
                        any_permitted_source = True
                        has_permitted = True
                if layer_status.get("strategy") != "blocked" and not has_permitted:
                    layer_status["strategy"] = "blocked"
                    if "strategy_source_policy_unknown" not in block_reasons:
                        block_reasons.append("strategy_source_policy_unknown")
                elif layer_status.get("strategy") != "blocked":
                    posture = str(row.get("evidence_posture") or "")
                    layer_status["strategy"] = "approved" if self._posture_meets_publish_threshold(posture) else "withheld"
        if "strategy" not in layer_status:
            layer_status["strategy"] = "no_data"

        # ── Meta ─────────────────────────────────────────────────────
        meta_record = self.fetch_card_meta_intel(resolved)
        if not meta_record:
            layer_status["meta"] = "no_data"
        else:
            prov = meta_record.get("provenance") or {}
            source_ids = list(prov.get("source_ids") or [])
            if not source_ids and (meta_record.get("source_count") or 0) > 0:
                layer_status["meta"] = "blocked"
                if "meta_provenance_incomplete" not in block_reasons:
                    block_reasons.append("meta_provenance_incomplete")
                    provenance_ok = False
            else:
                has_permitted = False
                for sid in source_ids:
                    st = source_policy_status(str(sid or ""))
                    if st == "blocked":
                        any_prohibited_source = True
                        layer_status["meta"] = "blocked"
                        if "prohibited_source_meta" not in block_reasons:
                            block_reasons.append("prohibited_source_meta")
                    elif st == "permitted":
                        any_permitted_source = True
                        has_permitted = True
                if layer_status.get("meta") != "blocked" and not has_permitted:
                    layer_status["meta"] = "blocked"
                    if "meta_source_policy_unknown" not in block_reasons:
                        block_reasons.append("meta_source_policy_unknown")
                elif layer_status.get("meta") != "blocked":
                    posture = str(meta_record.get("evidence_posture") or meta_record.get("meta_posture") or "")
                    layer_status["meta"] = "approved" if self._posture_meets_publish_threshold(posture) else "withheld"
        if "meta" not in layer_status:
            layer_status["meta"] = "no_data"

        # ── Ruling ────────────────────────────────────────────────────
        ruling_rows = self.fetch_card_rulings_intel(resolved, limit=5)
        if not ruling_rows:
            layer_status["ruling"] = "no_data"
        else:
            row = ruling_rows[0]
            sid = str(row.get("source_id") or "").strip().lower()
            if not sid:
                layer_status["ruling"] = "blocked"
                if "ruling_provenance_incomplete" not in block_reasons:
                    block_reasons.append("ruling_provenance_incomplete")
                    provenance_ok = False
            else:
                st = source_policy_status(sid)
                if st == "blocked":
                    any_prohibited_source = True
                    layer_status["ruling"] = "blocked"
                    if "prohibited_source_ruling" not in block_reasons:
                        block_reasons.append("prohibited_source_ruling")
                elif st == "permitted":
                    any_permitted_source = True
                if layer_status.get("ruling") != "blocked" and st != "permitted":
                    layer_status["ruling"] = "blocked"
                    if "ruling_source_policy_unknown" not in block_reasons:
                        block_reasons.append("ruling_source_policy_unknown")
                elif layer_status.get("ruling") != "blocked":
                    posture = str(row.get("evidence_posture") or "")
                    layer_status["ruling"] = "approved" if self._posture_meets_publish_threshold(posture) else "withheld"
        if "ruling" not in layer_status:
            layer_status["ruling"] = "no_data"

        # ── Synergy ──────────────────────────────────────────────────
        synergy_rows = self.fetch_card_synergy_intel(resolved, limit=5)
        if not synergy_rows:
            layer_status["synergy"] = "no_data"
        else:
            row = synergy_rows[0]
            prov = row.get("provenance") or {}
            source_ids = list(prov.get("source_ids") or [])
            if not source_ids and (row.get("source_count") or 0) > 0:
                layer_status["synergy"] = "blocked"
                if "synergy_provenance_incomplete" not in block_reasons:
                    block_reasons.append("synergy_provenance_incomplete")
                    provenance_ok = False
            else:
                has_permitted = False
                for sid in source_ids:
                    st = source_policy_status(str(sid or ""))
                    if st == "blocked":
                        any_prohibited_source = True
                        layer_status["synergy"] = "blocked"
                        if "prohibited_source_synergy" not in block_reasons:
                            block_reasons.append("prohibited_source_synergy")
                    elif st == "permitted":
                        any_permitted_source = True
                        has_permitted = True
                if layer_status.get("synergy") != "blocked" and not has_permitted:
                    layer_status["synergy"] = "blocked"
                    if "synergy_source_policy_unknown" not in block_reasons:
                        block_reasons.append("synergy_source_policy_unknown")
                elif layer_status.get("synergy") != "blocked":
                    posture = str(row.get("evidence_posture") or "")
                    layer_status["synergy"] = "approved" if self._posture_meets_publish_threshold(posture) else "withheld"
        if "synergy" not in layer_status:
            layer_status["synergy"] = "no_data"

        # ── Banlist (Phase 17): if present, must have permitted source. Phase 17.5: withhold when stale ─
        banlist_record = self.fetch_banlist_status(resolved)
        if banlist_record.get("status") == "legal" and not banlist_record.get("source_id"):
            layer_status["banlist"] = "no_data"
        elif banlist_record.get("status") in ("banned", "restricted", "format_restricted"):
            if str(banlist_record.get("freshness_status") or "").strip().lower() == "stale":
                layer_status["banlist"] = "withheld"
                if "banlist_stale_needs_review" not in block_reasons:
                    block_reasons.append("banlist_stale_needs_review")
            else:
                sid = str(banlist_record.get("source_id") or "").strip().lower()
                if not sid:
                    layer_status["banlist"] = "blocked"
                    if "banlist_provenance_incomplete" not in block_reasons:
                        block_reasons.append("banlist_provenance_incomplete")
                        provenance_ok = False
                else:
                    st = source_policy_status(sid)
                    if st == "blocked":
                        any_prohibited_source = True
                        layer_status["banlist"] = "blocked"
                        if "prohibited_source_banlist" not in block_reasons:
                            block_reasons.append("prohibited_source_banlist")
                    elif st == "permitted":
                        any_permitted_source = True
                        layer_status["banlist"] = "approved"
                    else:
                        layer_status["banlist"] = "blocked"
                        if "banlist_source_policy_unknown" not in block_reasons:
                            block_reasons.append("banlist_source_policy_unknown")
        else:
            layer_status["banlist"] = "no_data"

        # ── Lore (optional; only if source permitted) ──────────────────
        lore_record = self.fetch_card_lore_context(resolved)
        if not lore_record or lore_record.get("lore_posture") == "no_lore_available":
            layer_status["lore"] = "no_data"
        else:
            sid = str(lore_record.get("lore_source") or "").strip().lower()
            if not sid:
                layer_status["lore"] = "blocked"
                if "lore_provenance_incomplete" not in block_reasons:
                    block_reasons.append("lore_provenance_incomplete")
            else:
                st = source_policy_status(sid)
                if st == "blocked":
                    any_prohibited_source = True
                    layer_status["lore"] = "blocked"
                elif st == "permitted":
                    any_permitted_source = True
                    layer_status["lore"] = "approved"
                else:
                    layer_status["lore"] = "blocked"
                    if "lore_source_policy_unknown" not in block_reasons:
                        block_reasons.append("lore_source_policy_unknown")
        if "lore" not in layer_status:
            layer_status["lore"] = "no_data"

        # ── Failsafe: overall publish only if at least one approved and no prohibited ─
        approved_count = sum(1 for v in layer_status.values() if v == "approved")
        overall_publish_allowed = approved_count > 0 and not any_prohibited_source
        if not overall_publish_allowed and "no_audit_record" not in block_reasons:
            if any_prohibited_source and "prohibited_source_usage" not in block_reasons and "prohibited_source_strategy" not in block_reasons:
                block_reasons.append("prohibited_source")
            if approved_count == 0 and not block_reasons:
                block_reasons.append("no_approved_layer")

        # ── Preflight: known conflicts withhold publication ─
        known_conflicts = self.get_known_conflicts(resolved)
        for conflict_type in known_conflicts:
            reason = conflict_reason_to_block_reason(conflict_type) if callable(conflict_reason_to_block_reason) else f"conflict_{conflict_type}"
            if reason not in block_reasons:
                block_reasons.append(reason)
        if known_conflicts:
            overall_publish_allowed = False

        source_policy_status_val = "compliant" if not any_prohibited_source else "non_compliant"
        if not any_prohibited_source and not any_permitted_source and (usage_rows or strategy_rows or meta_record or ruling_rows or synergy_rows):
            source_policy_status_val = "unknown"
        provenance_status_val = "complete" if provenance_ok else ("incomplete" if block_reasons else "complete")

        self.upsert_publication_audit(
            card_code=resolved,
            audit_timestamp=now,
            overall_publish_allowed=overall_publish_allowed,
            publication_block_reasons=block_reasons,
            layer_status=layer_status,
            source_policy_status=source_policy_status_val,
            provenance_status=provenance_status_val,
        )
        return {
            "audit_done": True,
            "card_code": resolved,
            "publish_allowed": overall_publish_allowed,
            "publication_block_reasons": block_reasons,
            "layer_status": layer_status,
            "approved_count": approved_count,
        }

    def get_publication_eligibility(self, card_code: str) -> dict[str, Any]:
        """Lightweight read: return stored publication eligibility for a card.

        Fails closed: if no audit record exists, returns publish_allowed=False
        and publication_block_reasons=['no_audit_record'].
        """
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return {
                "publish_allowed": False,
                "publication_block_reasons": ["missing_card_code"],
                "layer_status": {},
                "audit_timestamp": "",
                "source_policy_status": "unknown",
                "provenance_status": "unknown",
            }
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT audit_timestamp, overall_publish_allowed,
                       publication_block_reasons_json, layer_status_json,
                       source_policy_status, provenance_status
                FROM card_publication_audit
                WHERE card_code = ?
                """,
                (resolved,),
            ).fetchone()
        if not row:
            return {
                "publish_allowed": False,
                "publication_block_reasons": ["no_audit_record"],
                "layer_status": {},
                "audit_timestamp": "",
                "source_policy_status": "unknown",
                "provenance_status": "unknown",
            }
        try:
            reasons = json.loads(row[2] or "[]")
        except (ValueError, TypeError):
            reasons = []
        try:
            layer_status = json.loads(row[3] or "{}")
        except (ValueError, TypeError):
            layer_status = {}
        return {
            "publish_allowed": bool(row[1]),
            "publication_block_reasons": reasons,
            "layer_status": layer_status,
            "audit_timestamp": str(row[0] or ""),
            "source_policy_status": str(row[4] or "unknown"),
            "provenance_status": str(row[5] or "unknown"),
        }

    def is_card_insight_publishable(self, card_code: str) -> bool:
        """Lightweight: return True only if stored audit says publish allowed."""
        return bool(self.get_publication_eligibility(str(card_code or "").strip().upper()).get("publish_allowed"))

    def build_compliance_summary(self, card_code: str) -> dict[str, Any]:
        """Compact compliance summary for a card (for Dev/UI)."""
        eligibility = self.get_publication_eligibility(str(card_code or "").strip().upper())
        layer_status = dict(eligibility.get("layer_status") or {})
        blocked_count = sum(1 for v in layer_status.values() if v == "blocked")
        approved_count = sum(1 for v in layer_status.values() if v == "approved")
        withheld_count = sum(1 for v in layer_status.values() if v == "withheld")
        return {
            "card_code": str(card_code or "").strip().upper(),
            "publish_allowed": bool(eligibility.get("publish_allowed")),
            "publication_block_reasons": list(eligibility.get("publication_block_reasons") or []),
            "blocked_layer_count": blocked_count,
            "approved_layer_count": approved_count,
            "withheld_layer_count": withheld_count,
            "layer_status": layer_status,
            "audit_timestamp": str(eligibility.get("audit_timestamp") or ""),
            "source_policy_status": str(eligibility.get("source_policy_status") or "unknown"),
            "provenance_status": str(eligibility.get("provenance_status") or "unknown"),
        }

    # ------------------------------------------------------------------
    # Phase 18 – Publish layer (stable, compliance-approved intelligence only)
    # ------------------------------------------------------------------

    def publish_card_intelligence(
        self,
        card_code: str,
        *,
        leader_code: str = "",
    ) -> dict[str, Any]:
        """Worker-side: build integrated insight, confirm eligibility, write stable publish record. Failsafe: no publish without audit_timestamp."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return {"published": False, "reason": "missing_card_code"}
        eligibility = self.get_publication_eligibility(resolved)
        audit_ts = str(eligibility.get("audit_timestamp") or "").strip()
        publish_allowed = bool(eligibility.get("publish_allowed"))
        block_reasons = list(eligibility.get("publication_block_reasons") or [])

        # Failsafe: do not publish if audit timestamp is missing (audit never run or record missing)
        if not audit_ts:
            self._upsert_publish_record(
                card_code=resolved,
                publish_allowed=False,
                publish_status="withheld",
                published_at="",
                publish_timestamp="",
                last_audit_timestamp="",
                publication_block_reasons=["no_audit_record"] if "no_audit_record" not in block_reasons else block_reasons,
                payload=None,
            )
            return {"published": False, "reason": "no_audit_timestamp"}

        if not publish_allowed:
            self._upsert_publish_record(
                card_code=resolved,
                publish_allowed=False,
                publish_status="withheld",
                published_at="",
                publish_timestamp=utc_timestamp(),
                last_audit_timestamp=audit_ts,
                publication_block_reasons=block_reasons,
                payload=None,
            )
            return {"published": False, "reason": "not_eligible", "publication_block_reasons": block_reasons}

        # Build integrated insight and write atomically
        full_insight = self.build_integrated_card_insight(resolved, leader_code=leader_code)
        now = utc_timestamp()
        self._upsert_publish_record(
            card_code=resolved,
            publish_allowed=True,
            publish_status="published",
            published_at=now,
            publish_timestamp=now,
            last_audit_timestamp=audit_ts,
            publication_block_reasons=[],
            payload=full_insight,
        )
        return {"published": True, "card_code": resolved, "publish_timestamp": now}

    def _upsert_publish_record(
        self,
        *,
        card_code: str,
        publish_allowed: bool,
        publish_status: str,
        published_at: str,
        publish_timestamp: str,
        last_audit_timestamp: str,
        publication_block_reasons: list[str],
        payload: dict[str, Any] | None,
    ) -> None:
        """Atomic upsert of one publish-layer row. Used by publish_card_intelligence and unpublish flow."""
        now = utc_timestamp()
        reasons_json = json.dumps(list(publication_block_reasons))
        payload_json = json.dumps(payload if payload else {}, ensure_ascii=False, sort_keys=True)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_published_insight
                    (card_code, publish_allowed, publish_status, published_at, publish_timestamp,
                     last_audit_timestamp, publication_block_reasons_json, published_payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    publish_allowed = excluded.publish_allowed,
                    publish_status = excluded.publish_status,
                    published_at = excluded.published_at,
                    publish_timestamp = excluded.publish_timestamp,
                    last_audit_timestamp = excluded.last_audit_timestamp,
                    publication_block_reasons_json = excluded.publication_block_reasons_json,
                    published_payload_json = excluded.published_payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    card_code,
                    1 if publish_allowed else 0,
                    str(publish_status or "withheld").strip(),
                    str(published_at or "").strip(),
                    str(publish_timestamp or now).strip(),
                    str(last_audit_timestamp or "").strip(),
                    reasons_json,
                    payload_json,
                    now,
                ),
            )

    def rebuild_publish_record(self, card_code: str, *, leader_code: str = "") -> dict[str, Any]:
        """Worker-side: re-evaluate eligibility and rebuild publish record (same as publish_card_intelligence)."""
        return self.publish_card_intelligence(card_code, leader_code=leader_code)

    def unpublish_blocked_cards(self, *, limit: int = 500) -> list[str]:
        """Worker-side: find cards that have a publish record but are no longer eligible; withhold them. Returns list of card_codes updated."""
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT card_code FROM card_published_insight
                WHERE publish_allowed = 1 AND publish_status = 'published'
                LIMIT ?
                """,
                (limit * 2,),
            ).fetchall()
        updated: list[str] = []
        for (code,) in rows[:limit]:
            eligibility = self.get_publication_eligibility(code)
            if not eligibility.get("publish_allowed") or not str(eligibility.get("audit_timestamp") or "").strip():
                self._upsert_publish_record(
                    card_code=code,
                    publish_allowed=False,
                    publish_status="withheld",
                    published_at="",
                    publish_timestamp=utc_timestamp(),
                    last_audit_timestamp=str(eligibility.get("audit_timestamp") or ""),
                    publication_block_reasons=list(eligibility.get("publication_block_reasons") or []),
                    payload=None,
                )
                updated.append(code)
        return updated

    def publish_due_cards(
        self,
        *,
        limit: int = 200,
        only_audited: bool = True,
    ) -> list[dict[str, Any]]:
        """Worker-side: publish cards that have passed audit and are eligible but not yet published (or need refresh)."""
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            audit_rows = conn.execute(
                """
                SELECT card_code FROM card_publication_audit
                WHERE overall_publish_allowed = 1 AND (audit_timestamp != '' AND audit_timestamp IS NOT NULL)
                ORDER BY audit_timestamp DESC
                LIMIT ?
                """,
                (limit * 2,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for (code,) in audit_rows[:limit]:
            out = self.publish_card_intelligence(code)
            results.append({"card_code": code, **out})
        return results

    def fetch_published_card_insight(self, card_code: str) -> dict[str, Any] | None:
        """Lightweight read for Project Miru: return published insight payload only if status is published and publish_allowed. No audit at request time."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return None
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT publish_allowed, publish_status, last_audit_timestamp, published_payload_json
                FROM card_published_insight
                WHERE card_code = ?
                """,
                (resolved,),
            ).fetchone()
        if not row:
            return None
        allowed, status, audit_ts, payload_json = row[0], str(row[1] or "").strip(), str(row[2] or "").strip(), row[3]
        # Failsafe: only return payload when published and audit timestamp present
        if not allowed or status != "published" or not audit_ts:
            return None
        try:
            return json.loads(payload_json or "{}")
        except (TypeError, json.JSONDecodeError):
            return None

    def is_card_published(self, card_code: str) -> bool:
        """Lightweight: True only if a published row exists with publish_status='published' and publish_allowed=1."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return False
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM card_published_insight
                WHERE card_code = ? AND publish_allowed = 1 AND publish_status = 'published' AND last_audit_timestamp != ''
                """,
                (resolved,),
            ).fetchone()
        return row is not None

    def fetch_publish_summary(self, card_code: str) -> dict[str, Any]:
        """Lightweight: card_code, publish_allowed, published_at, publish_timestamp, last_audit_timestamp, publication_block_reasons."""
        resolved = str(card_code or "").strip().upper()
        default = {
            "card_code": resolved or "",
            "publish_allowed": False,
            "published_at": "",
            "publish_timestamp": "",
            "last_audit_timestamp": "",
            "publication_block_reasons": [],
        }
        if not resolved:
            return default
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT publish_allowed, publish_status, published_at, publish_timestamp, last_audit_timestamp, publication_block_reasons_json
                FROM card_published_insight
                WHERE card_code = ?
                """,
                (resolved,),
            ).fetchone()
        if not row:
            default["card_code"] = resolved
            return default
        try:
            reasons = json.loads(row[5] or "[]")
        except (TypeError, json.JSONDecodeError):
            reasons = []
        return {
            "card_code": resolved,
            "publish_allowed": bool(row[0]),
            "published_at": str(row[2] or "").strip(),
            "publish_timestamp": str(row[3] or "").strip(),
            "last_audit_timestamp": str(row[4] or "").strip(),
            "publication_block_reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Phase 19 – Japanese card intake, verified pipeline, master image
    # ------------------------------------------------------------------

    def upsert_card_identity(
        self,
        card_code: str,
        *,
        card_name_jp: str = "",
        card_name_en: str = "",
        effect_text_jp: str = "",
        effect_text_en: str = "",
        trigger_text: str = "",
        color: str = "",
        card_type: str = "",
        cost: str = "",
        power: str = "",
        counter: str = "",
        life: str = "",
        rarity: str = "",
        set_code: str = "",
        set_name: str = "",
        block_icon: str = "",
        release_status: str = RELEASE_STATUS_RELEASED,
        translated_text_en: str = "",
        translation_confidence: float = 0.0,
        source_id: str = "",
        source_provenance: dict[str, Any] | None = None,
        image_source: str = "",
    ) -> dict[str, Any]:
        """Store or update card identity (Japanese-first). Never overwrites existing Japanese text with empty or different script."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return {"stored": False, "reason": "missing_card_code"}
        now = utc_timestamp()
        prov = json.dumps(dict(source_provenance or {}), ensure_ascii=False, sort_keys=True)
        stage = VERIFICATION_STAGE_DISCOVERED

        with closing(connect_dossier_db(self.db_path)) as conn:
            row = conn.execute(
                "SELECT card_name_jp, effect_text_jp FROM card_identity WHERE card_code = ?", (resolved,)
            ).fetchone()
            if row:
                existing_jp_name, existing_jp_effect = str(row[0] or "").strip(), str(row[1] or "").strip()
                name_jp = card_name_jp.strip() if card_name_jp.strip() else existing_jp_name
                effect_jp = effect_text_jp.strip() if effect_text_jp.strip() else existing_jp_effect
                # Do not overwrite Japanese with empty
                if not name_jp and existing_jp_name:
                    name_jp = existing_jp_name
                if not effect_jp and existing_jp_effect:
                    effect_jp = existing_jp_effect
            else:
                name_jp = str(card_name_jp or "").strip()
                effect_jp = str(effect_text_jp or "").strip()

            source_hash = content_hash_jp(name_jp, effect_jp)

            conn.execute(
                """
                INSERT INTO card_identity (
                    card_code, card_name_jp, card_name_en, effect_text_jp, effect_text_en, trigger_text,
                    color, card_type, cost, power, counter, life, rarity, set_code, set_name, block_icon,
                    release_status, verification_stage, translated_text_en, translation_confidence,
                    source_id, source_provenance_json, image_source, translation_source_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    card_name_jp = excluded.card_name_jp,
                    card_name_en = coalesce(nullif(trim(excluded.card_name_en), ''), card_name_en),
                    effect_text_jp = excluded.effect_text_jp,
                    effect_text_en = coalesce(nullif(trim(excluded.effect_text_en), ''), effect_text_en),
                    trigger_text = coalesce(nullif(trim(excluded.trigger_text), ''), trigger_text),
                    color = coalesce(nullif(trim(excluded.color), ''), color),
                    card_type = coalesce(nullif(trim(excluded.card_type), ''), card_type),
                    cost = coalesce(nullif(trim(excluded.cost), ''), cost),
                    power = coalesce(nullif(trim(excluded.power), ''), power),
                    counter = coalesce(nullif(trim(excluded.counter), ''), counter),
                    life = coalesce(nullif(trim(excluded.life), ''), life),
                    rarity = coalesce(nullif(trim(excluded.rarity), ''), rarity),
                    set_code = coalesce(nullif(trim(excluded.set_code), ''), set_code),
                    set_name = coalesce(nullif(trim(excluded.set_name), ''), set_name),
                    block_icon = coalesce(nullif(trim(excluded.block_icon), ''), block_icon),
                    release_status = excluded.release_status,
                    verification_stage = card_identity.verification_stage,
                    translated_text_en = CASE
                        WHEN trim(coalesce(card_identity.translated_text_en, '')) != '' AND card_identity.translation_source_hash = excluded.translation_source_hash
                        THEN card_identity.translated_text_en
                        ELSE coalesce(nullif(trim(excluded.translated_text_en), ''), card_identity.translated_text_en)
                    END,
                    translation_confidence = CASE
                        WHEN trim(coalesce(card_identity.translated_text_en, '')) != '' AND card_identity.translation_source_hash = excluded.translation_source_hash
                        THEN card_identity.translation_confidence
                        WHEN excluded.translation_confidence > 0 THEN excluded.translation_confidence
                        ELSE card_identity.translation_confidence
                    END,
                    source_id = coalesce(nullif(trim(excluded.source_id), ''), card_identity.source_id),
                    source_provenance_json = CASE WHEN excluded.source_provenance_json != '{}' THEN excluded.source_provenance_json ELSE card_identity.source_provenance_json END,
                    image_source = coalesce(nullif(trim(excluded.image_source), ''), card_identity.image_source),
                    translation_source_hash = excluded.translation_source_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved,
                    name_jp,
                    str(card_name_en or "").strip(),
                    effect_jp,
                    str(effect_text_en or "").strip(),
                    str(trigger_text or "").strip(),
                    str(color or "").strip(),
                    str(card_type or "").strip(),
                    str(cost or "").strip(),
                    str(power or "").strip(),
                    str(counter or "").strip(),
                    str(life or "").strip(),
                    str(rarity or "").strip(),
                    str(set_code or "").strip(),
                    str(set_name or "").strip(),
                    str(block_icon or "").strip(),
                    str(release_status or RELEASE_STATUS_RELEASED).strip(),
                    stage,
                    str(translated_text_en or "").strip(),
                    float(translation_confidence),
                    str(source_id or "").strip(),
                    prov,
                    str(image_source or "").strip(),
                    source_hash,
                    now,
                ),
            )
        return {"stored": True, "card_code": resolved}

    def translation_reusable(self, card_code: str, card_name_jp: str, effect_text_jp: str) -> bool:
        """Budget guardrail: True if we already have a stored translation for this same JP source text (by hash). Call before spending API on translation."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return False
        h = content_hash_jp(str(card_name_jp or "").strip(), str(effect_text_jp or "").strip())
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM card_identity
                WHERE card_code = ? AND translation_source_hash = ? AND trim(coalesce(translated_text_en, '')) != ''
                """,
                (resolved, h),
            ).fetchone()
        return row is not None

    def get_card_identity(self, card_code: str) -> dict[str, Any] | None:
        """Return full card_identity row as dict, or None if not found."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return None
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                "SELECT * FROM card_identity WHERE card_code = ?", (resolved,)
            ).fetchone()
        if not row:
            return None
        return dict(zip(row.keys(), row)) if hasattr(row, "keys") else None

    def set_verification_stage(self, card_code: str, stage: str) -> bool:
        """Set verification_stage to discovered | verified | publish_eligible. Returns True if row updated."""
        resolved = str(card_code or "").strip().upper()
        if not resolved or stage not in VERIFICATION_STAGES:
            return False
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            cur = conn.execute(
                "UPDATE card_identity SET verification_stage = ?, updated_at = ? WHERE card_code = ?",
                (stage, now, resolved),
            )
        return cur.rowcount > 0

    def list_cards_by_verification_stage(self, stage: str, *, limit: int = 500) -> list[str]:
        """Return card_codes in card_identity with the given verification_stage."""
        if stage not in VERIFICATION_STAGES:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                "SELECT card_code FROM card_identity WHERE verification_stage = ? ORDER BY updated_at DESC LIMIT ?",
                (stage, limit),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def image_quality_rank(self, quality: str) -> int:
        """Lower is better. clean=0, clear_sample=1, acceptable=2. Unknown=3."""
        try:
            return IMAGE_QUALITY_ORDER.index(quality)
        except ValueError:
            return len(IMAGE_QUALITY_ORDER)

    def upsert_card_master_image(
        self,
        card_code: str,
        *,
        master_image_path: str = "",
        master_image_url: str = "",
        thumbnail_path: str = "",
        full_size_modal_path: str = "",
        image_quality: str = IMAGE_QUALITY_ACCEPTABLE,
        watermark_status: str = WATERMARK_NONE,
        replacement_priority: str = REPLACEMENT_PRIORITY_MEDIUM,
        image_source_url: str = "",
        image_verified: bool = False,
        aspect_ratio_preserved: bool = True,
    ) -> dict[str, Any]:
        """Store or replace master image. Replaces only if new image is strictly better (e.g. clean > clear_sample)."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return {"stored": False, "reason": "missing_card_code"}
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            row = conn.execute(
                "SELECT image_quality FROM card_master_images WHERE card_code = ?", (resolved,)
            ).fetchone()
            current_rank = self.image_quality_rank(row[0]) if row else len(IMAGE_QUALITY_ORDER)
            new_rank = self.image_quality_rank(image_quality)
            if row and new_rank >= current_rank and (new_rank != current_rank or not master_image_path):
                return {"stored": False, "reason": "not_better", "current_quality": row[0]}
            conn.execute(
                """
                INSERT INTO card_master_images (
                    card_code, master_image_path, master_image_url, thumbnail_path, full_size_modal_path,
                    image_quality, watermark_status, replacement_priority, image_source_url,
                    image_verified, aspect_ratio_preserved, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    master_image_path = excluded.master_image_path,
                    master_image_url = excluded.master_image_url,
                    thumbnail_path = excluded.thumbnail_path,
                    full_size_modal_path = excluded.full_size_modal_path,
                    image_quality = excluded.image_quality,
                    watermark_status = excluded.watermark_status,
                    replacement_priority = excluded.replacement_priority,
                    image_source_url = excluded.image_source_url,
                    image_verified = excluded.image_verified,
                    aspect_ratio_preserved = excluded.aspect_ratio_preserved,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved,
                    str(master_image_path or "").strip(),
                    str(master_image_url or "").strip(),
                    str(thumbnail_path or "").strip(),
                    str(full_size_modal_path or "").strip(),
                    str(image_quality or IMAGE_QUALITY_ACCEPTABLE).strip(),
                    str(watermark_status or WATERMARK_NONE).strip(),
                    str(replacement_priority or REPLACEMENT_PRIORITY_MEDIUM).strip(),
                    str(image_source_url or "").strip(),
                    1 if image_verified else 0,
                    1 if aspect_ratio_preserved else 0,
                    now,
                ),
            )
        return {"stored": True, "card_code": resolved, "image_quality": image_quality}

    def get_card_master_image(self, card_code: str) -> dict[str, Any] | None:
        """Return master image record for card, or None."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return None
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            row = conn.execute(
                "SELECT * FROM card_master_images WHERE card_code = ?", (resolved,)
            ).fetchone()
        if not row:
            return None
        return dict(zip(row.keys(), row)) if hasattr(row, "keys") else None

    def update_master_image_derivatives(
        self,
        card_code: str,
        *,
        thumbnail_path: str = "",
        full_size_modal_path: str = "",
    ) -> bool:
        """Phase 21: Update only thumbnail_path and full_size_modal_path for existing master image. Returns True if row updated."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return False
        now = utc_timestamp()
        with closing(connect_dossier_db(self.db_path)) as conn:
            cur = conn.execute(
                """
                UPDATE card_master_images
                SET thumbnail_path = ?, full_size_modal_path = ?, updated_at = ?
                WHERE card_code = ?
                """,
                (str(thumbnail_path or "").strip(), str(full_size_modal_path or "").strip(), now, resolved),
            )
        return cur.rowcount > 0

    def list_card_codes_needing_image_upgrade(self, *, limit: int = 500) -> list[str]:
        """Phase 21: Card codes whose master image is low-quality, has replacement_priority=high, or is missing derivatives."""
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                """
                SELECT card_code FROM card_master_images
                WHERE image_quality IN ('acceptable', 'clear_sample')
                   OR replacement_priority = 'high'
                   OR trim(coalesce(thumbnail_path, '')) = ''
                   OR trim(coalesce(full_size_modal_path, '')) = ''
                ORDER BY
                    CASE replacement_priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    CASE image_quality WHEN 'acceptable' THEN 0 WHEN 'clear_sample' THEN 1 ELSE 2 END,
                    updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_prerelease_disclaimer(self, card_code: str) -> dict[str, Any]:
        """Return prerelease notice text and preview-image notice if applicable. For modal/UI."""
        out = {"prerelease_notice": "", "preview_image_notice": "", "is_prerelease": False}
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return out
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            id_row = conn.execute(
                "SELECT release_status FROM card_identity WHERE card_code = ?", (resolved,)
            ).fetchone()
            img_row = conn.execute(
                "SELECT image_quality, watermark_status FROM card_master_images WHERE card_code = ?", (resolved,)
            ).fetchone()
        if id_row and str(id_row[0] or "").strip().lower() == RELEASE_STATUS_PRERELEASE:
            out["is_prerelease"] = True
            out["prerelease_notice"] = (
                "Card information based on prerelease reveal. Details may change before official release."
            )
        if img_row and str(img_row[1] or "").strip().lower() == WATERMARK_SAMPLE:
            out["preview_image_notice"] = "Preview image – official release artwork may differ."
        return out

    def record_conflict(self, card_code: str, conflict_type: str, *, source_ids: list[str] | None = None) -> None:
        """Preflight: record a detected conflict for this card (e.g. contradictory facts, legality). Withholds from publish until resolved."""
        resolved = str(card_code or "").strip().upper()
        if not resolved or not conflict_type:
            return
        now = utc_timestamp()
        ids_json = json.dumps(list(source_ids or []), ensure_ascii=False)
        with closing(connect_dossier_db(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO card_conflict_flags (card_code, conflict_type, source_ids_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(card_code, conflict_type) DO UPDATE SET source_ids_json = excluded.source_ids_json, updated_at = excluded.updated_at
                """,
                (resolved, str(conflict_type).strip(), ids_json, now),
            )

    def get_known_conflicts(self, card_code: str) -> list[str]:
        """Preflight: return list of conflict_type for this card. Used by publication audit to withhold."""
        resolved = str(card_code or "").strip().upper()
        if not resolved:
            return []
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            rows = conn.execute(
                "SELECT conflict_type FROM card_conflict_flags WHERE card_code = ?", (resolved,)
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_card_confidence_by_category(self, card_code: str) -> dict[str, float]:
        """Preflight: return confidence by category (card_fact, image, translation, meta, price, ruling, legality) from existing layers."""
        resolved = str(card_code or "").strip().upper()
        out = confidence_by_category_schema() if callable(confidence_by_category_schema) else {}
        if not resolved:
            return out
        with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
            usage = (
                conn.execute(
                    "SELECT confidence FROM card_usage WHERE card_code = ? ORDER BY confidence DESC LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_usage")
                else None
            )
            strategy = (
                conn.execute(
                    "SELECT confidence FROM card_strategy_intel WHERE card_code = ? ORDER BY confidence DESC LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_strategy_intel")
                else None
            )
            meta = (
                conn.execute(
                    "SELECT confidence FROM card_meta_intel WHERE card_code = ? ORDER BY confidence DESC LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_meta_intel")
                else None
            )
            ruling = (
                conn.execute(
                    "SELECT confidence FROM card_rulings_intel WHERE card_code = ? ORDER BY confidence DESC LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_rulings_intel")
                else None
            )
            synergy = (
                conn.execute(
                    "SELECT confidence FROM card_synergy_intel WHERE card_code = ? ORDER BY confidence DESC LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_synergy_intel")
                else None
            )
            identity = (
                conn.execute(
                    "SELECT translation_confidence FROM card_identity WHERE card_code = ?", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_identity")
                else None
            )
            master_img = (
                conn.execute(
                    "SELECT 1 FROM card_master_images WHERE card_code = ? AND image_verified = 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_master_images")
                else None
            )
            banlist = (
                conn.execute(
                    "SELECT 1 FROM card_banlist WHERE card_code = ? LIMIT 1", (resolved,)
                ).fetchone()
                if _table_exists(conn, "card_banlist")
                else None
            )
            card_columns = _table_columns(conn, "cards") if _table_exists(conn, "cards") else set()
            cards_row = None
            if "card_code" in card_columns:
                cards_row = conn.execute("SELECT confidence FROM cards WHERE card_code = ?", (resolved,)).fetchone()
            elif "canonical_code" in card_columns:
                cards_row = conn.execute("SELECT overall_score FROM cards WHERE canonical_code = ?", (resolved,)).fetchone()
        usage_conf = float(usage[0] or 0) if usage else 0.0
        strategy_conf = float(strategy[0] or 0) if strategy else 0.0
        meta_conf = float(meta[0] or 0) if meta else 0.0
        ruling_conf = float(ruling[0] or 0) if ruling else 0.0
        synergy_conf = float(synergy[0] or 0) if synergy else 0.0
        out[CONFIDENCE_CATEGORY_META] = meta_conf
        out[CONFIDENCE_CATEGORY_RULING] = ruling_conf
        if CONFIDENCE_CATEGORY_IMAGE in out:
            out[CONFIDENCE_CATEGORY_IMAGE] = 0.9 if master_img else 0.0
        if identity and CONFIDENCE_CATEGORY_TRANSLATION in out:
            out[CONFIDENCE_CATEGORY_TRANSLATION] = float(identity[0] or 0)
        if CONFIDENCE_CATEGORY_LEGALITY in out:
            out[CONFIDENCE_CATEGORY_LEGALITY] = 0.85 if banlist else 0.0
        if CONFIDENCE_CATEGORY_PRICE in out:
            out[CONFIDENCE_CATEGORY_PRICE] = 0.0
        if CONFIDENCE_CATEGORY_CARD_FACT in out:
            out[CONFIDENCE_CATEGORY_CARD_FACT] = float(cards_row[0] or 0) if cards_row else max(usage_conf, strategy_conf, synergy_conf)
        return out

    def _read_legacy_dossier_bundle(self, card_code: str) -> dict[str, Any]:
        resolved = _clean_text(card_code).upper()
        if not resolved or not self.db_path.is_file():
            return {}
        try:
            with closing(connect_dossier_db(self.db_path, readonly=True)) as conn:
                if not _table_exists(conn, "cards"):
                    return {}
                card_columns = _table_columns(conn, "cards")
                if "canonical_code" not in card_columns:
                    return {}
                card_row = conn.execute(
                    "SELECT * FROM cards WHERE canonical_code = ? LIMIT 1",
                    (resolved,),
                ).fetchone()
                if card_row is None:
                    return {}
                fact_rows: list[sqlite3.Row] = []
                if _table_exists(conn, "card_facts"):
                    source_join = ""
                    select_sources = "'' AS selected_source_keys"
                    group_by = ""
                    if _table_exists(conn, "fact_sources"):
                        source_join = "LEFT JOIN fact_sources fs ON fs.fact_id = f.id AND fs.is_selected = 1"
                        select_sources = "COALESCE(group_concat(fs.source_key, '|'), '') AS selected_source_keys"
                        group_by = "GROUP BY f.id"
                    fact_rows = conn.execute(
                        f"""
                        SELECT
                            f.*,
                            {select_sources}
                        FROM card_facts f
                        {source_join}
                        WHERE f.card_id = ?
                        {group_by}
                        ORDER BY f.confidence_score DESC, f.supporting_source_count DESC, f.updated_at DESC
                        """,
                        (int(card_row["id"]),),
                    ).fetchall()
                confidence_rows = (
                    conn.execute(
                        """
                        SELECT scope, scope_key, verification_state, confidence_score, rationale_json
                        FROM confidence_records
                        WHERE card_id = ?
                        ORDER BY updated_at DESC
                        """,
                        (int(card_row["id"]),),
                    ).fetchall()
                    if _table_exists(conn, "confidence_records")
                    else []
                )
        except sqlite3.Error:
            return {}

        facts: dict[str, dict[str, Any]] = {}
        all_source_keys: list[str] = []
        for row in fact_rows:
            value_json = _clean_text(row["value_json"]) if "value_json" in row.keys() else ""
            value_type = _clean_text(row["value_type"]) if "value_type" in row.keys() else ""
            if value_json and value_type == "json":
                value = _json_load(value_json, _clean_text(row["value_text"]))
            else:
                value = _clean_text(row["value_text"])
            fact_name = _clean_text(row["field_name"])
            source_keys = _clean_list(row["selected_source_keys"] if "selected_source_keys" in row.keys() else "")
            for key in source_keys:
                if key not in all_source_keys:
                    all_source_keys.append(key)
            existing = facts.get(fact_name)
            candidate = {
                "fact_name": fact_name,
                "value": value,
                "value_text": _fact_text(value),
                "verification_state": _clean_text(row["verification_state"]),
                "confidence_score": round(_safe_float(row["confidence_score"]), 3),
                "supporting_source_count": _safe_int(row["supporting_source_count"]),
                "source_keys": source_keys,
                "updated_at": _clean_text(row["updated_at"]) if "updated_at" in row.keys() else "",
            }
            if existing is None:
                facts[fact_name] = candidate
                continue
            if candidate["confidence_score"] > float(existing.get("confidence_score") or 0.0):
                facts[fact_name] = candidate
                continue
            if candidate["confidence_score"] == float(existing.get("confidence_score") or 0.0) and candidate["supporting_source_count"] > int(existing.get("supporting_source_count") or 0):
                facts[fact_name] = candidate

        confidence_map = {
            f"{_clean_text(row['scope'])}:{_clean_text(row['scope_key'])}": {
                "verification_state": _clean_text(row["verification_state"]),
                "confidence_score": round(_safe_float(row["confidence_score"]), 3),
                "rationale": _json_load(_clean_text(row["rationale_json"]), {}),
            }
            for row in confidence_rows
        }
        return {
            "card": dict(card_row),
            "facts": facts,
            "confidence_records": confidence_map,
            "source_keys": all_source_keys,
        }

    def _read_learning_dossier_bundle(self, card_code: str, learning_db_path: Path | None) -> dict[str, Any]:
        path = Path(learning_db_path or (PROJECT_ROOT / "data" / "miru_learning_dossiers.db"))
        resolved = _clean_text(card_code).upper()
        if not resolved or not path.is_file():
            return {}
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                dossier_row = conn.execute(
                    """
                    SELECT card_code, card_name, set_code, rarity, basic_facts_json, source_summary,
                           confidence, verification_state, updated_at, COALESCE(trivia, '') AS trivia
                    FROM learning_dossiers
                    WHERE card_code = ?
                    LIMIT 1
                    """,
                    (resolved,),
                ).fetchone()
                source_rows = []
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_dossier_sources'").fetchone():
                    source_rows = conn.execute(
                        """
                        SELECT source_id, source_reference, field_payload_json, verification_state, fetched_at, updated_at
                        FROM learning_dossier_sources
                        WHERE card_code = ?
                        ORDER BY updated_at DESC, source_id ASC
                        """,
                        (resolved,),
                    ).fetchall()
        except sqlite3.Error:
            return {}
        if dossier_row is None:
            return {}
        sources: list[dict[str, Any]] = []
        for row in source_rows:
            payload = _json_load(_clean_text(row["field_payload_json"]), {})
            sources.append(
                {
                    "source_id": _clean_text(row["source_id"]).lower(),
                    "source_reference": _clean_text(row["source_reference"]),
                    "verification_state": _clean_text(row["verification_state"]),
                    "fetched_at": _clean_text(row["fetched_at"]),
                    "updated_at": _clean_text(row["updated_at"]),
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        basic_facts = _json_load(_clean_text(dossier_row["basic_facts_json"]), {})
        return {
            "card_code": resolved,
            "card_name": _clean_text(dossier_row["card_name"]),
            "set_code": _clean_text(dossier_row["set_code"]),
            "rarity": _clean_text(dossier_row["rarity"]),
            "basic_facts": basic_facts if isinstance(basic_facts, dict) else {},
            "source_summary": _clean_text(dossier_row["source_summary"]),
            "confidence": round(_safe_float(dossier_row["confidence"]), 3),
            "verification_state": _clean_text(dossier_row["verification_state"]),
            "updated_at": _clean_text(dossier_row["updated_at"]),
            "trivia": _clean_text(dossier_row["trivia"]),
            "sources": sources,
        }

    def _read_catalog_bundle(self, card_code: str, catalog_db_path: Path | None) -> dict[str, Any]:
        path = Path(catalog_db_path or (PROJECT_ROOT / "data" / "card_catalog.db"))
        resolved = _clean_text(card_code).upper()
        if not resolved or not path.is_file():
            return {}
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                card_row = conn.execute(
                    "SELECT * FROM cards WHERE canonical_code = ? LIMIT 1",
                    (resolved,),
                ).fetchone()
                intelligence_row = None
                if card_row is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_intelligence'").fetchone():
                    intelligence_row = conn.execute(
                        "SELECT * FROM card_intelligence WHERE card_id = ? LIMIT 1",
                        (int(card_row["id"]),),
                    ).fetchone()
                legality_rows = []
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='miru_card_legality'").fetchone():
                    legality_rows = conn.execute(
                        """
                        SELECT format, legality_state, effective_date, source_id, source_reference, last_checked_at, notes
                        FROM miru_card_legality
                        WHERE card_code = ?
                        ORDER BY last_checked_at DESC, updated_at DESC
                        """,
                        (resolved,),
                    ).fetchall()
        except sqlite3.Error:
            return {}
        return {
            "card": dict(card_row) if card_row is not None else {},
            "intelligence": dict(intelligence_row) if intelligence_row is not None else {},
            "legality": [dict(row) for row in legality_rows],
        }

    def _read_rules_bundle(self, card_code: str, rules_db_path: Path | None) -> dict[str, Any]:
        path = Path(rules_db_path or (PROJECT_ROOT / "data" / "miru_official_rules.db"))
        resolved = _clean_text(card_code).upper()
        if not resolved or not path.is_file():
            return {}
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                ruling_rows = []
                legality_rows = []
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_card_rulings'").fetchone():
                    ruling_rows = conn.execute(
                        """
                        SELECT card_code, topic_key, ruling_text, source_id, source_reference,
                               published_at, effective_at, status, normalized_summary, source_url, updated_at
                        FROM official_card_rulings
                        WHERE card_code = ?
                        ORDER BY COALESCE(effective_at, published_at) DESC, updated_at DESC
                        """,
                        (resolved,),
                    ).fetchall()
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_legality_history'").fetchone():
                    legality_rows = conn.execute(
                        """
                        SELECT format_name, region, legality_state, effective_start, effective_end,
                               source_id, source_reference, is_current, is_upcoming, notes, updated_at
                        FROM official_legality_history
                        WHERE card_code = ?
                        ORDER BY is_current DESC, effective_start DESC, updated_at DESC
                        """,
                        (resolved,),
                    ).fetchall()
        except sqlite3.Error:
            return {}
        return {
            "rulings": [dict(row) for row in ruling_rows],
            "legality": [dict(row) for row in legality_rows],
        }

    def _read_deck_bundle(self, card_code: str, deck_intel_db_path: Path | None) -> dict[str, Any]:
        path = Path(deck_intel_db_path or (PROJECT_ROOT / "data" / "miru_deck_intel.db"))
        resolved = _clean_text(card_code).upper()
        if not resolved or not path.is_file():
            return {}
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leader_card_signals'").fetchone():
                    return {}
                rows = conn.execute(
                    """
                    SELECT leader_code, role_label, deck_count, total_copies, usage_percent, avg_copies, updated_at
                    FROM leader_card_signals
                    WHERE card_code = ?
                    ORDER BY deck_count DESC, usage_percent DESC, leader_code ASC
                    """,
                    (resolved,),
                ).fetchall()
        except sqlite3.Error:
            return {}
        role_totals: dict[str, int] = {}
        top_leaders: list[dict[str, Any]] = []
        total_decks = 0
        max_usage = 0.0
        for row in rows:
            role = _clean_text(row["role_label"]).lower() or "tech"
            deck_count = _safe_int(row["deck_count"])
            usage_percent = round(_safe_float(row["usage_percent"]), 4)
            total_decks += deck_count
            max_usage = max(max_usage, usage_percent)
            role_totals[role] = role_totals.get(role, 0) + deck_count
            if len(top_leaders) < 5:
                top_leaders.append(
                    {
                        "leader_code": _clean_text(row["leader_code"]).upper(),
                        "role_label": role,
                        "deck_count": deck_count,
                        "usage_percent": usage_percent,
                        "avg_copies": round(_safe_float(row["avg_copies"]), 3),
                        "updated_at": _clean_text(row["updated_at"]),
                    }
                )
        primary_role = ""
        if role_totals:
            primary_role = sorted(role_totals.items(), key=lambda item: (-item[1], item[0]))[0][0]
        leader_count = len({item["leader_code"] for item in top_leaders if item.get("leader_code")})
        return {
            "top_leaders": top_leaders,
            "primary_role": primary_role,
            "leader_count": leader_count,
            "total_decks": total_decks,
            "max_usage_percent": max_usage,
            "role_totals": role_totals,
        }

    def _load_price_index(self, prices_path: Path | None) -> dict[str, dict[str, Any]]:
        path = Path(prices_path or (PROJECT_ROOT / "data" / "prices.json"))
        cache_key = str(path.resolve())
        if not path.is_file():
            self._price_index_cache.pop(cache_key, None)
            return {}
        try:
            stat = path.stat()
        except OSError:
            return {}
        cached = self._price_index_cache.get(cache_key)
        if cached and cached[0] == stat.st_mtime:
            return cached[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        source_updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        index: dict[str, dict[str, Any]] = {}
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            code = _clean_text(item.get("code")).upper()
            if not code:
                continue
            entry = dict(item)
            entry.setdefault("source_updated_at", source_updated_at)
            index[code] = entry
        self._price_index_cache[cache_key] = (stat.st_mtime, index)
        return index

    def _read_price_bundle(self, card_code: str, prices_path: Path | None) -> dict[str, Any]:
        resolved = _clean_text(card_code).upper()
        if not resolved:
            return {}
        return dict(self._load_price_index(prices_path).get(resolved) or {})

    def build_card_dossier(
        self,
        card_code: str,
        *,
        learning_db_path: Path | None = None,
        rules_db_path: Path | None = None,
        deck_intel_db_path: Path | None = None,
        catalog_db_path: Path | None = None,
        prices_path: Path | None = None,
    ) -> dict[str, Any]:
        resolved = _clean_text(card_code).upper()
        if not resolved:
            return {"card_id": "", "builder_path": "MiruDossierStore.build_card_dossier", "available": False}

        legacy = self._read_legacy_dossier_bundle(resolved)
        learning = self._read_learning_dossier_bundle(resolved, learning_db_path)
        catalog = self._read_catalog_bundle(resolved, catalog_db_path)
        rules = self._read_rules_bundle(resolved, rules_db_path)
        deck = self._read_deck_bundle(resolved, deck_intel_db_path)
        prices = self._read_price_bundle(resolved, prices_path)
        summary = self.build_card_intelligence_summary(resolved)
        modern_confidence = self.get_card_confidence_by_category(resolved)

        legacy_card = dict(legacy.get("card") or {})
        legacy_facts = dict(legacy.get("facts") or {})
        learning_facts = dict(learning.get("basic_facts") or {})
        catalog_card = dict(catalog.get("card") or {})
        catalog_intel = dict(catalog.get("intelligence") or {})
        identity_row = self.get_card_identity(resolved) or {}

        def fact_value(name: str) -> Any:
            row = legacy_facts.get(name) or {}
            return row.get("value")

        name = _first_nonempty(
            summary.get("identity", {}).get("card_name"),
            identity_row.get("card_name_en"),
            legacy_card.get("card_name"),
            fact_value("card_name"),
            learning.get("card_name"),
            learning_facts.get("card_name"),
            catalog_card.get("card_name"),
        )
        set_code = _first_nonempty(
            summary.get("identity", {}).get("set_code"),
            identity_row.get("set_code"),
            legacy_card.get("set_code"),
            learning.get("set_code"),
            learning_facts.get("set_code"),
            catalog_card.get("set_code"),
        )
        set_name = _first_nonempty(
            summary.get("identity", {}).get("set_name"),
            identity_row.get("set_name"),
            legacy_card.get("set_name"),
            fact_value("set_name"),
            learning_facts.get("set_name"),
            catalog_card.get("set_name"),
        )
        rarity = _first_nonempty(
            summary.get("identity", {}).get("rarity"),
            identity_row.get("rarity"),
            legacy_card.get("rarity"),
            fact_value("rarity"),
            learning.get("rarity"),
            learning_facts.get("rarity"),
            catalog_card.get("rarity"),
        )
        effect_text_official = _first_nonempty(
            fact_value("official_text"),
            legacy_card.get("official_text"),
            identity_row.get("effect_text_en"),
            learning_facts.get("official_text"),
            learning_facts.get("effect_text"),
            catalog_card.get("effect_text"),
        )
        effect_text_translated = _first_nonempty(
            identity_row.get("translated_text_en"),
            learning_facts.get("translated_text_en"),
            learning_facts.get("effect_text_translated"),
            fact_value("effect_text_translated"),
            fact_value("translated_text_en"),
        )

        ruling_rows = list(rules.get("rulings") or [])
        if not ruling_rows:
            ruling_rows = [
                {
                    "topic_key": _clean_text(item.get("ruling_topic")),
                    "ruling_text": _clean_text(item.get("ruling_text")),
                    "source_id": _clean_text(item.get("source_id")),
                    "source_reference": _clean_text(item.get("source_reference")),
                    "effective_at": _clean_text(item.get("freshness_at")),
                    "normalized_summary": _clean_text(item.get("ruling_text")),
                    "source_url": _clean_text(item.get("source_url")),
                }
                for item in self.fetch_card_rulings_intel(resolved, limit=5)
            ]
        rulings_summary_parts: list[str] = []
        rulings_sources: list[dict[str, Any]] = []
        for row in ruling_rows[:3]:
            summary_text = _clean_text(row.get("normalized_summary")) or _clean_text(row.get("ruling_text"))
            if summary_text and summary_text not in rulings_summary_parts:
                rulings_summary_parts.append(summary_text)
            source_entry = {
                "source_id": _clean_text(row.get("source_id")).lower(),
                "source_reference": _clean_text(row.get("source_reference")),
                "source_url": _clean_text(row.get("source_url")),
            }
            if source_entry not in rulings_sources and any(source_entry.values()):
                rulings_sources.append(source_entry)
        if not rulings_summary_parts:
            catalog_rulings_summary = _clean_text(catalog_intel.get("rulings_summary"))
            if catalog_rulings_summary:
                rulings_summary_parts.append(catalog_rulings_summary)

        deck_usage_summary = _first_nonempty(
            catalog_intel.get("deck_usage_summary"),
        )
        derived_deck_usage_summary = ""
        if deck.get("top_leaders"):
            leader_count = _safe_int(deck.get("leader_count"))
            total_decks = _safe_int(deck.get("total_decks"))
            primary_role = _clean_text(deck.get("primary_role")).lower()
            strongest = list(deck.get("top_leaders") or [])[:3]
            leader_parts = []
            for item in strongest:
                usage_percent = _safe_float(item.get("usage_percent"))
                deck_count = _safe_int(item.get("deck_count"))
                avg_copies = _safe_float(item.get("avg_copies"))
                pct = f"{int(round(usage_percent * 100))}% inclusion"
                copies = f", {avg_copies:.1f} avg copies" if avg_copies > 0 else ""
                leader_parts.append(
                    f"{item.get('leader_code')} ({_clean_text(item.get('role_label')).lower() or 'tech'}, {pct}, {deck_count} tracked decks{copies})"
                )
            usage_prefix = "Relevant in current meta"
            if primary_role:
                usage_prefix += f": {primary_role}"
            if leader_count > 0:
                usage_prefix += f" in {leader_count} leader{'s' if leader_count != 1 else ''}"
            if total_decks > 0:
                usage_prefix += f" across {total_decks} tracked deck slots"
            derived_deck_usage_summary = usage_prefix + "."
            if leader_parts:
                derived_deck_usage_summary += " Strongest observed coverage: " + "; ".join(leader_parts[:2]) + "."
        if derived_deck_usage_summary and (
            not _clean_text(deck_usage_summary)
            or "tracked deck" not in _clean_text(deck_usage_summary).lower()
            or len(_clean_text(derived_deck_usage_summary)) > len(_clean_text(deck_usage_summary))
        ):
            deck_usage_summary = derived_deck_usage_summary

        top_leaders_used_in = []
        for item in list(deck.get("top_leaders") or []):
            leader_code = _clean_text(item.get("leader_code")).upper()
            if not leader_code:
                continue
            top_leaders_used_in.append(
                    {
                        "leader_code": leader_code,
                        "role_label": _clean_text(item.get("role_label")).lower(),
                        "deck_count": _safe_int(item.get("deck_count")),
                        "usage_percent": round(_safe_float(item.get("usage_percent")), 4),
                        "avg_copies": round(_safe_float(item.get("avg_copies")), 3),
                        "updated_at": _clean_text(item.get("updated_at")),
                    }
                )
        gameplay_role = _first_nonempty(
            summary.get("role_label"),
            summary.get("strategy_posture", {}).get("role_label"),
            deck.get("primary_role"),
            catalog_intel.get("role_label"),
        )
        if gameplay_role:
            gameplay_role = _clean_text(gameplay_role).lower()

        meta_relevance_score = None
        derived_meta_score = None
        if deck.get("max_usage_percent") or deck.get("leader_count"):
            derived_meta_score = round(
                min(
                    0.98,
                    (_safe_float(deck.get("max_usage_percent")) * 0.7)
                    + (min(_safe_int(deck.get("leader_count")), 4) * 0.07)
                    + (min(_safe_int(deck.get("total_decks")), 12) * 0.01),
                ),
                3,
            )
        elif summary.get("meta_posture", {}).get("confidence_label") != "no_evidence":
            derived_meta_score = round(_safe_float(modern_confidence.get(CONFIDENCE_CATEGORY_META)), 3)
        catalog_meta_score = catalog_intel.get("meta_relevance_score")
        if catalog_meta_score not in (None, ""):
            meta_relevance_score = round(max(_safe_float(catalog_meta_score), _safe_float(derived_meta_score)), 3)
        elif derived_meta_score not in (None, ""):
            meta_relevance_score = derived_meta_score

        price_low = None
        price_value = _first_nonempty(prices.get("price"), catalog_intel.get("price_value"))
        if price_value not in ("", None):
            parsed_price = _safe_float(price_value, default=-1.0)
            if parsed_price > 0:
                price_low = round(parsed_price, 2)
        price_source = _first_nonempty(catalog_intel.get("price_source"), "prices.json" if price_low is not None else "")
        price_trend_note = ""
        if prices:
            if prices.get("trend") or prices.get("history") or prices.get("change"):
                price_trend_note = "Watch data includes a stored price trend signal."
            elif price_low is not None:
                price_trend_note = "Single stored watch-price point only."
        elif price_low is not None:
            price_trend_note = _clean_text(catalog_intel.get("price_trend_note")) or "Single stored watch-price point only."

        legality_rows = list(rules.get("legality") or []) or list(catalog.get("legality") or [])
        legality_history: list[dict[str, Any]] = []
        current_legality_row: dict[str, Any] = {}
        upcoming_legality_rows: list[dict[str, Any]] = []
        for row in legality_rows:
            entry = {
                "format_name": _first_nonempty(row.get("format_name"), row.get("format"), "standard"),
                "region": _clean_text(row.get("region")),
                "legality_state": _clean_text(row.get("legality_state")).lower(),
                "effective_start": _first_nonempty(row.get("effective_start"), row.get("effective_date")),
                "effective_end": _clean_text(row.get("effective_end")),
                "source_id": _clean_text(row.get("source_id")).lower(),
                "source_reference": _clean_text(row.get("source_reference")),
                "is_current": bool(row.get("is_current")),
                "is_upcoming": bool(row.get("is_upcoming")),
                "notes": _clean_text(row.get("notes")),
                "updated_at": _clean_text(row.get("updated_at")),
            }
            legality_history.append(entry)
            if entry["is_current"] and not current_legality_row:
                current_legality_row = entry
            if entry["is_upcoming"]:
                upcoming_legality_rows.append(entry)
        legality_state = _clean_text(current_legality_row.get("legality_state"))
        if not legality_state and legality_history:
            legality_state = _clean_text(legality_history[0].get("legality_state"))
        legality_note = ""
        if current_legality_row:
            legality_note = (
                f"Current official {current_legality_row.get('format_name') or 'standard'} legality is "
                f"{current_legality_row.get('legality_state') or 'unknown'}."
            )
            next_change = next(
                (
                    item
                    for item in upcoming_legality_rows
                    if _clean_text(item.get("legality_state")) and _clean_text(item.get("effective_start"))
                ),
                None,
            )
            if next_change:
                legality_note += (
                    f" Next stored notice changes it to {next_change.get('legality_state')} "
                    f"effective {next_change.get('effective_start')}."
                )
        elif upcoming_legality_rows:
            next_change = sorted(
                upcoming_legality_rows,
                key=lambda item: (_clean_text(item.get("effective_start")) or "9999-99-99", _clean_text(item.get("source_reference"))),
            )[0]
            legality_note = (
                f"Official {next_change.get('format_name') or 'standard'} notice marks this as "
                f"{next_change.get('legality_state') or 'unknown'} effective {next_change.get('effective_start') or 'an upcoming date'}."
            )
        elif legality_state:
            legality_note = f"Stored legality state is {legality_state}."

        agreement = (
            compute_card_source_agreement(resolved, Path(learning_db_path or (PROJECT_ROOT / "data" / "miru_learning_dossiers.db")))
            if callable(compute_card_source_agreement)
            else {
                "card_code": resolved,
                "source_count": len(list(learning.get("sources") or [])),
                "agreement_level": "single_source",
                "agree_count": 0,
                "conflict_count": 0,
            }
        )

        identity_source_ids = _clean_list(legacy.get("source_keys") or [])
        source_entries_seen: set[tuple[str, str, str, str]] = set()
        sources: list[dict[str, Any]] = []

        def add_source(section: str, *, source_id: str = "", source_reference: str = "", source_url: str = "", leader_code: str = "") -> None:
            entry = {
                "source_id": _clean_text(source_id).lower(),
                "source_reference": _clean_text(source_reference),
                "source_url": _clean_text(source_url),
                "section": _clean_text(section),
            }
            if leader_code:
                entry["leader_code"] = _clean_text(leader_code).upper()
            key = (
                entry.get("section", ""),
                entry.get("source_id", ""),
                entry.get("source_reference", ""),
                entry.get("leader_code", ""),
            )
            if key in source_entries_seen:
                return
            if not any(value for k, value in entry.items() if k != "section"):
                return
            source_entries_seen.add(key)
            sources.append(entry)

        for item in list(learning.get("sources") or []):
            sid = _clean_text(item.get("source_id")).lower()
            if sid and sid not in identity_source_ids:
                identity_source_ids.append(sid)
            add_source(
                "identity",
                source_id=sid,
                source_reference=item.get("source_reference"),
            )
        for source_id in identity_source_ids:
            add_source("identity", source_id=source_id)

        identity_confidence = round(
            max(
                _safe_float(legacy_card.get("overall_score")),
                _safe_float((legacy.get("confidence_records") or {}).get("card:overall", {}).get("confidence_score")),
                _safe_float(learning.get("confidence")),
            ),
            3,
        )
        rules_confidence = round(
            max(
                _safe_float(modern_confidence.get(CONFIDENCE_CATEGORY_RULING)),
                0.92 if rulings_sources else (0.84 if rulings_summary_parts else 0.0),
            ),
            3,
        )
        usage_confidence = round(
            max(
                _safe_float(modern_confidence.get(CONFIDENCE_CATEGORY_META)),
                min(0.9, ((_safe_float(deck.get("max_usage_percent")) * 0.8) + (min(_safe_int(deck.get("leader_count")), 3) * 0.05))),
            ),
            3,
        )
        price_confidence = 0.62 if prices.get("trend") or prices.get("history") or prices.get("change") else (0.55 if price_low is not None else 0.0)
        translation_confidence = round(_safe_float(identity_row.get("translation_confidence")), 3)
        for item in rulings_sources:
            add_source(
                "rulings",
                source_id=item.get("source_id"),
                source_reference=item.get("source_reference"),
                source_url=item.get("source_url"),
            )
        for item in legality_history:
            add_source(
                "legality",
                source_id=item.get("source_id"),
                source_reference=item.get("source_reference"),
            )
        for item in top_leaders_used_in:
            add_source("usage", source_id="deck_intel", leader_code=item.get("leader_code"))
        if price_low is not None:
            add_source("market", source_id=price_source or "prices.json")

        overall_candidates = [identity_confidence, usage_confidence, rules_confidence, translation_confidence, price_confidence]
        overall_values = [value for value in overall_candidates if value > 0]
        overall_confidence = round(sum(overall_values) / len(overall_values), 3) if overall_values else 0.0
        # leader_card_signals (miru_deck_intel.db): avoid averaging away usage when deck intel is present —
        # catalog insight sync uses overall confidence vs MIN_INSIGHT_CONFIDENCE (0.50 in miru_project_sync).
        if top_leaders_used_in and overall_confidence < 0.5 and usage_confidence >= 0.32:
            overall_confidence = max(
                overall_confidence,
                min(0.76, max(0.5, usage_confidence + 0.06)),
            )
        identity_updated_at = _latest_timestamp(
            [
                _clean_text(legacy_card.get("updated_at")),
                _clean_text(learning.get("updated_at")),
                _clean_text(summary.get("freshness_at")),
                _clean_text(identity_row.get("updated_at")),
            ]
            + [_clean_text(item.get("updated_at")) for item in list(learning.get("sources") or [])]
        )
        text_effects_updated_at = _latest_timestamp(
            [
                identity_updated_at,
                _clean_text(legacy_card.get("updated_at")),
                _clean_text(learning.get("updated_at")),
            ]
        )
        usage_updated_at = _latest_timestamp(
            [_clean_text(item.get("updated_at")) for item in top_leaders_used_in]
            + [_clean_text(catalog_intel.get("updated_at"))]
        )
        rulings_updated_at = _latest_timestamp(
            [
                _clean_text(item.get("updated_at"))
                or _clean_text(item.get("effective_at"))
                or _clean_text(item.get("published_at"))
                for item in ruling_rows
            ]
        )
        legality_updated_at = _latest_timestamp(
            [
                _clean_text(item.get("updated_at"))
                or _clean_text(item.get("effective_start"))
                for item in legality_history
            ]
        )
        market_updated_at = _latest_timestamp(
            [
                _clean_text(prices.get("source_updated_at")),
                _clean_text(catalog_intel.get("updated_at")) if price_low is not None else "",
            ]
        )
        section_updated_at = {
            "identity": identity_updated_at,
            "text_effects": text_effects_updated_at,
            "usage_meta": usage_updated_at,
            "rulings": rulings_updated_at,
            "legality": legality_updated_at,
            "market": market_updated_at,
        }
        source_updated_at = _latest_timestamp(section_updated_at.values())
        last_verified_at = _latest_timestamp(
            [
                _clean_text(legacy_card.get("last_checked_at")),
                _clean_text(learning.get("updated_at")),
                _clean_text(summary.get("freshness_at")),
                _clean_text(identity_row.get("updated_at")),
                _clean_text(catalog_intel.get("updated_at")),
                _clean_text(ruling_rows[0].get("effective_at")) if ruling_rows else "",
                _clean_text(legality_rows[0].get("effective_start")) if legality_rows else "",
                _clean_text(top_leaders_used_in[0].get("updated_at")) if top_leaders_used_in else "",
            ]
        )

        section_confidence = {
            "identity": identity_confidence,
            "translation": translation_confidence,
            "text_effects": max(identity_confidence, translation_confidence),
            "usage_meta": usage_confidence,
            "rulings": rules_confidence,
            "legality": round(max(_safe_float(modern_confidence.get(CONFIDENCE_CATEGORY_LEGALITY)), 0.9 if legality_history else 0.0), 3),
            "market": round(price_confidence, 3),
        }
        source_summary = {
            "total_sources": len(sources),
            "agreement_level": _clean_text(agreement.get("agreement_level")),
            "source_count": _safe_int(agreement.get("source_count")),
            "sections": {
                "identity": len([item for item in sources if item.get("section") == "identity"]),
                "rulings": len([item for item in sources if item.get("section") == "rulings"]),
                "legality": len([item for item in sources if item.get("section") == "legality"]),
                "usage": len([item for item in sources if item.get("section") == "usage"]),
                "market": len([item for item in sources if item.get("section") == "market"]),
            },
        }
        limitless_marker = "Limitless tournament corpus:"
        llu_facts = learning_facts.get("limitless_leader_usage")
        if isinstance(llu_facts, dict):
            app_l = _safe_int(llu_facts.get("appearances"))
            top8_l = _safe_int(llu_facts.get("top8"))
            wins_l = _safe_int(llu_facts.get("wins"))
            if app_l > 0 or top8_l > 0 or wins_l > 0:
                du_cur = _clean_text(deck_usage_summary)
                if limitless_marker not in du_cur:
                    sig_l = app_l + 2 * top8_l + 8 * wins_l
                    boost_l = min(0.98, max(0.26, 0.26 + min(0.72, sig_l / 400.0)))
                    try:
                        cur_ml = float(meta_relevance_score) if meta_relevance_score not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        cur_ml = 0.0
                    meta_relevance_score = round(max(cur_ml, boost_l), 3)
                    deck_usage_summary = (
                        (du_cur + " ") if du_cur else ""
                    ) + f"{limitless_marker} {app_l} appearances, {top8_l} top-8, {wins_l} wins."

        return {
            "builder_path": "MiruDossierStore.build_card_dossier",
            "available": any(
                bool(item)
                for item in (
                    name,
                    effect_text_official,
                    effect_text_translated,
                    rulings_summary_parts,
                    gameplay_role,
                    deck_usage_summary,
                    top_leaders_used_in,
                    price_low,
                )
            ),
            "card_id": resolved,
            "card_code": resolved,
            "basic_facts": learning_facts,
            "name": _clean_text(name),
            "set_code": _clean_text(set_code),
            "set_name": _clean_text(set_name),
            "rarity": _clean_text(rarity),
            "effect_text_official": _clean_text(effect_text_official),
            "effect_text_translated": _clean_text(effect_text_translated),
            "rulings_summary": " ".join(rulings_summary_parts[:2]).strip(),
            "rulings_sources": rulings_sources,
            "gameplay_role": _clean_text(gameplay_role),
            "deck_usage_summary": _clean_text(deck_usage_summary),
            "top_leaders_used_in": top_leaders_used_in,
            "leader_count": _safe_int(deck.get("leader_count")),
            "tracked_deck_count": _safe_int(deck.get("total_decks")),
            "meta_relevance_score": meta_relevance_score,
            "price_low": price_low,
            "price_source": _clean_text(price_source),
            "price_trend_note": price_trend_note,
            "sources": sources,
            "source_summary": source_summary,
            "confidence_score": overall_confidence,
            "last_verified_at": last_verified_at,
            "source_updated_at": source_updated_at,
            "legality_state": legality_state,
            "legality_note": legality_note,
            "legality_history": legality_history,
            "source_agreement": agreement,
            "agreement_level": _clean_text(agreement.get("agreement_level")),
            "field_mapping": {
                "card_id": resolved,
                "canonical_code": resolved,
                "card_name": _clean_text(name),
                "official_text": _clean_text(effect_text_official),
            },
            "section_confidence": section_confidence,
            "section_updated_at": section_updated_at,
            "sections": {
                "identity": {
                    "card_name": _clean_text(name),
                    "set_code": _clean_text(set_code),
                    "set_name": _clean_text(set_name),
                    "rarity": _clean_text(rarity),
                    "confidence_score": identity_confidence,
                    "source_ids": identity_source_ids,
                    "verification_state": _first_nonempty(
                        legacy_card.get("overall_state"),
                        learning.get("verification_state"),
                        summary.get("identity", {}).get("verification_state"),
                    ),
                },
                "translation": {
                    "confidence_score": translation_confidence,
                    "source_ids": _clean_list([_clean_text(identity_row.get("source_id")).lower()]),
                },
                "text_effects": {
                    "effect_text_official": _clean_text(effect_text_official),
                    "effect_text_translated": _clean_text(effect_text_translated),
                    "confidence_score": max(identity_confidence, translation_confidence),
                },
                "usage_meta": {
                    "summary": _clean_text(deck_usage_summary),
                    "gameplay_role": _clean_text(gameplay_role),
                    "confidence_score": usage_confidence,
                    "leader_count": _safe_int(deck.get("leader_count")),
                    "total_decks": _safe_int(deck.get("total_decks")),
                    "top_leaders": top_leaders_used_in,
                    "meta_relevance_score": meta_relevance_score,
                },
                "rulings": {
                    "summary": " ".join(rulings_summary_parts[:2]).strip(),
                    "confidence_score": rules_confidence,
                    "count": len(ruling_rows),
                    "source_count": len(rulings_sources),
                    "sources": rulings_sources,
                },
                "legality": {
                    "legality_state": legality_state,
                    "legality_note": legality_note,
                    "confidence_score": section_confidence["legality"],
                    "history": legality_history,
                },
                "market": {
                    "price_low": price_low,
                    "price_source": _clean_text(price_source),
                    "price_trend_note": price_trend_note,
                    "confidence_score": round(price_confidence, 3),
                    "source_ids": [_clean_text(price_source)] if price_low is not None and _clean_text(price_source) else [],
                },
                "provenance": {
                    "confidence_score": overall_confidence,
                    "source_summary": source_summary,
                    "source_agreement": agreement,
                    "last_verified_at": last_verified_at,
                    "source_updated_at": source_updated_at,
                    "section_updated_at": section_updated_at,
                },
            },
        }

    def _generate_card_insight_from_dossier(self, dossier: dict[str, Any]) -> dict[str, Any]:
        resolved = _clean_text(dossier.get("card_id")).upper()
        if not resolved:
            return {
                "card_id": "",
                "text": "Not enough verified data yet.",
                "confidence": 0.0,
                "used_sections": [],
                "provenance": [],
                "builder_path": "MiruDossierStore.generate_card_insight",
            }

        d_work = dict(dossier)
        limitless_marker = "Limitless tournament corpus:"
        basic_facts = dict(d_work.get("basic_facts") or {})
        if not isinstance(basic_facts.get("limitless_leader_usage"), dict):
            learn_bf = dict(self._read_learning_dossier_bundle(resolved, None).get("basic_facts") or {})
            for key in ("limitless_leader_usage", "limitless_fetched_at"):
                if key in learn_bf and key not in basic_facts:
                    basic_facts[key] = learn_bf[key]
        llu = basic_facts.get("limitless_leader_usage")
        if isinstance(llu, dict):
            appearances = _safe_int(llu.get("appearances"))
            top8 = _safe_int(llu.get("top8"))
            wins = _safe_int(llu.get("wins"))
            if appearances > 0 or top8 > 0 or wins > 0:
                basic_facts = {**basic_facts, "limitless_leader_usage": llu}
                d_work["basic_facts"] = basic_facts
                du0 = _clean_text(d_work.get("deck_usage_summary"))
                if limitless_marker not in du0:
                    signal = appearances + 2 * top8 + 8 * wins
                    boost = min(0.98, max(0.26, 0.26 + min(0.72, signal / 400.0)))
                    cur_meta = d_work.get("meta_relevance_score")
                    try:
                        cur_m = float(cur_meta) if cur_meta not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        cur_m = 0.0
                    d_work["meta_relevance_score"] = round(max(cur_m, boost), 3)
                    limitless_line = f"{limitless_marker} {appearances} appearances, {top8} top-8, {wins} wins."
                    d_work["deck_usage_summary"] = (du0 + " " if du0 else "") + limitless_line

        top_leaders = list(d_work.get("top_leaders_used_in") or [])
        if build_single_voice_insight:
            picked = build_single_voice_insight(d_work)
            if picked:
                text, used_sections = picked
                ordered_sections = list(dict.fromkeys(item for item in used_sections if item))
                return {
                    "card_id": resolved,
                    "text": text,
                    "confidence": round(_safe_float(d_work.get("confidence_score")), 3),
                    "used_sections": ordered_sections,
                    "provenance": list(d_work.get("sources") or []),
                    "builder_path": "MiruDossierStore.generate_card_insight",
                    "leader_code": _clean_text((top_leaders[0] if top_leaders else {}).get("leader_code")).upper(),
                    "source_ref": "|".join(
                        sorted(
                            {
                                str(item.get("source_id") or "").strip().lower()
                                for item in list(d_work.get("sources") or [])
                                if str(item.get("source_id") or "").strip()
                            }
                        )
                    ),
                    "dossier": d_work,
                }

        return {
            "card_id": resolved,
            "text": "Not enough verified data yet.",
            "confidence": round(_safe_float(d_work.get("confidence_score")), 3),
            "used_sections": [],
            "provenance": list(d_work.get("sources") or []),
            "builder_path": "MiruDossierStore.generate_card_insight",
            "dossier": d_work,
        }

    def generate_card_insight(
        self,
        card_code: str,
        *,
        learning_db_path: Path | None = None,
        rules_db_path: Path | None = None,
        deck_intel_db_path: Path | None = None,
        catalog_db_path: Path | None = None,
        prices_path: Path | None = None,
        dossier: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if dossier is None:
            dossier = self.build_card_dossier(
                card_code,
                learning_db_path=learning_db_path,
                rules_db_path=rules_db_path,
                deck_intel_db_path=deck_intel_db_path,
                catalog_db_path=catalog_db_path,
                prices_path=prices_path,
            )
        return self._generate_card_insight_from_dossier(dossier)

    def ingest_verified_source_record(self, *, source_record: Any, merged_dossier: dict[str, Any], acceptance: dict[str, Any], source_rollup: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = source_record.to_dict() if hasattr(source_record, "to_dict") else dict(source_record or {})
        card_code = str(payload.get("card_code") or merged_dossier.get("card_code") or "").strip().upper()
        if not card_code:
            return {"stored": False, "reason": "missing-card-code"}
        accepted_fields = list(acceptance.get("accepted_fields") or [])
        accepted_values = dict(acceptance.get("accepted_values") or {})
        basic_facts = dict(merged_dossier.get("basic_facts") or {})
        confidence = max(float(merged_dossier.get("confidence") or 0.0), 0.72 if accepted_fields else 0.0)
        verification_state = str(
            merged_dossier.get("verification_state") or ("source-backed" if accepted_fields else "pending")
        ).strip()
        source_id = str(payload.get("source_id") or "").strip().lower()
        source_reference = str(payload.get("source_reference") or "").strip()
        source_url = str(payload.get("source_url") or "").strip()
        trust_level = "official" if source_id.startswith("official") else ("trusted" if "reputable" in source_id else "source-backed")
        trust_score = 0.95 if trust_level == "official" else (0.76 if trust_level == "trusted" else 0.62)
        self.upsert_card_source(card_code=card_code, source_id=source_id, source_type=str(payload.get("source_type") or "source-record").strip(), source_url=source_url, source_reference=source_reference, fetched_at=str(payload.get("fetched_at") or ""), trust_level=trust_level, trust_score=trust_score, citation_payload={"source_id": source_id, "source_reference": source_reference, "source_url": source_url}, notes=f"verification_state={verification_state}")
        self.upsert_card_snapshot(card_code=card_code, canonical_code=card_code, facts=basic_facts, confidence=confidence, verification_state=verification_state, source_summary=str(merged_dossier.get("source_summary") or ""))
        source_ids = [source_id] if source_id else []
        for field_name in accepted_fields:
            raw_value = accepted_values.get(field_name, basic_facts.get(field_name))
            self.upsert_card_fact(card_code=card_code, fact_key=f"{field_name}:{card_code}", fact_type=field_name, fact_value=raw_value, confidence=confidence, status="verified", verification_state=verification_state, primary_source_id=source_id, source_ids=source_ids, citation_payload={"source_id": source_id, "source_reference": source_reference}, provenance={"source_id": source_id, "source_reference": source_reference, "storage_outcome": str(acceptance.get("storage_outcome") or ""), "source_count": int((source_rollup or {}).get("source_count") or basic_facts.get("source_count") or 0)})
            if field_name in {"effect_text", "trigger_text"} and str(raw_value or "").strip():
                self.upsert_card_effect(card_code=card_code, effect_type=field_name, effect_text=str(raw_value or "").strip(), confidence=confidence, primary_source_id=source_id, source_reference=source_reference, source_count=int((source_rollup or {}).get("source_count") or basic_facts.get("source_count") or 0), status="verified", parsed_payload={"effect_type": field_name})
        self.upsert_card_variant(card_code=card_code, variant_key="", variant_label="Base", print_label="Base", language_code="en", confidence=confidence, status=verification_state, print_profile={"source_count": int((source_rollup or {}).get("source_count") or 0), "verification_state": verification_state})
        card_name = str(basic_facts.get("card_name") or "").strip()
        set_name = str(basic_facts.get("set_name") or "").strip()
        effect_text = str(basic_facts.get("effect_text") or "").strip()
        if card_name:
            summary_text = f"{card_code} is {card_name}" + (f" from {set_name}" if set_name else "")
            self.upsert_answer_fragment(card_code=card_code, fragment_key="core_identity", fragment_type="verified_fact", answer_text=summary_text + ".", confidence_label=self.confidence_label(confidence), status=verification_state, provenance={"source_id": source_id, "kind": "identity-summary"})
        if effect_text:
            self.upsert_answer_fragment(card_code=card_code, fragment_key="gameplay_effect", fragment_type="verified_fact", answer_text=effect_text, confidence_label=self.confidence_label(confidence), status=verification_state, provenance={"source_id": source_id, "kind": "effect-summary"})
        self.upsert_answer_fragment(card_code=card_code, fragment_key="confidence_posture", fragment_type=self.confidence_label(confidence), answer_text="Miru has verified card facts stored for this card." if accepted_fields else "Miru has partial card evidence but not a complete verified answer yet.", confidence_label=self.confidence_label(confidence), status=verification_state, provenance={"source_id": source_id, "accepted_fields": accepted_fields[:8]})
        return {"stored": True, "card_code": card_code, "accepted_field_count": len(accepted_fields), "confidence_label": self.confidence_label(confidence)}


def inspect_miru_dossier_store(path: Path | None) -> dict[str, Any]:
    return MiruDossierStore(Path(path or "")).inspect_summary()


def build_card_dossier(
    card_id: str,
    *,
    dossier_db_path: Path | None = None,
    learning_db_path: Path | None = None,
    rules_db_path: Path | None = None,
    deck_intel_db_path: Path | None = None,
    catalog_db_path: Path | None = None,
    prices_path: Path | None = None,
) -> dict[str, Any]:
    store = MiruDossierStore(Path(dossier_db_path or (PROJECT_ROOT / "data" / "miru_dossiers.db")))
    return store.build_card_dossier(
        card_id,
        learning_db_path=learning_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        catalog_db_path=catalog_db_path,
        prices_path=prices_path,
    )


def generate_card_insight(
    card_id: str,
    *,
    dossier_db_path: Path | None = None,
    learning_db_path: Path | None = None,
    rules_db_path: Path | None = None,
    deck_intel_db_path: Path | None = None,
    catalog_db_path: Path | None = None,
    prices_path: Path | None = None,
) -> dict[str, Any]:
    store = MiruDossierStore(Path(dossier_db_path or (PROJECT_ROOT / "data" / "miru_dossiers.db")))
    return store.generate_card_insight(
        card_id,
        learning_db_path=learning_db_path,
        rules_db_path=rules_db_path,
        deck_intel_db_path=deck_intel_db_path,
        catalog_db_path=catalog_db_path,
        prices_path=prices_path,
    )
