from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Sequence

from tools.miru_ai_onepiece import clean_display_text, normalize_card_code, normalize_set_code
from tools.miru_dossier_store import MiruDossierStore
from tools.miru_source_adapters import NormalizedSourceRecord
from tools.miru_source_registry import (
    MiruSourceEntry,
    build_source_registry,
    build_unknown_source_entry,
    get_source_entry,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECT_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_PROJECT_PRICES_PATH = PROJECT_ROOT / "data" / "prices.json"
DEFAULT_DECK_INTEL_DB_PATH = PROJECT_ROOT / "data" / "miru_deck_intel.db"
DEFAULT_CANONICAL_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
DEFAULT_RULES_DB_PATH = PROJECT_ROOT / "data" / "miru_official_rules.db"
DEFAULT_RUNTIME_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
DEFAULT_SYNC_LOG_PATH = PROJECT_ROOT / "data" / "miru_project_sync.log"
INSIGHT_TYPES = ("meta", "usage", "price", "strength", "ruling")

# ---------------------------------------------------------------------------
# Insight quality classification  (see docs/miru_insight_upgrade_policy.md)
# ---------------------------------------------------------------------------

TIER_GENERIC    = "generic"       # rank 0 — formulaic template text
TIER_CONTEXTUAL = "contextual"    # rank 1 — adds context, no strategy
TIER_STRATEGIC  = "strategic"     # rank 2 — explains role / archetype
TIER_EVIDENCED  = "evidenced"     # rank 3 — strategic + strong evidence

_TIER_RANK: dict[str, int] = {
    TIER_GENERIC: 0,
    TIER_CONTEXTUAL: 1,
    TIER_STRATEGIC: 2,
    TIER_EVIDENCED: 3,
}

# Substrings that mark formulaic / template insight text.
_GENERIC_PATTERNS: tuple[str, ...] = (
    "in miru's verified card layer",
    "in miru's verified dossier",
    "currently filed as a",
    "currently anchored to",
    "synergy-first rather than standalone tech",
    "generic filler slot",
    "miru treats it as",
    "miru's verified text suggests",
    "works best when built around its on-card effect",
)

# Words that signal strategic / archetype-aware content.
_STRATEGIC_SIGNALS: tuple[str, ...] = (
    "core", "flex", "tech",
    "shell", "package", "variant",
    "archetype", "build", "meta",
    "inclusion", "staple",
    "every", "most", "consistently",
)

# Minimum confidence for the evidenced tier.
_EVIDENCED_CONFIDENCE_MIN = 0.70

# Minimum confidence delta to justify a same-tier replacement.
_CONFIDENCE_REPLACE_DELTA = 0.05
MIN_INSIGHT_CONFIDENCE = 0.50  # Do not emit insights below this; prefer no insight over weak insight
DEFAULT_INCREMENTAL_SYNC_LIMIT = 150


def classify_insight_quality(text: str, confidence: float) -> str:
    """
    Classify an insight into a quality tier.

    See docs/miru_insight_upgrade_policy.md §1 for definitions.
    """
    lower = text.lower()

    is_generic = any(pat in lower for pat in _GENERIC_PATTERNS)
    strategic_hits = sum(1 for sig in _STRATEGIC_SIGNALS if sig in lower)
    has_strategic = strategic_hits >= 2

    if is_generic and not has_strategic:
        return TIER_GENERIC

    if has_strategic and confidence >= _EVIDENCED_CONFIDENCE_MIN:
        return TIER_EVIDENCED

    if has_strategic:
        return TIER_STRATEGIC

    return TIER_CONTEXTUAL


def should_replace_insight(
    existing_tier: str,
    existing_confidence: float,
    new_tier: str,
    new_confidence: float,
) -> bool:
    """
    Decide whether a new insight should replace an existing one.

    Returns True only when the new insight is materially better.
    See docs/miru_insight_upgrade_policy.md §2 for the full rule set.
    """
    existing_rank = _TIER_RANK.get(existing_tier, 0)
    new_rank      = _TIER_RANK.get(new_tier, 0)

    # Higher tier always wins.
    if new_rank > existing_rank:
        return True

    # Lower tier never replaces.
    if new_rank < existing_rank:
        return False

    # Same tier: meaningfully higher confidence wins.
    if new_confidence > existing_confidence + _CONFIDENCE_REPLACE_DELTA:
        return True

    # Same tier, similar confidence: preserve existing to avoid churn.
    return False


def connect_catalog_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_catalog_sync_schema(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = """
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_code TEXT NOT NULL UNIQUE,
            set_name TEXT NOT NULL DEFAULT '',
            series_code_display TEXT NOT NULL DEFAULT '',
            series_id TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS cards (
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
        );
        CREATE TABLE IF NOT EXISTS card_variants (
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
            has_variant_evidence INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
            UNIQUE(card_id, variant_key, print_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cards_set_code ON cards(set_code);
        CREATE INDEX IF NOT EXISTS idx_cards_card_name ON cards(card_name);
        CREATE INDEX IF NOT EXISTS idx_variants_card_id ON card_variants(card_id);
        CREATE INDEX IF NOT EXISTS idx_variants_variant_key ON card_variants(variant_key);

        CREATE TABLE IF NOT EXISTS miru_validations (
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
        CREATE INDEX IF NOT EXISTS idx_miru_validations_verified_at ON miru_validations(verified_at);

        CREATE TABLE IF NOT EXISTS miru_card_insights (
            card_id TEXT NOT NULL,
            insight_type TEXT NOT NULL,
            insight_text TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sync_reason TEXT NOT NULL DEFAULT '',
            source_updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(card_id, insight_type),
            FOREIGN KEY(card_id) REFERENCES cards(canonical_code) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_miru_card_insights_card_id ON miru_card_insights(card_id, confidence DESC, updated_at DESC);

        CREATE TABLE IF NOT EXISTS miru_card_usage (
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

        CREATE TABLE IF NOT EXISTS miru_deck_archetypes (
            archetype_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT '',
            representative_leader_code TEXT NOT NULL DEFAULT '',
            confidence_score REAL NOT NULL DEFAULT 0.0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS miru_meta_events (
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
    """
    with closing(connect_catalog_db(path)) as conn:
        conn.executescript(schema)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS miru_action_history (
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
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_action_history_created_at "
            "ON miru_action_history(created_at DESC, action_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_action_history_target "
            "ON miru_action_history(target_type, target_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS miru_review_queue (
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
                approval_state TEXT NOT NULL DEFAULT '',
                promotion_state TEXT NOT NULL DEFAULT '',
                approval_note TEXT NOT NULL DEFAULT '',
                decision_source TEXT NOT NULL DEFAULT '',
                resolution_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT NOT NULL DEFAULT '',
                approval_updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_review_queue_status "
            "ON miru_review_queue(status, updated_at DESC, queue_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_review_queue_target "
            "ON miru_review_queue(target_type, target_id, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_variant_analysis (
                canonical_code TEXT NOT NULL PRIMARY KEY,
                image_path TEXT NOT NULL,
                sp_marker_detected INTEGER,
                parallel_marker_detected INTEGER,
                analysis_confidence TEXT NOT NULL DEFAULT 'high',
                raw_vision_response TEXT NOT NULL DEFAULT '',
                analysis_timestamp TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                operator_decision TEXT DEFAULT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_image_variant_analysis_review "
            "ON image_variant_analysis(review_status, sp_marker_detected)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS miru_publication_stage (
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
                candidate_score REAL NOT NULL DEFAULT 0.0,
                candidate_score_band TEXT NOT NULL DEFAULT '',
                candidate_profile TEXT NOT NULL DEFAULT '',
                candidate_score_reasons_json TEXT NOT NULL DEFAULT '[]',
                candidate_risk_factors_json TEXT NOT NULL DEFAULT '[]',
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
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_publication_stage_state "
            "ON miru_publication_stage(stage_state, updated_at DESC, target_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_publication_stage_batch "
            "ON miru_publication_stage(batch_id, stage_state, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS miru_publication_batches (
                batch_id TEXT PRIMARY KEY,
                batch_status TEXT NOT NULL DEFAULT 'draft',
                batch_title TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                summary_text TEXT NOT NULL DEFAULT '',
                guardrail_label TEXT NOT NULL DEFAULT '',
                batch_quality_score REAL NOT NULL DEFAULT 0.0,
                batch_quality_band TEXT NOT NULL DEFAULT '',
                batch_profile TEXT NOT NULL DEFAULT '',
                member_count INTEGER NOT NULL DEFAULT 0,
                ready_member_count INTEGER NOT NULL DEFAULT 0,
                review_member_count INTEGER NOT NULL DEFAULT 0,
                blocked_member_count INTEGER NOT NULL DEFAULT 0,
                deferred_member_count INTEGER NOT NULL DEFAULT 0,
                strongest_reasons_json TEXT NOT NULL DEFAULT '[]',
                unresolved_risks_json TEXT NOT NULL DEFAULT '[]',
                recommended_next_step TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                archived_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_publication_batches_status "
            "ON miru_publication_batches(batch_status, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS miru_publication_batch_items (
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
                candidate_score REAL NOT NULL DEFAULT 0.0,
                candidate_score_band TEXT NOT NULL DEFAULT '',
                candidate_profile TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                removed_at TEXT NOT NULL DEFAULT '',
                UNIQUE(batch_id, item_key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_publication_batch_items_batch "
            "ON miru_publication_batch_items(batch_id, status, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_miru_publication_batch_items_target "
            "ON miru_publication_batch_items(target_id, updated_at DESC)"
        )
        _ensure_column(conn, "miru_validations", "winning_source_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_validations", "rejected_sources_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_validations", "conflict_summary_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_validations", "confidence_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_card_insights", "quality_tier TEXT NOT NULL DEFAULT ''")
        # Source traceability and leader context columns — added 2026-03-16.
        # See docs/miru_db_schema.md §5 for full field definitions.
        _ensure_column(conn, "miru_card_insights", "source_ref TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_card_insights", "leader_code TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_card_insights", "generated_at INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_card_insights", "used_sections_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_card_insights", "sync_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_card_insights", "source_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_card_intelligence_table(conn)
        _ensure_card_legality_table(conn)
        _ensure_column(conn, "card_intelligence", "meta_relevance_score REAL")
        _ensure_column(conn, "card_intelligence", "top_leaders_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "rulings_summary TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "price_trend_note TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "confidence_score REAL")
        _ensure_column(conn, "card_intelligence", "source_summary TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "last_verified_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "legality_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "legality_note TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "rulings_sources_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "usage_profile_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_intelligence", "section_confidence_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_intelligence", "source_agreement_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_intelligence", "projection_sections_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "projection_source_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "last_sync_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "last_sync_mode TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "last_priority_score REAL")
        _ensure_column(conn, "card_intelligence", "last_priority_context_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_intelligence", "publication_readiness TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_guardrail TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_rationale TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "approval_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "promotion_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "promotion_rationale TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "promotion_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_candidate_score REAL")
        _ensure_column(conn, "card_intelligence", "publication_candidate_score_band TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_candidate_profile TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publication_candidate_reasons_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "publication_candidate_risks_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "publication_candidate_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publish_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "publish_reasons_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "publish_risks_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "publish_payload_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_intelligence", "publish_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "dossier_gap_class TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "dossier_gap_tags_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_intelligence", "coverage_value_score REAL")
        _ensure_column(conn, "card_intelligence", "coverage_value_band TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "coverage_gap_summary TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "revalidation_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "revalidation_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "revalidation_priority_score REAL")
        _ensure_column(conn, "card_intelligence", "revalidation_priority_bucket TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_intelligence", "revalidation_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_review_queue", "approval_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_review_queue", "promotion_state TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_review_queue", "approval_note TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_review_queue", "decision_source TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_review_queue", "approval_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "batch_id TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "note TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "decision_source TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "removed_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "candidate_score REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "miru_publication_stage", "candidate_score_band TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "candidate_profile TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_stage", "candidate_score_reasons_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_stage", "candidate_risk_factors_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_batches", "batch_title TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "batch_quality_score REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "miru_publication_batches", "batch_quality_band TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "batch_profile TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "ready_member_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_publication_batches", "review_member_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_publication_batches", "blocked_member_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_publication_batches", "deferred_member_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_publication_batches", "strongest_reasons_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_batches", "unresolved_risks_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_batches", "recommended_next_step TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "batch_publish_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "batch_publish_reasons_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_batches", "batch_publish_risks_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_publication_batches", "batch_publish_payload_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_publication_batches", "batch_publish_updated_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batches", "archived_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batch_items", "note TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batch_items", "removed_at TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batch_items", "candidate_score REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "miru_publication_batch_items", "candidate_score_band TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_publication_batch_items", "candidate_profile TEXT NOT NULL DEFAULT ''")


def _ensure_card_legality_table(conn: sqlite3.Connection) -> None:
    """Create card legality table for regulation/banlist state. Official-source-backed only; no fabricated data."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS miru_card_legality (
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
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_miru_card_legality_format_state "
        "ON miru_card_legality(format, legality_state)"
    )


def _ensure_card_intelligence_table(conn: sqlite3.Connection) -> None:
    """Create card_intelligence in catalog if missing; enables meta/usage persistence from deck intel."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS card_intelligence (
            card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            role_label TEXT NOT NULL DEFAULT '',
            role_summary TEXT NOT NULL DEFAULT '',
            deck_usage_summary TEXT NOT NULL DEFAULT '',
            price_value REAL,
            price_currency TEXT NOT NULL DEFAULT '',
            price_source TEXT NOT NULL DEFAULT '',
            price_url TEXT NOT NULL DEFAULT '',
            meta_relevance_score REAL,
            top_leaders_json TEXT NOT NULL DEFAULT '[]',
            rulings_summary TEXT NOT NULL DEFAULT '',
            price_trend_note TEXT NOT NULL DEFAULT '',
            confidence_score REAL,
            source_summary TEXT NOT NULL DEFAULT '',
            last_verified_at TEXT NOT NULL DEFAULT '',
            legality_state TEXT NOT NULL DEFAULT '',
            legality_note TEXT NOT NULL DEFAULT '',
            rulings_sources_json TEXT NOT NULL DEFAULT '[]',
            usage_profile_json TEXT NOT NULL DEFAULT '{}',
            section_confidence_json TEXT NOT NULL DEFAULT '{}',
            source_agreement_json TEXT NOT NULL DEFAULT '{}',
            projection_sections_json TEXT NOT NULL DEFAULT '[]',
            projection_source_updated_at TEXT NOT NULL DEFAULT '',
            last_sync_reason TEXT NOT NULL DEFAULT '',
            last_sync_mode TEXT NOT NULL DEFAULT '',
            last_priority_score REAL,
            last_priority_context_json TEXT NOT NULL DEFAULT '{}',
            publication_candidate_score REAL,
            publication_candidate_score_band TEXT NOT NULL DEFAULT '',
            publication_candidate_profile TEXT NOT NULL DEFAULT '',
            publication_candidate_reasons_json TEXT NOT NULL DEFAULT '[]',
            publication_candidate_risks_json TEXT NOT NULL DEFAULT '[]',
            publication_candidate_updated_at TEXT NOT NULL DEFAULT '',
            dossier_gap_class TEXT NOT NULL DEFAULT '',
            dossier_gap_tags_json TEXT NOT NULL DEFAULT '[]',
            coverage_value_score REAL,
            coverage_value_band TEXT NOT NULL DEFAULT '',
            coverage_gap_summary TEXT NOT NULL DEFAULT '',
            revalidation_status TEXT NOT NULL DEFAULT '',
            revalidation_reason TEXT NOT NULL DEFAULT '',
            revalidation_priority_score REAL,
            revalidation_priority_bucket TEXT NOT NULL DEFAULT '',
            revalidation_updated_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS miru_sync_metadata (
            sync_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_definition: str) -> None:
    existing_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    column_name = column_definition.split()[0]
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def load_card_validation_audit(
    card_code: str,
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any] | None:
    """Read-only validation audit payload for a canonical card code."""
    normalized = normalize_card_code(card_code)
    canonical_code = (normalized["canonical_code"] or card_code or "").strip().upper()
    if not canonical_code:
        return None
    path = Path(project_db_path)
    if not path.is_file():
        return None

    try:
        with closing(connect_catalog_db(path)) as conn:
            row = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.set_code,
                    c.card_number,
                    c.set_name,
                    c.card_name,
                    c.rarity,
                    c.color,
                    c.card_type,
                    c.cost,
                    c.power,
                    c.counter,
                    c.attribute,
                    c.traits,
                    c.life,
                    c.effect_text,
                    c.trigger_text,
                    c.sources_json AS canonical_sources_json,
                    v.confidence,
                    v.task_type,
                    v.verified_at,
                    v.sources_json,
                    v.winning_source_json,
                    v.rejected_sources_json,
                    v.validated_fields_json,
                    v.conflict_summary_json,
                    v.confidence_reason,
                    v.payload_json,
                    v.updated_at
                FROM cards c
                LEFT JOIN miru_validations v
                    ON v.card_code = c.canonical_code
                WHERE c.canonical_code = ?
                """,
                (canonical_code,),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None

    canonical_values = {
        "card_code": str(row["canonical_code"] or ""),
        "set_code": str(row["set_code"] or ""),
        "card_number": str(row["card_number"] or ""),
        "set_name": str(row["set_name"] or ""),
        "card_name": str(row["card_name"] or ""),
        "rarity": str(row["rarity"] or ""),
        "color": str(row["color"] or ""),
        "card_type": str(row["card_type"] or ""),
        "cost": "" if row["cost"] is None else str(row["cost"]),
        "power": str(row["power"] or ""),
        "counter": str(row["counter"] or ""),
        "attribute": str(row["attribute"] or ""),
        "traits": str(row["traits"] or ""),
        "life": str(row["life"] or ""),
        "effect_text": str(row["effect_text"] or ""),
        "trigger_text": str(row["trigger_text"] or ""),
    }
    sources = MiruProjectDbSync._load_json_objects(str(row["sources_json"] or "[]"))
    winning_source = MiruProjectDbSync._load_json_object(str(row["winning_source_json"] or "{}"))
    rejected_sources = MiruProjectDbSync._load_json_objects(str(row["rejected_sources_json"] or "[]"))
    conflict_summary = MiruProjectDbSync._load_json_object(str(row["conflict_summary_json"] or "{}"))
    validated_fields = MiruProjectDbSync._load_json_list(str(row["validated_fields_json"] or "[]"))
    payload_json = MiruProjectDbSync._load_json_object(str(row["payload_json"] or "{}"))
    canonical_source_keys = MiruProjectDbSync._load_json_list(str(row["canonical_sources_json"] or "[]"))

    return {
        "card_code": canonical_code,
        "validated_fields": validated_fields,
        "canonical_values": canonical_values,
        "canonical_source_keys": canonical_source_keys,
        "sources": sources,
        "winning_source": winning_source,
        "rejected_sources": rejected_sources,
        "conflict_summary": conflict_summary,
        "confidence": float(row["confidence"] or 0.0) if row["confidence"] is not None else 0.0,
        "confidence_reason": str(row["confidence_reason"] or ""),
        "verified_at": str(row["verified_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "task_type": str(row["task_type"] or ""),
        "payload": payload_json,
        "has_rejected_conflicts": bool(rejected_sources) or int(conflict_summary.get("rejected_field_count") or 0) > 0,
        "sync_boundary": {
            "owns_canonical_upsert": True,
            "summary": "MiruProjectDbSync is the trust-aware decision boundary and performs canonical card row upserts into card_catalog.db.",
        },
    }


def list_validation_audit_insights(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 6,
) -> dict[str, list[dict[str, Any]]]:
    """Read-only insight summaries for the Dev Monitor validation audit panel."""
    path = Path(project_db_path)
    if not path.is_file():
        return {
            "recent_conflicts": [],
            "lowest_confidence": [],
            "recently_validated": [],
            "rejected_evidence": [],
        }

    try:
        with closing(connect_catalog_db(path)) as conn:
            rows = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.card_name,
                    c.set_name,
                    v.confidence,
                    v.verified_at,
                    v.updated_at,
                    v.winning_source_json,
                    v.rejected_sources_json,
                    v.conflict_summary_json,
                    v.confidence_reason
                FROM miru_validations v
                JOIN cards c
                    ON c.canonical_code = v.card_code
                ORDER BY v.verified_at DESC, v.updated_at DESC, c.canonical_code ASC
                LIMIT 200
                """
            ).fetchall()
    except sqlite3.Error:
        rows = []

    items: list[dict[str, Any]] = []
    for row in rows:
        winning_source = MiruProjectDbSync._load_json_object(str(row["winning_source_json"] or "{}"))
        rejected_sources = MiruProjectDbSync._load_json_objects(str(row["rejected_sources_json"] or "[]"))
        conflict_summary = MiruProjectDbSync._load_json_object(str(row["conflict_summary_json"] or "{}"))
        items.append(
            {
                "card_code": str(row["canonical_code"] or ""),
                "card_name": str(row["card_name"] or ""),
                "set_name": str(row["set_name"] or ""),
                "confidence": float(row["confidence"] or 0.0) if row["confidence"] is not None else 0.0,
                "verified_at": str(row["verified_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "winning_source": winning_source,
                "winning_source_id": str(winning_source.get("source_id") or ""),
                "winning_trust_label": str(winning_source.get("trust_label") or ""),
                "rejected_sources": rejected_sources,
                "rejected_source_count": len(rejected_sources),
                "conflict_summary": conflict_summary,
                "conflict_rule": str(conflict_summary.get("rule") or "no-conflict"),
                "rejected_fields": [str(item) for item in (conflict_summary.get("rejected_fields") or []) if str(item).strip()],
                "confidence_reason": str(row["confidence_reason"] or ""),
            }
        )

    def summarize(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_code": entry["card_code"],
            "card_name": entry["card_name"],
            "set_name": entry["set_name"],
            "confidence": entry["confidence"],
            "verified_at": entry["verified_at"],
            "winning_source_id": entry["winning_source_id"],
            "winning_trust_label": entry["winning_trust_label"],
            "rejected_source_count": entry["rejected_source_count"],
            "rejected_fields": entry["rejected_fields"],
            "conflict_rule": entry["conflict_rule"],
            "confidence_reason": entry["confidence_reason"],
        }

    recent_conflicts = [
        summarize(item)
        for item in items
        if item["conflict_rule"] != "no-conflict"
    ][:limit]
    lowest_confidence = [
        summarize(item)
        for item in sorted(items, key=lambda item: (item["confidence"], item["verified_at"], item["card_code"]))
    ][:limit]
    recently_validated = [summarize(item) for item in items[:limit]]
    rejected_evidence = [
        summarize(item)
        for item in items
        if item["rejected_source_count"] > 0
    ][:limit]
    return {
        "recent_conflicts": recent_conflicts,
        "lowest_confidence": lowest_confidence,
        "recently_validated": recently_validated,
        "rejected_evidence": rejected_evidence,
    }


def _append_sync_log(log_path: str | Path, message: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def _utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_sync_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _latest_sync_timestamp(values: list[Any] | tuple[Any, ...] | set[Any] | Any) -> str:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set)):
        candidates = [values]
    else:
        candidates = list(values)
    best: tuple[float, str] | None = None
    for value in candidates:
        text = str(value or "").strip()
        parsed = _parse_sync_timestamp(text)
        if not text or parsed is None:
            continue
        candidate = (parsed.timestamp(), text)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else ""


def _sync_timestamp_is_newer(left: Any, right: Any) -> bool:
    left_dt = _parse_sync_timestamp(left)
    right_dt = _parse_sync_timestamp(right)
    if left_dt is None:
        return False
    if right_dt is None:
        return bool(str(left or "").strip())
    return left_dt > right_dt


def _load_json_list(raw_value: str) -> list[Any]:
    try:
        payload = json.loads(raw_value or "[]")
    except Exception:
        return []
    return list(payload) if isinstance(payload, list) else []


def _store_sync_metadata(conn: sqlite3.Connection, *, sync_key: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO miru_sync_metadata (sync_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(sync_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            str(sync_key or "").strip(),
            json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True),
            _utc_now_timestamp(),
        ),
    )


def _load_sync_metadata(conn: sqlite3.Connection, *, sync_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT payload_json FROM miru_sync_metadata WHERE sync_key = ? LIMIT 1",
        (str(sync_key or "").strip(),),
    ).fetchone()
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return _safe_load_json_dict(str(row["payload_json"] or "{}"))
    return _safe_load_json_dict(str(row[0] or "{}"))


def _derive_projection_sections(dossier: dict[str, Any]) -> list[str]:
    sections = dict(dossier.get("sections") or {})
    results: list[str] = []
    for section_name in ("identity", "text_effects", "usage_meta", "rulings", "legality", "market", "provenance"):
        payload = sections.get(section_name)
        if not payload:
            continue
        if isinstance(payload, dict):
            if not any(value not in ("", None, [], {}, False) for value in payload.values()):
                continue
        results.append(section_name)
    return results


def _load_projection_state(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            c.canonical_code,
            c.rarity,
            ci.updated_at,
            ci.last_verified_at,
            ci.confidence_score,
            ci.projection_source_updated_at,
            ci.projection_sections_json,
            ci.deck_usage_summary,
            ci.role_label,
            ci.meta_relevance_score,
            ci.rulings_summary,
            ci.legality_note,
            ci.price_value,
            ci.section_confidence_json,
            ci.source_agreement_json,
            ci.last_priority_score,
            ci.last_priority_context_json,
            ci.publication_readiness,
            ci.publish_status,
            ci.dossier_gap_class,
            ci.revalidation_status,
            ci.publication_candidate_score,
            ci.publication_updated_at,
            ci.revalidation_priority_score
        FROM cards c
        LEFT JOIN card_intelligence ci
            ON ci.card_id = c.id
        """
    ).fetchall()
    state: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["projection_sections"] = {
            str(value).strip()
            for value in _load_json_list(str(item.get("projection_sections_json") or "[]"))
            if str(value).strip()
        }
        item["section_confidence"] = _safe_load_json_dict(str(item.get("section_confidence_json") or "{}"))
        item["source_agreement"] = _safe_load_json_dict(str(item.get("source_agreement_json") or "{}"))
        item["last_priority_context"] = _safe_load_json_dict(str(item.get("last_priority_context_json") or "{}"))
        state[str(item.get("canonical_code") or "").strip().upper()] = item
    return state


def _load_existing_insight_card_ids(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0] or "").strip().upper()
        for row in conn.execute("SELECT DISTINCT card_id FROM miru_card_insights").fetchall()
        if str(row[0] or "").strip()
    }


def _load_runtime_dossier_updates(runtime_db_path: str | Path) -> dict[str, str]:
    updates: dict[str, str] = {}
    for item in _load_runtime_dossiers(runtime_db_path):
        code = str(item.get("card_code") or "").strip().upper()
        if not code:
            continue
        updates[code] = _latest_sync_timestamp([updates.get(code, ""), item.get("updated_at")])
    return updates


def _load_grouped_updates(db_path: str | Path, sql: str) -> dict[str, str]:
    path = Path(db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            return {
                str(row[0] or "").strip().upper(): str(row[1] or "").strip()
                for row in conn.execute(sql).fetchall()
                if str(row[0] or "").strip()
            }
    except sqlite3.Error:
        return {}


def _load_price_code_updates(prices_path: str | Path) -> dict[str, str]:
    path = Path(prices_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(item.get("code") or "").strip().upper(): source_updated_at
        for item in payload.values()
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    }


def _load_grouped_count(db_path: str | Path, sql: str) -> dict[str, int]:
    path = Path(db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            return {
                str(row[0] or "").strip().upper(): int(row[1] or 0)
                for row in conn.execute(sql).fetchall()
                if str(row[0] or "").strip()
            }
    except sqlite3.Error:
        return {}


def _load_runtime_source_counts(runtime_db_path: str | Path) -> dict[str, int]:
    return _load_grouped_count(
        runtime_db_path,
        """
        SELECT card_code, COUNT(DISTINCT source_id)
        FROM learning_dossier_sources
        GROUP BY card_code
        """,
    )


def _load_canonical_priority_metrics(canonical_dossier_db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(canonical_dossier_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT canonical_code, overall_score, overall_state, updated_at
                FROM cards
                WHERE trim(coalesce(canonical_code, '')) != ''
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row["canonical_code"] or "").strip().upper(): {
            "overall_score": float(row["overall_score"] or 0.0) if row["overall_score"] is not None else 0.0,
            "overall_state": str(row["overall_state"] or "").strip(),
            "updated_at": str(row["updated_at"] or "").strip(),
        }
        for row in rows
        if str(row["canonical_code"] or "").strip()
    }


def _load_deck_priority_metrics(deck_intel_db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(deck_intel_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    card_code,
                    COUNT(DISTINCT leader_code) AS leader_count,
                    SUM(deck_count) AS total_decks,
                    MAX(usage_percent) AS max_usage_percent,
                    MAX(avg_copies) AS max_avg_copies,
                    MAX(CASE WHEN lower(role_label) = 'core' THEN 1 ELSE 0 END) AS has_core_role,
                    MAX(CASE WHEN lower(role_label) = 'flex' THEN 1 ELSE 0 END) AS has_flex_role,
                    MAX(CASE WHEN lower(role_label) = 'tech' THEN 1 ELSE 0 END) AS has_tech_role,
                    MAX(updated_at) AS updated_at
                FROM leader_card_signals
                WHERE trim(coalesce(card_code, '')) != ''
                GROUP BY card_code
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    metrics: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row["card_code"] or "").strip().upper()
        if not code:
            continue
        primary_role = ""
        if int(row["has_core_role"] or 0):
            primary_role = "core"
        elif int(row["has_flex_role"] or 0):
            primary_role = "flex"
        elif int(row["has_tech_role"] or 0):
            primary_role = "tech"
        metrics[code] = {
            "leader_count": int(row["leader_count"] or 0),
            "total_decks": int(row["total_decks"] or 0),
            "max_usage_percent": float(row["max_usage_percent"] or 0.0),
            "max_avg_copies": float(row["max_avg_copies"] or 0.0),
            "primary_role": primary_role,
            "updated_at": str(row["updated_at"] or "").strip(),
        }
    return metrics


def _load_rules_priority_metrics(rules_db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(rules_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    card_code,
                    COUNT(*) AS ruling_count,
                    COUNT(DISTINCT source_reference) AS ruling_source_count,
                    MAX(updated_at) AS updated_at
                FROM official_card_rulings
                WHERE trim(coalesce(card_code, '')) != ''
                GROUP BY card_code
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row["card_code"] or "").strip().upper(): {
            "ruling_count": int(row["ruling_count"] or 0),
            "ruling_source_count": int(row["ruling_source_count"] or 0),
            "updated_at": str(row["updated_at"] or "").strip(),
        }
        for row in rows
        if str(row["card_code"] or "").strip()
    }


def _load_legality_priority_metrics(rules_db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(rules_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    card_code,
                    COUNT(*) AS legality_count,
                    SUM(CASE WHEN is_current = 1 THEN 1 ELSE 0 END) AS current_count,
                    SUM(CASE WHEN is_upcoming = 1 THEN 1 ELSE 0 END) AS upcoming_count,
                    MAX(updated_at) AS updated_at
                FROM official_legality_history
                WHERE trim(coalesce(card_code, '')) != ''
                GROUP BY card_code
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row["card_code"] or "").strip().upper(): {
            "legality_count": int(row["legality_count"] or 0),
            "current_count": int(row["current_count"] or 0),
            "upcoming_count": int(row["upcoming_count"] or 0),
            "updated_at": str(row["updated_at"] or "").strip(),
        }
        for row in rows
        if str(row["card_code"] or "").strip()
    }


def _load_price_priority_metrics(prices_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(prices_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    metrics: dict[str, dict[str, Any]] = {}
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        if not code:
            continue
        price_value = item.get("price")
        try:
            normalized_price = float(price_value)
        except (TypeError, ValueError):
            normalized_price = 0.0
        metrics[code] = {
            "price_value": normalized_price if normalized_price > 0 else None,
            "has_trend": bool(item.get("trend") or item.get("history") or item.get("change")),
            "updated_at": source_updated_at,
        }
    return metrics


def _priority_bucket_for_score(score: float) -> str:
    if score >= 470:
        return "critical"
    if score >= 360:
        return "high"
    if score >= 260:
        return "elevated"
    if score >= 180:
        return "medium"
    return "baseline"


def _incremental_priority_objectives(
    *,
    reason: str,
    candidate_sections: list[str],
    stored_sections: set[str],
    projection_row: dict[str, Any],
    source_count: int,
    deck_metrics: dict[str, Any],
    legality_metrics: dict[str, Any],
) -> dict[str, Any]:
    agreement = dict(projection_row.get("source_agreement") or {})
    agreement_level = str(agreement.get("agreement_level") or "").strip().lower()
    gap_class = str(projection_row.get("dossier_gap_class") or "").strip().lower()
    readiness_state = str(projection_row.get("publication_readiness") or "").strip().lower()
    publish_status = str(projection_row.get("publish_status") or "").strip().lower()
    revalidation_status = str(projection_row.get("revalidation_status") or "").strip().lower()
    stored_usage = bool(str(projection_row.get("deck_usage_summary") or "").strip()) or "usage_meta" in stored_sections
    leader_count = int(deck_metrics.get("leader_count") or 0)
    total_decks = int(deck_metrics.get("total_decks") or 0)
    max_usage_percent = float(deck_metrics.get("max_usage_percent") or 0.0)
    current_legality_rows = int(legality_metrics.get("current_count") or 0)
    upcoming_legality_rows = int(legality_metrics.get("upcoming_count") or 0)
    legality_count = int(legality_metrics.get("legality_count") or 0)
    is_leader = str(projection_row.get("rarity") or "").strip().upper() == "L"

    thin_source_support = source_count <= 1 or agreement_level in {"single_source", "partial"}
    leader_significance = is_leader or leader_count >= 3 or total_decks >= 8 or max_usage_percent >= 0.35
    legality_sensitive = (
        "legality" in candidate_sections
        or "rulings" in candidate_sections
        or gap_class == "missing_rules_legality"
        or legality_count > 0
        or current_legality_rows > 0
        or upcoming_legality_rows > 0
    )
    publication_proximity = (
        readiness_state in {"ready_for_publish_candidate", "ready_for_review"}
        or publish_status in {"publish_ready", "publish_requires_review", "publish_deferred"}
    )
    stale_promising = gap_class in {"stale_dossier", "partial_but_promising", "ready_for_revalidation"} or revalidation_status in {
        "recheck_soon",
        "recheck_later",
        "escalate_review",
    }

    objectives: list[str] = []
    if "usage_meta" in candidate_sections or (leader_significance and not stored_usage):
        objectives.append("usage_meta_fill")
    if leader_significance and (is_leader or reason == "usage_meta_activation" or gap_class in {"missing_usage_meta", "partial_but_promising"}):
        objectives.append("leader_profile_expand")
    if thin_source_support and (
        publication_proximity
        or leader_significance
        or gap_class in {"thin_source_support", "partial_but_promising", "weak_provenance"}
        or any(section in candidate_sections for section in ("usage_meta", "rulings", "legality"))
    ):
        objectives.append("source_depth_fill")
    if legality_sensitive and (
        publication_proximity
        or gap_class == "missing_rules_legality"
        or current_legality_rows > 0
        or upcoming_legality_rows > 0
    ):
        objectives.append("legality_recheck")
    if stale_promising or (
        publication_proximity
        and gap_class in {"thin_source_support", "missing_usage_meta"}
        and reason in {"identity_refresh", "projection_missing", "insight_missing"}
    ):
        objectives.append("stale_refresh")

    if not objectives:
        if "usage_meta" in candidate_sections:
            objectives.append("usage_meta_fill")
        elif any(section in candidate_sections for section in ("rulings", "legality")):
            objectives.append("legality_recheck")
        else:
            objectives.append("source_depth_fill")

    return {
        "objectives": list(dict.fromkeys(objectives)),
        "thin_source_support": thin_source_support,
        "leader_significance": leader_significance,
        "legality_sensitive": legality_sensitive,
        "publication_proximity": publication_proximity,
        "stale_promising": stale_promising,
        "gap_class": gap_class,
        "is_leader": is_leader,
        "leader_count": leader_count,
        "total_decks": total_decks,
        "max_usage_percent": round(max_usage_percent, 4),
        "current_legality_rows": current_legality_rows,
        "upcoming_legality_rows": upcoming_legality_rows,
        "legality_count": legality_count,
    }


def _score_incremental_candidate(
    *,
    code: str,
    reason: str,
    base_priority: int,
    candidate_sections: list[str],
    available_sections: list[str],
    stored_sections: set[str],
    has_projection: bool,
    has_existing_insight: bool,
    projection_row: dict[str, Any],
    canonical_metrics: dict[str, Any],
    source_count: int,
    deck_metrics: dict[str, Any],
    rules_metrics: dict[str, Any],
    legality_metrics: dict[str, Any],
    price_metrics: dict[str, Any],
) -> dict[str, Any]:
    score = float(base_priority)
    objective_context = _incremental_priority_objectives(
        reason=reason,
        candidate_sections=candidate_sections,
        stored_sections=stored_sections,
        projection_row=projection_row,
        source_count=source_count,
        deck_metrics=deck_metrics,
        legality_metrics=legality_metrics,
    )
    factors: dict[str, Any] = {
        "reason": reason,
        "base_priority": int(base_priority),
        "candidate_sections": list(candidate_sections),
        "available_sections": list(available_sections),
        "priority_objectives": list(objective_context.get("objectives") or []),
    }
    missing_sections = [section for section in candidate_sections if section not in stored_sections]
    if missing_sections:
        score += min(len(missing_sections) * 8, 24)
    factors["missing_sections"] = missing_sections

    canonical_confidence = float(canonical_metrics.get("overall_score") or projection_row.get("confidence_score") or 0.0)
    if canonical_confidence > 0:
        score += min(canonical_confidence * 35.0, 35.0)
    factors["canonical_confidence"] = round(canonical_confidence, 3)

    if source_count > 0:
        score += min(source_count, 4) * 7
    factors["source_count"] = int(source_count or 0)

    agreement = dict(projection_row.get("source_agreement") or {})
    agreement_level = str(agreement.get("agreement_level") or "").strip().lower()
    agreement_bonus = {
        "full": 12,
        "majority": 8,
        "partial": 5,
        "single_source": 2,
        "conflict": -8,
    }.get(agreement_level, 0)
    score += agreement_bonus
    factors["agreement_level"] = agreement_level

    gap_class = str(objective_context.get("gap_class") or "")
    gap_bonus = {
        "missing_rules_legality": 34,
        "missing_usage_meta": 30,
        "partial_but_promising": 28,
        "ready_for_revalidation": 24,
        "thin_source_support": 18,
        "stale_dossier": 18,
        "weak_provenance": 14,
        "market_only": 6,
    }.get(gap_class, 0)
    if gap_bonus:
        score += gap_bonus
    factors["gap_class"] = gap_class

    if bool(objective_context.get("thin_source_support")):
        score += 18 if bool(objective_context.get("publication_proximity")) else 10
    factors["thin_source_support"] = bool(objective_context.get("thin_source_support"))

    if bool(objective_context.get("leader_significance")):
        score += 26 if bool(objective_context.get("is_leader")) else 18
    factors["leader_significance"] = bool(objective_context.get("leader_significance"))
    factors["is_leader"] = bool(objective_context.get("is_leader"))

    if bool(objective_context.get("legality_sensitive")):
        score += 26 if bool(objective_context.get("publication_proximity")) else 14
    factors["legality_sensitive"] = bool(objective_context.get("legality_sensitive"))

    if bool(objective_context.get("publication_proximity")):
        score += 28
    factors["publication_proximity"] = bool(objective_context.get("publication_proximity"))

    if bool(objective_context.get("stale_promising")):
        score += 18
    factors["stale_promising"] = bool(objective_context.get("stale_promising"))

    if not has_projection:
        score += 10
    if not has_existing_insight and any(section in candidate_sections for section in ("usage_meta", "rulings", "legality", "market")):
        score += 14

    if reason == "rules_legality_activation":
        ruling_count = int(rules_metrics.get("ruling_count") or 0)
        ruling_source_count = int(rules_metrics.get("ruling_source_count") or 0)
        legality_count = int(legality_metrics.get("legality_count") or 0)
        current_count = int(legality_metrics.get("current_count") or 0)
        upcoming_count = int(legality_metrics.get("upcoming_count") or 0)
        score += min(ruling_count * 18, 54)
        score += min(ruling_source_count * 8, 16)
        score += min(legality_count * 14, 42)
        score += current_count * 8
        score += upcoming_count * 12
        factors["ruling_count"] = ruling_count
        factors["ruling_source_count"] = ruling_source_count
        factors["legality_count"] = legality_count
        factors["current_legality_rows"] = current_count
        factors["upcoming_legality_rows"] = upcoming_count

    if reason == "usage_meta_activation" or "usage_meta" in candidate_sections:
        leader_count = int(deck_metrics.get("leader_count") or 0)
        total_decks = int(deck_metrics.get("total_decks") or 0)
        max_usage_percent = float(deck_metrics.get("max_usage_percent") or 0.0)
        max_avg_copies = float(deck_metrics.get("max_avg_copies") or 0.0)
        primary_role = str(deck_metrics.get("primary_role") or "").strip().lower()
        score += min(leader_count, 4) * 10
        score += min(total_decks, 25) * 1.8
        score += min(max_usage_percent, 1.0) * 35
        score += min(max_avg_copies, 4.0) * 3
        if primary_role == "core":
            score += 12
        elif primary_role == "flex":
            score += 6
        factors["leader_count"] = leader_count
        factors["total_decks"] = total_decks
        factors["max_usage_percent"] = round(max_usage_percent, 4)
        factors["max_avg_copies"] = round(max_avg_copies, 3)
        factors["primary_role"] = primary_role

    if reason == "market_activation" or "market" in candidate_sections:
        price_value = price_metrics.get("price_value")
        has_trend = bool(price_metrics.get("has_trend"))
        if price_value not in (None, ""):
            score += min(float(price_value), 40.0) * 1.25
            if float(price_value) >= 15:
                score += 10
            elif float(price_value) >= 5:
                score += 5
        if has_trend:
            score += 8
        factors["price_value"] = None if price_value in (None, "") else round(float(price_value), 2)
        factors["has_trend"] = has_trend

    if reason == "projection_missing" and any(section in available_sections for section in ("usage_meta", "rulings", "legality", "market")):
        score += 18
    elif reason == "projection_missing":
        score += 6

    priority_score = round(score, 3)
    priority_bucket = _priority_bucket_for_score(priority_score)
    selection_reasons: list[str] = []
    if gap_class:
        selection_reasons.append(f"gap={gap_class}")
    if bool(objective_context.get("leader_significance")):
        selection_reasons.append("leader-significant")
    if bool(objective_context.get("thin_source_support")):
        selection_reasons.append("thin-source-support")
    if bool(objective_context.get("legality_sensitive")):
        selection_reasons.append("legality-sensitive")
    if bool(objective_context.get("publication_proximity")):
        selection_reasons.append("near-review-publish")
    if bool(objective_context.get("stale_promising")):
        selection_reasons.append("stale-but-promising")
    evidence_bits: list[str] = []
    if int(factors.get("ruling_count") or 0) or int(factors.get("legality_count") or 0):
        evidence_bits.append(
            f"official_rules={int(factors.get('ruling_count') or 0)} rulings/{int(factors.get('legality_count') or 0)} legality"
        )
    if int(factors.get("leader_count") or 0):
        evidence_bits.append(
            f"usage={int(factors.get('leader_count') or 0)} leaders/{int(factors.get('total_decks') or 0)} decks"
        )
    if factors.get("price_value") not in (None, ""):
        evidence_bits.append(f"price=${float(factors.get('price_value') or 0.0):.2f}")
    if canonical_confidence > 0:
        evidence_bits.append(f"confidence={canonical_confidence:.2f}")
    if int(source_count or 0) > 0:
        evidence_bits.append(f"sources={int(source_count)}")
    if selection_reasons:
        evidence_bits.extend(selection_reasons[:3])
    priority_summary = f"{reason}: " + ", ".join(evidence_bits or ["bounded projection follow-up"])
    return {
        "priority_score": priority_score,
        "priority_bucket": priority_bucket,
        "priority_summary": priority_summary,
        "priority_factors": factors,
        "priority_objectives": list(objective_context.get("objectives") or []),
        "selection_reasons": selection_reasons,
    }


def _collect_incremental_sync_candidates(
    *,
    project_db_path: Path,
    runtime_dossier_db_path: Path,
    canonical_dossier_db_path: Path,
    rules_db_path: Path,
    deck_intel_db_path: Path,
    prices_path: Path,
    limit: int | None,
) -> dict[str, Any]:
    ensure_catalog_sync_schema(project_db_path)
    learning_updates = _load_runtime_dossier_updates(runtime_dossier_db_path)
    learning_source_counts = _load_runtime_source_counts(runtime_dossier_db_path)
    learning_source_updates = _load_grouped_updates(
        runtime_dossier_db_path,
        """
        SELECT card_code, MAX(updated_at)
        FROM learning_dossier_sources
        GROUP BY card_code
        """,
    )
    canonical_card_updates = _load_grouped_updates(
        canonical_dossier_db_path,
        """
        SELECT canonical_code, MAX(updated_at)
        FROM cards
        GROUP BY canonical_code
        """,
    )
    canonical_fact_updates = _load_grouped_updates(
        canonical_dossier_db_path,
        """
        SELECT c.canonical_code, MAX(f.updated_at)
        FROM card_facts f
        JOIN cards c ON c.id = f.card_id
        GROUP BY c.canonical_code
        """,
    )
    rules_updates = _load_grouped_updates(
        rules_db_path,
        """
        SELECT card_code, MAX(updated_at)
        FROM official_card_rulings
        WHERE trim(coalesce(card_code, '')) != ''
        GROUP BY card_code
        """,
    )
    legality_updates = _load_grouped_updates(
        rules_db_path,
        """
        SELECT card_code, MAX(updated_at)
        FROM official_legality_history
        WHERE trim(coalesce(card_code, '')) != ''
        GROUP BY card_code
        """,
    )
    deck_updates = _load_grouped_updates(
        deck_intel_db_path,
        """
        SELECT card_code, MAX(updated_at)
        FROM leader_card_signals
        GROUP BY card_code
        """,
    )
    price_updates = _load_price_code_updates(prices_path)
    canonical_metrics = _load_canonical_priority_metrics(canonical_dossier_db_path)
    deck_priority_metrics = _load_deck_priority_metrics(deck_intel_db_path)
    rules_priority_metrics = _load_rules_priority_metrics(rules_db_path)
    legality_priority_metrics = _load_legality_priority_metrics(rules_db_path)
    price_priority_metrics = _load_price_priority_metrics(prices_path)
    with closing(connect_catalog_db(project_db_path)) as conn:
        projection_state = _load_projection_state(conn)
        insight_cards = _load_existing_insight_card_ids(conn)

    all_codes = set(projection_state) | set(learning_updates) | set(learning_source_updates) | set(canonical_card_updates) | set(canonical_fact_updates) | set(rules_updates) | set(legality_updates) | set(deck_updates) | set(price_updates)
    candidates: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    for code in sorted(all_codes):
        row = projection_state.get(code) or {}
        stored_source_updated_at = str(row.get("projection_source_updated_at") or row.get("updated_at") or row.get("last_verified_at") or "").strip()
        stored_sections = set(row.get("projection_sections") or set())
        stored_gap_class = str(row.get("dossier_gap_class") or "").strip().lower()
        stored_readiness_state = str(row.get("publication_readiness") or "").strip().lower()
        stored_publish_status = str(row.get("publish_status") or "").strip().lower()
        stored_revalidation_status = str(row.get("revalidation_status") or "").strip().lower()
        has_projection = any(
            row.get(field_name) not in (None, "")
            for field_name in (
                "updated_at",
                "last_verified_at",
                "projection_source_updated_at",
                "deck_usage_summary",
                "meta_relevance_score",
                "rulings_summary",
                "legality_note",
                "price_value",
            )
        )
        usage_updated_at = deck_updates.get(code, "")
        rules_updated_at = rules_updates.get(code, "")
        legality_updated_at = legality_updates.get(code, "")
        market_updated_at = price_updates.get(code, "")
        identity_updated_at = _latest_sync_timestamp([
            learning_updates.get(code, ""),
            learning_source_updates.get(code, ""),
            canonical_card_updates.get(code, ""),
            canonical_fact_updates.get(code, ""),
        ])
        source_updated_at = _latest_sync_timestamp([
            identity_updated_at,
            usage_updated_at,
            rules_updated_at,
            legality_updated_at,
            market_updated_at,
        ])
        available_sections: list[str] = []
        if identity_updated_at:
            available_sections.extend(["identity", "text_effects"])
        if usage_updated_at:
            available_sections.append("usage_meta")
        if rules_updated_at:
            available_sections.append("rulings")
        if legality_updated_at:
            available_sections.append("legality")
        if market_updated_at:
            available_sections.append("market")

        reason = ""
        priority = 0
        candidate_sections: list[str] = []
        if rules_updated_at or legality_updated_at:
            rules_missing = (bool(rules_updated_at) and not str(row.get("rulings_summary") or "").strip()) or "rulings" not in stored_sections
            legality_missing = (bool(legality_updated_at) and not str(row.get("legality_note") or "").strip()) or ("legality" not in stored_sections and bool(legality_updated_at))
            if rules_missing or legality_missing or _sync_timestamp_is_newer(_latest_sync_timestamp([rules_updated_at, legality_updated_at]), stored_source_updated_at):
                reason = "rules_legality_activation"
                priority = 400
                candidate_sections = [section for section in ("rulings", "legality") if section in available_sections]
        if not reason and usage_updated_at:
            usage_missing = (
                not str(row.get("deck_usage_summary") or "").strip()
                or row.get("meta_relevance_score") in (None, "")
                or "usage_meta" not in stored_sections
            )
            if usage_missing or _sync_timestamp_is_newer(usage_updated_at, stored_source_updated_at):
                reason = "usage_meta_activation"
                priority = 300
                candidate_sections = ["usage_meta"]
        if not reason and market_updated_at:
            market_missing = row.get("price_value") in (None, "") or "market" not in stored_sections
            if market_missing or _sync_timestamp_is_newer(market_updated_at, stored_source_updated_at):
                reason = "market_activation"
                priority = 250
                candidate_sections = ["market"]
        if not reason and not has_projection:
            reason = "projection_missing"
            priority = 200 if candidate_sections or any(section in available_sections for section in ("usage_meta", "rulings", "legality", "market")) else 120
            candidate_sections = list(dict.fromkeys(candidate_sections + available_sections))
        if not reason and source_updated_at and _sync_timestamp_is_newer(source_updated_at, stored_source_updated_at):
            reason = "identity_refresh"
            priority = 150
            candidate_sections = list(dict.fromkeys(available_sections or ["identity"]))
        if not reason and code not in insight_cards and any(section in available_sections for section in ("usage_meta", "rulings", "legality", "market")):
            reason = "insight_missing"
            priority = 140
            candidate_sections = [section for section in available_sections if section in {"usage_meta", "rulings", "legality", "market"}]
        stored_priority_sections = [section for section in ("usage_meta", "rulings", "legality", "market") if section in stored_sections]
        available_priority_sections = [section for section in ("usage_meta", "rulings", "legality", "market") if section in available_sections]
        if not reason and stored_gap_class == "missing_rules_legality":
            reason = "legality_recheck_priority"
            priority = 360
            candidate_sections = list(dict.fromkeys([section for section in ("rulings", "legality") if section in (stored_sections | set(available_sections))] or stored_priority_sections or available_priority_sections or ["identity"]))
        if not reason and stored_gap_class == "missing_usage_meta":
            reason = "leader_staple_meta_followup"
            priority = 340
            candidate_sections = list(dict.fromkeys((["usage_meta"] if ("usage_meta" in stored_sections or "usage_meta" in available_sections) else []) or stored_priority_sections or available_priority_sections or ["identity"]))
        if not reason and (
            stored_gap_class in {"thin_source_support", "weak_provenance"}
            and (
                stored_readiness_state in {"ready_for_publish_candidate", "ready_for_review"}
                or stored_publish_status in {"publish_requires_review", "publish_deferred"}
                or stored_revalidation_status in {"recheck_soon", "escalate_review"}
            )
        ):
            reason = "source_depth_followup"
            priority = 320
            candidate_sections = list(dict.fromkeys(stored_priority_sections or available_priority_sections or ["identity"]))
        if not reason and (
            stored_gap_class in {"stale_dossier", "partial_but_promising", "ready_for_revalidation"}
            or stored_revalidation_status in {"recheck_soon", "recheck_later", "escalate_review"}
            or stored_readiness_state in {"ready_for_publish_candidate", "ready_for_review"}
            or stored_publish_status in {"publish_requires_review", "publish_deferred"}
        ):
            reason = "stale_promising_followup"
            priority = 300
            candidate_sections = list(dict.fromkeys(stored_priority_sections or available_priority_sections or list(stored_sections) or available_sections or ["identity"]))
        if not reason:
            continue
        priority_payload = _score_incremental_candidate(
            code=code,
            reason=reason,
            base_priority=priority,
            candidate_sections=list(dict.fromkeys(candidate_sections)),
            available_sections=list(dict.fromkeys(available_sections)),
            stored_sections=stored_sections,
            has_projection=has_projection,
            has_existing_insight=code in insight_cards,
            projection_row=row,
            canonical_metrics=canonical_metrics.get(code) or {},
            source_count=int(learning_source_counts.get(code) or 0),
            deck_metrics=deck_priority_metrics.get(code) or {},
            rules_metrics=rules_priority_metrics.get(code) or {},
            legality_metrics=legality_priority_metrics.get(code) or {},
            price_metrics=price_priority_metrics.get(code) or {},
        )
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        bucket = str(priority_payload.get("priority_bucket") or "")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        candidates.append(
            {
                "card_code": code,
                "reason": reason,
                "priority": priority,
                "sections": list(dict.fromkeys(candidate_sections)),
                "source_updated_at": source_updated_at,
                "available_sections": list(dict.fromkeys(available_sections)),
                "priority_score": float(priority_payload.get("priority_score") or 0.0),
                "priority_bucket": bucket,
                "priority_summary": str(priority_payload.get("priority_summary") or "").strip(),
                "priority_factors": dict(priority_payload.get("priority_factors") or {}),
                "priority_objectives": list(priority_payload.get("priority_objectives") or []),
                "selection_reasons": list(priority_payload.get("selection_reasons") or []),
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item.get("priority_score") or 0.0),
            -int(item.get("priority") or 0),
            -(_parse_sync_timestamp(item.get("source_updated_at")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(),
            str(item.get("card_code") or ""),
        )
    )
    selected_limit = max(int(limit), 0) if limit is not None else DEFAULT_INCREMENTAL_SYNC_LIMIT
    selected = candidates[:selected_limit] if selected_limit > 0 else candidates
    remaining = candidates[len(selected):]
    selected_reason_counts: dict[str, int] = {}
    selected_bucket_counts: dict[str, int] = {}
    for item in selected:
        selected_reason_counts[item["reason"]] = selected_reason_counts.get(item["reason"], 0) + 1
        selected_bucket = str(item.get("priority_bucket") or "")
        selected_bucket_counts[selected_bucket] = selected_bucket_counts.get(selected_bucket, 0) + 1
    remaining_reason_counts: dict[str, int] = {}
    remaining_bucket_counts: dict[str, int] = {}
    for item in remaining:
        remaining_reason_counts[item["reason"]] = remaining_reason_counts.get(item["reason"], 0) + 1
        remaining_bucket = str(item.get("priority_bucket") or "")
        remaining_bucket_counts[remaining_bucket] = remaining_bucket_counts.get(remaining_bucket, 0) + 1
    return {
        "candidates": candidates,
        "selected": selected,
        "candidate_count": len(candidates),
        "remaining_count": max(len(candidates) - len(selected), 0),
        "reason_counts": reason_counts,
        "priority_bucket_counts": bucket_counts,
        "selected_reason_counts": selected_reason_counts,
        "selected_priority_bucket_counts": selected_bucket_counts,
        "remaining_reason_counts": remaining_reason_counts,
        "remaining_priority_bucket_counts": remaining_bucket_counts,
        "top_pending_candidates": [
            {
                "card_code": str(item.get("card_code") or ""),
                "reason": str(item.get("reason") or ""),
                "priority_score": float(item.get("priority_score") or 0.0),
                "priority_bucket": str(item.get("priority_bucket") or ""),
                "priority_summary": str(item.get("priority_summary") or ""),
                "sections": list(item.get("sections") or []),
                "priority_objectives": list(item.get("priority_objectives") or []),
                "selection_reasons": list(item.get("selection_reasons") or []),
            }
            for item in candidates[:10]
        ],
        "top_remaining_candidates": [
            {
                "card_code": str(item.get("card_code") or ""),
                "reason": str(item.get("reason") or ""),
                "priority_score": float(item.get("priority_score") or 0.0),
                "priority_bucket": str(item.get("priority_bucket") or ""),
                "priority_summary": str(item.get("priority_summary") or ""),
                "sections": list(item.get("sections") or []),
                "priority_objectives": list(item.get("priority_objectives") or []),
                "selection_reasons": list(item.get("selection_reasons") or []),
            }
            for item in remaining[:10]
        ],
        "top_selected_candidates": [
            {
                "card_code": str(item.get("card_code") or ""),
                "reason": str(item.get("reason") or ""),
                "priority_score": float(item.get("priority_score") or 0.0),
                "priority_bucket": str(item.get("priority_bucket") or ""),
                "priority_summary": str(item.get("priority_summary") or ""),
                "sections": list(item.get("sections") or []),
                "priority_objectives": list(item.get("priority_objectives") or []),
                "selection_reasons": list(item.get("selection_reasons") or []),
            }
            for item in selected[:10]
        ],
        "limit_applied": selected_limit,
    }


def _ensure_dossier_trivia_column(conn: sqlite3.Connection) -> None:
    """Add trivia column to learning_dossiers if missing (minimal schema extension)."""
    try:
        info = conn.execute("PRAGMA table_info(learning_dossiers)").fetchall()
        names = [row[1] for row in info]
        if "trivia" not in names:
            conn.execute("ALTER TABLE learning_dossiers ADD COLUMN trivia TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass


def _load_runtime_dossiers(runtime_db_path: str | Path) -> list[dict[str, Any]]:
    path = Path(runtime_db_path)
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_dossier_trivia_column(conn)
        rows = conn.execute(
            """
            SELECT
                card_code,
                card_name,
                set_code,
                rarity,
                basic_facts_json,
                source_summary,
                confidence,
                verification_state,
                updated_at,
                COALESCE(trivia, '') AS trivia
            FROM learning_dossiers
            WHERE verification_state IN ('verified', 'source-backed')
            ORDER BY updated_at DESC, card_code ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _load_dossier_source_counts(runtime_db_path: str | Path) -> dict[str, int]:
    """Load card_code -> count of distinct source_id from learning_dossier_sources. For confidence-aware insight selection."""
    path = Path(runtime_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'learning_dossier_sources'"
            ).fetchone()
            if not table:
                return {}
            rows = conn.execute(
                """
                SELECT card_code, COUNT(DISTINCT source_id) AS n
                FROM learning_dossier_sources
                WHERE TRIM(COALESCE(card_code, '')) != ''
                GROUP BY card_code
                """
            ).fetchall()
        return {str(row["card_code"] or "").strip().upper(): int(row["n"] or 0) for row in rows if row["card_code"]}
    except Exception:
        return {}


def _load_watch_prices(prices_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(prices_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    by_code: dict[str, dict[str, Any]] = {}
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        card_code = str(item.get("code") or "").strip().upper()
        if card_code:
            by_code[card_code] = dict(item)
    return by_code


def _load_card_intelligence_rows(project_db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(project_db_path)
    if not path.is_file():
        return {}
    ensure_catalog_sync_schema(path)
    with closing(connect_catalog_db(path)) as conn:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'card_intelligence'"
        ).fetchone()
        if table_row is None:
            return {}
        rows = conn.execute(
            """
            SELECT
                c.canonical_code,
                ci.role_label,
                ci.role_summary,
                ci.deck_usage_summary,
                ci.price_value,
                ci.price_currency,
                ci.price_source,
                ci.price_url,
                ci.updated_at
            FROM cards c
            LEFT JOIN card_intelligence ci
                ON ci.card_id = c.id
            """
        ).fetchall()
    return {
        str(row["canonical_code"] or "").strip().upper(): dict(row)
        for row in rows
        if str(row["canonical_code"] or "").strip()
    }


def _load_usage_from_deck_intel(deck_intel_db_path: str | Path) -> dict[str, dict[str, Any]]:
    """
    Aggregate leader_card_signals by card_code to get usage/role summaries.
    Used when card_intelligence is missing in the catalog. Permitted source: existing pipeline data.
    """
    path = Path(deck_intel_db_path)
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'leader_card_signals'"
            ).fetchone()
            if not table:
                return {}
            rows = conn.execute(
                """
                SELECT
                    card_code,
                    role_label,
                    SUM(deck_count) AS total_decks,
                    COUNT(DISTINCT leader_code) AS leader_count,
                    AVG(usage_percent) AS avg_usage
                FROM leader_card_signals
                WHERE TRIM(COALESCE(card_code, '')) != ''
                GROUP BY card_code, role_label
                """
            ).fetchall()
    except Exception:
        return {}
    by_code: dict[str, list[tuple[str, int, float]]] = {}
    for row in rows:
        code = str(row["card_code"] or "").strip().upper()
        if not code:
            continue
        role = str(row["role_label"] or "").strip().lower() or "tech"
        leaders = int(row["leader_count"] or 0)
        avg_usage = float(row["avg_usage"] or 0)
        by_code.setdefault(code, []).append((role, leaders, avg_usage))
    out: dict[str, dict[str, Any]] = {}
    for code, role_list in by_code.items():
        if not role_list:
            continue
        parts = []
        total_leader_appearances = 0
        max_usage = 0.0
        for role, leader_count, avg_usage in sorted(role_list, key=lambda x: -x[1]):
            if leader_count <= 0:
                continue
            total_leader_appearances += leader_count
            if avg_usage > max_usage:
                max_usage = avg_usage
            pct = f" ({int(round(avg_usage * 100))}% inclusion)" if avg_usage > 0.1 else ""
            if role == "core":
                parts.append(f"core in {leader_count} leader{'s' if leader_count != 1 else ''}{pct}")
            elif role == "flex":
                parts.append(f"flex in {leader_count} leader{'s' if leader_count != 1 else ''}{pct}")
            elif role == "tech":
                parts.append(f"tech in {leader_count} leader{'s' if leader_count != 1 else ''}{pct}")
        if not parts:
            continue
        # Relevance label: how relevant is this card right now? (core/common/niche/tech/weak)
        if max_usage >= 0.35 and total_leader_appearances >= 3:
            relevance = "core"
        elif max_usage >= 0.2 or total_leader_appearances >= 2:
            relevance = "common"
        elif total_leader_appearances == 1 and max_usage < 0.15:
            relevance = "weak signal"
        else:
            relevance = "niche"
        summary = "Relevant in current meta: " + ", ".join(parts[:3]) + "."
        if relevance == "weak signal":
            summary = "Weak meta signal: " + ", ".join(parts[:2]) + "."
        elif relevance == "niche":
            summary = "Niche in current meta: " + ", ".join(parts[:3]) + "."
        out[code] = {
            "deck_usage_summary": summary,
            "role_summary": "Deck intel: " + ", ".join(parts[:2]) + "." if parts else "",
        }
    return out


def _merge_card_intelligence(
    catalog_intel: dict[str, dict[str, Any]],
    deck_intel: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge catalog (wins) with deck-intel fallback. Keys are canonical card codes."""
    merged = dict(deck_intel)
    for code, row in catalog_intel.items():
        if not code:
            continue
        if row.get("deck_usage_summary") or row.get("role_summary"):
            merged[code] = dict(row)
    return merged


def enrich_card_intelligence_from_deck_intel(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    deck_intel_db_path: str | Path = DEFAULT_DECK_INTEL_DB_PATH,
) -> dict[str, Any]:
    """
    Bulk enrichment: write aggregated deck-intel meta/usage into catalog card_intelligence
    so sync and API have persistent meta without re-reading deck intel every time.
    Worktree-safe; uses only existing pipeline data.
    """
    project_path = Path(project_db_path)
    deck_path = Path(deck_intel_db_path)
    result: dict[str, Any] = {
        "cards_enriched": 0,
        "cards_skipped_no_catalog": 0,
        "deck_intel_cards": 0,
        "ok": True,
    }
    if not deck_path.is_file():
        result["deck_intel_present"] = False
        return result
    result["deck_intel_present"] = True
    deck_intel = _load_usage_from_deck_intel(deck_path)
    result["deck_intel_cards"] = len(deck_intel)
    if not deck_intel:
        return result
    ensure_catalog_sync_schema(project_path)
    if not project_path.is_file():
        return result
    updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with closing(connect_catalog_db(project_path)) as conn:
        for code, row in deck_intel.items():
            if not code or not (row.get("deck_usage_summary") or row.get("role_summary")):
                continue
            canonical = normalize_card_code(code).get("canonical_code") or code
            r = conn.execute(
                "SELECT id FROM cards WHERE canonical_code = ?",
                (canonical.strip().upper(),),
            ).fetchone()
            if not r:
                result["cards_skipped_no_catalog"] = result.get("cards_skipped_no_catalog", 0) + 1
                continue
            card_id = int(r[0])
            role_summary = str(row.get("role_summary") or "").strip() or ""
            deck_usage_summary = str(row.get("deck_usage_summary") or "").strip() or ""
            conn.execute(
                """
                INSERT INTO card_intelligence (card_id, role_label, role_summary, deck_usage_summary, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                    role_summary = excluded.role_summary,
                    deck_usage_summary = excluded.deck_usage_summary,
                    updated_at = excluded.updated_at
                """,
                (card_id, "", role_summary, deck_usage_summary, updated_at),
            )
            result["cards_enriched"] = result.get("cards_enriched", 0) + 1
    return result


def _safe_load_json_dict(raw_value: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_value or "{}")
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _clean_traits(raw_traits: Any) -> list[str]:
    if isinstance(raw_traits, list):
        traits = [str(item).strip() for item in raw_traits if str(item).strip()]
    else:
        traits = [str(raw_traits or "").strip()] if str(raw_traits or "").strip() else []
    # Some older synced rows store traits as split characters; collapse those back into words.
    if traits and len(traits) > 4 and all(len(item) == 1 for item in traits):
        joined = "".join(traits)
        traits = [part.strip() for part in joined.split("/") if part.strip()]
    normalized: list[str] = []
    for item in traits:
        compact = " ".join(item.split())
        if compact and compact not in normalized:
            normalized.append(compact)
    return normalized


def _is_generic_filler(text: str) -> bool:
    """True if the text is formulaic filler that does not add gameplay or market value."""
    lower = (text or "").lower()
    return any(pat in lower for pat in _GENERIC_PATTERNS)


def _is_supported_dossier_insight(insight: dict[str, Any], dossier: dict[str, Any]) -> bool:
    text = str(insight.get("text") or "").strip()
    if not text or _is_generic_filler(text):
        return False
    if text.lower() in {
        "this card is strong",
        "this card is useful in many decks",
        "this card can be valuable",
        "this card is relevant in the meta",
    }:
        return False
    used_sections = {str(item).strip() for item in list(insight.get("used_sections") or []) if str(item).strip()}
    if not used_sections:
        return False
    meta_score = dossier.get("meta_relevance_score")
    support_map = {
        "usage_meta": bool(
            str(dossier.get("deck_usage_summary") or "").strip()
            or list(dossier.get("top_leaders_used_in") or [])
            or meta_score not in (None, "")
        ),
        "gameplay_role": str(dossier.get("gameplay_role") or "").strip(),
        "top_leaders": list(dossier.get("top_leaders_used_in") or []),
        "rulings": str(dossier.get("rulings_summary") or "").strip(),
        "market": dossier.get("price_low"),
        "legality": bool(str(dossier.get("legality_note") or "").strip() or str(dossier.get("legality_state") or "").strip()),
    }
    if not all(support_map.get(section) not in ("", None, [], {}, False) for section in used_sections):
        return False
    # Voice-layer insights are dossier-backed via used_sections; do not require legacy pipeline phrases.
    return True


def _build_strict_dossier_insight_candidates(dossier: dict[str, Any], generated: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(generated.get("text") or "").strip()
    if not text:
        return []
    card_id = str(generated.get("card_id") or dossier.get("card_id") or "").strip().upper()
    base_conf = float(generated.get("confidence") or dossier.get("confidence_score") or 0.0)
    used_all = {str(item).strip() for item in list(generated.get("used_sections") or []) if str(item).strip()}
    sync_reason = str(generated.get("sync_reason") or "").strip()
    source_updated_at = str(generated.get("source_updated_at") or dossier.get("source_updated_at") or "").strip()
    leader_code = str(generated.get("leader_code") or "").strip().upper()
    source_ref = str(generated.get("source_ref") or "").strip()

    try:
        from tools.miru_insight_voice import dominant_insight_type
    except ImportError:

        def dominant_insight_type(sections: list[str]) -> str:  # type: ignore[misc]
            s = set(sections or [])
            if s & {"usage_meta", "top_leaders"}:
                return "usage"
            if "usage_meta" in s:
                return "meta"
            if "gameplay_role" in s:
                return "strength"
            if "rulings" in s:
                return "ruling"
            if "legality" in s:
                return "ruling"
            if "market" in s:
                return "price"
            return "meta"

    def _pack(itype: str, insight_text: str, sections: set[str]) -> dict[str, Any] | None:
        probe = {"text": insight_text, "used_sections": list(sections)}
        if not _is_supported_dossier_insight(probe, dossier):
            return None
        return {
            "card_id": card_id,
            "insight_type": itype,
            "insight_text": insight_text.strip(),
            "confidence": base_conf,
            "updated_at": str(dossier.get("last_verified_at") or int(time.time())),
            "source_ref": source_ref,
            "leader_code": leader_code,
            "used_sections": sorted(sections),
            "used_sections_json": json.dumps(sorted(sections), ensure_ascii=True, sort_keys=True),
            "sync_reason": sync_reason,
            "source_updated_at": source_updated_at,
        }

    selected_type = dominant_insight_type(list(used_all))
    gen_full = {"text": text, "used_sections": list(used_all)}
    if not _is_supported_dossier_insight(gen_full, dossier):
        return []
    row = _pack(selected_type, text, used_all)
    return [row] if row else []


def _upsert_catalog_dossier_projection(
    conn: sqlite3.Connection,
    *,
    canonical_code: str,
    dossier: dict[str, Any],
    sync_context: dict[str, Any] | None = None,
) -> bool:
    row = conn.execute(
        "SELECT id FROM cards WHERE canonical_code = ? LIMIT 1",
        (canonical_code,),
    ).fetchone()
    if row is None:
        return False
    card_id = int(row[0])
    conn.execute(
        """
        UPDATE cards
        SET
            card_name = CASE WHEN trim(coalesce(?, '')) != '' THEN ? ELSE card_name END,
            set_code = CASE WHEN trim(coalesce(?, '')) != '' THEN ? ELSE set_code END,
            set_name = CASE WHEN trim(coalesce(?, '')) != '' THEN ? ELSE set_name END,
            rarity = CASE WHEN trim(coalesce(?, '')) != '' THEN ? ELSE rarity END,
            effect_text = CASE WHEN trim(coalesce(?, '')) != '' THEN ? ELSE effect_text END
        WHERE id = ?
        """,
        (
            str(dossier.get("name") or "").strip(),
            str(dossier.get("name") or "").strip(),
            str(dossier.get("set_code") or "").strip(),
            str(dossier.get("set_code") or "").strip(),
            str(dossier.get("set_name") or "").strip(),
            str(dossier.get("set_name") or "").strip(),
            str(dossier.get("rarity") or "").strip(),
            str(dossier.get("rarity") or "").strip(),
            str(dossier.get("effect_text_official") or "").strip(),
            str(dossier.get("effect_text_official") or "").strip(),
            card_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO card_intelligence (
            card_id, role_label, role_summary, deck_usage_summary, price_value,
            price_currency, price_source, price_url, meta_relevance_score, top_leaders_json,
            rulings_summary, price_trend_note, confidence_score, source_summary,
            last_verified_at, legality_state, legality_note, rulings_sources_json,
            usage_profile_json, section_confidence_json, source_agreement_json,
            projection_sections_json, projection_source_updated_at, last_sync_reason, last_sync_mode,
            last_priority_score, last_priority_context_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(card_id) DO UPDATE SET
            role_label = excluded.role_label,
            role_summary = excluded.role_summary,
            deck_usage_summary = excluded.deck_usage_summary,
            price_value = excluded.price_value,
            price_currency = excluded.price_currency,
            price_source = excluded.price_source,
            price_url = excluded.price_url,
            meta_relevance_score = excluded.meta_relevance_score,
            top_leaders_json = excluded.top_leaders_json,
            rulings_summary = excluded.rulings_summary,
            price_trend_note = excluded.price_trend_note,
            confidence_score = excluded.confidence_score,
            source_summary = excluded.source_summary,
            last_verified_at = excluded.last_verified_at,
            legality_state = excluded.legality_state,
            legality_note = excluded.legality_note,
            rulings_sources_json = excluded.rulings_sources_json,
            usage_profile_json = excluded.usage_profile_json,
            section_confidence_json = excluded.section_confidence_json,
            source_agreement_json = excluded.source_agreement_json,
            projection_sections_json = excluded.projection_sections_json,
            projection_source_updated_at = excluded.projection_source_updated_at,
            last_sync_reason = excluded.last_sync_reason,
            last_sync_mode = excluded.last_sync_mode,
            last_priority_score = excluded.last_priority_score,
            last_priority_context_json = excluded.last_priority_context_json,
            updated_at = excluded.updated_at
        """,
        (
            card_id,
            str(dossier.get("gameplay_role") or "").strip(),
            (
                f"Stored gameplay role: {str(dossier.get('gameplay_role') or '').strip()}."
                if str(dossier.get("gameplay_role") or "").strip()
                else ""
            ),
            str(dossier.get("deck_usage_summary") or "").strip(),
            dossier.get("price_low"),
            "USD" if dossier.get("price_low") not in (None, "") else "",
            str(dossier.get("price_source") or ("prices.json" if dossier.get("price_low") not in (None, "") else "")).strip(),
            "",
            dossier.get("meta_relevance_score"),
            json.dumps(list(dossier.get("top_leaders_used_in") or []), ensure_ascii=True, sort_keys=True),
            str(dossier.get("rulings_summary") or "").strip(),
            str(dossier.get("price_trend_note") or "").strip(),
            float(dossier.get("confidence_score") or 0.0),
            json.dumps(dict(dossier.get("source_summary") or {}), ensure_ascii=True, sort_keys=True),
            str(dossier.get("last_verified_at") or "").strip(),
            str(dossier.get("legality_state") or "").strip(),
            str(dossier.get("legality_note") or "").strip(),
            json.dumps(list(dossier.get("rulings_sources") or []), ensure_ascii=True, sort_keys=True),
            json.dumps(
                {
                    "leader_count": int(dossier.get("leader_count") or 0),
                    "tracked_deck_count": int(dossier.get("tracked_deck_count") or 0),
                    "top_leaders": list(dossier.get("top_leaders_used_in") or []),
                    "meta_relevance_score": dossier.get("meta_relevance_score"),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            json.dumps(dict(dossier.get("section_confidence") or {}), ensure_ascii=True, sort_keys=True),
            json.dumps(dict(dossier.get("source_agreement") or {}), ensure_ascii=True, sort_keys=True),
            json.dumps(_derive_projection_sections(dossier), ensure_ascii=True, sort_keys=True),
            str((sync_context or {}).get("source_updated_at") or dossier.get("source_updated_at") or dossier.get("last_verified_at") or "").strip(),
            str((sync_context or {}).get("reason") or "").strip(),
            str((sync_context or {}).get("mode") or "").strip(),
            float((sync_context or {}).get("priority_score") or 0.0),
            json.dumps(
                {
                    "priority_bucket": str((sync_context or {}).get("priority_bucket") or "").strip(),
                    "priority_summary": str((sync_context or {}).get("priority_summary") or "").strip(),
                    "priority_factors": dict((sync_context or {}).get("priority_factors") or {}),
                    "priority_objectives": list((sync_context or {}).get("priority_objectives") or []),
                    "selection_reasons": list((sync_context or {}).get("selection_reasons") or []),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            str(dossier.get("last_verified_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
        ),
    )
    return True


# Keywords for strength/role classification from effect text (evidence-based; no fabrication).
# Aggressive: only attack/damage language; exclude "reduce life" so removal/control don't leak into aggressive.
_STRENGTH_AGGRESSIVE = ("attack", "deal", "damage", "battle", "strike")
_STRENGTH_CONTROL = ("counter", "block", "rest", "don't attack", "can't attack", "k.o.", "bottom deck", "return to hand")
_STRENGTH_SUPPORT = ("draw", "search", "add to hand", "give", "gain", "restore", "trigger", "when you play")
_STRENGTH_TEMPO = ("cost", "reduce cost", "play", "reduce by", "minus cost")
_STRENGTH_REMOVAL = ("destroy", "remove", "k.o.", "bottom deck", "return to hand", "minus", "reduce life")
_STRENGTH_ENGINE = ("draw", "search", "add to hand", "when you play", "trigger", "reduce cost", "play for")
_STRENGTH_DEFENSIVE = ("block", "counter", "rest", "life", "restore", "don't attack", "can't attack")
_STRENGTH_PRESSURE = ("attack", "deal", "damage", "battle", "strike", "minus")


def _derive_strength_tags(effect_text: str) -> list[str]:
    """Derive role/strength tags from effect text when keywords appear. Removal/control stay out of aggressive."""
    lower = (effect_text or "").lower()
    if len(lower) < 15:
        return []
    tags: list[str] = []
    if any(k in lower for k in _STRENGTH_AGGRESSIVE):
        tags.append("aggressive")
    if any(k in lower for k in _STRENGTH_CONTROL):
        tags.append("control")
    if any(k in lower for k in _STRENGTH_SUPPORT):
        tags.append("support")
    if any(k in lower for k in _STRENGTH_TEMPO):
        tags.append("tempo")
    if any(k in lower for k in _STRENGTH_REMOVAL) and "removal" not in tags:
        tags.append("removal support")
    if any(k in lower for k in _STRENGTH_ENGINE) and "engine" not in tags:
        tags.append("engine piece")
    if any(k in lower for k in _STRENGTH_DEFENSIVE) and "defensive" not in tags:
        tags.append("defensive")
    if any(k in lower for k in _STRENGTH_PRESSURE) and "pressure" not in tags:
        tags.append("pressure")
    if ("counter" in lower or "block" in lower) and "utility" not in tags:
        tags.append("utility")
    return tags[:4]


def _build_strength_insight(dossier: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any] | None:
    """Gameplay strength/role from effect-text keywords only; evidence-based. Stronger phrasing when meta-bearing + two-source."""
    effect_text = str(facts.get("effect_text") or "").strip()
    tags = _derive_strength_tags(effect_text)
    if not tags:
        return None
    card_name = str(dossier.get("card_name") or "").strip()
    label = ", ".join(tags)
    source_count = int(dossier.get("source_count") or 1)
    meta_bearing = bool(dossier.get("meta_bearing"))
    cap = 0.87 if (meta_bearing and source_count >= 2) else (0.85 if source_count >= 2 else 0.82)
    confidence = max(0.0, min(float(dossier.get("confidence") or 0.0) * 0.75, cap))
    if meta_bearing and source_count >= 2:
        text = f"In competitive contexts, {card_name or 'This card'} reads as {label} from its effect text."
    else:
        text = f"{card_name or 'This card'} reads as {label} from its effect text."
    return {"type": "strength", "text": text, "confidence": round(confidence, 2)}


def _build_synergy_insight(dossier: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any] | None:
    """Gameplay synergy: trait-based or deck-affiliation only. No effect-role branch (that duplicates strength)."""
    card_name = str(dossier.get("card_name") or "").strip()
    traits = _clean_traits(facts.get("traits"))
    effect_text = str(facts.get("effect_text") or "").strip()
    source_count = int(dossier.get("source_count") or 1)
    cap = 0.94 if source_count >= 2 else 0.92
    confidence = max(0.0, min(float(dossier.get("confidence") or 0.0) * 0.9, cap))
    if traits and len(traits) >= 1:
        trait_label = ", ".join(traits[:2])
        text = f"{card_name or 'This card'} lines up with {trait_label} shells."
        if _is_generic_filler(text):
            return None
        return {"type": "synergy", "text": text, "confidence": round(confidence, 2)}
    if effect_text and len(effect_text) > 40:
        text = f"{card_name or 'This card'} works best when built around its on-card effect rather than as generic filler."
        if _is_generic_filler(text):
            return None
        return {"type": "synergy", "text": text, "confidence": round(confidence, 2)}
    return None


def _build_lore_insight(dossier: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any] | None:
    """Spoiler-free trivia only when it adds charm; skip generic set/trait anchoring."""
    trivia = str(dossier.get("trivia") or facts.get("trivia") or "").strip()
    if not trivia or len(trivia) < 20:
        return None
    confidence = max(0.0, min(float(dossier.get("confidence") or 0.0) * 0.82, 0.86))
    return {"type": "lore", "text": trivia[:280] + ("..." if len(trivia) > 280 else ""), "confidence": round(confidence, 2)}


def _build_price_insight(
    card_code: str,
    dossier: dict[str, Any],
    price_lookup: dict[str, dict[str, Any]],
    intelligence_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Price level only when we have real price data; restrained when single data point; no invented trends."""
    price_item = dict(price_lookup.get(card_code) or {})
    price_value = price_item.get("price")
    if price_value in (None, "", 0):
        price_value = (intelligence_row or {}).get("price_value")
    try:
        normalized_price = float(price_value)
    except (TypeError, ValueError):
        return None
    if normalized_price <= 0:
        return None
    card_name = str(dossier.get("card_name") or "").strip()
    has_trend = bool(price_item.get("trend") or price_item.get("history") or price_item.get("change"))
    if has_trend:
        text = f"Watch data last saw {card_name or card_code} around ${normalized_price:.2f}."
    else:
        text = f"Watch data last saw {card_name or card_code} around ${normalized_price:.2f}. Single data point; no trend."
    return {"type": "price", "text": text, "confidence": 0.58 if not has_trend else 0.62}


def _build_meta_insight(
    card_code: str,
    dossier: dict[str, Any],
    intelligence_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Meta relevancy / usage: only when we have verified deck or role data; no filler. Stronger evidence = higher confidence cap."""
    row = intelligence_row or {}
    role_summary = str(row.get("role_summary") or "").strip()
    deck_usage_summary = str(row.get("deck_usage_summary") or "").strip()
    if deck_usage_summary and "unavailable" not in deck_usage_summary.lower():
        text = deck_usage_summary
    elif role_summary and "still needs" not in role_summary.lower() and "unavailable" not in role_summary.lower():
        text = role_summary
    else:
        return None
    if not text or len(text) < 15:
        return None
    source_count = int(dossier.get("source_count") or 1)
    cap = 0.82 if source_count >= 2 else 0.79
    confidence = round(max(0.55, min(float(dossier.get("confidence") or 0.0) * 0.7, cap)), 2)
    return {
        "type": "meta",
        "text": text if text.endswith(".") else f"{text}.",
        "confidence": confidence,
    }


def build_card_insight_candidates(
    dossier: dict[str, Any],
    *,
    price_lookup: dict[str, dict[str, Any]] | None = None,
    intelligence_row: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    card_code = str(dossier.get("card_code") or "").strip().upper()
    facts = _safe_load_json_dict(str(dossier.get("basic_facts_json") or "{}"))
    candidates = [
        _build_meta_insight(card_code, dossier, intelligence_row),
        _build_lore_insight(dossier, facts),
        _build_price_insight(card_code, dossier, price_lookup or {}, intelligence_row),
        _build_strength_insight(dossier, facts),
        _build_synergy_insight(dossier, facts),
    ]
    results: list[dict[str, Any]] = []
    from tools.miru_ethics_gates import check_insight_confidence_gate
    for item in candidates:
        if not item or item["type"] not in INSIGHT_TYPES:
            continue
        if not str(item.get("text") or "").strip():
            continue
        conf = float(item.get("confidence") or 0.0)
        if conf < MIN_INSIGHT_CONFIDENCE:
            check_insight_confidence_gate(conf, MIN_INSIGHT_CONFIDENCE, card_id=card_code, insight_type=str(item.get("type", "")))
            continue
        results.append(
            {
                "card_id": card_code,
                "insight_type": str(item["type"]),
                "insight_text": str(item["text"]).strip(),
                "confidence": conf,
                "updated_at": str(dossier.get("updated_at") or ""),
            }
        )
    return results


def sync_miru_card_insights(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
    prices_path: str | Path = DEFAULT_PROJECT_PRICES_PATH,
    deck_intel_db_path: str | Path | None = None,
    log_path: str | Path = DEFAULT_SYNC_LOG_PATH,
    limit: int | None = None,
    force_rebuild: bool = False,
    only_card_codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Sync dossier-backed projections and strict insights into card_catalog.db.

    Default behavior is an incremental bounded pass over the highest-value dirty
    cards first. force_rebuild=True retains the full rebuild path.

    When ``only_card_codes`` is not None, only those card codes are processed
    (bounded safe pass for operator-driven publication population). Ignored when
    ``force_rebuild`` is True.
    """
    project_path = Path(project_db_path)
    runtime_path = Path(runtime_dossier_db_path)
    canonical_path = Path(DEFAULT_CANONICAL_DOSSIER_DB_PATH)
    if not canonical_path.is_file():
        canonical_path = runtime_path
    ensure_catalog_sync_schema(project_path)
    dossier_store = MiruDossierStore(canonical_path)
    if canonical_path == Path(DEFAULT_CANONICAL_DOSSIER_DB_PATH):
        dossier_store.ensure_schema()
    sync_mode = "force_rebuild" if force_rebuild else "incremental"
    plan: dict[str, Any]
    if force_rebuild:
        dossiers = _load_runtime_dossiers(runtime_path)
        ordered_codes = [str(item.get("card_code") or "").strip().upper() for item in dossiers if str(item.get("card_code") or "").strip()]
        if canonical_path.is_file():
            with closing(sqlite3.connect(canonical_path)) as conn:
                card_column = "canonical_code"
                try:
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
                except sqlite3.Error:
                    cols = set()
                if "card_code" in cols:
                    card_column = "card_code"
                rows = conn.execute(f"SELECT {card_column} FROM cards ORDER BY updated_at DESC").fetchall()
                canonical_codes = [str(row[0] or "").strip().upper() for row in rows if str(row[0] or "").strip()]
                seen_codes = set(ordered_codes)
                for code in canonical_codes:
                    if code in seen_codes:
                        continue
                    seen_codes.add(code)
                    ordered_codes.append(code)
        if limit is not None:
            ordered_codes = ordered_codes[: max(int(limit), 0)]
        selected_candidates = [
            {
                "card_code": code,
                "reason": "force_rebuild",
                "sections": [],
                "source_updated_at": "",
                "mode": sync_mode,
                "priority_score": 999.0,
                "priority_bucket": "critical",
                "priority_summary": "force_rebuild: explicit full refresh request.",
                "priority_factors": {"reason": "force_rebuild", "base_priority": 999},
            }
            for code in ordered_codes
        ]
        plan = {
            "candidate_count": len(ordered_codes),
            "remaining_count": 0,
            "reason_counts": {"force_rebuild": len(ordered_codes)},
            "priority_bucket_counts": {"critical": len(ordered_codes)} if ordered_codes else {},
            "remaining_reason_counts": {},
            "remaining_priority_bucket_counts": {},
            "selected_reason_counts": {"force_rebuild": len(ordered_codes)},
            "selected_priority_bucket_counts": {"critical": len(ordered_codes)} if ordered_codes else {},
            "top_pending_candidates": [],
            "top_remaining_candidates": [],
            "top_selected_candidates": [
                {
                    "card_code": code,
                    "reason": "force_rebuild",
                    "priority_score": 999.0,
                    "priority_bucket": "critical",
                    "priority_summary": "force_rebuild: explicit full refresh request.",
                    "sections": [],
                }
                for code in ordered_codes[:10]
            ],
            "limit_applied": len(ordered_codes),
        }
    elif not force_rebuild and only_card_codes is not None:
        sync_mode = "publish_eligible_allowlist"
        seen_codes: set[str] = set()
        ordered_codes: list[str] = []
        for raw in only_card_codes:
            parsed = normalize_card_code(str(raw or "").strip())
            code = str(parsed.get("canonical_code") or "").strip().upper() if isinstance(parsed, dict) else str(parsed or "").strip().upper()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            ordered_codes.append(code)
        if limit is not None:
            ordered_codes = ordered_codes[: max(int(limit), 0)]
        selected_candidates = [
            {
                "card_code": code,
                "reason": "publish_eligible_allowlist",
                "sections": [],
                "source_updated_at": "",
                "mode": sync_mode,
                "priority_score": 800.0,
                "priority_bucket": "high",
                "priority_summary": "Bounded allowlist sync: explicit card codes only (operator-safe publication path).",
                "priority_factors": {"reason": "publish_eligible_allowlist", "base_priority": 800},
            }
            for code in ordered_codes
        ]
        plan = {
            "candidate_count": len(ordered_codes),
            "remaining_count": 0,
            "reason_counts": {"publish_eligible_allowlist": len(ordered_codes)},
            "priority_bucket_counts": {"high": len(ordered_codes)} if ordered_codes else {},
            "remaining_reason_counts": {},
            "remaining_priority_bucket_counts": {},
            "selected_reason_counts": {"publish_eligible_allowlist": len(ordered_codes)},
            "selected_priority_bucket_counts": {"high": len(ordered_codes)} if ordered_codes else {},
            "top_pending_candidates": [],
            "top_remaining_candidates": [],
            "top_selected_candidates": [
                {
                    "card_code": code,
                    "reason": "publish_eligible_allowlist",
                    "priority_score": 800.0,
                    "priority_bucket": "high",
                    "priority_summary": "Bounded allowlist sync: explicit card codes only.",
                    "sections": [],
                }
                for code in ordered_codes[:10]
            ],
            "limit_applied": len(ordered_codes),
        }
    else:
        plan = _collect_incremental_sync_candidates(
            project_db_path=project_path,
            runtime_dossier_db_path=runtime_path,
            canonical_dossier_db_path=canonical_path,
            rules_db_path=DEFAULT_RULES_DB_PATH,
            deck_intel_db_path=Path(deck_intel_db_path or DEFAULT_DECK_INTEL_DB_PATH),
            prices_path=Path(prices_path),
            limit=limit,
        )
        selected_candidates = [
            {
                **item,
                "mode": sync_mode,
            }
            for item in list(plan.get("selected") or [])
        ]
    synced_cards = 0
    written_insights = 0
    skipped_cards = 0
    projected_cards = 0

    preserved_insights = 0
    replaced_insights = 0
    inserted_insights = 0
    deleted_before_rebuild = 0
    purged_legacy_insights = 0
    by_type: dict[str, int] = {}  # counts per insight_type written this run
    processed_reason_counts: dict[str, int] = {}
    selected_codes = [str(item.get("card_code") or "").strip().upper() for item in selected_candidates if str(item.get("card_code") or "").strip()]

    with closing(connect_catalog_db(project_path)) as conn:
        if force_rebuild:
            row = conn.execute("SELECT COUNT(*) FROM miru_card_insights").fetchone()
            deleted_before_rebuild = int(row[0] if row else 0)
            conn.execute("DELETE FROM miru_card_insights")
            # In autocommit (isolation_level=None) mode the DELETE above commits
            # immediately.  Open an explicit transaction so the rebuild inserts
            # are batched into a single commit for performance.
            conn.execute("BEGIN")
        for candidate in selected_candidates:
            card_code = str(candidate.get("card_code") or "").strip().upper()
            if not card_code:
                skipped_cards += 1
                continue
            processed_reason_counts[str(candidate.get("reason") or "").strip() or "unknown"] = processed_reason_counts.get(str(candidate.get("reason") or "").strip() or "unknown", 0) + 1
            canonical_dossier = dossier_store.build_card_dossier(
                card_code,
                learning_db_path=runtime_path,
                rules_db_path=DEFAULT_RULES_DB_PATH,
                deck_intel_db_path=deck_intel_db_path or DEFAULT_DECK_INTEL_DB_PATH,
                catalog_db_path=project_path,
                prices_path=prices_path,
            )
            if not canonical_dossier.get("available"):
                # No new candidates — preserve whatever exists (never blind-delete).
                skipped_cards += 1
                continue
            if _upsert_catalog_dossier_projection(conn, canonical_code=card_code, dossier=canonical_dossier, sync_context=candidate):
                projected_cards += 1
            generated = dossier_store.generate_card_insight(
                card_code,
                learning_db_path=runtime_path,
                rules_db_path=DEFAULT_RULES_DB_PATH,
                deck_intel_db_path=deck_intel_db_path or DEFAULT_DECK_INTEL_DB_PATH,
                catalog_db_path=project_path,
                prices_path=prices_path,
                dossier=canonical_dossier,
            )
            generated["sync_reason"] = str(candidate.get("reason") or "").strip()
            generated["source_updated_at"] = str(candidate.get("source_updated_at") or canonical_dossier.get("source_updated_at") or "").strip()
            candidates = _build_strict_dossier_insight_candidates(canonical_dossier, generated)
            candidates = [
                item
                for item in candidates
                if _is_supported_dossier_insight(
                    {"text": item.get("insight_text"), "used_sections": item.get("used_sections")},
                    canonical_dossier,
                )
                and float(item.get("confidence") or 0.0) >= MIN_INSIGHT_CONFIDENCE
            ]
            if not candidates:
                skipped_cards += 1
                continue
            purge_cursor = conn.execute(
                """
                DELETE FROM miru_card_insights
                WHERE card_id = ?
                  AND trim(coalesce(source_ref, '')) = ''
                  AND coalesce(generated_at, 0) = 0
                """,
                (card_code,),
            )
            purged_legacy_insights += int(purge_cursor.rowcount or 0)

            # Load existing insights only when we need them for the replace guard.
            existing: dict[str, dict[str, Any]] = {}
            if not force_rebuild:
                for row in conn.execute(
                    "SELECT insight_type, insight_text, confidence, quality_tier, source_ref, leader_code "
                    "FROM miru_card_insights WHERE card_id = ?",
                    (card_code,),
                ).fetchall():
                    existing[row["insight_type"]] = {
                        "text": row["insight_text"],
                        "confidence": float(row["confidence"]),
                        "quality_tier": row["quality_tier"] or "",
                        "source_ref": row["source_ref"] or "",
                        "leader_code": row["leader_code"] or "",
                    }

            card_wrote = False
            for item in candidates:
                itype      = str(item["insight_type"])
                new_text   = str(item["insight_text"]).strip()
                new_conf   = float(item.get("confidence") or 0.0)
                new_tier   = classify_insight_quality(new_text, new_conf)
                updated_at = item.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S")
                source_ref = str(item.get("source_ref") or "").strip()
                leader_code = str(item.get("leader_code") or "").strip().upper()
                used_sections_json = str(item.get("used_sections_json") or "[]")
                sync_reason = str(item.get("sync_reason") or "").strip()
                source_updated_at = str(item.get("source_updated_at") or "").strip()
                generated_at = int(time.time())

                if force_rebuild:
                    # Table was wiped — always insert fresh; skip replace guard.
                    conn.execute(
                        "INSERT INTO miru_card_insights "
                        "(card_id, insight_type, insight_text, confidence, quality_tier, source_ref, leader_code, used_sections_json, sync_reason, source_updated_at, generated_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (card_code, itype, new_text, new_conf, new_tier, source_ref, leader_code, used_sections_json, sync_reason, source_updated_at, generated_at, updated_at),
                    )
                    inserted_insights += 1
                    by_type[itype] = by_type.get(itype, 0) + 1
                    card_wrote = True
                    continue

                prev = existing.get(itype)
                if prev is not None:
                    # Re-classify existing if it has no stored tier (backfill).
                    existing_tier = prev["quality_tier"] or classify_insight_quality(
                        prev["text"], prev["confidence"]
                    )
                    if not should_replace_insight(
                        existing_tier, prev["confidence"],
                        new_tier, new_conf,
                    ):
                        preserved_insights += 1
                        backfill_fields: list[str] = []
                        backfill_values: list[Any] = []
                        if not prev["quality_tier"]:
                            backfill_fields.append("quality_tier = ?")
                            backfill_values.append(existing_tier)
                        if source_ref and not prev["source_ref"]:
                            backfill_fields.append("source_ref = ?")
                            backfill_values.append(source_ref)
                        if leader_code and not prev["leader_code"]:
                            backfill_fields.append("leader_code = ?")
                            backfill_values.append(leader_code)
                        if used_sections_json and used_sections_json != "[]":
                            backfill_fields.append("used_sections_json = ?")
                            backfill_values.append(used_sections_json)
                        if sync_reason:
                            backfill_fields.append("sync_reason = ?")
                            backfill_values.append(sync_reason)
                        if source_updated_at:
                            backfill_fields.append("source_updated_at = ?")
                            backfill_values.append(source_updated_at)
                        if backfill_fields:
                            conn.execute(
                                f"UPDATE miru_card_insights SET {', '.join(backfill_fields)} "
                                "WHERE card_id = ? AND insight_type = ?",
                                tuple(backfill_values + [card_code, itype]),
                            )
                        continue

                    # Replace — existing is weaker.
                    conn.execute(
                        "UPDATE miru_card_insights "
                        "SET insight_text = ?, confidence = ?, quality_tier = ?, source_ref = ?, leader_code = ?, used_sections_json = ?, sync_reason = ?, source_updated_at = ?, generated_at = ?, updated_at = ? "
                        "WHERE card_id = ? AND insight_type = ?",
                        (new_text, new_conf, new_tier, source_ref, leader_code, used_sections_json, sync_reason, source_updated_at, generated_at, updated_at, card_code, itype),
                    )
                    replaced_insights += 1
                    by_type[itype] = by_type.get(itype, 0) + 1
                    card_wrote = True
                else:
                    # No existing insight for this type — insert.
                    conn.execute(
                        "INSERT INTO miru_card_insights "
                        "(card_id, insight_type, insight_text, confidence, quality_tier, source_ref, leader_code, used_sections_json, sync_reason, source_updated_at, generated_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (card_code, itype, new_text, new_conf, new_tier, source_ref, leader_code, used_sections_json, sync_reason, source_updated_at, generated_at, updated_at),
                    )
                    inserted_insights += 1
                    by_type[itype] = by_type.get(itype, 0) + 1
                    card_wrote = True

            if card_wrote:
                synced_cards += 1
                written_insights += 1
            else:
                # All candidates were weaker — card unchanged but still processed.
                synced_cards += 1

        if force_rebuild:
            conn.execute("COMMIT")
        _store_sync_metadata(
            conn,
            sync_key="miru_card_insights",
            payload={
                "sync_mode": sync_mode,
                "limit_applied": plan.get("limit_applied"),
                "candidate_count": plan.get("candidate_count"),
                "remaining_count": plan.get("remaining_count"),
                "reason_counts": plan.get("reason_counts"),
                "priority_bucket_counts": plan.get("priority_bucket_counts"),
                "remaining_reason_counts": plan.get("remaining_reason_counts"),
                "remaining_priority_bucket_counts": plan.get("remaining_priority_bucket_counts"),
                "selected_reason_counts": plan.get("selected_reason_counts"),
                "selected_priority_bucket_counts": plan.get("selected_priority_bucket_counts"),
                "top_pending_candidates": plan.get("top_pending_candidates"),
                "top_remaining_candidates": plan.get("top_remaining_candidates"),
                "top_selected_candidates": plan.get("top_selected_candidates"),
                "processed_reason_counts": processed_reason_counts,
                "selected_card_count": len(selected_codes),
                "selected_card_sample": selected_codes[:12],
                "synced_cards": synced_cards,
                "projected_cards": projected_cards,
                "written_insights": written_insights,
                "inserted_insights": inserted_insights,
                "replaced_insights": replaced_insights,
                "preserved_insights": preserved_insights,
                "skipped_cards": skipped_cards,
                "updated_at": _utc_now_timestamp(),
            },
        )

    status = load_miru_card_insight_status(project_db_path=project_path, runtime_dossier_db_path=runtime_path)
    _append_sync_log(
        log_path,
        (
            f"miru_card_insights sync complete: mode={sync_mode} "
            f"selected_cards={len(selected_codes)} candidate_count={plan.get('candidate_count', 0)} "
            f"remaining_candidates={plan.get('remaining_count', 0)} "
            f"synced_cards={synced_cards} "
            f"projected_cards={projected_cards} "
            f"inserted={inserted_insights} replaced={replaced_insights} "
            f"preserved={preserved_insights} skipped_cards={skipped_cards} "
            f"purged_legacy_insights={purged_legacy_insights} "
            f"deleted_before_rebuild={deleted_before_rebuild} "
            f"by_type={by_type} selected_reason_counts={plan.get('selected_reason_counts', {})} "
            f"selected_priority_buckets={plan.get('selected_priority_bucket_counts', {})} "
            f"project_db={project_path} runtime_db={runtime_path}"
        ),
    )
    return {
        "ok": True,
        "sync_mode": sync_mode,
        "candidate_count": int(plan.get("candidate_count") or 0),
        "remaining_count": int(plan.get("remaining_count") or 0),
        "limit_applied": int(plan.get("limit_applied") or 0),
        "selected_card_count": len(selected_codes),
        "selected_reason_counts": dict(plan.get("selected_reason_counts") or {}),
        "selected_priority_bucket_counts": dict(plan.get("selected_priority_bucket_counts") or {}),
        "top_selected_candidates": list(plan.get("top_selected_candidates") or []),
        "synced_cards": synced_cards,
        "written_insights": written_insights,
        "inserted_insights": inserted_insights,
        "replaced_insights": replaced_insights,
        "preserved_insights": preserved_insights,
        "skipped_cards": skipped_cards,
        "projected_cards": projected_cards,
        "purged_legacy_insights": purged_legacy_insights,
        "deleted_before_rebuild": deleted_before_rebuild,
        "by_type": by_type,
        "status": status,
    }


def load_miru_card_insight_status(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    runtime_dossier_db_path: str | Path = DEFAULT_RUNTIME_DOSSIER_DB_PATH,
) -> dict[str, Any]:
    project_path = Path(project_db_path)
    runtime_path = Path(runtime_dossier_db_path)
    ensure_catalog_sync_schema(project_path)
    with closing(connect_catalog_db(project_path)) as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS insight_count,
                MAX(updated_at) AS last_sync_time
            FROM miru_card_insights
            """
        ).fetchone()
        sync_summary = _load_sync_metadata(conn, sync_key="miru_card_insights")
    return {
        "connected": project_path.is_file() and runtime_path.is_file(),
        "sync_running": False,
        "last_sync_time": str(sync_summary.get("updated_at") or (counts["last_sync_time"] if counts else "") or ""),
        "insight_count": int((counts["insight_count"] if counts else 0) or 0),
        "sync_mode": str(sync_summary.get("sync_mode") or ""),
        "remaining_candidate_count": int(sync_summary.get("remaining_count") or 0),
        "pending_reason_counts": dict(sync_summary.get("remaining_reason_counts") or {}),
        "pending_priority_bucket_counts": dict(sync_summary.get("remaining_priority_bucket_counts") or {}),
        "last_selected_reason_counts": dict(sync_summary.get("selected_reason_counts") or {}),
        "last_selected_priority_bucket_counts": dict(sync_summary.get("selected_priority_bucket_counts") or {}),
        "top_pending_candidates": list(sync_summary.get("top_remaining_candidates") or []),
        "top_selected_candidates": list(sync_summary.get("top_selected_candidates") or []),
        "last_prioritized_sync_reason": str(
            ((sync_summary.get("top_selected_candidates") or [{}])[0] or {}).get("reason") or ""
        ),
        "last_sync_summary": sync_summary,
        "db_health": {
            "project_catalog_writable": project_path.is_file(),
            "runtime_dossier_readable": runtime_path.is_file(),
        },
    }


def load_miru_card_insight(
    card_id: str,
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    rotate_window_seconds: int = 60,
) -> dict[str, Any] | None:
    canonical = normalize_card_code(card_id).get("canonical_code") or str(card_id or "").strip().upper()
    if not canonical:
        return None
    ensure_catalog_sync_schema(project_db_path)
    with closing(connect_catalog_db(project_db_path)) as conn:
        rows = conn.execute(
            """
            SELECT card_id, insight_type, insight_text, confidence, updated_at
            FROM miru_card_insights
            WHERE card_id = ?
            ORDER BY confidence DESC, updated_at DESC, insight_type ASC
            """,
            (canonical,),
        ).fetchall()
    if not rows:
        return None
    items = [dict(row) for row in rows]
    bucket = max(int(rotate_window_seconds), 1)
    selected = items[(int(time.time()) // bucket) % len(items)]
    return {
        "card_id": canonical,
        "insight": str(selected.get("insight_text") or ""),
        "type": str(selected.get("insight_type") or ""),
        "confidence": float(selected.get("confidence") or 0.0),
        "updated_at": str(selected.get("updated_at") or ""),
    }


class MiruProjectDbSync:
    def __init__(
        self,
        *,
        project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
        batch_size: int = 3,
        sync_immediate: bool = True,
        confidence_threshold: float = 0.75,
        # Minimum distinct sources required for dossier→catalog promotion eligibility
        # (see miru_learning_engine.MiruLearningEngine.is_dossier_promotable). Kept >= 2
        # so weak single-source rows are not auto-promoted.
        min_verified_sources: int = 2,
        preferred_verified_sources: int = 3,
        logger: Callable[..., None] | None = None,
    ) -> None:
        self.project_db_path = str(project_db_path)
        self.batch_size = max(int(batch_size), 1)
        self.sync_immediate = bool(sync_immediate)
        self.confidence_threshold = float(confidence_threshold)
        self.min_verified_sources = max(int(min_verified_sources), 2)
        self.preferred_verified_sources = max(int(preferred_verified_sources), self.min_verified_sources)
        self.logger = logger
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._source_registry = build_source_registry()
        ensure_catalog_sync_schema(self.project_db_path)

    def queue_validated_record(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str = "verify_official_fields",
        additional_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = self.build_sync_payload(
            record,
            task_type=task_type,
            additional_sources=additional_sources,
        )
        card_code = payload["card_code"]
        self._log(
            event_type="card_validated",
            message=f"Validated {card_code} for Project Miru library sync.",
            card_code=card_code,
        )
        with self._lock:
            self._pending[card_code] = payload
            self._log(
                event_type="card_sync_queued",
                message=f"Queued {card_code} for Project Miru library sync.",
                card_code=card_code,
            )

            flushed = 0
            failed = 0
            if self.sync_immediate:
                result = self.flush_cards([card_code], reason="immediate")
                flushed += result["flushed"]
                failed += result["failed"]
            elif len(self._pending) >= self.batch_size:
                result = self.flush_pending(reason="batch-threshold")
                flushed += result["flushed"]
                failed += result["failed"]
            return {"queued": len(self._pending), "flushed": flushed, "failed": failed}

    def queue_validated_records(
        self,
        records: list[dict[str, Any]],
        *,
        task_type: str = "bulk_ingest_registry",
        reason: str = "bulk-registry-ingest",
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        """Batch enqueue validated registry records (e.g. bulk_ingest_registry) and flush to catalog."""
        queued_codes: list[str] = []
        with self._lock:
            for item in records:
                record = item.get("record")
                if not isinstance(record, NormalizedSourceRecord):
                    continue
                payload = self.build_sync_payload(
                    record,
                    task_type=str(item.get("task_type") or task_type),
                    additional_sources=item.get("additional_sources"),
                )
                card_code = payload["card_code"]
                self._pending[card_code] = payload
                queued_codes.append(card_code)
                self._log(
                    event_type="card_sync_queued",
                    message=f"Queued {card_code} for Project Miru library sync.",
                    card_code=card_code,
                )
            if not queued_codes:
                return {
                    "queued": 0,
                    "flushed": 0,
                    "failed": 0,
                    "pending": len(self._pending),
                    "outcomes": [],
                }
            return self.flush_cards(
                queued_codes,
                reason=reason,
                progress_callback=progress_callback,
            )

    def flush_pending(
        self,
        *,
        reason: str = "manual",
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self.flush_cards(
                list(self._pending.keys()),
                reason=reason,
                progress_callback=progress_callback,
            )

    def flush_cards(
        self,
        card_codes: list[str],
        *,
        reason: str,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        flushed = 0
        failed = 0
        outcomes: list[dict[str, Any]] = []
        with self._lock:
            for index, card_code in enumerate(list(card_codes), start=1):
                payload = self._pending.get(card_code)
                if not payload:
                    continue
                if progress_callback and (index == 1 or index % 10 == 0):
                    progress_callback(card_code, reason)
                try:
                    sync_out = self._sync_payload(payload)
                except Exception as exc:
                    failed += 1
                    self._log(
                        event_type="card_sync_failed",
                        level="error",
                        message=f"Library sync failed for {card_code} during {reason}: {exc}",
                        card_code=card_code,
                    )
                    continue
                flushed += 1
                self._pending.pop(card_code, None)
                self._log(
                    event_type="card_synced",
                    message=f"Synced {card_code} into card_catalog.db during {reason}.",
                    card_code=card_code,
                )
                row = dict(sync_out or {})
                row.setdefault("card_code", card_code)
                outcomes.append(row)
            return {"flushed": flushed, "failed": failed, "pending": len(self._pending), "outcomes": outcomes}

    def build_sync_payload(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str = "verify_official_fields",
        additional_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profile = self._resolve_source_profile(record.source_id)
        source_entry = self._build_source_entry(profile, record)
        source_entries = self._dedupe_source_entries([source_entry, *(additional_sources or [])])
        confidence_score = self._score_source_confidence(source_entries)
        normalized = normalize_card_code(record.card_code)
        card_code = normalized["canonical_code"] or record.card_code.strip().upper()
        set_code = normalize_set_code(record.set_code or normalized["set_code"])
        traits_text = " / ".join(clean_display_text(item) for item in (record.traits or []) if clean_display_text(item))
        validated_fields = [
            key
            for key, value in {
                "card_name": record.card_name,
                "set_code": set_code,
                "set_name": record.set_name,
                "rarity": record.rarity,
                "color": record.color,
                "card_type": record.card_type,
                "cost": record.cost,
                "power": record.power,
                "counter": record.counter,
                "attribute": record.attribute,
                "traits": traits_text,
                "life": record.life,
                "effect_text": record.effect_text,
                "trigger_text": record.trigger_text,
            }.items()
            if value not in (None, "", [], {})
        ]
        confidence_reason = self._describe_confidence(
            source_entries=source_entries,
            conflict_count=0,
        )
        conflict_reason = "Only one validation source contributed to this sync payload."
        conflict_rule = "single-source validation"
        if len(source_entries) > 1:
            conflict_rule = "multi-source corroboration"
            conflict_reason = "Multiple corroborating source lanes contributed to this sync payload."
        return {
            "card_code": card_code,
            "set_code": set_code,
            "card_number": clean_display_text(normalized["card_number"]),
            "set_name": clean_display_text(record.set_name),
            "card_name": clean_display_text(record.card_name),
            "rarity": clean_display_text(record.rarity),
            "color": clean_display_text(record.color),
            "card_type": clean_display_text(record.card_type),
            "cost": self._coerce_int(record.cost),
            "power": clean_display_text(record.power),
            "counter": clean_display_text(record.counter),
            "attribute": clean_display_text(record.attribute),
            "traits": traits_text,
            "life": clean_display_text(record.life),
            "effect_text": clean_display_text(record.effect_text),
            "trigger_text": clean_display_text(record.trigger_text),
            "confidence_score": confidence_score,
            "confidence_reason": confidence_reason,
            "validated_at": record.fetched_at,
            "validated_fields": validated_fields,
            "task_type": task_type,
            "sources": source_entries,
            "winning_source": source_entry,
            "rejected_sources": [],
            "conflict_summary": {
                "rule": conflict_rule,
                "conflicts": [],
                "reason": conflict_reason,
            },
            "payload_json": record.to_dict(),
        }

    def _sync_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not card_code:
            raise ValueError("Sync payload is missing card_code.")
        confidence_score = float(payload.get("confidence_score") or 0.0)
        if confidence_score < self.confidence_threshold:
            raise ValueError(
                f"Refusing to sync {card_code} because confidence {confidence_score:.2f} is below threshold {self.confidence_threshold:.2f}."
            )

        ensure_catalog_sync_schema(self.project_db_path)
        with closing(connect_catalog_db(self.project_db_path)) as conn:
            set_code = str(payload.get("set_code") or "").strip().upper()
            set_name = str(payload.get("set_name") or "").strip()
            if set_code:
                existing_set = conn.execute(
                    "SELECT * FROM sets WHERE set_code = ?",
                    (set_code,),
                ).fetchone()
                existing_sources = self._load_json_list(existing_set["sources_json"] if existing_set else "[]")
                merged_set_sources = self._merge_source_keys(existing_sources, payload.get("sources") or [])
                if existing_set:
                    conn.execute(
                        """
                        UPDATE sets
                        SET set_name = ?, series_code_display = ?, sources_json = ?
                        WHERE set_code = ?
                        """,
                        (
                            set_name or existing_set["set_name"] or "",
                            existing_set["series_code_display"] or set_code,
                            json.dumps(merged_set_sources, ensure_ascii=True, sort_keys=True),
                            set_code,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO sets (
                            set_code, set_name, series_code_display, series_id, sources_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            set_code,
                            set_name,
                            set_code,
                            "",
                            json.dumps(merged_set_sources, ensure_ascii=True, sort_keys=True),
                        ),
                    )

            existing_card = conn.execute(
                "SELECT * FROM cards WHERE canonical_code = ?",
                (card_code,),
            ).fetchone()
            existing_validation = conn.execute(
                "SELECT * FROM miru_validations WHERE card_code = ?",
                (card_code,),
            ).fetchone()
            existing_sources = self._load_json_list(existing_card["sources_json"] if existing_card else "[]")
            merged_sources = self._merge_source_keys(existing_sources, payload.get("sources") or [])
            aliases_json = existing_card["aliases_json"] if existing_card else "[]"
            decision_context = self._build_decision_context(existing_validation, payload.get("sources") or [])
            field_decisions: dict[str, dict[str, Any]] = {}
            merged_card = {
                "canonical_code": card_code,
                "set_code": set_code or (existing_card["set_code"] if existing_card else ""),
                "card_number": str(payload.get("card_number") or "").strip() or (existing_card["card_number"] if existing_card else ""),
                "set_name": self._merge_text("set_name", existing_card["set_name"] if existing_card else "", payload.get("set_name"), decision_context, field_decisions),
                "card_name": self._merge_text("card_name", existing_card["card_name"] if existing_card else "", payload.get("card_name"), decision_context, field_decisions),
                "rarity": self._merge_text("rarity", existing_card["rarity"] if existing_card else "", payload.get("rarity"), decision_context, field_decisions),
                "color": self._merge_text("color", existing_card["color"] if existing_card else "", payload.get("color"), decision_context, field_decisions),
                "card_type": self._merge_text("card_type", existing_card["card_type"] if existing_card else "", payload.get("card_type"), decision_context, field_decisions),
                "cost": self._merge_int("cost", existing_card["cost"] if existing_card else None, payload.get("cost"), decision_context, field_decisions),
                "power": self._merge_text("power", existing_card["power"] if existing_card else "", payload.get("power"), decision_context, field_decisions),
                "counter": self._merge_text("counter", existing_card["counter"] if existing_card else "", payload.get("counter"), decision_context, field_decisions),
                "attribute": self._merge_text("attribute", existing_card["attribute"] if existing_card else "", payload.get("attribute"), decision_context, field_decisions),
                "traits": self._merge_text("traits", existing_card["traits"] if existing_card else "", payload.get("traits"), decision_context, field_decisions),
                "life": self._merge_text("life", existing_card["life"] if existing_card else "", payload.get("life"), decision_context, field_decisions),
                "block_icon": existing_card["block_icon"] if existing_card else "",
                "effect_text": self._merge_text("effect_text", existing_card["effect_text"] if existing_card else "", payload.get("effect_text"), decision_context, field_decisions),
                "trigger_text": self._merge_text("trigger_text", existing_card["trigger_text"] if existing_card else "", payload.get("trigger_text"), decision_context, field_decisions),
                "aliases_json": aliases_json,
                "sources_json": json.dumps(merged_sources, ensure_ascii=True, sort_keys=True),
            }
            conflict_summary = self._build_conflict_summary(field_decisions, payload.get("sources") or [], decision_context)
            winning_source = self._build_winning_source(payload.get("sources") or [], conflict_summary, decision_context)
            rejected_sources = self._build_rejected_sources(payload.get("sources") or [], conflict_summary)
            confidence_reason = self._describe_confidence(
                source_entries=payload.get("sources") or [],
                conflict_count=int(conflict_summary.get("rejected_field_count") or 0),
            )
            self._log(
                event_type="card_sync_decision",
                message=(
                    f"{card_code}: chose {winning_source.get('source_id') or 'unknown'} "
                    f"({winning_source.get('trust_label') or 'unknown'})"
                    + (
                        f"; rejected {', '.join(item.get('source_id', '') for item in rejected_sources if item.get('source_id'))}"
                        if rejected_sources
                        else "; no conflicting lower-trust source won"
                    )
                    + f"; {confidence_reason}"
                ),
                card_code=card_code,
            )

            if existing_card:
                conn.execute(
                    """
                    UPDATE cards
                    SET set_code = ?, card_number = ?, set_name = ?, card_name = ?, rarity = ?,
                        color = ?, card_type = ?, cost = ?, power = ?, counter = ?, attribute = ?,
                        traits = ?, life = ?, effect_text = ?, trigger_text = ?, aliases_json = ?, sources_json = ?
                    WHERE canonical_code = ?
                    """,
                    (
                        merged_card["set_code"],
                        merged_card["card_number"],
                        merged_card["set_name"],
                        merged_card["card_name"],
                        merged_card["rarity"],
                        merged_card["color"],
                        merged_card["card_type"],
                        merged_card["cost"],
                        merged_card["power"],
                        merged_card["counter"],
                        merged_card["attribute"],
                        merged_card["traits"],
                        merged_card["life"],
                        merged_card["effect_text"],
                        merged_card["trigger_text"],
                        merged_card["aliases_json"],
                        merged_card["sources_json"],
                        card_code,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO cards (
                        canonical_code, set_code, card_number, set_name, card_name, rarity, color,
                        card_type, cost, power, counter, attribute, traits, life, block_icon,
                        effect_text, trigger_text, aliases_json, sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        merged_card["canonical_code"],
                        merged_card["set_code"],
                        merged_card["card_number"],
                        merged_card["set_name"],
                        merged_card["card_name"],
                        merged_card["rarity"],
                        merged_card["color"],
                        merged_card["card_type"],
                        merged_card["cost"],
                        merged_card["power"],
                        merged_card["counter"],
                        merged_card["attribute"],
                        merged_card["traits"],
                        merged_card["life"],
                        merged_card["block_icon"],
                        merged_card["effect_text"],
                        merged_card["trigger_text"],
                        merged_card["aliases_json"],
                        merged_card["sources_json"],
                    ),
                )

            conn.execute(
                """
                INSERT INTO miru_validations (
                    card_code, confidence, task_type, verified_at, sources_json,
                    winning_source_json, rejected_sources_json, validated_fields_json,
                    conflict_summary_json, confidence_reason, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(card_code) DO UPDATE SET
                    confidence = excluded.confidence,
                    task_type = excluded.task_type,
                    verified_at = excluded.verified_at,
                    sources_json = excluded.sources_json,
                    winning_source_json = excluded.winning_source_json,
                    rejected_sources_json = excluded.rejected_sources_json,
                    validated_fields_json = excluded.validated_fields_json,
                    conflict_summary_json = excluded.conflict_summary_json,
                    confidence_reason = excluded.confidence_reason,
                    payload_json = excluded.payload_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    card_code,
                    confidence_score,
                    str(payload.get("task_type") or ""),
                    str(payload.get("validated_at") or ""),
                    json.dumps(payload.get("sources") or [], ensure_ascii=True, sort_keys=True),
                    json.dumps(winning_source, ensure_ascii=True, sort_keys=True),
                    json.dumps(rejected_sources, ensure_ascii=True, sort_keys=True),
                    json.dumps(payload.get("validated_fields") or [], ensure_ascii=True, sort_keys=True),
                    json.dumps(conflict_summary, ensure_ascii=True, sort_keys=True),
                    confidence_reason,
                    json.dumps(payload.get("payload_json") or {}, ensure_ascii=True, sort_keys=True),
                ),
            )

        sources = list(payload.get("sources") or [])
        display_names = [
            str(entry.get("display_name") or entry.get("source_id") or "").strip() for entry in sources if entry
        ]
        return {
            "status": "synced",
            "card_code": card_code,
            "confidence_score": confidence_score,
            "verification_status": "verified" if confidence_score >= 0.75 else "pending-confirmation",
            "source_rollup": {
                "source_count": len(sources),
                "source_names": display_names,
                "confidence_level": "official" if confidence_score >= 0.9 else "community",
            },
            "conflict_summary": conflict_summary,
        }

    @staticmethod
    def _load_json_list(value: str) -> list[str]:
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item).strip() for item in payload if str(item).strip()]

    @staticmethod
    def _merge_source_keys(existing: list[str], source_entries: list[dict[str, Any]]) -> list[str]:
        merged = list(existing)
        for entry in source_entries:
            key = str(entry.get("source_id") or "").strip()
            if key and key not in merged:
                merged.append(key)
        return merged

    def _resolve_source_profile(self, source_id: str) -> MiruSourceEntry:
        try:
            return get_source_entry(source_id, self._source_registry)
        except KeyError:
            return build_unknown_source_entry(source_id)

    @staticmethod
    def _build_source_entry(profile: MiruSourceEntry, record: NormalizedSourceRecord) -> dict[str, Any]:
        return {
            "source_id": record.source_id,
            "source_url": record.source_url,
            "source_reference": record.source_reference,
            "display_name": profile.source_name,
            "source_type": profile.source_type,
            "trust_tier": profile.trust_tier,
            "trust_label": profile.trust_label,
            "enabled": profile.enabled,
            "review_state": profile.review_state,
            "rate_limit_hint": profile.rate_limit_hint,
            "backoff_policy": profile.backoff_policy,
            "notes": profile.notes,
            "observed_at": record.fetched_at,
        }

    @staticmethod
    def _dedupe_source_entries(source_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in source_entries:
            entry = dict(raw_entry or {})
            source_id = str(entry.get("source_id") or "").strip().lower()
            source_reference = str(entry.get("source_reference") or "").strip()
            if not source_id:
                continue
            key = (source_id, source_reference)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        return deduped

    @staticmethod
    def _score_source_confidence(source_entries: list[dict[str, Any]]) -> float:
        if not source_entries:
            return 0.0
        best_tier = min(int(entry.get("trust_tier") or 4) for entry in source_entries)
        distinct_sources = len(
            {str(entry.get("source_id") or "").strip().lower() for entry in source_entries if str(entry.get("source_id") or "").strip()}
        )
        if best_tier <= 1:
            base = 0.95
        elif best_tier == 2:
            base = 0.78
        elif best_tier == 3:
            base = 0.58
        else:
            base = 0.35
        if best_tier == 2 and distinct_sources >= 2:
            base = min(base + 0.07, 0.85)
        if best_tier >= 3 and distinct_sources == 1:
            base = max(base - 0.05, 0.0)
        return round(base, 2)

    @staticmethod
    def _describe_confidence(*, source_entries: list[dict[str, Any]], conflict_count: int) -> str:
        if not source_entries:
            return "No source evidence was attached to this validation."
        best_tier = min(int(entry.get("trust_tier") or 4) for entry in source_entries)
        distinct_sources = len(
            {str(entry.get("source_id") or "").strip().lower() for entry in source_entries if str(entry.get("source_id") or "").strip()}
        )
        if best_tier == 1:
            reason = "Official source evidence drives verified confidence."
        elif best_tier == 2 and distinct_sources >= 2:
            reason = "Multiple high-confidence community sources agree, so Miru allows moderate confidence."
        elif best_tier == 2:
            reason = "Single high-confidence community source is accepted, but below official certainty."
        elif best_tier == 3:
            reason = "Secondary/reference evidence is advisory and kept below strong validation confidence."
        else:
            reason = "Experimental or unknown source evidence remains review-only unless stronger support exists."
        if conflict_count:
            reason += f" {conflict_count} field conflict(s) were rejected in favor of stronger existing evidence."
        return reason

    @staticmethod
    def _build_decision_context(existing_validation: sqlite3.Row | None, incoming_sources: list[dict[str, Any]]) -> dict[str, Any]:
        existing_sources = MiruProjectDbSync._load_json_objects(existing_validation["sources_json"] if existing_validation else "[]")
        existing_winning = MiruProjectDbSync._load_json_object(existing_validation["winning_source_json"] if existing_validation else "{}")
        existing_confidence = float(existing_validation["confidence"] if existing_validation else 0.0)
        existing_best_tier = min(
            [int(item.get("trust_tier") or 4) for item in existing_sources] or [int(existing_winning.get("trust_tier") or 4)]
        )
        incoming_best_tier = min([int(item.get("trust_tier") or 4) for item in incoming_sources] or [4])
        incoming_confidence = MiruProjectDbSync._score_source_confidence(incoming_sources)
        return {
            "existing_sources": existing_sources,
            "existing_winning_source": existing_winning,
            "existing_confidence": existing_confidence,
            "existing_best_tier": existing_best_tier,
            "incoming_sources": incoming_sources,
            "incoming_best_tier": incoming_best_tier,
            "incoming_confidence": incoming_confidence,
        }

    def _merge_text(
        self,
        field_name: str,
        existing: Any,
        incoming: Any,
        context: dict[str, Any],
        field_decisions: dict[str, dict[str, Any]],
    ) -> str:
        incoming_text = clean_display_text(str(incoming or ""))
        existing_text = clean_display_text(str(existing or ""))
        selected, decision = self._select_value(field_name, existing_text, incoming_text, context)
        field_decisions[field_name] = decision
        return clean_display_text(str(selected or ""))

    def _merge_int(
        self,
        field_name: str,
        existing: Any,
        incoming: Any,
        context: dict[str, Any],
        field_decisions: dict[str, dict[str, Any]],
    ) -> int | None:
        incoming_value = MiruProjectDbSync._coerce_int(incoming)
        existing_value = MiruProjectDbSync._coerce_int(existing)
        selected, decision = self._select_value(field_name, existing_value, incoming_value, context)
        field_decisions[field_name] = decision
        return MiruProjectDbSync._coerce_int(selected)

    @staticmethod
    def _select_value(field_name: str, existing: Any, incoming: Any, context: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        existing_present = existing not in (None, "", [], {})
        incoming_present = incoming not in (None, "", [], {})
        if not incoming_present:
            return existing, {
                "field_name": field_name,
                "selected": "existing",
                "reason": "incoming-blank",
                "conflict": False,
            }
        if not existing_present:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "fill-missing",
                "conflict": False,
            }
        if existing == incoming:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "agreement",
                "conflict": False,
            }
        incoming_tier = int(context.get("incoming_best_tier") or 4)
        existing_tier = int(context.get("existing_best_tier") or 4)
        incoming_confidence = float(context.get("incoming_confidence") or 0.0)
        existing_confidence = float(context.get("existing_confidence") or 0.0)
        if incoming_tier < existing_tier:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "higher-trust-source",
                "conflict": True,
                "existing_value": existing,
                "incoming_value": incoming,
            }
        if incoming_tier == existing_tier and incoming_confidence >= existing_confidence:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "same-tier-refresh",
                "conflict": True,
                "existing_value": existing,
                "incoming_value": incoming,
            }
        return existing, {
            "field_name": field_name,
            "selected": "existing",
            "reason": "preserve-higher-trust-existing",
            "conflict": True,
            "existing_value": existing,
            "incoming_value": incoming,
        }

    @staticmethod
    def _build_conflict_summary(
        field_decisions: dict[str, dict[str, Any]],
        incoming_sources: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        rejected_fields = [
            decision["field_name"]
            for decision in field_decisions.values()
            if decision.get("conflict") and decision.get("selected") == "existing"
        ]
        accepted_conflicts = [
            decision["field_name"]
            for decision in field_decisions.values()
            if decision.get("conflict") and decision.get("selected") == "incoming"
        ]
        if rejected_fields:
            rule = "prefer-existing-higher-trust"
            summary = "Conflicting lower-trust data was rejected in favor of stronger previously verified evidence."
        elif accepted_conflicts:
            rule = "incoming-higher-trust-wins"
            summary = "Incoming higher-trust validation replaced weaker previously stored values."
        elif len(incoming_sources) >= 2 and int(context.get("incoming_best_tier") or 4) == 2:
            rule = "trusted-non-official-agreement"
            summary = "Multiple high-confidence community sources agreed, so Miru accepted a moderate-confidence validation."
        elif int(context.get("incoming_best_tier") or 4) >= 3:
            rule = "single-weak-source"
            summary = "Single weak source remained low confidence and was only accepted when no stronger verified value existed."
        else:
            rule = "no-conflict"
            summary = "No conflicting higher-trust evidence was present."
        return {
            "rule": rule,
            "summary": summary,
            "rejected_field_count": len(rejected_fields),
            "accepted_conflict_count": len(accepted_conflicts),
            "rejected_fields": rejected_fields,
            "accepted_conflict_fields": accepted_conflicts,
            "field_decisions": field_decisions,
        }

    @staticmethod
    def _build_winning_source(
        source_entries: list[dict[str, Any]],
        conflict_summary: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not source_entries:
            return dict(context.get("existing_winning_source") or {})
        if conflict_summary.get("rule") == "prefer-existing-higher-trust":
            existing_winner = dict(context.get("existing_winning_source") or {})
            if existing_winner:
                return existing_winner
        if conflict_summary.get("rule") == "single-weak-source" and len(source_entries) == 1:
            return dict(source_entries[0])
        winner = min(
            source_entries,
            key=lambda entry: (
                int(entry.get("trust_tier") or 4),
                str(entry.get("source_id") or ""),
            ),
        )
        return dict(winner)

    @staticmethod
    def _build_rejected_sources(source_entries: list[dict[str, Any]], conflict_summary: dict[str, Any]) -> list[dict[str, Any]]:
        if not source_entries:
            return []
        if not conflict_summary.get("rejected_fields"):
            return []
        return [
            {
                **dict(entry),
                "rejected_fields": list(conflict_summary.get("rejected_fields") or []),
                "rejection_reason": "lower-trust conflicting source did not override stronger existing evidence",
            }
            for entry in source_entries
        ]

    @staticmethod
    def _load_json_objects(value: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _load_json_object(value: str) -> dict[str, Any]:
        try:
            payload = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _log(
        self,
        *,
        event_type: str,
        message: str,
        card_code: str = "",
        level: str = "info",
    ) -> None:
        if self.logger is None:
            return
        self.logger(
            level=level,
            event_type=event_type,
            message=message,
            card_code=card_code,
            task_type="project_db_sync",
        )


# ---------------------------------------------------------------------------
# Worktree-first card insight sync (CLI)
# ---------------------------------------------------------------------------

def _worktree_dossier_status(runtime_dossier_db_path: str | Path) -> dict[str, Any]:
    """Report whether learning_dossiers exists and counts by verification_state. Worktree-safe."""
    path = Path(runtime_dossier_db_path)
    out: dict[str, Any] = {
        "runtime_path_exists": path.is_file(),
        "learning_dossiers_table_exists": False,
        "verified_count": 0,
        "source_backed_count": 0,
    }
    if not path.is_file():
        return out
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='learning_dossiers'"
            ).fetchone()
            out["learning_dossiers_table_exists"] = row is not None
            if row is not None:
                for state, key in (("verified", "verified_count"), ("source-backed", "source_backed_count")):
                    count_row = conn.execute(
                        "SELECT COUNT(*) AS n FROM learning_dossiers WHERE verification_state = ?",
                        (state,),
                    ).fetchone()
                    out[key] = int(count_row[0] or 0) if count_row else 0
    except Exception:
        pass
    return out


def run_worktree_card_insight_sync(
    *,
    limit: int | None = None,
    rebuild: bool = False,
    only_card_codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Run card insight sync using worktree-local paths only.
    Uses: data/card_catalog.db, data/miru_learning_dossiers.db, data/prices.json.
    Ensures catalog schema exists, then runs sync_miru_card_insights.
    Pass rebuild=True to wipe all existing insights and regenerate from scratch.
    When ``only_card_codes`` is set (and rebuild is False), only those cards are synced.
    Returns a report dict for CLI or callers.
    """
    project_path = PROJECT_ROOT / "data" / "card_catalog.db"
    runtime_path = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
    prices_path = PROJECT_ROOT / "data" / "prices.json"
    log_path = PROJECT_ROOT / "data" / "miru_project_sync.log"

    deck_intel_path = PROJECT_ROOT / "data" / "miru_deck_intel.db"
    report: dict[str, Any] = {
        "project_db_path": str(project_path),
        "runtime_dossier_db_path": str(runtime_path),
        "prices_path": str(prices_path),
        "deck_intel_path": str(deck_intel_path),
        "dossier_status": _worktree_dossier_status(runtime_path),
        "catalog_schema_ensured": False,
        "miru_card_insights_exists": False,
        "enrichment": None,
        "sync_result": None,
        "insight_count_after": 0,
    }

    report["catalog_schema_ensured"] = project_path.is_file() or True  # ensure creates if needed
    ensure_catalog_sync_schema(project_path)
    report["catalog_schema_ensured"] = True

    enrichment = enrich_card_intelligence_from_deck_intel(
        project_db_path=project_path,
        deck_intel_db_path=deck_intel_path,
    )
    report["enrichment"] = enrichment

    if project_path.is_file():
        try:
            with closing(connect_catalog_db(project_path)) as conn:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='miru_card_insights'"
                ).fetchone()
                report["miru_card_insights_exists"] = row is not None
        except Exception:
            pass

    result = sync_miru_card_insights(
        project_db_path=project_path,
        runtime_dossier_db_path=runtime_path,
        prices_path=prices_path,
        log_path=log_path,
        limit=limit,
        force_rebuild=rebuild,
        only_card_codes=only_card_codes,
    )
    report["sync_result"] = result
    report["insight_count_after"] = int((result.get("status") or {}).get("insight_count") or 0)
    if only_card_codes is not None:
        report["only_card_codes"] = list(only_card_codes)
    return report


def run_publish_ready_insight_sync(
    *,
    limit: int = 8,
    project_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Bounded sync for cards with ``publish_status='publish_ready'`` in card_catalog only.

    Populates ``miru_card_insights`` / projections for storefront consumption through the
    normal catalog DB bridge — no review gate bypass; only rows already marked publish-ready.
    """
    project_path = Path(project_db_path or DEFAULT_PROJECT_DB_PATH)
    ensure_catalog_sync_schema(project_path)
    bounded = max(1, min(int(limit or 8), 80))
    codes: list[str] = []
    with closing(connect_catalog_db(project_path)) as conn:
        rows = conn.execute(
            """
            SELECT c.canonical_code AS card_code
            FROM card_intelligence ci
            JOIN cards c ON c.id = ci.card_id
            WHERE lower(trim(coalesce(ci.publish_status, ''))) = 'publish_ready'
            ORDER BY c.canonical_code ASC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
        codes = [str(row["card_code"] or "").strip().upper() for row in rows if str(row["card_code"] or "").strip()]
    if not codes:
        return {
            "ok": True,
            "publish_ready_codes": [],
            "message": "No publish_ready rows in card_intelligence; nothing to populate.",
            "sync_report": None,
        }
    sync_report = run_worktree_card_insight_sync(
        limit=len(codes),
        rebuild=False,
        only_card_codes=codes,
    )
    return {
        "ok": True,
        "publish_ready_codes": codes,
        "sync_report": sync_report,
    }


def plan_worktree_card_insight_sync(
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    project_path = PROJECT_ROOT / "data" / "card_catalog.db"
    runtime_path = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
    prices_path = PROJECT_ROOT / "data" / "prices.json"
    deck_intel_path = PROJECT_ROOT / "data" / "miru_deck_intel.db"
    canonical_path = PROJECT_ROOT / "data" / "miru_dossiers.db"
    rules_path = PROJECT_ROOT / "data" / "miru_official_rules.db"

    ensure_catalog_sync_schema(project_path)
    enrich_card_intelligence_from_deck_intel(
        project_db_path=project_path,
        deck_intel_db_path=deck_intel_path,
    )
    return _collect_incremental_sync_candidates(
        project_db_path=project_path,
        runtime_dossier_db_path=runtime_path,
        canonical_dossier_db_path=canonical_path if canonical_path.is_file() else runtime_path,
        rules_db_path=rules_path,
        deck_intel_db_path=deck_intel_path,
        prices_path=prices_path,
        limit=limit,
    )


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Miru project sync — card insight generation")
    parser.add_argument(
        "--rebuild-insights",
        action="store_true",
        default=False,
        help=(
            "Wipe all existing miru_card_insights rows and regenerate from scratch. "
            "Use after upgrading insight logic to purge stale generic filler."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of dossiers processed (useful for smoke-tests).",
    )
    args = parser.parse_args()

    if args.rebuild_insights:
        print("=== REBUILD MODE: all existing insights will be deleted first ===")

    report = run_worktree_card_insight_sync(rebuild=args.rebuild_insights, limit=args.limit)
    d = report.get("dossier_status") or {}
    enr = report.get("enrichment") or {}
    print("Worktree card insight sync report")
    print("  enrichment (deck intel -> catalog): deck_intel_present =", enr.get("deck_intel_present"), "| cards_enriched =", enr.get("cards_enriched", 0), "| deck_intel_cards =", enr.get("deck_intel_cards", 0), "| skipped_no_catalog =", enr.get("cards_skipped_no_catalog", 0))
    print("  runtime_dossier_db_path:", report.get("runtime_dossier_db_path", ""))
    print("  learning_dossiers exists:", d.get("runtime_path_exists"), "(file)", d.get("learning_dossiers_table_exists"), "(table)")
    print("  verified dossiers count:", d.get("verified_count", 0))
    print("  source-backed dossiers count:", d.get("source_backed_count", 0))
    print("  miru_card_insights table exists:", report.get("miru_card_insights_exists"))
    res = report.get("sync_result") or {}
    print("  synced_cards:", res.get("synced_cards", 0))
    print("  inserted_insights:", res.get("inserted_insights", 0))
    print("  replaced_insights:", res.get("replaced_insights", 0))
    print("  preserved_insights:", res.get("preserved_insights", 0))
    print("  skipped_cards:", res.get("skipped_cards", 0))
    if args.rebuild_insights:
        print("  deleted_before_rebuild:", res.get("deleted_before_rebuild", 0))
    print("  insight_count_after:", report.get("insight_count_after", 0))
    by_type = res.get("by_type") or {}
    if by_type:
        print("  by_type breakdown:")
        for itype, count in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"    {itype}: {count}")
    sys.exit(0)
