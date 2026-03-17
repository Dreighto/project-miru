from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from tools.miru_ai_onepiece import clean_display_text, normalize_card_code, normalize_set_code
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
DEFAULT_RUNTIME_DOSSIER_DB_PATH = Path(r"D:\docker\tcg-watcher\data\miru_learning_dossiers.db")
DEFAULT_SYNC_LOG_PATH = (
    Path("/data/miru_project_sync.log")
    if Path("/data").exists()
    else Path(r"D:\docker\tcg-watcher\data\miru_project_sync.log")
)
INSIGHT_TYPES = ("meta", "lore", "price", "strength", "synergy")

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
        _ensure_card_intelligence_table(conn)
        _ensure_card_legality_table(conn)


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
            updated_at TEXT NOT NULL DEFAULT ''
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
) -> dict[str, Any]:
    """Sync card insights from dossiers into miru_card_insights.

    Intelligence for meta/usage comes from catalog card_intelligence when present,
    otherwise from aggregated leader_card_signals in deck_intel_db (if provided).

    When force_rebuild=True: all existing rows are deleted first, then fresh
    candidates are inserted without the preserve-existing guard.  Use this
    after upgrading insight-generation logic to ensure stale rows are purged.
    """
    project_path = Path(project_db_path)
    runtime_path = Path(runtime_dossier_db_path)
    ensure_catalog_sync_schema(project_path)
    dossiers = _load_runtime_dossiers(runtime_path)
    if limit is not None:
        dossiers = dossiers[: max(int(limit), 0)]
    source_count_by_code = _load_dossier_source_counts(runtime_path)
    price_lookup = _load_watch_prices(prices_path)
    catalog_intel = _load_card_intelligence_rows(project_path)
    deck_intel = _load_usage_from_deck_intel(deck_intel_db_path or DEFAULT_DECK_INTEL_DB_PATH)
    intelligence_rows = _merge_card_intelligence(catalog_intel, deck_intel)
    synced_cards = 0
    written_insights = 0
    skipped_cards = 0

    preserved_insights = 0
    replaced_insights = 0
    inserted_insights = 0
    deleted_before_rebuild = 0
    by_type: dict[str, int] = {}  # counts per insight_type written this run

    with closing(connect_catalog_db(project_path)) as conn:
        if force_rebuild:
            row = conn.execute("SELECT COUNT(*) FROM miru_card_insights").fetchone()
            deleted_before_rebuild = int(row[0] if row else 0)
            conn.execute("DELETE FROM miru_card_insights")
            # In autocommit (isolation_level=None) mode the DELETE above commits
            # immediately.  Open an explicit transaction so the rebuild inserts
            # are batched into a single commit for performance.
            conn.execute("BEGIN")
        for dossier in dossiers:
            card_code = str(dossier.get("card_code") or "").strip().upper()
            if not card_code:
                skipped_cards += 1
                continue
            intel_row = intelligence_rows.get(card_code)
            meta_bearing = bool(
                intel_row
                and (
                    str(intel_row.get("deck_usage_summary") or "").strip()
                    or str(intel_row.get("role_summary") or "").strip()
                )
            )
            dossier_with_sources = {
                **dossier,
                "source_count": source_count_by_code.get(card_code, 1),
                "meta_bearing": meta_bearing,
            }
            candidates = build_card_insight_candidates(
                dossier_with_sources,
                price_lookup=price_lookup,
                intelligence_row=intel_row,
            )

            if not candidates:
                # No new candidates — preserve whatever exists (never blind-delete).
                skipped_cards += 1
                continue

            # Load existing insights only when we need them for the replace guard.
            existing: dict[str, dict[str, Any]] = {}
            if not force_rebuild:
                for row in conn.execute(
                    "SELECT insight_type, insight_text, confidence, quality_tier "
                    "FROM miru_card_insights WHERE card_id = ?",
                    (card_code,),
                ).fetchall():
                    existing[row["insight_type"]] = {
                        "text": row["insight_text"],
                        "confidence": float(row["confidence"]),
                        "quality_tier": row["quality_tier"] or "",
                    }

            card_wrote = False
            for item in candidates:
                itype      = str(item["insight_type"])
                new_text   = str(item["insight_text"]).strip()
                new_conf   = float(item.get("confidence") or 0.0)
                new_tier   = classify_insight_quality(new_text, new_conf)
                updated_at = item.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S")

                if force_rebuild:
                    # Table was wiped — always insert fresh; skip replace guard.
                    conn.execute(
                        "INSERT INTO miru_card_insights "
                        "(card_id, insight_type, insight_text, confidence, quality_tier, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (card_code, itype, new_text, new_conf, new_tier, updated_at),
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
                        # Backfill quality_tier if it was empty.
                        if not prev["quality_tier"]:
                            conn.execute(
                                "UPDATE miru_card_insights SET quality_tier = ? "
                                "WHERE card_id = ? AND insight_type = ?",
                                (existing_tier, card_code, itype),
                            )
                        continue

                    # Replace — existing is weaker.
                    conn.execute(
                        "UPDATE miru_card_insights "
                        "SET insight_text = ?, confidence = ?, quality_tier = ?, updated_at = ? "
                        "WHERE card_id = ? AND insight_type = ?",
                        (new_text, new_conf, new_tier, updated_at, card_code, itype),
                    )
                    replaced_insights += 1
                    by_type[itype] = by_type.get(itype, 0) + 1
                    card_wrote = True
                else:
                    # No existing insight for this type — insert.
                    conn.execute(
                        "INSERT INTO miru_card_insights "
                        "(card_id, insight_type, insight_text, confidence, quality_tier, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (card_code, itype, new_text, new_conf, new_tier, updated_at),
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

    status = load_miru_card_insight_status(project_db_path=project_path, runtime_dossier_db_path=runtime_path)
    _append_sync_log(
        log_path,
        (
            f"miru_card_insights sync complete: synced_cards={synced_cards} "
            f"inserted={inserted_insights} replaced={replaced_insights} "
            f"preserved={preserved_insights} skipped_cards={skipped_cards} "
            f"deleted_before_rebuild={deleted_before_rebuild} "
            f"by_type={by_type} "
            f"project_db={project_path} runtime_db={runtime_path}"
        ),
    )
    return {
        "ok": True,
        "synced_cards": synced_cards,
        "written_insights": written_insights,
        "inserted_insights": inserted_insights,
        "replaced_insights": replaced_insights,
        "preserved_insights": preserved_insights,
        "skipped_cards": skipped_cards,
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
    return {
        "connected": project_path.is_file() and runtime_path.is_file(),
        "sync_running": False,
        "last_sync_time": str((counts["last_sync_time"] if counts else "") or ""),
        "insight_count": int((counts["insight_count"] if counts else 0) or 0),
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
        logger: Callable[..., None] | None = None,
    ) -> None:
        self.project_db_path = str(project_db_path)
        self.batch_size = max(int(batch_size), 1)
        self.sync_immediate = bool(sync_immediate)
        self.confidence_threshold = float(confidence_threshold)
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
    ) -> dict[str, Any]:
        payload = self.build_sync_payload(record, task_type=task_type)
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

    def flush_pending(self, *, reason: str = "manual") -> dict[str, int]:
        with self._lock:
            return self.flush_cards(list(self._pending.keys()), reason=reason)

    def flush_cards(self, card_codes: list[str], *, reason: str) -> dict[str, int]:
        flushed = 0
        failed = 0
        with self._lock:
            for card_code in list(card_codes):
                payload = self._pending.get(card_code)
                if not payload:
                    continue
                try:
                    self._sync_payload(payload)
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
            return {"flushed": flushed, "failed": failed, "pending": len(self._pending)}

    def build_sync_payload(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str = "verify_official_fields",
    ) -> dict[str, Any]:
        profile = self._resolve_source_profile(record.source_id)
        confidence_score = self._score_source_confidence([self._build_source_entry(profile, record)])
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
        source_entry = self._build_source_entry(profile, record)
        confidence_reason = self._describe_confidence(
            source_entries=[source_entry],
            conflict_count=0,
        )
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
            "sources": [source_entry],
            "winning_source": source_entry,
            "rejected_sources": [],
            "conflict_summary": {
                "rule": "single-source validation",
                "conflicts": [],
                "reason": "Only one validation source contributed to this sync payload.",
            },
            "payload_json": record.to_dict(),
        }

    def _sync_payload(self, payload: dict[str, Any]) -> None:
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
) -> dict[str, Any]:
    """
    Run card insight sync using worktree-local paths only.
    Uses: data/card_catalog.db, data/miru_learning_dossiers.db, data/prices.json.
    Ensures catalog schema exists, then runs sync_miru_card_insights.
    Pass rebuild=True to wipe all existing insights and regenerate from scratch.
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
    )
    report["sync_result"] = result
    report["insight_count_after"] = int((result.get("status") or {}).get("insight_count") or 0)
    return report


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
