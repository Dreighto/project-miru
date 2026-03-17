import json
import time
import os
import re
import html
import logging
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
from threading import Lock, Thread
from pathlib import Path

from flask import (
    Flask,
    send_from_directory,
    request,
    jsonify,
    render_template,
)
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None

from catalog_db import (
    CATALOG_DB_PATH,
    get_catalog_conn,
    init_catalog_schema,
    rebuild_catalog_from_indexes,
    search_catalog_cards,
)
from catalog_import import (
    CATALOG_METADATA_PATH,
    apply_metadata_to_catalog,
)
from config.miru_storage_layout import build_storage_layout
from miru_card_intel import MiruCardIntelService, clean_card_name as shared_clean_card_name
from tools.miru_insight_cache import (
    CARD_INTELLIGENCE_SUMMARY_TYPE,
    DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
    DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
    MiruInsightCacheRepository,
    get_persistent_insight_cache_rollup_snapshot,
    get_insight_cache_metrics_snapshot,
    record_contextual_opportunity_metrics,
    record_contextual_view_metric,
    resolve_local_first_card_insight,
    resolve_local_first_meta_insight,
    resolve_local_first_strategy_insight,
    resolve_local_first_surface_response,
    resolve_local_first_usage_insight,
    resolve_local_first_verified_loop_summary,
)
from tools.miru_dossier_store import MiruDossierStore
try:
    from tools.miru_insight_voice import shape_section_display
except Exception:
    shape_section_display = None
try:
    from tools.miru_contextual_insight import (
        MiruContextWindow,
        build_contextual_opportunities,
    )
except Exception:
    MiruContextWindow = None
    build_contextual_opportunities = None
try:
    from tools.miru_maintenance import (
        load_backfill_queue_report,
        load_cache_effectiveness_report,
    )
except Exception:
    load_cache_effectiveness_report = None
    load_backfill_queue_report = None
from state_db import (
    MIRU_STATE_DB_PATH,
    disable_watchlist_entry,
    get_card_link_entry,
    get_watchlist_entry,
    get_watchlist_runtime_status,
    init_state_schema as init_state_db_schema,
    list_card_link_entries,
    list_watchlist_entries,
    list_watchlist_runtime_rows,
    mirror_card_url_map,
    resolve_card_link_memory,
    upsert_card_link_memory,
    upsert_watchlist_entry,
)

LOCAL_AI_IMPORT_ERROR = ""
try:
    from local_ai import analyze_card_image, ai_result_has_useful_signals
except Exception as exc:
    analyze_card_image = None
    ai_result_has_useful_signals = None
    LOCAL_AI_IMPORT_ERROR = str(exc)

BUILD_ID = os.getenv("BUILD_ID") or str(int(time.time()))

PRICES_PATH = "/data/prices.json"
IMAGES_ROOT = os.getenv("IMAGES_ROOT", "/images")
PRICES_PATH = os.getenv("PRICES_PATH", PRICES_PATH)
LAST_PRICES_PATH = os.getenv("LAST_PRICES_PATH", "/data/last_prices.json")  # used for price change arrows
CARD_URL_MAP_PATH = os.getenv("CARD_URL_MAP_PATH", "/data/card_url_map.json")
CARD_LOOKUP_TIMEOUT = 10
LEARNING_QUEUE_DB_PATH = os.getenv("MIRU_LEARNING_QUEUE_DB_PATH", "/data/miru_learning_queue.db")
LEARNING_LOG_DB_PATH = os.getenv("MIRU_LEARNING_LOG_DB_PATH", "/data/miru_learning_log.db")
MIRU_DOSSIER_DB_PATH = os.getenv("MIRU_DOSSIER_DB_PATH", "/data/miru_dossiers.db")
MIRU_INSIGHT_CACHE_DB_PATH = os.getenv("MIRU_INSIGHT_CACHE_DB_PATH", "/data/cache/insights/miru_insight_cache.db")
WATCHLIST_AUTHORITY_MODE = "local-first"
ENABLE_LOCAL_AI = os.getenv("ENABLE_LOCAL_AI", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LOCAL_AI_MODEL = (os.getenv("LOCAL_AI_MODEL") or "gemma3").strip() or "gemma3"
MIRU_CARD_INTEL = MiruCardIntelService(
    prices_path=PRICES_PATH,
    logger=logging.getLogger("miru-card-intel"),
)
THUMB_ROOT_NAME = "thumbs"
THUMB_MAX_WIDTH = int(os.getenv("THUMB_MAX_WIDTH", "220"))
THUMB_MAX_HEIGHT = int(os.getenv("THUMB_MAX_HEIGHT", "308"))
THUMB_QUALITY = int(os.getenv("THUMB_QUALITY", "80"))
THUMB_FORMAT = "WEBP"
THUMB_LOCK = Lock()
THUMB_THREAD = None
IMAGE_API_CACHE = {"payload": []}
MIRU_CONTEXT_WINDOW = MiruContextWindow(max_events=80) if MiruContextWindow is not None else None

# Keep local watchlist reads lightly cached for faster repeat page loads.
WATCHLIST_CACHE_SECONDS = int(os.getenv("WATCHLIST_CACHE_SECONDS", "300"))

CODE_RE = re.compile(r"\b([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\b", re.I)
ANY_CODE_VARIANT_RE = re.compile(r"([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\(([^)]+)\)", re.I)
CARD_CODE_RE = re.compile(
    r"^(?P<set_code>[A-Z]{1,4}\d{2}|P)-(?P<card_number>\d{3})$",
    re.I,
)
CARD_ID_IN_TEXT_RE = re.compile(
    r"(?P<set_code>(?:OP|EB|ST|PRB)\d{2}|P)\s*[-_ ]?\s*(?P<card_number>\d{3})(?:(?P<suffix>[A-Z])(?![A-Z]))?",
    re.I,
)
CARD_FILENAME_RE = re.compile(
    r"^(?P<code>(?P<set_code>[A-Z]{1,4}\d{2}|P)-(?P<card_number>\d{3}))"
    r"(?:\((?P<variant>[^)]+)\))?$",
    re.I,
)
TCGPLAYER_LINK_RE = re.compile(
    r'href="(https://www\.tcgplayer\.com/[^"]+)"[^>]*>\s*View on TCGPlayer\s*<',
    re.I,
)
TCGPLAYER_PRODUCT_LINK_RE = re.compile(
    r'href="((?:https://www\.tcgplayer\.com)?/product/\d+/[^"?#]+[^"]*)"',
    re.I,
)
TCGPLAYER_PRODUCT_ID_RE = re.compile(r"/product/(\d+)", re.I)
LIMITLESS_TITLE_RE = re.compile(
    r"<title>\s*([^<]+?)\s*\(([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\)\s*•\s*([^<]+?)\s*[<\u2013-]",
    re.I,
)
OPCARDLIST_TITLE_RE = re.compile(
    r"<title>\s*([^<]+?)\s*\|\s*OPCardList",
    re.I,
)
IMAGE_DIR_VARIANT_HINTS = {
    "alts": "alt",
    "alt": "alt",
    "parallel": "parallel",
    "sp": "sp",
    "promos": "promo",
    "promo": "promo",
    "illustration": "illustration",
    "illustrations": "illustration",
    "manga": "manga",
}
IMAGE_PATH_SKIP_PARTS = {"thumbs", "thumbnails", "__macosx"}
VARIANT_NOISE_PARTS = {"cleaned", "edited", "edit", "cropped", "crop", "upscaled", "raw"}

SET_CODE_TO_NAME = {
    "OP01": "Romance Dawn",
    "OP02": "Paramount War",
    "OP03": "Pillars of Strength",
    "OP04": "Kingdoms of Intrigue",
    "OP05": "Awakening of the New Era",
    "OP06": "Wings of the Captain",
    "OP07": "500 Years in the Future",
    "OP08": "Two Legends",
    "OP09": "Emperors in the New World",
    "OP10": "Royal Blood",
    "OP11": "A Fist of Divine Speed",
    "OP12": "Legacy of the Master",
    "OP13": "Carrying on His Will",
    "OP14": "The Azure Seas Seven",
    "OP15": "Adventure on Kamis Island",
    "EB01": "Memorial Collection",
    "EB02": "Anime 25th Collection",
    "EB03": "One Piece Heroines Edition",
    "PRB01": "Premium Booster One Piece Card The Best",
    "PRB02": "Premium Booster One Piece Card The Best Vol 2",
    "ST21": "Starter Deck EX Gear 5",
    "ST22": "Starter Deck Ace Newgate",
    "ST23": "Starter Deck Red Shanks",
    "ST24": "Starter Deck Green Jewelry Bonney",
    "ST25": "Starter Deck Blue Buggy",
    "ST26": "Starter Deck Purple Black Monkey D Luffy",
    "ST27": "Starter Deck Black Marshall D Teach",
    "ST28": "Starter Deck Green Yellow Yamato",
    "ST29": "Starter Deck Egghead",
    "P": "One Piece Promotion Cards",
}

PROJECT_MIRU_NAV = (
    ("home", "Home", "/"),
    ("cards", "Cards", "/cards"),
    ("sets", "Sets", "/sets"),
    ("leaders", "Leaders", "/leaders"),
    ("insights", "Insights (Beta)", "/insights"),
    ("watchlist", "Watchlist", "/#watchlist"),
    ("verified", "Verified", "/verified"),
    ("status", "Miru Status", "/status"),
)


def _project_nav(active_key: str) -> list[dict[str, str | bool]]:
    return [
        {
            "key": key,
            "label": label,
            "href": href,
            "active": key == active_key,
        }
        for key, label, href in PROJECT_MIRU_NAV
    ]


def _project_page_context(active_key: str, **extra):
    context = {
        "BUILD_ID": BUILD_ID,
        "nav_items": _project_nav(active_key),
        "active_nav": active_key,
    }
    context.update(extra)
    return context


class RowSqliteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _open_sqlite_row_db(db_path: str):
    if os.path.isfile(db_path):
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=1",
            uri=True,
            factory=RowSqliteConnection,
        )
    else:
        conn = sqlite3.connect(db_path, factory=RowSqliteConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_set_summaries():
    with get_catalog_conn(CATALOG_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                c.set_code,
                COALESCE(
                    NULLIF(MAX(c.set_name), ''),
                    NULLIF(MAX(s.set_name), ''),
                    c.set_code
                ) AS set_name,
                COUNT(*) AS total_cards,
                SUM(CASE WHEN lower(coalesce(c.verification_status, '')) = 'verified' THEN 1 ELSE 0 END) AS verified_cards,
                SUM(CASE WHEN lower(coalesce(c.verification_status, '')) = 'pending_confirmation' THEN 1 ELSE 0 END) AS pending_cards
            FROM cards c
            LEFT JOIN sets s ON s.set_code = c.set_code
            GROUP BY c.set_code
            ORDER BY
                CASE
                    WHEN c.set_code LIKE 'OP%' THEN 0
                    WHEN c.set_code LIKE 'EB%' THEN 1
                    WHEN c.set_code LIKE 'PRB%' THEN 2
                    WHEN c.set_code LIKE 'ST%' THEN 3
                    WHEN c.set_code = 'P' THEN 4
                    ELSE 5
                END,
                c.set_code
            """
        ).fetchall()
    items = []
    for row in rows:
        total_cards = int(row["total_cards"] or 0)
        verified_cards = int(row["verified_cards"] or 0)
        pending_cards = int(row["pending_cards"] or 0)
        completion_pct = round((verified_cards / total_cards) * 100, 1) if total_cards else 0.0
        items.append(
            {
                "set_code": row["set_code"],
                "set_name": row["set_name"] or row["set_code"],
                "total_cards": total_cards,
                "verified_cards": verified_cards,
                "pending_cards": pending_cards,
                "remaining_cards": max(total_cards - verified_cards, 0),
                "completion_pct": completion_pct,
                "is_complete": verified_cards >= total_cards and total_cards > 0,
            }
        )
    return items


def _fetch_leader_rows(limit: int = 200):
    with get_catalog_conn(CATALOG_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                c.canonical_code,
                c.card_name,
                c.set_code,
                c.set_name,
                c.color,
                c.life,
                c.power,
                c.effect_text,
                c.verification_status,
                c.confidence_level,
                c.traits,
                v.image_path
            FROM cards c
            LEFT JOIN card_variants v
                ON v.card_id = c.id
               AND v.is_base = 1
            WHERE lower(coalesce(c.card_type, '')) = 'leader'
            ORDER BY c.set_code, c.canonical_code
            LIMIT ?
            """,
            (max(1, min(int(limit or 200), 300)),),
        ).fetchall()
    items = []
    for row in rows:
        image_path = (row["image_path"] or "").strip()
        items.append(
            {
                **dict(row),
                "thumb_path": _thumb_path(image_path) or image_path,
            }
        )
    return items


def _fetch_verified_snapshot():
    with get_catalog_conn(CATALOG_DB_PATH) as conn:
        card_counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_cards,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) = 'verified' THEN 1 ELSE 0 END) AS verified_cards,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) = 'pending_confirmation' THEN 1 ELSE 0 END) AS pending_cards,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) IN ('', 'local-bootstrap') OR verification_status IS NULL THEN 1 ELSE 0 END) AS local_cards
            FROM cards
            """
        ).fetchone()
        validation_counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_validations,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) = 'verified' THEN 1 ELSE 0 END) AS verified_validations,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) = 'pending_confirmation' THEN 1 ELSE 0 END) AS pending_validations
            FROM miru_validations
            """
        ).fetchone()
        recent_rows = conn.execute(
            """
            SELECT
                v.card_code,
                COALESCE(c.card_name, '') AS card_name,
                COALESCE(c.set_code, '') AS set_code,
                v.verification_status,
                v.confidence_level,
                v.verified_at
            FROM miru_validations v
            LEFT JOIN cards c ON c.canonical_code = v.card_code
            ORDER BY COALESCE(v.updated_at, v.verified_at) DESC
            LIMIT 18
            """
        ).fetchall()
    return {
        "card_counts": dict(card_counts or {}),
        "validation_counts": dict(validation_counts or {}),
        "recent_rows": [dict(row) for row in recent_rows],
    }


def _fetch_status_snapshot():
    with get_catalog_conn(CATALOG_DB_PATH) as conn:
        catalog_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_cards,
                SUM(CASE WHEN lower(coalesce(verification_status, '')) = 'verified' THEN 1 ELSE 0 END) AS verified_cards
            FROM cards
            """
        ).fetchone()

    queue_counts: dict[str, int] = {}
    with _open_sqlite_row_db(LEARNING_QUEUE_DB_PATH) as conn:
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM learning_queue GROUP BY status"
        ).fetchall():
            queue_counts[(row["status"] or "").strip()] = int(row["count"] or 0)

    with _open_sqlite_row_db(LEARNING_LOG_DB_PATH) as conn:
        engine_row = conn.execute("SELECT * FROM engine_status LIMIT 1").fetchone()

    maintenance_state_path = Path(build_storage_layout().recommended_runtime_paths()["maintenance_state_json"])
    maintenance_state = {}
    if maintenance_state_path.is_file():
        try:
            maintenance_state = json.loads(maintenance_state_path.read_text(encoding="utf-8"))
        except Exception:
            maintenance_state = {"error": "maintenance_state_unreadable", "path": str(maintenance_state_path)}

    return {
        "catalog": dict(catalog_row or {}),
        "queue_counts": queue_counts,
        "engine": dict(engine_row or {}),
        "maintenance": maintenance_state,
        "insight_cache_metrics": get_insight_cache_metrics_snapshot(),
    }


def _fetch_insight_rows(limit: int = 18):
    with get_catalog_conn(CATALOG_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                c.canonical_code,
                c.card_name,
                c.set_code,
                c.card_type,
                c.color,
                c.verification_status,
                c.confidence_level,
                i.role_label,
                i.role_summary,
                i.deck_usage_summary,
                i.sync_status,
                v.image_path
            FROM card_intelligence i
            JOIN cards c ON c.id = i.card_id
            LEFT JOIN card_variants v
                ON v.card_id = c.id
               AND v.is_base = 1
            ORDER BY
                CASE WHEN lower(coalesce(c.verification_status, '')) = 'verified' THEN 0 ELSE 1 END,
                c.set_code,
                c.canonical_code
            LIMIT ?
            """,
            (max(1, min(int(limit or 18), 60)),),
        ).fetchall()
    items = []
    dossier = _get_miru_dossier_store()
    insight_cache = _get_miru_insight_cache()
    summary_cache: dict[str, dict] = {}
    bundle_cache: dict[str, dict] = {}
    for row in rows:
        image_path = (row["image_path"] or "").strip()
        row_payload = {**dict(row), "thumb_path": _thumb_path(image_path) or image_path}
        canonical_code = str(row_payload.get("canonical_code") or "").strip().upper()
        if canonical_code:
            if canonical_code not in summary_cache:
                try:
                    summary_cache[canonical_code] = _resolve_card_intelligence_summary(
                        canonical_code,
                        dossier=dossier,
                        insight_cache=insight_cache,
                        context_tag="insights_page:card_intelligence_summary",
                    )
                except Exception:
                    app.logger.warning("Insight page summary lookup failed for %s", canonical_code, exc_info=True)
                    summary_cache[canonical_code] = {}
            if canonical_code not in bundle_cache:
                try:
                    bundle_cache[canonical_code] = _resolve_card_typed_intelligence_bundle(
                        canonical_code,
                        dossier=dossier,
                        insight_cache=insight_cache,
                        context_prefix="insights_page",
                    )
                except Exception:
                    app.logger.warning("Insight page bundle lookup failed for %s", canonical_code, exc_info=True)
                    bundle_cache[canonical_code] = {}
            bundle = dict(bundle_cache.get(canonical_code) or {})
            row_payload["miru_card_summary"] = dict(summary_cache.get(canonical_code) or {})
            row_payload["miru_quick_insights"] = {
                "usage_insight": dict(bundle.get("usage_insight") or {}),
                "strategy_insight": dict(bundle.get("strategy_insight") or {}),
                "meta_insight": dict(bundle.get("meta_insight") or {}),
                "verified_loop_card_summary": dict(bundle.get("verified_loop_card_summary") or {}),
                "voice_primary": str(bundle.get("voice_primary") or ""),
            }
            row_payload["miru_voice_summary"] = str(bundle.get("voice_primary") or "").strip()
        else:
            row_payload["miru_card_summary"] = {}
            row_payload["miru_quick_insights"] = {}
            row_payload["miru_voice_summary"] = ""
        items.append(
            row_payload
        )
    return items


def _get_miru_dossier_store():
    return MiruDossierStore(Path(MIRU_DOSSIER_DB_PATH))


def _get_miru_insight_cache():
    return MiruInsightCacheRepository(MIRU_INSIGHT_CACHE_DB_PATH)


def _derive_effect_clarifier(effect_text: str) -> str:
    text = (effect_text or "").strip()
    lowered = text.lower()
    if not text:
        return ""
    if "[on play]" in lowered and "draw 1 card" in lowered:
        return "For newer players, that means you get the draw as soon as you play the card."
    if "when attacking" in lowered and "draw 1" in lowered:
        return "In practical terms, the draw happens when this card attacks."
    if "trigger" in lowered:
        return "For newer players, trigger text only matters when the card is revealed from life."
    return ""


def _join_miru_notes(*notes: str) -> str:
    parts = []
    seen = set()
    for note in notes:
        text = (note or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)
    return " ".join(parts).strip()


def _shape_miru_usage_text(*, usage_context: dict) -> dict:
    usage_posture = dict(usage_context.get("usage_posture") or {})
    usage_records = list(usage_context.get("usage_records") or [])
    leader_links = list(usage_context.get("leader_links") or [])
    evidence_posture = str(usage_posture.get("evidence_posture") or "no_usage_evidence_found")
    if not usage_records:
        return {
            "usage_summary": "",
            "usage_caution_note": "",
            "usage_reassurance_note": "",
            "usage_posture": usage_posture,
            "usage_records": usage_records,
            "leader_links": leader_links,
        }

    top_row = dict(usage_records[0] or {})
    leader_name = str(top_row.get("leader_name") or usage_posture.get("leader_name") or "").strip()
    leader_code = str(top_row.get("leader_code") or usage_posture.get("leader_code") or "").strip().upper()
    archetype_label = str(top_row.get("archetype_label") or usage_posture.get("archetype_label") or "").strip()
    role_classification = str(top_row.get("role_classification") or usage_posture.get("role_classification") or "").strip().lower()
    support_count = int(top_row.get("support_count") or usage_posture.get("support_count") or 0)
    leader_display = leader_name or leader_code

    role_text = ""
    if role_classification == "core":
        role_text = "as a core card"
    elif role_classification == "flex":
        role_text = "as a flex option"
    elif role_classification == "tech":
        role_text = "as a tech option"
    elif role_classification == "staple":
        role_text = "as a staple-level inclusion"

    summary_parts = []
    if evidence_posture == "verified_usage":
        if leader_display:
            summary_parts.append(f"Miru has most often seen this card with {leader_display} in verified usage records")
        else:
            summary_parts.append("Miru has verified usage records for this card")
    elif evidence_posture == "stale_usage_evidence":
        if leader_display:
            summary_parts.append(f"Miru has older stored usage tying this card to {leader_display}")
        else:
            summary_parts.append("Miru has older stored usage evidence for this card")
    elif evidence_posture == "partial_usage_evidence":
        if leader_display:
            summary_parts.append(f"So far, Miru has mostly seen this card with {leader_display}")
        else:
            summary_parts.append("Miru has some stored usage evidence for this card")
    else:
        summary_parts.append("Miru has a small amount of stored usage evidence for this card")

    detail_parts = []
    if archetype_label:
        detail_parts.append(f"in {archetype_label} lists")
    if role_text:
        detail_parts.append(role_text)
    if support_count > 0:
        detail_parts.append(f"across {support_count} supporting usage record{'s' if support_count != 1 else ''}")

    usage_summary = " ".join(summary_parts + detail_parts).strip()
    if usage_summary and not usage_summary.endswith("."):
        usage_summary += "."

    if evidence_posture == "verified_usage":
        usage_caution = ""
        usage_reassurance = "Miru is keeping that usage note tied to stored list evidence rather than guessing broader meta impact."
    elif evidence_posture == "stale_usage_evidence":
        usage_caution = str(usage_posture.get("caution_note") or "Miru's stored usage evidence looks stale, so it should not present this pattern as current meta certainty.").strip()
        usage_reassurance = str(usage_posture.get("reassurance_note") or "It can still share the historical pattern carefully while waiting for fresher verified decklist coverage.").strip()
    elif evidence_posture == "partial_usage_evidence":
        usage_caution = str(usage_posture.get("caution_note") or "Usage evidence exists, but coverage is still limited.").strip()
        usage_reassurance = str(usage_posture.get("reassurance_note") or "Miru can describe the current pattern carefully without overstating staple or meta status.").strip()
    else:
        usage_caution = str(usage_posture.get("caution_note") or "Usage evidence is still too thin for a strong deck-role claim.").strip()
        usage_reassurance = str(usage_posture.get("reassurance_note") or "Miru can stay cautious and avoid calling this a staple until coverage improves.").strip()

    return {
        "usage_summary": usage_summary,
        "usage_caution_note": usage_caution,
        "usage_reassurance_note": usage_reassurance,
        "usage_posture": usage_posture,
        "usage_records": usage_records,
        "leader_links": leader_links,
    }


def _shape_miru_insight_text(*, snapshot: dict, posture: dict, effect_text: str, verified_facts: list[dict], answer_fragments: list[dict], usage_context: dict) -> dict:
    card_code = str(snapshot.get("card_code") or "").strip().upper()
    card_name = str(snapshot.get("card_name") or "").strip()
    set_name = str(snapshot.get("set_name") or "").strip()
    card_type = str(snapshot.get("card_type") or "").strip()
    color = str(snapshot.get("color") or "").strip()
    rarity = str(snapshot.get("rarity") or "").strip()
    confidence_label = str(posture.get("confidence_label") or snapshot.get("confidence_label") or "no_evidence")
    evidence_posture = str(posture.get("evidence_posture") or "no_evidence_found")
    usage_shaped = _shape_miru_usage_text(usage_context=usage_context)
    caution_note = _join_miru_notes(
        str(posture.get("caution_note") or "").strip(),
        str(usage_shaped.get("usage_caution_note") or "").strip(),
    )
    reassurance_note = _join_miru_notes(
        str(posture.get("reassurance_note") or "").strip(),
        str(usage_shaped.get("usage_reassurance_note") or "").strip(),
    )

    identity_line = ""
    if card_name:
        identity_line = f"{card_code} is {card_name}"
        extras = [item for item in (color, card_type, rarity) if item]
        if extras:
            identity_line += " (" + ", ".join(extras) + ")"
        if set_name:
            identity_line += f" from {set_name}"
        identity_line += "."

    lines: list[str] = []
    if identity_line:
        if evidence_posture == "verified_fact":
            lines.append(identity_line + " Miru can answer this one from verified dossier data.")
        elif evidence_posture == "partial_evidence":
            lines.append(identity_line + " Miru has strong stored evidence here, but some details may still need stronger corroboration.")
        else:
            lines.append(identity_line + " Miru has some stored dossier context, but verified support is still incomplete.")

    if effect_text:
        prefix = (
            "Miru has verified the following effect text:"
            if evidence_posture == "verified_fact"
            else "Miru has the following stored effect text:"
        )
        lines.append(f"{prefix} {effect_text}")
        clarifier = _derive_effect_clarifier(effect_text)
        if clarifier:
            lines.append(clarifier)

    if not effect_text and evidence_posture != "no_evidence_found":
        fragment_map = {str(item.get("fragment_key") or ""): str(item.get("answer_text") or "").strip() for item in answer_fragments}
        fallback_summary = fragment_map.get("core_identity") or ""
        if fallback_summary and fallback_summary not in lines:
            lines.append(fallback_summary)

    usage_summary = str(usage_shaped.get("usage_summary") or "").strip()
    if usage_summary:
        lines.append(usage_summary)

    if caution_note:
        lines.append(caution_note)
    if reassurance_note:
        lines.append(reassurance_note)

    insight = " ".join(part.strip() for part in lines if part and part.strip()).strip()
    return {
        "insight": insight,
        "summary": insight,
        "confidence_label": confidence_label,
        "evidence_posture": evidence_posture,
        "caution_note": caution_note,
        "reassurance_note": reassurance_note,
        "usage_summary": usage_summary,
        "usage_posture": dict(usage_shaped.get("usage_posture") or {}),
        "response_style": "calm_verified_brief",
        "fact_count": len(list(verified_facts or [])),
    }


def _render_miru_voice_section(*, voice_type: str, summary: str, evidence_posture: str, context: dict) -> dict:
    text = str(summary or "").strip()
    if not text:
        return {}
    if shape_section_display is None:
        return {
            "insight_type": voice_type,
            "primary_text": text,
            "follow_up_text": None,
            "source_note": None,
            "category": "deck_usage",
        }
    try:
        display = dict(
            shape_section_display(
                voice_type,
                {"summary": text, "evidence_posture": str(evidence_posture or "").strip()},
                dict(context or {}),
            )
            or {}
        )
    except Exception:
        return {}
    if display.get("is_filler") or not str(display.get("primary_text") or "").strip():
        return {}
    display.pop("is_filler", None)
    display["insight_type"] = voice_type
    return display


def _build_miru_voice_sections(
    *,
    snapshot: dict,
    usage_context: dict,
    usage_payload: dict,
    strategy_payload: dict,
    meta_payload: dict,
    verified_loop_payload: dict,
) -> list[dict]:
    usage_records = list((usage_context or {}).get("usage_records") or [])
    top_usage = dict(usage_records[0] or {}) if usage_records else {}
    context = {
        "usage_leader": str(top_usage.get("leader_name") or "").strip(),
        "usage_archetype": str(top_usage.get("archetype_label") or "").strip(),
        "leader_name": str(top_usage.get("leader_name") or "").strip(),
        "archetype_label": str(top_usage.get("archetype_label") or "").strip(),
        "role_label": str(strategy_payload.get("role_label") or "").strip().lower(),
        "role_purpose": str(strategy_payload.get("role_purpose") or "").strip(),
        "trend_label": str(meta_payload.get("trend_label") or "").strip(),
        "card_name": str(snapshot.get("card_name") or "").strip(),
    }
    sections = [
        _render_miru_voice_section(
            voice_type="usage",
            summary=str(usage_payload.get("summary") or ""),
            evidence_posture=str(((usage_context or {}).get("usage_posture") or {}).get("evidence_posture") or ""),
            context=context,
        ),
        _render_miru_voice_section(
            voice_type="strategy",
            summary=str(strategy_payload.get("summary") or ""),
            evidence_posture=str(strategy_payload.get("evidence_posture") or ""),
            context=context,
        ),
        _render_miru_voice_section(
            voice_type="meta",
            summary=str(meta_payload.get("summary") or ""),
            evidence_posture=str(meta_payload.get("evidence_posture") or ""),
            context=context,
        ),
        _render_miru_voice_section(
            voice_type="usage",
            summary=str(verified_loop_payload.get("summary") or ""),
            evidence_posture=str(verified_loop_payload.get("evidence_posture") or ""),
            context=context,
        ),
    ]
    return [dict(item) for item in sections if item]


def _record_context_window_view(*, card_code: str, snapshot: dict, usage_context: dict) -> None:
    context_tag = "context_view:card_detail"
    try:
        record_contextual_view_metric(context_tag=context_tag)
    except Exception:
        pass
    if MIRU_CONTEXT_WINDOW is None:
        return
    usage_records = list((usage_context or {}).get("usage_records") or [])
    usage_top = dict(usage_records[0] or {}) if usage_records else {}
    leader_code = str(usage_top.get("leader_code") or "").strip().upper()
    try:
        MIRU_CONTEXT_WINDOW.record_card_view(
            card_code,
            leader_code=leader_code,
            is_leader=str(snapshot.get("card_type") or "").strip().lower() == "leader",
        )
    except Exception:
        return


def _build_contextual_opportunities_for_card(
    *,
    card_code: str,
    snapshot: dict,
    typed_bundle: dict,
    watchlist_context: dict | None = None,
    context_tag: str = "",
) -> list[dict]:
    if build_contextual_opportunities is None:
        return []
    context_snapshot = {}
    if MIRU_CONTEXT_WINDOW is not None:
        try:
            context_snapshot = dict(MIRU_CONTEXT_WINDOW.snapshot() or {})
        except Exception:
            context_snapshot = {}
    typed_payload = dict(typed_bundle.get("typed_insights") or {})
    if not typed_payload:
        typed_payload = {
            "card_intelligence_summary": dict(typed_bundle.get("card_intelligence_summary") or {}),
            "usage_insight": dict(typed_bundle.get("usage_insight") or {}),
            "strategy_insight": dict(typed_bundle.get("strategy_insight") or {}),
            "meta_insight": dict(typed_bundle.get("meta_insight") or {}),
        }
    try:
        opportunities = list(
            build_contextual_opportunities(
                card_code=card_code,
                snapshot=dict(snapshot or {}),
                typed_insights=typed_payload,
                context_snapshot=context_snapshot,
                watchlist_context=dict(watchlist_context or {}),
                max_items=3,
            )
            or []
        )
        if opportunities:
            opportunity_types = [str(item.get("opportunity_type") or "").strip() for item in opportunities]
            opportunity_types = [item for item in opportunity_types if item]
            if opportunity_types:
                try:
                    record_contextual_opportunity_metrics(
                        opportunity_types,
                        context_tag=str(context_tag or "").strip() or "contextual:unspecified",
                    )
                except Exception:
                    pass
        return opportunities
    except Exception:
        app.logger.warning("Contextual insight helper failed for %s", card_code, exc_info=True)
        return []


def _build_usage_insight_payload(*, canonical_code: str, usage_context: dict) -> dict:
    shaped = _shape_miru_usage_text(usage_context=usage_context)
    posture = dict(shaped.get("usage_posture") or {})
    return {
        "card_code": canonical_code,
        "summary": str(shaped.get("usage_summary") or "").strip(),
        "evidence_posture": str(posture.get("evidence_posture") or "no_usage_evidence_found"),
        "confidence_label": str(posture.get("confidence_label") or "no_evidence"),
        "leader_name": str(posture.get("leader_name") or "").strip(),
        "leader_code": str(posture.get("leader_code") or "").strip().upper(),
        "archetype_label": str(posture.get("archetype_label") or "").strip(),
        "role_classification": str(posture.get("role_classification") or "").strip().lower(),
        "support_count": int(posture.get("support_count") or 0),
        "caution_note": str(shaped.get("usage_caution_note") or "").strip(),
        "reassurance_note": str(shaped.get("usage_reassurance_note") or "").strip(),
        "source": "verified_dossier",
    }


def _build_strategy_insight_payload(*, canonical_code: str, strategy_posture: dict) -> dict:
    role_label = str(strategy_posture.get("role_label") or "").strip().lower()
    role_purpose = str(strategy_posture.get("role_purpose") or "").strip()
    summary = ""
    if role_label and role_purpose:
        summary = f"I usually slot this as a {role_label} piece: {role_purpose}."
    elif role_label:
        summary = f"I usually see this card filling a {role_label} role."
    elif role_purpose:
        summary = f"I usually use this card for {role_purpose}."
    return {
        "card_code": canonical_code,
        "summary": summary.strip(),
        "evidence_posture": str(strategy_posture.get("evidence_posture") or "no_strategy_evidence_found"),
        "confidence_label": str(strategy_posture.get("confidence_label") or "no_evidence"),
        "role_label": role_label,
        "role_purpose": role_purpose,
        "synergy_tags": list(strategy_posture.get("synergy_tags") or []),
        "caution_note": str(strategy_posture.get("caution_note") or "").strip(),
        "reassurance_note": str(strategy_posture.get("reassurance_note") or "").strip(),
        "source": "verified_dossier",
    }


def _build_meta_insight_payload(*, canonical_code: str, meta_posture: dict) -> dict:
    trend_label = str(meta_posture.get("trend_label") or "unknown").strip().lower()
    if trend_label == "rising":
        summary = "I am seeing this card trend up in the local verified meta sample."
    elif trend_label == "stable":
        summary = "I am seeing this card stay fairly stable in the local verified meta sample."
    elif trend_label == "stale":
        summary = "I am mostly seeing older meta evidence for this card right now."
    else:
        summary = "I have only a light local meta signal for this card right now."
    return {
        "card_code": canonical_code,
        "summary": summary,
        "evidence_posture": str(meta_posture.get("meta_posture") or "no_meta_evidence_found"),
        "confidence_label": str(meta_posture.get("confidence_label") or "no_evidence"),
        "trend_label": str(meta_posture.get("trend_label") or "unknown"),
        "support_count": int(meta_posture.get("support_count") or 0),
        "source_count": int(meta_posture.get("source_count") or 0),
        "caution_note": str(meta_posture.get("caution_note") or "").strip(),
        "reassurance_note": str(meta_posture.get("reassurance_note") or "").strip(),
        "source": "verified_dossier",
    }


def _build_verified_loop_summary_payload(
    *,
    canonical_code: str,
    snapshot: dict,
    posture: dict,
    card_summary: dict,
    usage_payload: dict,
) -> dict:
    card_name = str(snapshot.get("card_name") or "").strip()
    set_name = str(snapshot.get("set_name") or "").strip()
    confidence_label = str(posture.get("confidence_label") or "no_evidence")
    evidence_posture = str(posture.get("evidence_posture") or "no_evidence_found")
    overview_bits = []
    if card_name:
        overview_bits.append(f"{canonical_code} ({card_name})")
    else:
        overview_bits.append(canonical_code)
    if set_name:
        overview_bits.append(f"from {set_name}")
    summary = " ".join(overview_bits).strip()
    if summary:
        summary += "."
    usage_line = str(usage_payload.get("summary") or "").strip()
    if usage_line:
        summary = (summary + " " + usage_line).strip()
    return {
        "card_code": canonical_code,
        "summary": summary,
        "confidence_label": confidence_label,
        "evidence_posture": evidence_posture,
        "overall_confidence": float(card_summary.get("overall_confidence") or snapshot.get("confidence_score") or 0.0),
        "source": "verified_dossier",
    }


def _build_dashboard_card_insight_payload(
    *,
    canonical_code: str,
    dossier: MiruDossierStore,
    snapshot: dict,
    posture: dict,
    verified_facts: list[dict],
    effect_rows: list[dict],
    answer_fragments: list[dict],
    usage_context: dict,
) -> dict:
    card_name = str(snapshot.get("card_name") or "").strip()
    effect_text = str(dossier.fetch_effect_text(canonical_code) or "").strip()
    shaped = _shape_miru_insight_text(
        snapshot=snapshot,
        posture=posture,
        effect_text=effect_text,
        verified_facts=verified_facts,
        answer_fragments=answer_fragments,
        usage_context=usage_context,
    )
    return {
        "ok": True,
        "card_code": canonical_code,
        "card_name": card_name,
        "insight": str(shaped.get("insight") or ""),
        "summary": str(shaped.get("summary") or ""),
        "confidence_label": str(shaped.get("confidence_label") or "no_evidence"),
        "evidence_posture": str(shaped.get("evidence_posture") or "no_evidence_found"),
        "caution_note": str(shaped.get("caution_note") or ""),
        "reassurance_note": str(shaped.get("reassurance_note") or ""),
        "facts": verified_facts,
        "effects": effect_rows,
        "answer_fragments": answer_fragments,
        "usage_posture": dict(shaped.get("usage_posture") or {}),
        "usage_records": list((usage_context or {}).get("usage_records") or []),
        "leader_links": list((usage_context or {}).get("leader_links") or []),
        "usage_summary": str(shaped.get("usage_summary") or ""),
        "source": "verified_dossier",
        "response_style": str(shaped.get("response_style") or "calm_verified_brief"),
    }


def _truncate_insight_text(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    shortened = text[: max(limit - 3, 1)].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (shortened or text[: max(limit - 3, 1)]).strip() + "..."


def _build_watchlist_card_insight_payload(
    *,
    canonical_code: str,
    dossier: MiruDossierStore,
    snapshot: dict,
    posture: dict,
    verified_facts: list[dict],
    answer_fragments: list[dict],
    usage_context: dict,
) -> dict:
    effect_text = str(dossier.fetch_effect_text(canonical_code) or "").strip()
    shaped = _shape_miru_insight_text(
        snapshot=snapshot,
        posture=posture,
        effect_text=effect_text,
        verified_facts=verified_facts,
        answer_fragments=answer_fragments,
        usage_context=usage_context,
    )
    short_summary = _truncate_insight_text(
        str(shaped.get("summary") or shaped.get("insight") or ""),
        limit=160,
    )
    usage_posture = dict((usage_context or {}).get("usage_posture") or {})
    usage_records = list((usage_context or {}).get("usage_records") or [])
    usage_top = dict(usage_records[0] or {}) if usage_records else {}
    voice_payload = _render_miru_voice_section(
        voice_type="usage",
        summary=str(shaped.get("usage_summary") or short_summary),
        evidence_posture=str(usage_posture.get("evidence_posture") or shaped.get("evidence_posture") or ""),
        context={
            "usage_leader": str(usage_top.get("leader_name") or "").strip(),
            "usage_archetype": str(usage_top.get("archetype_label") or "").strip(),
            "leader_name": str(usage_top.get("leader_name") or "").strip(),
            "archetype_label": str(usage_top.get("archetype_label") or "").strip(),
        },
    )
    return {
        "card_code": canonical_code,
        "summary_short": short_summary,
        "confidence_label": str(shaped.get("confidence_label") or "no_evidence"),
        "evidence_posture": str(shaped.get("evidence_posture") or "no_evidence_found"),
        "voice_line": str(voice_payload.get("primary_text") or "").strip(),
        "source": "verified_dossier",
    }


def _resolve_card_intelligence_summary(
    card_code: str,
    *,
    dossier: MiruDossierStore | None = None,
    insight_cache: MiruInsightCacheRepository | None = None,
    precomputed_summary: dict | None = None,
    precomputed_snapshot: dict | None = None,
    context_tag: str = "card_summary_generic",
) -> dict:
    canonical_code = (card_code or "").strip().upper()
    if not canonical_code:
        return {}

    resolved_dossier = dossier or _get_miru_dossier_store()
    summary = dict(precomputed_summary or resolved_dossier.build_card_intelligence_summary(canonical_code) or {})
    if not summary:
        return {}

    snapshot = dict(precomputed_snapshot or resolved_dossier.fetch_card_snapshot(canonical_code) or {})
    resolved_cache = insight_cache or _get_miru_insight_cache()
    resolution = resolve_local_first_card_insight(
        resolved_cache,
        entity_id=canonical_code,
        insight_type=CARD_INTELLIGENCE_SUMMARY_TYPE,
        truth_context=summary,
        deterministic_builder=lambda: summary,
        confidence=float(
            summary.get("overall_confidence")
            or snapshot.get("confidence")
            or 0.0
        ),
        last_verified_at=str(snapshot.get("verified_at") or snapshot.get("updated_at") or ""),
        freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        context_tag=context_tag,
    )
    payload = dict(resolution.get("payload") or {})
    identity = dict(payload.get("identity") or {})
    payload.setdefault("card_name", str(identity.get("card_name") or ""))
    payload.setdefault("set_code", str(identity.get("set_code") or ""))
    payload.setdefault("set_name", str(identity.get("set_name") or ""))
    payload.setdefault("card_type", str(identity.get("card_type") or ""))
    payload["cache_status"] = "hit" if resolution.get("cache_hit") else "miss"
    payload["cache_layer"] = str(resolution.get("layer") or "")
    return payload


def _resolve_card_typed_intelligence_bundle(
    card_code: str,
    *,
    dossier: MiruDossierStore | None = None,
    insight_cache: MiruInsightCacheRepository | None = None,
    context_prefix: str = "typed_bundle",
) -> dict:
    canonical_code = (card_code or "").strip().upper()
    if not canonical_code:
        return {}

    resolved_dossier = dossier or _get_miru_dossier_store()
    resolved_cache = insight_cache or _get_miru_insight_cache()

    answer_context = resolved_dossier.build_answer_context(canonical_code)
    snapshot = dict(answer_context.get("snapshot") or {})
    if not snapshot:
        return {}
    posture = dict(answer_context.get("answer_posture") or {})
    facts = list(answer_context.get("facts") or [])
    effects = list(answer_context.get("effects") or [])
    answer_fragments = list(answer_context.get("answer_fragments") or [])
    usage_context = resolved_dossier.build_usage_context(canonical_code)
    strategy_posture = dict(resolved_dossier.build_strategy_posture(canonical_code) or {})
    meta_posture = dict(resolved_dossier.build_card_meta_posture(canonical_code) or {})
    card_summary = dict(resolved_dossier.build_card_intelligence_summary(canonical_code) or {})
    if not card_summary:
        card_summary = {}

    effect_text = str(resolved_dossier.fetch_effect_text(canonical_code) or "").strip()
    shaped = _shape_miru_insight_text(
        snapshot=snapshot,
        posture=posture,
        effect_text=effect_text,
        verified_facts=facts,
        answer_fragments=answer_fragments,
        usage_context=usage_context,
    )
    card_insight_truth = {
        "snapshot": snapshot,
        "posture": posture,
        "effect_text": effect_text,
        "verified_fact_count": len(facts),
        "answer_fragment_count": len(answer_fragments),
        "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
    }
    card_insight_resolution = resolve_local_first_card_insight(
        resolved_cache,
        entity_id=canonical_code,
        insight_type="card_insight",
        truth_context=card_insight_truth,
        deterministic_builder=lambda: {
            "card_code": canonical_code,
            "summary": str(shaped.get("summary") or ""),
            "insight": str(shaped.get("insight") or ""),
            "confidence_label": str(shaped.get("confidence_label") or "no_evidence"),
            "evidence_posture": str(shaped.get("evidence_posture") or "no_evidence_found"),
            "caution_note": str(shaped.get("caution_note") or ""),
            "reassurance_note": str(shaped.get("reassurance_note") or ""),
            "source": "verified_dossier",
        },
        confidence=float(snapshot.get("confidence_score") or posture.get("confidence_score") or 0.0),
        last_verified_at=str(snapshot.get("last_verified_at") or snapshot.get("verified_at") or ""),
        freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        context_tag=f"{context_prefix}:card_insight",
    )
    card_insight_payload = dict(card_insight_resolution.get("payload") or {})
    card_insight_payload["cache_status"] = "hit" if card_insight_resolution.get("cache_hit") else "miss"
    card_insight_payload["cache_layer"] = str(card_insight_resolution.get("layer") or "")

    usage_payload_seed = _build_usage_insight_payload(canonical_code=canonical_code, usage_context=usage_context)
    usage_last_verified_at = str(
        snapshot.get("last_verified_at")
        or snapshot.get("verified_at")
        or ((usage_context or {}).get("usage_posture") or {}).get("freshness_at")
        or ""
    )
    usage_posture_full = dict((usage_context or {}).get("usage_posture") or {})
    stable_usage_posture = {
        "evidence_posture": str(usage_posture_full.get("evidence_posture") or ""),
        "confidence_label": str(usage_posture_full.get("confidence_label") or ""),
        "leader_code": str(usage_posture_full.get("leader_code") or "").strip().upper(),
        "leader_name": str(usage_posture_full.get("leader_name") or "").strip(),
        "archetype_label": str(usage_posture_full.get("archetype_label") or "").strip(),
        "role_classification": str(usage_posture_full.get("role_classification") or "").strip().lower(),
        "support_count": int(usage_posture_full.get("support_count") or 0),
        "freshness_at": str(usage_posture_full.get("freshness_at") or "").strip(),
    }
    stable_usage_records = [
        {
            "leader_code": str(item.get("leader_code") or "").strip().upper(),
            "leader_name": str(item.get("leader_name") or "").strip(),
            "archetype_label": str(item.get("archetype_label") or "").strip(),
            "role_classification": str(item.get("role_classification") or "").strip().lower(),
            "support_count": int(item.get("support_count") or 0),
            "status": str(item.get("status") or "").strip().lower(),
        }
        for item in list((usage_context or {}).get("usage_records") or [])
    ]
    stable_leader_links = [
        {
            "leader_code": str(item.get("linked_card_code") or item.get("leader_code") or "").strip().upper(),
            "leader_name": str(item.get("leader_name") or "").strip(),
            "archetype_label": str(item.get("archetype_label") or "").strip(),
            "role_classification": str(item.get("role_classification") or "").strip().lower(),
            "support_count": int(item.get("support_count") or 0),
            "status": str(item.get("status") or "").strip().lower(),
        }
        for item in list((usage_context or {}).get("leader_links") or [])
    ]
    usage_truth_context = {
        "snapshot": snapshot,
        "usage_posture": stable_usage_posture,
        "usage_records": stable_usage_records,
        "leader_links": stable_leader_links,
    }
    usage_resolution = resolve_local_first_usage_insight(
        resolved_cache,
        entity_id=canonical_code,
        insight_type="usage_insight",
        truth_context=usage_truth_context,
        deterministic_builder=lambda: usage_payload_seed,
        confidence=float(((usage_context or {}).get("usage_posture") or {}).get("confidence_score") or 0.0),
        last_verified_at=usage_last_verified_at,
        freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        context_tag=f"{context_prefix}:usage_insight",
    )
    usage_payload = dict(usage_resolution.get("payload") or {})
    usage_payload["cache_status"] = "hit" if usage_resolution.get("cache_hit") else "miss"
    usage_payload["cache_layer"] = str(usage_resolution.get("layer") or "")

    leader_payload = {}
    if str(snapshot.get("card_type") or "").strip().lower() == "leader":
        leader_resolution = resolve_local_first_usage_insight(
            resolved_cache,
            entity_id=canonical_code,
            insight_type="leader_insight",
            truth_context=usage_truth_context,
            deterministic_builder=lambda: dict(usage_payload_seed),
            confidence=float(((usage_context or {}).get("usage_posture") or {}).get("confidence_score") or 0.0),
            last_verified_at=usage_last_verified_at,
            freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
            context_tag=f"{context_prefix}:leader_insight",
        )
        leader_payload = dict(leader_resolution.get("payload") or {})
        leader_payload["cache_status"] = "hit" if leader_resolution.get("cache_hit") else "miss"
        leader_payload["cache_layer"] = str(leader_resolution.get("layer") or "")

    strategy_payload_seed = _build_strategy_insight_payload(
        canonical_code=canonical_code,
        strategy_posture=strategy_posture,
    )
    strategy_truth_context = {
        "snapshot": snapshot,
        "strategy_posture": strategy_posture,
        "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
    }
    strategy_resolution = resolve_local_first_strategy_insight(
        resolved_cache,
        entity_id=canonical_code,
        insight_type="strategy_insight",
        truth_context=strategy_truth_context,
        deterministic_builder=lambda: strategy_payload_seed,
        confidence=float(strategy_posture.get("confidence_score") or 0.0),
        last_verified_at=str(strategy_posture.get("freshness_at") or snapshot.get("last_verified_at") or ""),
        freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        context_tag=f"{context_prefix}:strategy_insight",
    )
    strategy_payload = dict(strategy_resolution.get("payload") or {})
    strategy_payload["cache_status"] = "hit" if strategy_resolution.get("cache_hit") else "miss"
    strategy_payload["cache_layer"] = str(strategy_resolution.get("layer") or "")

    meta_payload_seed = _build_meta_insight_payload(canonical_code=canonical_code, meta_posture=meta_posture)
    meta_truth_context = {
        "snapshot": snapshot,
        "meta_posture": meta_posture,
        "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
    }
    meta_resolution = resolve_local_first_meta_insight(
        resolved_cache,
        entity_id=canonical_code,
        insight_type="meta_insight",
        truth_context=meta_truth_context,
        deterministic_builder=lambda: meta_payload_seed,
        confidence=float(meta_posture.get("recency_score") or 0.0),
        last_verified_at=str(meta_posture.get("freshness_at") or snapshot.get("last_verified_at") or ""),
        freshness_window=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        context_tag=f"{context_prefix}:meta_insight",
    )
    meta_payload = dict(meta_resolution.get("payload") or {})
    meta_payload["cache_status"] = "hit" if meta_resolution.get("cache_hit") else "miss"
    meta_payload["cache_layer"] = str(meta_resolution.get("layer") or "")

    verified_loop_seed = _build_verified_loop_summary_payload(
        canonical_code=canonical_code,
        snapshot=snapshot,
        posture=posture,
        card_summary=card_summary,
        usage_payload=usage_payload,
    )
    verified_loop_truth = {
        "snapshot": snapshot,
        "posture": posture,
        "card_summary": card_summary,
        "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
    }
    verified_loop_resolution = resolve_local_first_verified_loop_summary(
        resolved_cache,
        entity_id=canonical_code,
        insight_type="verified_loop_card_summary",
        truth_context=verified_loop_truth,
        deterministic_builder=lambda: verified_loop_seed,
        confidence=float(card_summary.get("overall_confidence") or snapshot.get("confidence_score") or 0.0),
        last_verified_at=str(snapshot.get("last_verified_at") or snapshot.get("verified_at") or ""),
        freshness_window=DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
        context_tag=f"{context_prefix}:verified_loop_card_summary",
    )
    verified_loop_payload = dict(verified_loop_resolution.get("payload") or {})
    verified_loop_payload["cache_status"] = "hit" if verified_loop_resolution.get("cache_hit") else "miss"
    verified_loop_payload["cache_layer"] = str(verified_loop_resolution.get("layer") or "")

    summary_payload = _resolve_card_intelligence_summary(
        canonical_code,
        dossier=resolved_dossier,
        insight_cache=resolved_cache,
        precomputed_summary=card_summary,
        precomputed_snapshot=snapshot,
        context_tag=f"{context_prefix}:card_intelligence_summary",
    )

    voice_sections = _build_miru_voice_sections(
        snapshot=snapshot,
        usage_context=usage_context,
        usage_payload=usage_payload,
        strategy_payload=strategy_payload,
        meta_payload=meta_payload,
        verified_loop_payload=verified_loop_payload,
    )
    voice_primary = str(voice_sections[0].get("primary_text") or "").strip() if voice_sections else ""
    typed_insights = {
        "card_insight": card_insight_payload,
        "usage_insight": usage_payload,
        "leader_insight": leader_payload,
        "strategy_insight": strategy_payload,
        "meta_insight": meta_payload,
        "verified_loop_card_summary": verified_loop_payload,
        "card_intelligence_summary": summary_payload,
    }
    return {
        "card_code": canonical_code,
        "card_insight": card_insight_payload,
        "usage_insight": usage_payload,
        "leader_insight": leader_payload,
        "strategy_insight": strategy_payload,
        "meta_insight": meta_payload,
        "verified_loop_card_summary": verified_loop_payload,
        "card_intelligence_summary": summary_payload,
        "typed_insights": typed_insights,
        "voice_sections": voice_sections,
        "voice_primary": voice_primary,
    }


def _resolve_watchlist_card_insight(card_code: str) -> dict:
    canonical_code = (card_code or "").strip().upper()
    if not canonical_code:
        return {}

    dossier = _get_miru_dossier_store()
    answer_context = dossier.build_answer_context(canonical_code)
    snapshot = dict(answer_context.get("snapshot") or {})
    if not snapshot:
        return {}

    verified_facts = list(answer_context.get("facts") or [])
    answer_fragments = list(answer_context.get("answer_fragments") or [])
    posture = dict(answer_context.get("answer_posture") or {})
    usage_context = dossier.build_usage_context(canonical_code)
    truth_context = {
        "snapshot": snapshot,
        "posture": posture,
        "verified_facts": verified_facts,
        "answer_fragments": answer_fragments,
        "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
        "leader_links": list((usage_context or {}).get("leader_links") or []),
    }
    insight_cache = _get_miru_insight_cache()
    resolution = resolve_local_first_surface_response(
        insight_cache,
        entity_id=canonical_code,
        insight_type="watchlist_card_brief",
        truth_context=truth_context,
        deterministic_builder=lambda: _build_watchlist_card_insight_payload(
            canonical_code=canonical_code,
            dossier=dossier,
            snapshot=snapshot,
            posture=posture,
            verified_facts=verified_facts,
            answer_fragments=answer_fragments,
            usage_context=usage_context,
        ),
        confidence=float(snapshot.get("confidence_score") or posture.get("confidence_score") or 0.0),
        last_verified_at=str(snapshot.get("last_verified_at") or snapshot.get("verified_at") or ""),
        freshness_window=DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
        context_tag="watchlist:card_brief",
    )
    payload = dict(resolution.get("payload") or {})
    payload["cache_status"] = "hit" if resolution.get("cache_hit") else "miss"
    payload["cache_layer"] = str(resolution.get("layer") or "")
    if not str(payload.get("voice_line") or "").strip():
        payload["voice_line"] = str(payload.get("summary_short") or "").strip()
    return payload


def _build_miru_insight_response(card_code: str):
    canonical_code = (card_code or "").strip().upper()
    dossier = _get_miru_dossier_store()
    answer_context = dossier.build_answer_context(canonical_code)
    snapshot = dict(answer_context.get("snapshot") or {})
    verified_facts = list(answer_context.get("facts") or [])
    effect_rows = list(answer_context.get("effects") or [])
    answer_fragments = list(answer_context.get("answer_fragments") or [])
    posture = dict(answer_context.get("answer_posture") or {})
    usage_context = dossier.build_usage_context(canonical_code)

    if not snapshot:
        return {
            "ok": False,
            "card_code": canonical_code,
            "insight": "",
            "summary": "",
            "confidence_label": str(posture.get("confidence_label") or "no_evidence"),
            "evidence_posture": str(posture.get("evidence_posture") or "no_evidence_found"),
            "caution_note": str(posture.get("caution_note") or ""),
            "reassurance_note": str(posture.get("reassurance_note") or ""),
            "facts": [],
            "effects": [],
            "answer_fragments": answer_fragments,
            "usage_posture": dict((usage_context or {}).get("usage_posture") or {}),
            "usage_records": list((usage_context or {}).get("usage_records") or []),
            "leader_links": list((usage_context or {}).get("leader_links") or []),
            "usage_summary": "",
            "source": "verified_dossier",
            "response_style": "calm_verified_brief",
            "typed_insights": {},
            "miru_voice_sections": [],
            "miru_voice_primary": "",
            "contextual_opportunities": [],
        }

    truth_context = {
        "snapshot": snapshot,
        "posture": posture,
        "verified_facts": verified_facts,
        "effects": effect_rows,
        "answer_fragments": answer_fragments,
        "usage_context": usage_context,
    }
    insight_cache = _get_miru_insight_cache()
    resolution = resolve_local_first_surface_response(
        insight_cache,
        entity_id=canonical_code,
        insight_type="dashboard_card_insight",
        truth_context=truth_context,
        deterministic_builder=lambda: _build_dashboard_card_insight_payload(
            canonical_code=canonical_code,
            dossier=dossier,
            snapshot=snapshot,
            posture=posture,
            verified_facts=verified_facts,
            effect_rows=effect_rows,
            answer_fragments=answer_fragments,
            usage_context=usage_context,
        ),
        confidence=float(snapshot.get("confidence_score") or posture.get("confidence_score") or 0.0),
        last_verified_at=str(snapshot.get("last_verified_at") or snapshot.get("verified_at") or ""),
        freshness_window=DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
        context_tag="api_miru_insight:dashboard_card_insight",
    )
    payload = dict(resolution.get("payload") or {})
    payload.setdefault("source", "verified_dossier")
    payload["cache_status"] = "hit" if resolution.get("cache_hit") else "miss"
    payload["cache_layer"] = str(resolution.get("layer") or "")
    typed_bundle = _resolve_card_typed_intelligence_bundle(
        canonical_code,
        dossier=dossier,
        insight_cache=insight_cache,
        context_prefix="api_miru_insight",
    )
    payload["typed_insights"] = dict(typed_bundle.get("typed_insights") or {})
    payload["card_intelligence_summary"] = dict(typed_bundle.get("card_intelligence_summary") or {})
    payload["miru_voice_sections"] = list(typed_bundle.get("voice_sections") or [])
    payload["miru_voice_primary"] = str(typed_bundle.get("voice_primary") or "")
    _record_context_window_view(card_code=canonical_code, snapshot=snapshot, usage_context=usage_context)
    payload["contextual_opportunities"] = _build_contextual_opportunities_for_card(
        card_code=canonical_code,
        snapshot=snapshot,
        typed_bundle=typed_bundle,
        context_tag="api_miru_insight:contextual",
    )
    return payload

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static",
    template_folder="templates",
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.logger.setLevel(logging.INFO)

if ENABLE_LOCAL_AI and LOCAL_AI_IMPORT_ERROR:
    app.logger.warning(
        "ENABLE_LOCAL_AI is set, but local AI helper could not be loaded: %s",
        LOCAL_AI_IMPORT_ERROR,
    )

ALT_MARKERS = (
    "alternate art",
    "alt art",
    "alt-art",
    "alternate-art",
    "parallel",
    "manga",
    "special art",
    "special",
    "pirate foil",
    "promo foil",
    "foil",
    "aa",
)

ILLUST_MARKERS = (
    "illustration",
    "illustration box",
    "illustrationbox",
    "illustrationboxvol",
    "illustrationboxvol.",
    "illustration box vol",
    "illustration box vol.",
)


def canonical_url(u: str) -> str:
    """
    Normalize URLs so:
    - prices.json (watcher) can match watchlist URLs even if query differs
    - we drop query + fragment
    """
    s = (u or "").strip()
    if not s:
        return ""
    try:
        p = urllib.parse.urlsplit(s)
        s = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except Exception:
        s = s.split("#")[0].split("?")[0]
    return s.rstrip("/")


def load_prices():
    try:
        with open(PRICES_PATH, "r", encoding="utf-8") as f:
            obj = json.load(f)
            return list(obj.values()) if isinstance(obj, dict) else []
    except Exception:
        return []


def load_last_prices():
    try:
        with open(LAST_PRICES_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def load_card_url_map():
    try:
        with open(CARD_URL_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_card_url_map(data: dict):
    try:
        tmp = CARD_URL_MAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CARD_URL_MAP_PATH)
        return True
    except Exception:
        return False


def _lookup_state_db_card_link(code: str, variant: str = ""):
    try:
        entry = resolve_card_link_memory(
            code,
            variant=variant,
            db_path=MIRU_STATE_DB_PATH,
        )
    except Exception as exc:
        app.logger.warning("State DB link lookup failed for %s: %s", code, exc)
        return None
    return entry if isinstance(entry, dict) else None


def save_confirmed_card_url(card_code: str, url: str, variant: str = "") -> str:
    map_key = _build_card_url_map_key(card_code, variant=variant)
    normalized_url = _normalize_exact_tcgplayer_product_url(url)
    if not map_key or not _is_direct_tcgplayer_product_url(normalized_url):
        return ""

    sqlite_ok = False
    try:
        sqlite_ok = bool(
            upsert_card_link_memory(
            card_code,
            normalized_url,
            variant=variant,
            source_kind="dashboard-confirmed-link",
            db_path=MIRU_STATE_DB_PATH,
        )
        )
        if sqlite_ok:
            app.logger.info("State DB link write ok for %s", map_key)
    except Exception:
        app.logger.warning("State DB link write failed for %s", map_key)

    card_url_map = load_card_url_map()
    card_url_map[map_key] = normalized_url
    json_ok = save_card_url_map(card_url_map)
    if json_ok:
        app.logger.info("JSON link compatibility write ok for %s", map_key)
    else:
        app.logger.warning("JSON link compatibility write failed for %s", map_key)

    if sqlite_ok or json_ok:
        refresh_local_indexes()
        return normalized_url
    return ""


def save_last_prices(d: dict):
    try:
        tmp = LAST_PRICES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LAST_PRICES_PATH)
    except Exception:
        pass


def fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def wants_alt(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ALT_MARKERS)


def wants_illustration(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ILLUST_MARKERS)


def variant_is_altish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    if v == "alt":
        return True
    return any(m in v for m in ALT_MARKERS)


def variant_is_illustrationish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    return any(m in v for m in ILLUST_MARKERS)


def _normalize_variant_text(value: str) -> str:
    s = (value or "").strip().lower()
    if not s:
        return ""

    s = re.sub(r"[\[\]()]+", " ", s)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\balternate\s+art\b", "alt", s)
    s = re.sub(r"\balt\s+art\b", "alt", s)
    s = re.sub(r"\balt(?:ernate)?\s*0*([1-9])\b", r"alt\1", s)
    s = re.sub(r"\bspecial\s+art\b", "sp", s)
    s = re.sub(r"\billustration\s+box\b", "illustration", s)
    s = re.sub(r"\bstarterpromo\b", "starter promo", s)
    s = re.sub(r"\bpremiumalt\b", "premium alt", s)
    s = re.sub(r"\bmagazinealt\b", "magazine alt", s)
    s = re.sub(r"\beb0?2alt\b", "eb02 alt", s)
    s = re.sub(r"\billustrationboxvol\.?\s*0*([1-9])\b", r"illustration vol \1", s)
    s = re.sub(r"\billustrationvol\.?\s*0*([1-9])\b", r"illustration vol \1", s)
    parts = []
    for token in re.split(r"\s+", s):
        token = (token or "").strip()
        if not token or token in VARIANT_NOISE_PARTS:
            continue
        parts.append(token)
    deduped = []
    for token in parts:
        if token not in deduped:
            deduped.append(token)
    s = " ".join(deduped).strip()
    return s


def _variant_tokens(value: str):
    normalized = _normalize_variant_text(value)
    tokens = set()
    if not normalized:
        return tokens

    for token in re.findall(r"[a-z0-9]+", normalized):
        if token in ("alt", "aa", "alternate"):
            tokens.add("alt")
            continue
        if token.startswith("alt") and token[3:].isdigit():
            tokens.add("alt")
            tokens.add(token)
            continue
        if token.startswith("illustration"):
            tokens.add("illustration")
            tokens.add(token)
            continue
        if token.startswith("magazine"):
            tokens.add("magazine")
            tokens.add(token)
            continue
        if token.startswith("premium"):
            tokens.add("premium")
            tokens.add(token)
            continue
        if token in ("sp", "special"):
            tokens.add("sp")
            continue
        if token in ("illust", "illustration"):
            tokens.add("illustration")
            continue
        if token in ("promo", "pr"):
            tokens.add("promo")
            continue
        if token in ("starter", "regional", "reward", "winner", "magazine", "premium", "championship", "store"):
            tokens.add(token)
            continue
        if token in ("parallel", "manga", "signed", "reprint", "winner", "reward", "store", "regional", "championship", "magazine", "premium", "tr", "cs"):
            tokens.add(token)
            continue
        if token in ("piratefoil",):
            tokens.add("pirate")
            tokens.add("foil")
            continue
        if len(token) == 1 and token.isalpha():
            tokens.add(f"suffix:{token}")
            continue
        tokens.add(token)

    return tokens


def _variant_signals(tokens):
    signals = set()
    token_set = set(tokens or [])
    if "alt" in token_set or any(t.startswith("alt") for t in token_set):
        signals.add("alt")
    if "sp" in token_set:
        signals.add("sp")
    if "illustration" in token_set:
        signals.add("illustration")
    if "promo" in token_set:
        signals.add("promo")
    if any(t.startswith("suffix:") for t in token_set):
        signals.add("suffix")
    if "foil" in token_set:
        signals.add("foil")
    if "parallel" in token_set:
        signals.add("parallel")
    if "manga" in token_set:
        signals.add("manga")
    if "signed" in token_set:
        signals.add("signed")
    if "reprint" in token_set:
        signals.add("reprint")
    return signals


def _build_variant_info(value: str):
    normalized = _normalize_variant_text(value)
    tokens = _variant_tokens(normalized)
    return {
        "display": normalized,
        "normalized": normalized,
        "tokens": sorted(tokens),
        "signals": sorted(_variant_signals(tokens)),
        "has_variant_evidence": bool(tokens),
    }


def _format_variant_label(value: str) -> str:
    normalized = _build_variant_info(value).get("normalized", "")
    if not normalized:
        return ""
    words = []
    for token in normalized.split():
        if token == "sp":
            words.append("SP")
        elif token == "alt":
            words.append("Alt")
        elif token == "promo":
            words.append("Promo")
        elif token == "eb02":
            words.append("EB02")
        elif token.isdigit():
            words.append(token)
        else:
            words.append(token.capitalize())
    label = " ".join(words)
    label = re.sub(r"Boxvol\s*([0-9])", r"Box Vol \1", label, flags=re.I)
    return label


def _build_card_url_map_key(card_code: str, variant: str = "") -> str:
    normalized_code = (
        normalize_card_code(card_code).get("canonical_code")
        or (card_code or "").strip().upper()
    )
    normalized_variant = _build_variant_info(variant).get("normalized", "")
    if not normalized_code:
        return ""
    if normalized_variant:
        return f"{normalized_code}::{normalized_variant}"
    return normalized_code


def _parse_card_url_map_key(value: str):
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if "::" not in raw:
        return raw.upper(), ""
    code, variant = raw.split("::", 1)
    return (code or "").strip().upper(), _build_variant_info(variant).get("normalized", "")


def parse_card_code(code: str):
    normalized = (code or "").strip().upper()
    if not normalized:
        return {}

    match = CARD_CODE_RE.match(normalized)
    if not match:
        return {}

    return {
        "code": normalized,
        "set_code": (match.group("set_code") or "").upper(),
        "card_number": (match.group("card_number") or "").strip(),
    }


def normalize_card_code(value: str):
    stem = os.path.splitext(os.path.basename(value or ""))[0].strip()
    if not stem:
        return {}

    match = CARD_ID_IN_TEXT_RE.search(stem)
    if not match:
        return {}

    set_code = (match.group("set_code") or "").upper()
    card_number = (match.group("card_number") or "").strip()
    canonical_code = f"{set_code}-{card_number}"

    variant_parts = []
    suffix = (match.group("suffix") or "").strip()
    if suffix:
        variant_parts.append(suffix)

    trailing = (stem[match.end() :] or "").strip()
    trailing = trailing.strip(" _-()[]")
    trailing = re.sub(r"[_\-]+", " ", trailing).strip()
    if trailing:
        variant_parts.append(trailing)

    variant_info = _build_variant_info(" ".join(part for part in variant_parts if part).strip())

    return {
        "set_code": set_code,
        "card_number": card_number,
        "variant": variant_info["display"],
        "variant_normalized": variant_info["normalized"],
        "variant_tokens": variant_info["tokens"],
        "variant_signals": variant_info["signals"],
        "has_variant_evidence": variant_info["has_variant_evidence"],
        "canonical_code": canonical_code,
    }


def parse_card_info_from_filename(filename: str):
    info = normalize_card_code(filename)
    if not info:
        return {}

    parsed = dict(info)
    parsed["filename"] = os.path.basename(filename or "")
    return parsed


def parse_card_filename(filename: str):
    parsed = parse_card_info_from_filename(filename)
    if not parsed:
        return {}
    return {
        "code": parsed["canonical_code"],
        "set_code": parsed["set_code"],
        "card_number": parsed["card_number"],
        "variant": parsed["variant"],
    }


def _path_variant_hint_tokens(rel_path: str):
    rel_path = (rel_path or "").replace("\\", "/").strip("/")
    if not rel_path:
        return []
    parts = [part.strip().lower() for part in rel_path.split("/")[:-1] if part.strip()]
    tokens = []
    for part in parts:
        if part in IMAGE_PATH_SKIP_PARTS:
            return ["__skip__"]
        token = IMAGE_DIR_VARIANT_HINTS.get(part)
        if token:
            tokens.append(token)
    return tokens


def parse_card_asset_path(rel_path: str):
    rel_path = (rel_path or "").replace("\\", "/").strip()
    if not rel_path:
        return {}

    filename = os.path.basename(rel_path)
    parsed = parse_card_info_from_filename(filename)
    if not parsed:
        return {}

    path_tokens = _path_variant_hint_tokens(rel_path)
    if "__skip__" in path_tokens:
        return {"skip": True, "filename": filename, "relative_path": rel_path}

    # Promotion cards often live under a "Promos" folder because of their set, not because
    # the folder name indicates a distinct print variant. Treating that path token as variant
    # evidence causes base P-xxx images like P-088 to be indexed as variant-only and drop out
    # of normal dashboard image resolution.
    if (parsed.get("set_code") or "").upper() == "P":
        path_tokens = [token for token in path_tokens if token != "promo"]

    merged_variant_parts = []
    if parsed.get("variant"):
        merged_variant_parts.append(parsed.get("variant") or "")
    if path_tokens:
        merged_variant_parts.append(" ".join(path_tokens))

    merged_variant_info = _build_variant_info(" ".join(part for part in merged_variant_parts if part).strip())
    if not merged_variant_info.get("has_variant_evidence") and "alt" in path_tokens:
        merged_variant_info = _build_variant_info("alt")

    enriched = dict(parsed)
    enriched["relative_path"] = rel_path
    enriched["filename"] = filename
    enriched["folder_context"] = "/".join(rel_path.split("/")[:-1])
    enriched["variant"] = merged_variant_info.get("display", "")
    enriched["variant_normalized"] = merged_variant_info.get("normalized", "")
    enriched["variant_tokens"] = merged_variant_info.get("tokens", [])
    enriched["variant_signals"] = merged_variant_info.get("signals", [])
    enriched["has_variant_evidence"] = bool(merged_variant_info.get("has_variant_evidence"))
    return enriched


def _set_name_from_code(code: str) -> str:
    parsed = parse_card_code(code)
    return SET_CODE_TO_NAME.get(parsed.get("set_code", ""), "")


def _extract_record_code(item: dict) -> str:
    code = (
        item.get("code")
        or item.get("card_id")
        or ""
    )
    code = str(code).strip().upper()
    if code:
        return code

    name = str(item.get("name") or item.get("tcgplayer_name") or "").strip()
    match = CODE_RE.search(name)
    return match.group(1).upper() if match else ""


def _new_local_card_entry(code: str):
    parsed = parse_card_code(code)
    normalized_code = (code or "").strip().upper()
    return {
        "code": normalized_code,
        "set_code": parsed.get("set_code", ""),
        "card_number": parsed.get("card_number", ""),
        "set_name": SET_CODE_TO_NAME.get(parsed.get("set_code", ""), ""),
        "normal": "",
        "alt": "",
        "illust": "",
        "variants": {},
        "variant_meta": {},
        "variant_direct_urls": {},
        "images": [],
        "urls": [],
        "direct_url": "",
        "names": [],
        "primary_name": "",
        "sources": [],
    }


def _append_unique_value(values: list, value: str):
    normalized = (value or "").strip()
    if normalized and normalized not in values:
        values.append(normalized)


def build_existing_record_index():
    by_code = {}

    for item in load_prices():
        code = _extract_record_code(item)
        if not code:
            continue

        entry = by_code.setdefault(code, _new_local_card_entry(code))
        name = clean_display_name((item.get("name") or "").strip(), code)
        url = _canonical_product_url((item.get("url") or "").strip())

        if name:
            _append_unique_value(entry["names"], name)
            if not entry["primary_name"]:
                entry["primary_name"] = name

        if url:
            _append_unique_value(entry["urls"], url)
            if _is_direct_tcgplayer_product_url(url) and not entry["direct_url"]:
                entry["direct_url"] = url

        _append_unique_value(entry["sources"], "prices")

    for link_entry in list_card_link_entries(db_path=MIRU_STATE_DB_PATH):
        normalized_code = (link_entry.get("canonical_code") or "").strip().upper()
        if not normalized_code:
            continue

        entry = by_code.setdefault(normalized_code, _new_local_card_entry(normalized_code))
        url = _canonical_product_url(link_entry.get("url"))
        mapped_variant = _build_variant_info(link_entry.get("variant_key") or "").get("normalized", "")
        if not url:
            continue

        _append_unique_value(entry["urls"], url)
        if mapped_variant and _is_direct_tcgplayer_product_url(url):
            entry["variant_direct_urls"][mapped_variant] = url
        elif _is_direct_tcgplayer_product_url(url) and not entry["direct_url"]:
            entry["direct_url"] = url
        _append_unique_value(entry["sources"], "state-db")

    for map_key, raw_url in load_card_url_map().items():
        normalized_code, mapped_variant = _parse_card_url_map_key(map_key)
        if not normalized_code:
            continue

        entry = by_code.setdefault(normalized_code, _new_local_card_entry(normalized_code))
        url = _canonical_product_url(raw_url)
        if not url:
            continue

        _append_unique_value(entry["urls"], url)
        if (
            mapped_variant
            and _is_direct_tcgplayer_product_url(url)
            and mapped_variant not in entry["variant_direct_urls"]
        ):
            entry["variant_direct_urls"][mapped_variant] = url
        elif _is_direct_tcgplayer_product_url(url) and not entry["direct_url"]:
            entry["direct_url"] = url
        _append_unique_value(entry["sources"], "map")

    return by_code


def build_image_index():
    by_base = {}
    by_code = {}

    if not os.path.isdir(IMAGES_ROOT):
        return {"by_base": by_base, "by_code": by_code}

    for root, _, files in os.walk(IMAGES_ROOT):
        for fn in files:
            low = fn.lower()
            if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue

            base, _ext = os.path.splitext(fn)
            if not base:
                continue

            abs_path = os.path.join(root, fn)
            rel_path = os.path.relpath(abs_path, IMAGES_ROOT).replace("\\", "/")
            parsed = parse_card_asset_path(rel_path)
            if not parsed or parsed.get("skip"):
                continue

            code = parsed["canonical_code"]
            variant = parsed["variant"]
            variant_l = variant.lower()
            if not parsed.get("has_variant_evidence"):
                by_base[base.upper()] = rel_path

            entry = by_code.setdefault(code, {"variants": {}, "variant_meta": {}})

            if variant == "":
                entry["normal"] = rel_path
            else:
                entry["variants"][variant_l] = rel_path
                entry["variant_meta"][variant_l] = {
                    "variant": variant,
                    "normalized": parsed.get("variant_normalized", variant_l),
                    "tokens": parsed.get("variant_tokens") or [],
                    "signals": parsed.get("variant_signals") or [],
                    "has_variant_evidence": bool(parsed.get("has_variant_evidence")),
                    "folder_context": parsed.get("folder_context") or "",
                    "relative_path": rel_path,
                }
                by_base[f"{code}({variant})".upper()] = rel_path
                if variant_is_altish(variant_l):
                    entry["alt"] = rel_path
                if variant_is_illustrationish(variant_l):
                    entry["illust"] = rel_path

    return {"by_base": by_base, "by_code": by_code}


def build_local_card_index(image_index=None):
    image_index = image_index or build_image_index()
    by_code = {}

    for code, image_entry in (image_index.get("by_code") or {}).items():
        normalized_code = (code or "").strip().upper()
        if not normalized_code:
            continue

        entry = by_code.setdefault(normalized_code, _new_local_card_entry(normalized_code))

        normal_path = (image_entry.get("normal") or "").strip()
        if normal_path:
            entry["normal"] = normal_path
            _append_unique_value(entry["images"], normal_path)

        alt_path = (image_entry.get("alt") or "").strip()
        if alt_path:
            entry["alt"] = alt_path

        illust_path = (image_entry.get("illust") or "").strip()
        if illust_path:
            entry["illust"] = illust_path

        for variant_name, path in (image_entry.get("variants") or {}).items():
            variant_key = (variant_name or "").strip().lower()
            path = (path or "").strip()
            if not variant_key or not path:
                continue
            entry["variants"][variant_key] = path
            entry["variant_meta"][variant_key] = dict(
                ((image_entry.get("variant_meta") or {}).get(variant_key) or {})
            )
            _append_unique_value(entry["images"], path)

        _append_unique_value(entry["sources"], "images")

    for code, record_entry in build_existing_record_index().items():
        entry = by_code.setdefault(code, _new_local_card_entry(code))

        if record_entry.get("direct_url") and not entry["direct_url"]:
            entry["direct_url"] = record_entry["direct_url"]

        for name in record_entry.get("names") or []:
            _append_unique_value(entry["names"], name)
        if not entry["primary_name"]:
            entry["primary_name"] = record_entry.get("primary_name") or ""

        for url in record_entry.get("urls") or []:
            _append_unique_value(entry["urls"], url)

        for source in record_entry.get("sources") or []:
            _append_unique_value(entry["sources"], source)
        for variant_key, direct_url in (record_entry.get("variant_direct_urls") or {}).items():
            if variant_key and direct_url:
                entry["variant_direct_urls"][variant_key] = direct_url

    return {
        "image_index": image_index,
        "by_code": by_code,
    }


def _build_candidate_payload(entry: dict, variant: str, image_path: str, confidence: str, reason: str):
    variant_info = _build_variant_info(variant)
    variant_key = (variant_info.get("normalized") or "").strip().lower()
    direct_url = ""
    if variant_key:
        direct_url = _canonical_product_url(
            (entry.get("variant_direct_urls") or {}).get(variant_key)
        )
    if not direct_url:
        direct_url = _canonical_product_url(entry.get("direct_url"))

    intelligence_snapshot = MIRU_CARD_INTEL.build_candidate_snapshot(
        (entry.get("code") or "").strip().upper(),
        variant_key=variant_key,
        image_path=image_path,
    )
    return {
        "canonical_code": (entry.get("code") or "").strip().upper(),
        "set_code": (entry.get("set_code") or "").strip().upper(),
        "card_number": (entry.get("card_number") or "").strip(),
        "variant": (
            (intelligence_snapshot.get("variant_profile") or {}).get("variant_label")
            or _format_variant_label(variant)
            or (variant or "").strip()
        ),
        "variant_display": (
            (intelligence_snapshot.get("variant_profile") or {}).get("variant_label")
            or _format_variant_label(variant)
            or (variant or "").strip()
        ),
        "variant_key": variant_key,
        "image_path": (image_path or "").strip(),
        "name": (
            (intelligence_snapshot.get("card_profile") or {}).get("card_name")
            or (entry.get("primary_name") or "").strip()
        ),
        "direct_url": direct_url,
        "confidence": confidence,
        "reason": reason,
        "card_profile": intelligence_snapshot.get("card_profile") or {},
        "variant_profile": intelligence_snapshot.get("variant_profile") or {},
        "intelligence": intelligence_snapshot.get("intelligence") or {},
    }


_CATALOG_HINT_CACHE = {}


def _normalize_hint_text(value: str) -> str:
    s = (value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _candidate_sort_key(candidate: dict):
    return (
        (candidate.get("canonical_code") or "").strip().upper(),
        (candidate.get("variant") or "").strip().lower(),
        (candidate.get("image_path") or "").strip(),
    )


def _get_catalog_hint(canonical_code: str):
    code = (canonical_code or "").strip().upper()
    if not code:
        return {}
    if code in _CATALOG_HINT_CACHE:
        return _CATALOG_HINT_CACHE[code]

    hint = {}
    try:
        with get_catalog_conn(CATALOG_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT canonical_code, card_name, set_name, rarity
                FROM cards
                WHERE canonical_code = ?
                """,
                (code,),
            ).fetchone()
            if row:
                hint = dict(row)
    except Exception:
        hint = {}

    _CATALOG_HINT_CACHE[code] = hint
    return hint


def _resolve_ai_image_path(card_input: str, parsed: dict, local_index=None) -> str:
    local_index = local_index or LOCAL_CARD_INDEX
    image_index = (local_index.get("image_index") or {}) if isinstance(local_index, dict) else {}
    by_base = (image_index.get("by_base") or {}) if isinstance(image_index, dict) else {}

    stem = os.path.splitext(os.path.basename(card_input or ""))[0].strip().upper()
    rel_path = (by_base.get(stem) or "").strip()

    if not rel_path and parsed:
        code = (parsed.get("canonical_code") or "").strip().upper()
        entry = ((local_index.get("by_code") or {}).get(code) or {})
        variant_key = (parsed.get("variant_normalized") or "").strip().lower()
        if variant_key:
            rel_path = (((entry.get("variants") or {}).get(variant_key)) or "").strip()
        if not rel_path:
            rel_path = (choose_image_path(os.path.basename(card_input or ""), code) or "").strip()

    if not rel_path:
        return ""
    return os.path.join(IMAGES_ROOT, rel_path.replace("/", os.sep))


def _should_attempt_local_ai(card_input: str, result: dict) -> bool:
    if not ENABLE_LOCAL_AI or not callable(analyze_card_image) or not callable(ai_result_has_useful_signals):
        return False
    if not (card_input or "").strip():
        return False
    # Preserve the existing source-of-truth path: strong local matches do not call AI.
    if result.get("match_status") == "auto_matched" and result.get("confidence") == "high":
        return False

    parsed = result.get("parsed") or {}
    candidates = result.get("candidates") or []
    if not parsed:
        return True
    if parsed.get("has_variant_evidence"):
        return True
    if result.get("match_status") in ("unresolved", "needs_review"):
        return True
    if result.get("confidence") in ("none", "low"):
        return True
    if len(candidates) != 1:
        return True
    return bool(candidates and candidates[0].get("confidence") != "high")


def _ai_visible_canonical_code(ai_result: dict) -> str:
    full_code = (ai_result.get("visible_full_code") or "").strip().upper()
    if parse_card_code(full_code):
        return full_code

    set_code = (ai_result.get("visible_set_code") or "").strip().upper()
    card_number = (ai_result.get("visible_card_number") or "").strip().upper()
    if not set_code or not card_number:
        return ""

    candidate_code = f"{set_code}-{card_number}"
    return candidate_code if parse_card_code(candidate_code) else ""


def _candidate_matches_ai_variant_hint(candidate: dict, ai_variant_hint: str) -> bool:
    ai_variant_hint = (ai_variant_hint or "").strip().lower()
    if not ai_variant_hint:
        return not (candidate.get("variant") or "").strip()

    info = _build_variant_info(candidate.get("variant") or "")
    signals = set(info.get("signals") or [])
    tokens = set(info.get("tokens") or [])

    if ai_variant_hint == "alt art":
        return "alt" in signals
    if ai_variant_hint == "sp":
        return "sp" in signals or "sp" in tokens
    if ai_variant_hint == "promo":
        return "promo" in signals or "promo" in tokens
    if ai_variant_hint == "parallel":
        return "parallel" in tokens
    if ai_variant_hint == "manga":
        return "manga" in tokens
    if ai_variant_hint == "signed":
        return "signed" in tokens
    return False


def _score_candidate_with_ai(candidate: dict, ai_result: dict, allow_base_preference: bool = False):
    score = 0
    reasons = []

    ai_code = _ai_visible_canonical_code(ai_result)
    candidate_code = (candidate.get("canonical_code") or "").strip().upper()
    if ai_code and candidate_code == ai_code:
        score += 120
        reasons.append("AI visible code")
    else:
        ai_set_code = (ai_result.get("visible_set_code") or "").strip().upper()
        ai_card_number = (ai_result.get("visible_card_number") or "").strip().upper()
        candidate_set_code = (candidate.get("set_code") or "").strip().upper()
        candidate_card_number = (candidate.get("card_number") or "").strip().upper()
        if ai_set_code and ai_card_number and candidate_set_code == ai_set_code and candidate_card_number == ai_card_number:
            score += 100
            reasons.append("AI set/number")

    ai_rarity = (ai_result.get("rarity_text") or "").strip().upper()
    if ai_rarity:
        hint = _get_catalog_hint(candidate_code)
        if (hint.get("rarity") or "").strip().upper() == ai_rarity:
            score += 40
            reasons.append("AI rarity")

    ai_name = _normalize_hint_text(ai_result.get("visible_name_guess") or "")
    candidate_name = _normalize_hint_text(candidate.get("name") or "")
    if ai_name and candidate_name:
        if ai_name == candidate_name:
            score += 14
            reasons.append("AI name")
        elif ai_name in candidate_name or candidate_name in ai_name:
            score += 8
            reasons.append("AI partial name")

    ai_variant_hint = (ai_result.get("variant_hint") or "").strip().lower()
    if ai_variant_hint and _candidate_matches_ai_variant_hint(candidate, ai_variant_hint):
        score += 10
        reasons.append("AI variant hint")
    # A missing variant hint is not proof of a base print. At most, allow a tiny base-only nudge
    # when the parsed input itself has no variant evidence and the existing logic already needed help.
    elif allow_base_preference and not ai_variant_hint and not (candidate.get("variant") or "").strip():
        score += 1
        reasons.append("AI no-variant signal")

    return score, reasons


def _build_ai_review_candidates(entry: dict) -> list:
    candidates = []
    if entry.get("normal"):
        candidates.append(
            _build_candidate_payload(
                entry,
                "",
                entry.get("normal"),
                "medium",
                "Local AI visible code hint",
            )
        )

    for variant_name, image_path in sorted((entry.get("variants") or {}).items()):
        candidates.append(
            _build_candidate_payload(
                entry,
                variant_name,
                image_path,
                "low",
                "Local AI visible code hint",
            )
        )
        if len(candidates) >= 6:
            break

    return candidates[:6]


def _dedupe_candidates(candidates: list) -> list:
    deduped = []
    seen = set()
    for candidate in candidates or []:
        key = _candidate_sort_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _is_local_ai_failure_note(note: str) -> bool:
    s = (note or "").strip().lower()
    return s.startswith("request failed") or s.startswith("parse failure")


def _maybe_apply_local_ai_hints(result: dict, card_input: str = "", local_index=None):
    if not _should_attempt_local_ai(card_input, result):
        return result

    local_index = local_index or LOCAL_CARD_INDEX
    parsed = result.get("parsed") or {}
    image_path = _resolve_ai_image_path(card_input, parsed, local_index=local_index)
    if not image_path or not os.path.isfile(image_path):
        return result

    ai_result = analyze_card_image(image_path=image_path, model=LOCAL_AI_MODEL)
    enriched = dict(result)
    enriched["ai_signals"] = ai_result
    enriched["ai_used"] = True
    enriched["ai_applied"] = False
    enriched["ai_skip_reason"] = None

    if not ai_result_has_useful_signals(ai_result):
        note = (ai_result.get("notes") or "").strip()
        enriched["ai_skip_reason"] = note or "no useful signals"
        if _is_local_ai_failure_note(note):
            app.logger.warning(
                "Local AI helper unavailable for %s: %s",
                os.path.basename(image_path),
                note,
            )
        return enriched

    candidates = [dict(candidate) for candidate in (result.get("candidates") or [])]
    ai_code = _ai_visible_canonical_code(ai_result)
    if ai_code:
        ai_entry = ((local_index.get("by_code") or {}).get(ai_code) or {})
        if ai_entry:
            candidates.extend(_build_ai_review_candidates(ai_entry))

    candidates = _dedupe_candidates(candidates)
    ranked = []
    ai_helped = False
    allow_base_preference = not bool(parsed.get("has_variant_evidence"))
    for index, candidate in enumerate(candidates):
        score, reasons = _score_candidate_with_ai(
            candidate,
            ai_result,
            allow_base_preference=allow_base_preference,
        )
        updated = dict(candidate)
        if reasons:
            ai_helped = True
            updated["reason"] = (
                f"{candidate.get('reason')}. AI hints: {', '.join(reasons)}"
                if candidate.get("reason")
                else f"AI hints: {', '.join(reasons)}"
            )
        ranked.append((score, index, updated))

    if not ai_helped:
        enriched["ai_skip_reason"] = "signals did not change ranking"
        return enriched

    ranked.sort(key=lambda item: (-item[0], item[1]))
    enriched["candidates"] = [item[2] for item in ranked[:6]]
    enriched["auto_accept"] = False
    enriched["ai_applied"] = True

    if enriched.get("match_status") == "unresolved" and enriched["candidates"]:
        enriched["match_status"] = "needs_review"
        enriched["confidence"] = "low"
        enriched["msg"] = "Local AI produced weak ranking hints. Review suggested candidates below."
    elif enriched.get("match_status") == "needs_review":
        enriched["msg"] = (
            f"{enriched.get('msg')} Local AI ranking hints applied."
            if enriched.get("msg")
            else "Local AI ranking hints applied."
        )

    return enriched


def _score_variant_candidate(parsed: dict, variant_name: str, variant_meta: dict):
    parsed_variant = _build_variant_info(parsed.get("variant") or "")
    candidate_variant = _build_variant_info(variant_name or "")

    parsed_norm = parsed_variant.get("normalized", "")
    candidate_norm = candidate_variant.get("normalized", "")
    parsed_tokens = set(parsed_variant.get("tokens") or [])
    candidate_tokens = set(
        (variant_meta or {}).get("tokens") or candidate_variant.get("tokens") or []
    )
    parsed_signals = set(parsed_variant.get("signals") or [])
    candidate_signals = set(
        (variant_meta or {}).get("signals") or candidate_variant.get("signals") or []
    )

    score = 0
    if parsed_norm and candidate_norm and parsed_norm == candidate_norm:
        score += 120

    token_overlap = parsed_tokens & candidate_tokens
    score += len(token_overlap) * 18

    signal_overlap = parsed_signals & candidate_signals
    score += len(signal_overlap) * 22

    parsed_suffix = {t for t in parsed_tokens if t.startswith("suffix:")}
    candidate_suffix = {t for t in candidate_tokens if t.startswith("suffix:")}
    if parsed_suffix and candidate_suffix and parsed_suffix == candidate_suffix:
        score += 20

    if parsed_norm and candidate_norm and parsed_norm in candidate_norm:
        score += 8

    return score


def find_card_candidates(card_input: str = "", variant: str = "", local_index=None):
    local_index = local_index or LOCAL_CARD_INDEX
    parsed = parse_card_info_from_filename(card_input) or normalize_card_code(card_input)

    if parsed and (variant or "").strip():
        variant_info = _build_variant_info(variant)
        parsed["variant"] = variant_info["display"]
        parsed["variant_normalized"] = variant_info["normalized"]
        parsed["variant_tokens"] = variant_info["tokens"]
        parsed["variant_signals"] = variant_info["signals"]
        parsed["has_variant_evidence"] = variant_info["has_variant_evidence"]

    if not parsed:
        query = (os.path.splitext(os.path.basename(card_input or ""))[0]).strip().lower()
        candidates = []
        if query:
            for entry in (local_index.get("by_code") or {}).values():
                hay = " ".join(
                    [
                        (entry.get("code") or "").lower(),
                        (entry.get("primary_name") or "").lower(),
                        " ".join((entry.get("variants") or {}).keys()).lower(),
                    ]
                )
                if query in hay:
                    candidates.append(
                        _build_candidate_payload(
                            entry,
                            "",
                            (entry.get("normal") or "").strip(),
                            "low",
                            "Fuzzy local match",
                        )
                    )
                if len(candidates) >= 6:
                    break

        return _maybe_apply_local_ai_hints({
            "parsed": {},
            "match_status": "needs_review" if candidates else "unresolved",
            "confidence": "low" if candidates else "none",
            "auto_accept": False,
            "candidates": candidates,
            "msg": "No exact card code parsed from input",
        }, card_input=card_input, local_index=local_index)

    canonical_code = parsed["canonical_code"]
    entry = (local_index.get("by_code") or {}).get(canonical_code)
    if not entry:
        snapshot = MIRU_CARD_INTEL.build_candidate_snapshot(canonical_code)
        return _maybe_apply_local_ai_hints({
            "parsed": parsed,
            "match_status": "unresolved",
            "confidence": "none",
            "auto_accept": False,
            "candidates": [],
            "msg": (
                f"No local image-backed card match found for {canonical_code}. "
                "Trusted canonical data may exist, but Miru does not have a local image or variant record yet."
                if snapshot
                else f"No local card match found for {canonical_code}"
            ),
            "card_profile": snapshot.get("card_profile") or {},
            "variant_profile": snapshot.get("variant_profile") or {},
            "intelligence": snapshot.get("intelligence") or {},
        }, card_input=card_input, local_index=local_index)

    variant_value = (parsed.get("variant") or "").strip()
    variant_key = (parsed.get("variant_normalized") or variant_value.lower()).strip()
    has_variant_evidence = bool(parsed.get("has_variant_evidence"))
    candidates = []

    if has_variant_evidence:
        ranked = []
        for variant_name, image_path in sorted((entry.get("variants") or {}).items()):
            variant_meta = ((entry.get("variant_meta") or {}).get(variant_name) or {})
            score = _score_variant_candidate(parsed, variant_name, variant_meta)
            if score <= 0:
                continue
            confidence = "high" if score >= 120 else "medium"
            ranked.append(
                (
                    score,
                    _build_candidate_payload(
                        entry,
                        variant_name,
                        image_path,
                        confidence,
                        "Variant evidence aligned with selected image",
                    ),
                )
            )

        ranked.sort(key=lambda item: (-item[0], item[1].get("variant", "")))
        candidates = [item[1] for item in ranked[:6]]

        if len(candidates) == 1 and candidates[0]["confidence"] in ("high", "medium"):
            return {
                "parsed": parsed,
                "match_status": "auto_matched",
                "confidence": candidates[0]["confidence"],
                "auto_accept": True,
                "candidates": candidates,
                "msg": f"Auto-matched {canonical_code} variant",
            }

        if len(ranked) >= 2:
            top_score = ranked[0][0]
            runner_up_score = ranked[1][0]
            if top_score >= 120 and (top_score - runner_up_score) >= 60:
                return {
                    "parsed": parsed,
                    "match_status": "auto_matched",
                    "confidence": candidates[0]["confidence"],
                    "auto_accept": True,
                    "candidates": [candidates[0]],
                    "msg": f"Auto-matched {canonical_code} variant with a clear score lead",
                }

        if candidates:
            app.logger.info(
                "Variant review required for %s from %s; matched_candidates=%s",
                canonical_code,
                card_input,
                len(candidates),
            )
            return _maybe_apply_local_ai_hints({
                "parsed": parsed,
                "match_status": "needs_review",
                "confidence": "medium",
                "auto_accept": False,
                "candidates": candidates,
                "msg": (
                    f"Variant evidence detected for {canonical_code}. "
                    "Review the matching variant candidates below."
                ),
            }, card_input=card_input, local_index=local_index)

        app.logger.warning(
            "Variant mismatch for %s from %s; requested_variant=%s",
            canonical_code,
            card_input,
            parsed.get("variant"),
        )
        return _maybe_apply_local_ai_hints({
            "parsed": parsed,
            "match_status": "unresolved",
            "confidence": "none",
            "auto_accept": False,
            "candidates": [],
            "msg": (
                f"Variant evidence detected for {canonical_code}, but no matching local variant was found. "
                "Base card was not auto-selected."
            ),
        }, card_input=card_input, local_index=local_index)

    if entry.get("normal"):
        candidates.append(
            _build_candidate_payload(
                entry,
                "",
                entry.get("normal"),
                "high",
                "Exact base-card code match",
            )
        )
        return {
            "parsed": parsed,
            "match_status": "auto_matched",
            "confidence": "high",
            "auto_accept": True,
            "candidates": candidates,
            "msg": f"Auto-matched {canonical_code}",
        }

    for variant_name, image_path in sorted((entry.get("variants") or {}).items()):
        candidates.append(
            _build_candidate_payload(
                entry,
                variant_name,
                image_path,
                "medium",
                "Exact code found, but only variant images are available",
            )
        )

    return _maybe_apply_local_ai_hints({
        "parsed": parsed,
        "match_status": "needs_review" if candidates else "unresolved",
        "confidence": "medium" if candidates else "none",
        "auto_accept": False,
        "candidates": candidates,
        "msg": (
            f"Multiple local matches found for {canonical_code}"
            if candidates
            else f"No local card match found for {canonical_code}"
        ),
    }, card_input=card_input, local_index=local_index)


def _thumb_path(img_path: str):
    if not img_path:
        return None
    rel_path = (img_path or "").replace("\\", "/").strip("/")
    if not rel_path or rel_path.lower().startswith(f"{THUMB_ROOT_NAME}/"):
        return rel_path or None
    stem, _ext = os.path.splitext(rel_path)
    return f"{THUMB_ROOT_NAME}/{stem}.webp"


def _thumb_abs_path(img_path: str):
    rel_path = _thumb_path(img_path)
    if not rel_path:
        return "", ""
    return rel_path, os.path.join(IMAGES_ROOT, rel_path.replace("/", os.sep))


def _source_abs_path(rel_path: str):
    rel_path = (rel_path or "").replace("\\", "/").strip("/")
    if not rel_path:
        return "", ""
    return rel_path, os.path.join(IMAGES_ROOT, rel_path.replace("/", os.sep))


def _source_rel_from_thumb_rel(thumb_rel_path: str):
    thumb_rel_path = (thumb_rel_path or "").replace("\\", "/").strip("/")
    prefix = f"{THUMB_ROOT_NAME}/"
    if not thumb_rel_path.lower().startswith(prefix):
        return ""
    base_without_ext, _ext = os.path.splitext(thumb_rel_path[len(prefix) :])
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = f"{base_without_ext}{ext}"
        if os.path.isfile(os.path.join(IMAGES_ROOT, candidate.replace("/", os.sep))):
            return candidate
    return ""


def ensure_thumbnail(img_path: str):
    if Image is None or ImageOps is None:
        return None
    source_rel, source_abs = _source_abs_path(img_path)
    if not source_rel or not os.path.isfile(source_abs):
        return None
    thumb_rel, thumb_abs = _thumb_abs_path(source_rel)
    if not thumb_rel:
        return None

    source_mtime = os.path.getmtime(source_abs)
    if os.path.isfile(thumb_abs) and os.path.getmtime(thumb_abs) >= source_mtime:
        return thumb_rel

    with THUMB_LOCK:
        try:
            if os.path.isfile(thumb_abs) and os.path.getmtime(thumb_abs) >= source_mtime:
                return thumb_rel
            os.makedirs(os.path.dirname(thumb_abs), exist_ok=True)
            with Image.open(source_abs) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode == "RGBA":
                    background = Image.new("RGB", image.size, (18, 18, 20))
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail((THUMB_MAX_WIDTH, THUMB_MAX_HEIGHT))
                image.save(
                    thumb_abs,
                    THUMB_FORMAT,
                    quality=THUMB_QUALITY,
                    method=6,
                    optimize=True,
                )
            return thumb_rel if os.path.isfile(thumb_abs) else None
        except Exception as exc:
            app.logger.warning("Thumbnail generation failed for %s: %s", source_rel, exc)
            return None


def _collect_image_paths(image_index: dict):
    paths = []
    for entry in ((image_index or {}).get("by_code") or {}).values():
        for key in ("normal", "alt", "illust"):
            path = (entry.get(key) or "").strip()
            if path:
                paths.append(path)
        for path in ((entry.get("variants") or {}).values()):
            if path:
                paths.append(path)
    deduped = []
    seen = set()
    for path in paths:
        key = (path or "").replace("\\", "/").strip("/")
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _prewarm_missing_thumbnails(image_paths: list[str]):
    if Image is None or ImageOps is None:
        return
    generated = 0
    for img_path in image_paths:
        thumb_rel, thumb_abs = _thumb_abs_path(img_path)
        if not thumb_rel:
            continue
        source_rel, source_abs = _source_abs_path(img_path)
        if not source_rel or not os.path.isfile(source_abs):
            continue
        if os.path.isfile(thumb_abs) and os.path.getmtime(thumb_abs) >= os.path.getmtime(source_abs):
            continue
        if ensure_thumbnail(img_path):
            generated += 1
    if generated:
        app.logger.info("Generated %s missing/stale thumbnails", generated)


def start_thumbnail_prewarm(image_index: dict):
    global THUMB_THREAD
    if Image is None or ImageOps is None:
        return
    image_paths = _collect_image_paths(image_index)
    if not image_paths:
        return
    if THUMB_THREAD and THUMB_THREAD.is_alive():
        return
    THUMB_THREAD = Thread(
        target=_prewarm_missing_thumbnails,
        args=(image_paths,),
        name="miru-thumb-prewarm",
        daemon=True,
    )
    THUMB_THREAD.start()


def rebuild_image_api_cache():
    out = []
    idx_code = IMAGE_INDEX.get("by_code", {}) or {}
    for code, entry in idx_code.items():
        parsed = normalize_card_code(code)
        normp = entry.get("normal")
        if normp:
            out.append(
                {
                    "code": code,
                    "canonical_code": parsed.get("canonical_code", code),
                    "set_code": parsed.get("set_code", ""),
                    "card_number": parsed.get("card_number", ""),
                    "set": _set_name_from_code(code),
                    "variant": "",
                    "path": normp,
                    "thumb_path": _thumb_path(normp) or normp,
                }
            )
        variants = entry.get("variants", {}) or {}
        for vname, path in variants.items():
            out.append(
                {
                    "code": code,
                    "canonical_code": parsed.get("canonical_code", code),
                    "set_code": parsed.get("set_code", ""),
                    "card_number": parsed.get("card_number", ""),
                    "set": _set_name_from_code(code),
                    "variant": vname,
                    "path": path,
                    "thumb_path": _thumb_path(path) or path,
                }
            )
    out.sort(key=lambda x: (x["set"], x["code"], x["variant"]))
    IMAGE_API_CACHE["payload"] = out


IMAGE_INDEX = build_image_index()
LOCAL_CARD_INDEX = {"image_index": IMAGE_INDEX, "by_code": {}}
start_thumbnail_prewarm(IMAGE_INDEX)
rebuild_image_api_cache()


@app.get("/img/<path:filename>")
def img(filename):
    normalized = (filename or "").replace("\\", "/").strip("/")
    if normalized.lower().startswith(f"{THUMB_ROOT_NAME}/"):
        thumb_rel, thumb_abs = _thumb_abs_path(_source_rel_from_thumb_rel(normalized))
        if thumb_rel and thumb_rel == normalized and os.path.isfile(thumb_abs):
            resp = send_from_directory(IMAGES_ROOT, normalized)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        source_rel = _source_rel_from_thumb_rel(normalized)
        generated_rel = ensure_thumbnail(source_rel)
        if generated_rel and generated_rel == normalized:
            resp = send_from_directory(IMAGES_ROOT, normalized)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
    resp = send_from_directory(IMAGES_ROOT, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


def choose_image_path(name: str, code: str):
    idx_base = IMAGE_INDEX["by_base"]
    idx_code = IMAGE_INDEX["by_code"]

    candidates = []
    for m in ANY_CODE_VARIANT_RE.finditer(name or ""):
        c = (m.group(1) or "").upper()
        v = (m.group(2) or "").strip()
        full = f"{c}({v})".upper()
        if full in idx_base:
            candidates.append((c, v, full))

    if candidates:
        if wants_illustration(name):
            for _c, v, full in candidates:
                if variant_is_illustrationish(v):
                    return idx_base[full]
        if wants_alt(name):
            for _c, v, full in candidates:
                if variant_is_altish(v):
                    return idx_base[full]
        return idx_base[candidates[0][2]]

    if not code:
        return None

    entry = idx_code.get(code, {}) or {}
    variants = entry.get("variants", {}) or {}

    if wants_illustration(name):
        if entry.get("illust"):
            return entry["illust"]
        for vname_l, path in variants.items():
            if variant_is_illustrationish(vname_l):
                return path
        return entry.get("normal") or entry.get("alt")

    if wants_alt(name):
        if entry.get("alt"):
            return entry["alt"]
        for vname_l, path in variants.items():
            if variant_is_altish(vname_l):
                return path
        return entry.get("alt") or entry.get("normal")

    return entry.get("normal") or entry.get("alt")


def _legacy_clean_display_name(name: str, code: str) -> str:
    s = (name or "").strip()
    if not s:
        return s

    if code:
        re_lead = re.compile(rf"^\s*(?:{re.escape(code)}(?:\([^)]+\))?\s+)+", re.I)
        s2 = re_lead.sub("", s).strip()
        if s2:
            s = s2

    s = re.sub(
        r"^\s*([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(?:\([^)]+\))?\s+",
        "",
        s,
        flags=re.I,
    ).strip()

    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _display_name_compare_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def clean_display_name(name: str, code: str) -> str:
    legacy = _legacy_clean_display_name(name, code)
    if not legacy:
        return legacy

    try:
        shared = shared_clean_card_name(name, code)
    except Exception:
        return legacy

    if shared and _display_name_compare_key(shared) == _display_name_compare_key(legacy):
        return shared
    return legacy


def _strip_redundant_set_prefix(title: str, subtitle: str) -> str:
    value = (title or "").strip()
    set_name = (subtitle or "").strip()
    if not value or not set_name:
        return value

    pattern = re.compile(
        rf"^\s*{re.escape(set_name)}(?:\s*[-:|•]\s*|\s+)+",
        re.I,
    )
    stripped = pattern.sub("", value).strip()
    return stripped or value


def get_watchlist_display_name(raw_name: str, code: str):
    normalized_code = (code or "").strip().upper()
    catalog_hint = _get_catalog_hint(normalized_code) if normalized_code else {}
    local_entry = ((LOCAL_CARD_INDEX.get("by_code") or {}).get(normalized_code) or {}) if normalized_code else {}

    subtitle = (
        _set_name_from_code(normalized_code)
        or (catalog_hint.get("set_name") or "").strip()
        or (local_entry.get("set_name") or "").strip()
        or ""
    )

    catalog_title = (catalog_hint.get("card_name") or "").strip()
    if catalog_title:
        return catalog_title, subtitle

    structured_title = (local_entry.get("primary_name") or "").strip()
    structured_title = _strip_redundant_set_prefix(structured_title, subtitle)
    if structured_title:
        return structured_title, subtitle

    normalized_title = clean_display_name(raw_name, normalized_code)
    normalized_title = _strip_redundant_set_prefix(normalized_title, subtitle)
    if normalized_title:
        return normalized_title, subtitle

    fallback = (raw_name or "").strip()
    return fallback or normalized_code or "Pending…", subtitle


def calc_deal(price: float, target: float):
    if target is None or price is None:
        return False, None, "", "watch"
    if target <= 0:
        return False, None, "", "watch"

    diff = target - price
    pct = (diff / target) * 100.0

    if price <= target:
        if pct >= 15:
            tier = "deal3"
        elif pct >= 5:
            tier = "deal2"
        else:
            tier = "deal1"
        return True, pct, f"\u25bc {pct:.1f}% under target", tier

    return False, pct, f"\u25b2 {abs(pct):.1f}% above target", "over"


# --- Tiny in-memory cache for watchlist fetch ---
_WL_CACHE = {"ts": 0, "mode": "", "ok": False, "msg": "", "rows": [], "source": ""}


def get_cached_watchlist(mode="enabled"):
    rows = _WL_CACHE.get("rows")
    if not (
        _WL_CACHE.get("ok")
        and _WL_CACHE.get("mode") == mode
        and isinstance(rows, list)
    ):
        return False, []
    return True, list(rows)


def _infer_pricing_status(url: str, explicit_status: str = "") -> str:
    status = (explicit_status or "").strip().lower()
    if status in ("confirmed_exact", "missing_link"):
        return status
    if _is_direct_tcgplayer_product_url(url):
        return "confirmed_exact"
    return "missing_link"


def update_cached_watchlist_row(payload: dict):
    if _WL_CACHE.get("mode") != "enabled":
        return

    rows = list(_WL_CACHE.get("rows") or [])
    url = (payload.get("url") or "").strip()
    if not url:
        return

    enabled = bool(payload.get("enabled", True))
    canon = canonical_url(url)
    replace_canon = canonical_url((payload.get("replace_url") or "").strip())
    idx = -1
    existing = {}

    if enabled and replace_canon and replace_canon != canon:
        rows = [
            row
            for row in rows
            if canonical_url((row.get("url") or "").strip()) != replace_canon
        ]

    for i, row in enumerate(rows):
        if canonical_url((row.get("url") or "").strip()) == canon:
            idx = i
            existing = row or {}
            break

    if not enabled:
        if idx >= 0:
            del rows[idx]
        _WL_CACHE.update(
            {"ts": time.time(), "mode": "enabled", "ok": True, "msg": "ok", "rows": rows, "source": "local-state-db"}
        )
        return

    row = {
        "enabled": True,
        "url": url,
        "product_id": str(existing.get("product_id") or "").strip(),
        "tcgplayer_name": str(existing.get("tcgplayer_name") or "").strip(),
        "card_id": str(
            payload.get("card_id")
            or existing.get("card_id")
            or ""
        ).strip().upper(),
        "target_price": str(
            payload.get("target_price")
            if payload.get("target_price") is not None
            else existing.get("target_price")
            or ""
        ).strip(),
        "cooldown_minutes": str(
            payload.get("cooldown_minutes")
            if payload.get("cooldown_minutes") is not None
            else existing.get("cooldown_minutes")
            or ""
        ).strip(),
        "notes": str(
            payload.get("notes")
            if payload.get("notes") is not None
            else existing.get("notes")
            or ""
        ).strip(),
        "pricing_status": _infer_pricing_status(
            url, payload.get("pricing_status") or existing.get("pricing_status") or ""
        ),
    }

    if idx >= 0:
        rows[idx] = row
    else:
        rows.append(row)

    _WL_CACHE.update(
        {"ts": time.time(), "mode": "enabled", "ok": True, "msg": "ok", "rows": rows, "source": "local-state-db"}
    )


def _fetch_watchlist_from_state_db(mode="enabled"):
    try:
        rows = list_watchlist_runtime_rows(mode=mode, db_path=MIRU_STATE_DB_PATH)
        status = get_watchlist_runtime_status(db_path=MIRU_STATE_DB_PATH)
    except Exception as exc:
        app.logger.warning("Watchlist local-state fetch failure: mode=%s error=%s", mode, exc)
        return False, str(exc), [], {}
    if not status.get("db_exists"):
        return False, "local_state_db_missing", [], status
    msg = (
        "local_state_db "
        f"authority={status.get('authority') or 'unset'} "
        f"bootstrap_complete={1 if status.get('bootstrap_complete') else 0} "
        f"enabled_rows={int(status.get('enabled_rows') or 0)} "
        f"total_rows={int(status.get('total_rows') or 0)}"
    )
    return True, msg, rows, status


def fetch_watchlist(mode="enabled"):
    now = time.time()
    if (
        WATCHLIST_CACHE_SECONDS > 0
        and _WL_CACHE["mode"] == mode
        and (now - _WL_CACHE["ts"]) <= WATCHLIST_CACHE_SECONDS
    ):
        return _WL_CACHE["ok"], _WL_CACHE["msg"], list(_WL_CACHE["rows"])

    local_ok, local_msg, local_rows, local_status = _fetch_watchlist_from_state_db(mode=mode)
    if local_ok:
        _WL_CACHE.update(
            {"ts": now, "mode": mode, "ok": True, "msg": local_msg, "rows": local_rows, "source": "local-state-db"}
        )
        return True, local_msg, local_rows
    return False, local_msg, []


def build_prices_by_url():
    out = {}
    for it in load_prices():
        u = canonical_url((it.get("url", "") or "").strip())
        if not u:
            continue
        out[u] = it
    return out


def _safe_attr(value) -> str:
    return (value or "").replace('"', "").strip()


def _safe_html(value) -> str:
    return (value or "").replace("<", "&lt;").replace(">", "&gt;")


def _parse_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _find_code(raw_name: str, code: str) -> str:
    normalized_code = (code or "").upper()
    if normalized_code:
        return normalized_code

    match = CODE_RE.search(raw_name)
    return match.group(1).upper() if match else ""


def _build_watchlist_items(watchlist_rows, prices_by_url):
    items = []
    for row in watchlist_rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue

        price_row = prices_by_url.get(canonical_url(url), {}) or {}
        card_id = (row.get("card_id") or "").strip().upper()

        code = (price_row.get("code") or "").strip().upper()
        if not code and card_id:
            code = card_id

        target_val = (row.get("target_price") or "").strip()
        if target_val == "":
            target_val = (
                price_row.get("target") if price_row.get("target") is not None else ""
            )

        name = (row.get("tcgplayer_name") or "").strip() or (
            price_row.get("name") or ""
        ).strip()
        pricing_status = _infer_pricing_status(url, row.get("pricing_status") or "")

        items.append(
            {
                "url": url,
                "name": name or card_id or code or "Pending\u2026",
                "code": code,
                "card_id": card_id,
                "price": price_row.get("price", None),
                "target": target_val,
                "target_price": (row.get("target_price") or "").strip(),
                "cooldown_minutes": (row.get("cooldown_minutes") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
                "last_checked_ts": price_row.get("last_checked_ts", 0),
                "pricing_status": pricing_status,
            }
        )

    return items


def _build_items(wl_ok, watchlist_rows):
    if wl_ok:
        return _build_watchlist_items(watchlist_rows, build_prices_by_url())
    return load_prices()


def _code_matches_item(item: dict, code: str) -> bool:
    item_code = (item.get("code") or "").strip().upper()
    if item_code == code:
        return True

    item_name = (item.get("name") or "").strip()
    match = CODE_RE.search(item_name)
    return bool(match and match.group(1).upper() == code)


def _lookup_tcgplayer_url_from_card_site(code: str) -> str:
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return ""

    # OPCardlist card pages are keyed by code and expose a TCGplayer button when available.
    card_page_url = f"https://www.opcardlist.com/card/{normalized_code.lower()}"
    req = urllib.request.Request(
        card_page_url,
        headers={"User-Agent": "tcg-watcher/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=CARD_LOOKUP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

    match = TCGPLAYER_LINK_RE.search(body)
    if match:
        return html.unescape(match.group(1)).strip()

    generic_match = re.search(r'href="(https://www\.tcgplayer\.com/[^"]+)"', body, re.I)
    if generic_match:
        return html.unescape(generic_match.group(1)).strip()

    return ""


def _lookup_opcardlist_card_info(code: str):
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return {}

    card_page_url = f"https://www.opcardlist.com/card/{normalized_code.lower()}"
    req = urllib.request.Request(
        card_page_url,
        headers={"User-Agent": "tcg-watcher/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=CARD_LOOKUP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return {}

    info = {
        "code": normalized_code,
        "name": "",
        "set_name": "",
        "url": "",
        "source": "opcardlist",
    }

    title_match = OPCARDLIST_TITLE_RE.search(body)
    if title_match:
        title = html.unescape((title_match.group(1) or "").strip())
        # Usually looks like "Card Name [OP12-061]" or similar.
        title = re.sub(r"\s*\[[A-Z0-9-]+\]\s*$", "", title).strip()
        info["name"] = title

    tcg_match = re.search(r'href="(https://www\.tcgplayer\.com/[^"]+)"', body, re.I)
    if tcg_match:
        info["url"] = html.unescape((tcg_match.group(1) or "").strip())

    return info


def _lookup_limitless_card_info(code: str):
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return {}

    url = f"https://onepiece.limitlesstcg.com/cards/{normalized_code}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tcg-watcher/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=CARD_LOOKUP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return {}

    info = {
        "code": normalized_code,
        "name": "",
        "set_name": "",
        "url": "",
        "source": "limitless",
    }

    title_match = LIMITLESS_TITLE_RE.search(body)
    if title_match:
        info["name"] = html.unescape((title_match.group(1) or "").strip())
        info["set_name"] = html.unescape((title_match.group(3) or "").strip())

    tcg_match = re.search(r'href="(https://www\.tcgplayer\.com/[^"]+)"', body, re.I)
    if tcg_match:
        info["url"] = html.unescape((tcg_match.group(1) or "").strip())
        return info

    query_parts = [info["name"], info["set_name"], normalized_code, "one piece"]
    query = " ".join(part for part in query_parts if part).strip()
    if query:
        info["url"] = "https://www.tcgplayer.com/search/all/product?q=" + urllib.parse.quote(
            query
        )
        info["source"] = "limitless-search"

    return info


def _split_card_code(code: str):
    normalized = (code or "").strip().upper()
    if not normalized:
        return "", ""

    if normalized.startswith("P-"):
        return "P", normalized.split("-", 1)[1]

    match = re.match(r"^([A-Z]{2,4}\d{2})-(\d{3})$", normalized)
    if not match:
        return "", ""

    return match.group(1), match.group(2)


def _build_tcgplayer_search_query(set_name: str, card_number: str) -> str:
    parts = [
        "one piece",
        (set_name or "").strip(),
        (card_number or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def _build_generated_tcgplayer_search_result(code: str, variant: str = ""):
    normalized_code = (code or "").strip().upper()
    parsed = normalize_card_code(normalized_code) or {}
    if not parsed:
        code_parts = parse_card_code(normalized_code)
        if code_parts:
            parsed = {
                "set_code": code_parts.get("set_code", ""),
                "card_number": code_parts.get("card_number", ""),
                "variant": "",
                "canonical_code": normalized_code,
            }

    if not parsed:
        return {}

    local_entry = (LOCAL_CARD_INDEX.get("by_code") or {}).get(normalized_code, {})
    set_name = (local_entry.get("set_name") or "").strip() or _set_name_from_code(normalized_code)
    card_name = (local_entry.get("primary_name") or "").strip()
    card_number = (parsed.get("card_number") or "").strip()
    variant_info = _build_variant_info(variant)
    variant_query = (variant_info.get("display") or "").strip()

    query_parts = ["one piece", set_name, card_name, normalized_code, card_number, variant_query]
    query = " ".join(part.strip() for part in query_parts if part and part.strip()).strip()
    if not query:
        return {}

    return {
        "code": normalized_code,
        "url": "https://www.tcgplayer.com/search/all/product?q="
        + urllib.parse.quote(query)
        + "&view=grid",
        "name": card_name,
        "source": "generated-search",
        "link_status": "generated_search",
        "query": query,
        "variant": variant_query,
    }


def _normalize_exact_tcgplayer_product_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""

    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return ""

    host = (parsed.netloc or "").strip().lower()
    if host not in ("tcgplayer.com", "www.tcgplayer.com"):
        return ""

    path = (parsed.path or "").strip().rstrip("/")
    if not re.match(r"^/product/\d+(?:/[^/?#]+)?$", path, re.I):
        return ""

    return urllib.parse.urlunsplit(("https", "www.tcgplayer.com", path, "", ""))


def _canonical_product_url(url: str) -> str:
    exact = _normalize_exact_tcgplayer_product_url(url)
    if exact:
        return exact

    canonical = canonical_url(url)
    if not canonical:
        return ""
    match = TCGPLAYER_PRODUCT_ID_RE.search(canonical)
    if not match:
        return canonical
    return canonical


def _is_direct_tcgplayer_product_url(url: str) -> bool:
    return bool(_normalize_exact_tcgplayer_product_url(url))


def _find_card_id_conflict(card_id: str, url: str, rows) -> dict:
    normalized_code = (card_id or "").strip().upper()
    normalized_url = canonical_url(url)
    if not normalized_code or not normalized_url or not isinstance(rows, list):
        return {}

    for row in rows:
        existing_code = (row.get("card_id") or "").strip().upper()
        existing_url = canonical_url((row.get("url") or "").strip())
        if existing_code != normalized_code or not existing_url or existing_url == normalized_url:
            continue
        return {
            "card_id": normalized_code,
            "url": existing_url,
            "is_exact": _is_direct_tcgplayer_product_url(existing_url),
        }

    return {}


def refresh_local_indexes():
    global IMAGE_INDEX, LOCAL_CARD_INDEX
    MIRU_CARD_INTEL.clear_caches()
    _CATALOG_HINT_CACHE.clear()
    IMAGE_INDEX = build_image_index()
    LOCAL_CARD_INDEX = build_local_card_index(IMAGE_INDEX)
    rebuild_image_api_cache()
    start_thumbnail_prewarm(IMAGE_INDEX)


def refresh_catalog_index():
    try:
        init_catalog_schema(CATALOG_DB_PATH)
        rebuild_catalog_from_indexes(
            image_index=IMAGE_INDEX,
            local_index=LOCAL_CARD_INDEX,
            normalize_card_code_func=normalize_card_code,
            build_variant_info_func=_build_variant_info,
            lookup_card_data_func=MIRU_CARD_INTEL.build_card_record,
            lookup_variant_data_func=MIRU_CARD_INTEL.build_variant_record,
            lookup_intelligence_func=MIRU_CARD_INTEL.build_intelligence_record,
            db_path=CATALOG_DB_PATH,
        )
    except sqlite3.OperationalError as exc:
        message = str(exc or "").lower()
        if "disk i/o error" not in message and "readonly" not in message:
            raise
        app.logger.warning(
            "Catalog rebuild skipped at startup because %s is not writable; using existing catalog DB state.",
            CATALOG_DB_PATH,
        )
        init_catalog_schema(CATALOG_DB_PATH)


def refresh_catalog_metadata(overwrite: bool = False):
    return apply_metadata_to_catalog(
        db_path=CATALOG_DB_PATH,
        metadata_path=CATALOG_METADATA_PATH,
        overwrite=overwrite,
    )


refresh_local_indexes()
refresh_catalog_index()
if os.path.isfile(CATALOG_METADATA_PATH):
    try:
        metadata_summary = refresh_catalog_metadata(overwrite=False)
        app.logger.info(
            "Catalog metadata import applied at startup: updated=%s skipped=%s unmatched=%s source=%s",
            metadata_summary.get("rows_updated"),
            metadata_summary.get("rows_skipped"),
            metadata_summary.get("rows_unmatched"),
            CATALOG_METADATA_PATH,
        )
    except Exception:
        app.logger.exception(
            "Catalog metadata import failed at startup from %s",
            CATALOG_METADATA_PATH,
        )


def _extract_tcgplayer_product_links(body: str):
    matches = []

    for match in TCGPLAYER_PRODUCT_LINK_RE.finditer(body or ""):
        href = html.unescape((match.group(1) or "").strip())
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.tcgplayer.com" + href
        href = _canonical_product_url(href)
        if href and href not in matches:
            matches.append(href)
        if len(matches) >= 8:
            return matches

    generic = re.finditer(
        r'https://www\.tcgplayer\.com/product/\d+/[A-Za-z0-9%\-._~+/]+',
        body or "",
        re.I,
    )
    for match in generic:
        href = _canonical_product_url(match.group(0))
        if href and href not in matches:
            matches.append(href)
        if len(matches) >= 8:
            break

    return matches


def _fetch_tcgplayer_search_candidates(query: str):
    if not query:
        return []

    search_urls = [
        "https://www.tcgplayer.com/search/all/product?q="
        + urllib.parse.quote(query)
        + "&view=grid",
        "https://www.tcgplayer.com/search/one-piece-card-game/product?q="
        + urllib.parse.quote(query)
        + "&view=grid",
        "https://www.tcgplayer.com/search/one-piece-card-game/product?productLineName=one-piece-card-game&q="
        + urllib.parse.quote(query),
    ]

    candidates = []
    seen = set()

    for search_url in search_urls:
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "tcg-watcher/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=CARD_LOOKUP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        for href in _extract_tcgplayer_product_links(body):
            if href in seen:
                continue
            seen.add(href)
            candidates.append(href)
            if len(candidates) >= 8:
                return candidates

    return candidates


def _build_tcgplayer_search_queries(
    set_name: str, card_number: str, code: str = "", card_name: str = ""
):
    set_name = (set_name or "").strip()
    card_number = (card_number or "").strip()
    code = (code or "").strip().upper()
    card_name = (card_name or "").strip()

    queries = []

    def add_query(*parts):
        query = " ".join(part.strip() for part in parts if part and part.strip()).strip()
        if query and query not in queries:
            queries.append(query)

    add_query("one piece", set_name, card_number, code)
    add_query("one piece", set_name, card_number)
    add_query("one piece", set_name, code)
    add_query("one piece", code, card_number)
    add_query("one piece", code)

    if card_name:
        add_query("one piece", set_name, card_name, card_number, code)
        add_query("one piece", set_name, card_name, code)
        add_query("one piece", card_name, code)
        add_query("one piece", card_name, set_name)
        add_query(card_name, set_name, card_number, "tcgplayer")

    if set_name and card_number:
        add_query(set_name, card_number, "one piece tcgplayer")

    return queries


def search_tcgplayer_url(set_name: str, card_number: str, code: str = "", card_name: str = ""):
    queries = _build_tcgplayer_search_queries(
        set_name=set_name,
        card_number=card_number,
        code=code,
        card_name=card_name,
    )
    if not queries:
        return False, "Missing set name or card number", {}

    all_candidates = []
    for query in queries:
        candidates = _fetch_tcgplayer_search_candidates(query)
        for candidate in candidates:
            if candidate not in all_candidates:
                all_candidates.append(candidate)
        if all_candidates:
            return True, "ok", {
                "query": query,
                "queries": queries,
                "url": all_candidates[0],
                "candidates": all_candidates[:8],
                "source": "tcgplayer-search",
            }

    fallback_query = queries[0]
    fallback_search_url = (
        "https://www.tcgplayer.com/search/all/product?q="
        + urllib.parse.quote(fallback_query)
        + "&view=grid"
    )
    return False, "No TCGplayer search results found", {
        "query": fallback_query,
        "queries": queries,
        "search_url": fallback_search_url,
    }


def search_tcgplayer_url_for_code(code: str):
    set_code, card_number = _split_card_code(code)
    set_name = SET_CODE_TO_NAME.get(set_code, "")
    if not set_name or not card_number:
        return False, f"Cannot derive search details for {code}", {}

    local_entry = (LOCAL_CARD_INDEX.get("by_code") or {}).get((code or "").strip().upper(), {})
    limitless_info = _lookup_limitless_card_info(code)
    opcardlist_info = _lookup_opcardlist_card_info(code)
    card_name = (
        (local_entry.get("primary_name") or "").strip()
        or ((local_entry.get("names") or [""])[0] or "").strip()
        or (limitless_info.get("name") or "").strip()
        or (opcardlist_info.get("name") or "").strip()
    )

    ok, msg, data = search_tcgplayer_url(
        set_name,
        card_number,
        code=code,
        card_name=card_name,
    )
    if not ok:
        return ok, msg, data

    data.update(
        {
            "code": (code or "").strip().upper(),
            "set_name": set_name,
            "card_number": card_number,
            "card_name": card_name,
        }
    )
    return True, msg, data


def resolve_card_url(code: str, variant: str = ""):
    normalized_code = (code or "").strip().upper()
    variant_info = _build_variant_info(variant)
    normalized_variant = (variant_info.get("normalized") or "").strip()
    if not normalized_code:
        return False, "Missing code", {"link_status": "unresolved_manual"}

    state_entry = _lookup_state_db_card_link(normalized_code, normalized_variant)
    if state_entry:
        sqlite_url = _canonical_product_url(state_entry.get("url"))
        state_variant = _build_variant_info(state_entry.get("variant_key") or "").get("normalized", "")
        if _is_direct_tcgplayer_product_url(sqlite_url):
            app.logger.info(
                "State DB link hit for %s%s",
                normalized_code,
                f"::{state_variant}" if state_variant else "",
            )
            payload = {
                "code": normalized_code,
                "url": sqlite_url,
                "name": "",
                "source": "state-db",
                "link_status": "confirmed_exact",
            }
            if state_variant:
                payload["variant"] = state_variant
            return True, "ok", payload
    else:
        app.logger.info(
            "State DB link miss for %s%s; falling back to JSON/local lookup",
            normalized_code,
            f"::{normalized_variant}" if normalized_variant else "",
        )

    local_entry = (LOCAL_CARD_INDEX.get("by_code") or {}).get(normalized_code, {})
    if normalized_variant:
        variant_local_url = _canonical_product_url(
            (local_entry.get("variant_direct_urls") or {}).get(normalized_variant)
        )
        if _is_direct_tcgplayer_product_url(variant_local_url):
            return True, "ok", {
                "code": normalized_code,
                "url": variant_local_url,
                "name": (local_entry.get("primary_name") or "").strip(),
                "source": "local-index-variant",
                "link_status": "confirmed_exact",
                "variant": normalized_variant,
            }

    local_url = _canonical_product_url(local_entry.get("direct_url"))
    if _is_direct_tcgplayer_product_url(local_url):
        return True, "ok", {
            "code": normalized_code,
            "url": local_url,
            "name": (local_entry.get("primary_name") or "").strip(),
            "source": "local-index",
            "link_status": "confirmed_exact",
        }

    remembered = load_card_url_map()
    if normalized_variant:
        remembered_variant_url = _canonical_product_url(
            remembered.get(_build_card_url_map_key(normalized_code, normalized_variant))
        )
        if _is_direct_tcgplayer_product_url(remembered_variant_url):
            return True, "ok", {
                "code": normalized_code,
                "url": remembered_variant_url,
                "name": "",
                "source": "map-variant",
                "link_status": "confirmed_exact",
                "variant": normalized_variant,
            }

    remembered_url = _canonical_product_url(remembered.get(normalized_code))
    if _is_direct_tcgplayer_product_url(remembered_url):
        return True, "ok", {
            "code": normalized_code,
            "url": remembered_url,
            "name": "",
            "source": "map",
            "link_status": "confirmed_exact",
        }

    matches = []
    for item in load_prices():
        url = (item.get("url") or "").strip()
        if not url:
            continue
        if _code_matches_item(item, normalized_code):
            matches.append(item)

    if not matches:
        generated = _build_generated_tcgplayer_search_result(normalized_code, variant=normalized_variant)
        if generated.get("url"):
            return True, "Generated search URL", generated
        return False, f"No URL found for {normalized_code}", {
            "code": normalized_code,
            "link_status": "unresolved_manual",
        }

    preferred = None
    for item in matches:
        if _is_direct_tcgplayer_product_url(item.get("url")):
            preferred = item
            break

    if preferred:
        return True, "ok", {
            "code": normalized_code,
            "url": _canonical_product_url(preferred.get("url")),
            "name": (preferred.get("name") or "").strip(),
            "source": "prices",
            "link_status": "confirmed_exact",
        }

    generated = _build_generated_tcgplayer_search_result(normalized_code, variant=normalized_variant)
    if generated.get("url"):
        return True, "Generated search URL", generated

    fallback_name = (matches[0].get("name") or "").strip() if matches else ""
    return False, f"No direct TCGplayer product link found for {normalized_code}", {
        "code": normalized_code,
        "name": fallback_name,
        "link_status": "unresolved_manual",
    }


def _build_price_delta(price_f, prev_value):
    delta_txt = ""
    delta_cls = ""

    if not isinstance(price_f, (int, float)) or prev_value is None:
        return delta_txt, delta_cls

    try:
        prev_f = float(prev_value)
        diff = price_f - prev_f
        if abs(diff) >= 0.005:
            if diff < 0:
                delta_cls = "down"
                delta_txt = f"\u2193 ${abs(diff):.2f} since last refresh"
            else:
                delta_cls = "up"
                delta_txt = f"\u2191 ${abs(diff):.2f} since last refresh"
    except Exception:
        pass

    return delta_txt, delta_cls


def _sync_card_url_map_to_state_db(source_kind: str = "card-url-map-bootstrap"):
    try:
        return mirror_card_url_map(
            load_card_url_map(),
            source_kind=source_kind,
            db_path=MIRU_STATE_DB_PATH,
        )
    except Exception:
        app.logger.exception("State DB card-link mirror failed")
        return 0


def _upsert_watchlist_payload_to_state_db(
    payload: dict,
    *,
    source_kind: str,
    sheet_sync_status: str,
    sync_error: str = "",
):
    try:
        return upsert_watchlist_entry(
            payload,
            source_kind=source_kind,
            sheet_sync_status=sheet_sync_status,
            sync_error=sync_error,
            db_path=MIRU_STATE_DB_PATH,
        )
    except Exception:
        app.logger.exception(
            "State DB watchlist upsert failed for url=%s",
            (payload.get("url") or "").strip(),
        )
        return False


def _disable_watchlist_payload_in_state_db(
    url: str,
    *,
    source_kind: str,
    sheet_sync_status: str,
    sync_error: str = "",
):
    try:
        return disable_watchlist_entry(
            url,
            source_kind=source_kind,
            sheet_sync_status=sheet_sync_status,
            sync_error=sync_error,
            db_path=MIRU_STATE_DB_PATH,
        )
    except Exception:
        app.logger.exception("State DB watchlist disable failed for url=%s", url)
        return False


def _split_compare_card_code_parts(card_code: str):
    normalized = (card_code or "").strip().upper()
    if not normalized or "-" not in normalized:
        return normalized, "", ""
    set_code, card_number = normalized.split("-", 1)
    return normalized, set_code, card_number


def _normalize_compare_text(value):
    return (value or "").strip()


def _normalize_compare_float(value):
    try:
        number = float(value)
    except Exception:
        return ""
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalize_compare_int(value):
    try:
        return str(int(float(value)))
    except Exception:
        return ""


def _coerce_compare_sample_limit(value, default=20):
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, 100))


def _normalize_watchlist_compare_current_row(row, prices_by_url):
    url = (row.get("url") or "").strip()
    canonical = canonical_url(url)
    price_row = prices_by_url.get(canonical, {}) or {}
    row_name = (row.get("tcgplayer_name") or row.get("name") or "").strip()
    card_id = (
        (row.get("card_id") or row.get("code") or price_row.get("code") or "").strip().upper()
    )
    if not card_id:
        card_id = _find_code(row_name, "")
    card_id, set_code, card_number = _split_compare_card_code_parts(card_id)
    target_price = (row.get("target_price") or row.get("target") or "").strip()
    latest_price = price_row.get("price")
    variant_key = _build_variant_info(
        row.get("variant") or row.get("variant_key") or ""
    ).get("normalized", "")
    variant_label = (
        (row.get("variant_label") or "").strip()
        or (_format_variant_label(variant_key) if variant_key else "")
    )
    return {
        "canonical_url": canonical,
        "url": url,
        "card_id": card_id,
        "set_code": set_code,
        "card_number": card_number,
        "variant_key": variant_key,
        "variant_label": variant_label,
        "target_price": _normalize_compare_float(target_price),
        "latest_price": _normalize_compare_float(latest_price),
        "cooldown_minutes": _normalize_compare_int(row.get("cooldown_minutes")),
        "notes": _normalize_compare_text(row.get("notes")),
        "pricing_status": _normalize_compare_text(
            _infer_pricing_status(url, row.get("pricing_status") or "")
        ),
        "sheet_sync_status": _normalize_compare_text(row.get("sheet_sync_status")) or "retired",
    }


def _normalize_watchlist_compare_state_row(row):
    card_id, set_code, card_number = _split_compare_card_code_parts(
        (row.get("card_id") or row.get("normalized_code") or "").strip().upper()
    )
    variant_key = _build_variant_info(row.get("variant_key") or "").get(
        "normalized", ""
    )
    variant_label = (
        (row.get("variant_label") or "").strip()
        or (_format_variant_label(variant_key) if variant_key else "")
    )
    return {
        "canonical_url": canonical_url((row.get("url") or "").strip()),
        "url": (row.get("url") or "").strip(),
        "card_id": card_id,
        "set_code": set_code or _normalize_compare_text(row.get("set_code")),
        "card_number": card_number or _normalize_compare_text(row.get("card_number")),
        "variant_key": variant_key,
        "variant_label": variant_label,
        "target_price": _normalize_compare_float(
            row.get("target_price_value")
            if row.get("target_price_value") is not None
            else row.get("target_price_text")
        ),
        "latest_price": _normalize_compare_float(row.get("latest_price_value")),
        "cooldown_minutes": _normalize_compare_int(row.get("cooldown_minutes")),
        "notes": _normalize_compare_text(row.get("notes")),
        "pricing_status": _normalize_compare_text(row.get("pricing_status")),
        "sheet_sync_status": _normalize_compare_text(row.get("sheet_sync_status")),
    }


def _build_watchlist_compare_report(current_rows, sqlite_rows, *, sample_limit=20):
    field_order = (
        "card_id",
        "set_code",
        "card_number",
        "variant_key",
        "variant_label",
        "target_price",
        "latest_price",
        "cooldown_minutes",
        "notes",
        "pricing_status",
        "sheet_sync_status",
    )
    prices_by_url = build_prices_by_url()
    current_index = {}
    sqlite_index = {}

    for row in current_rows:
        normalized = _normalize_watchlist_compare_current_row(row, prices_by_url)
        if normalized["canonical_url"]:
            current_index[normalized["canonical_url"]] = normalized

    for row in sqlite_rows:
        normalized = _normalize_watchlist_compare_state_row(row)
        if normalized["canonical_url"]:
            sqlite_index[normalized["canonical_url"]] = normalized

    current_keys = set(current_index)
    sqlite_keys = set(sqlite_index)
    matched_keys = sorted(current_keys & sqlite_keys)
    missing_in_sqlite_keys = sorted(current_keys - sqlite_keys)
    missing_in_current_keys = sorted(sqlite_keys - current_keys)

    mismatches = []
    for key in matched_keys:
        current_row = current_index[key]
        sqlite_row = sqlite_index[key]
        fields = {}
        for field_name in field_order:
            if current_row.get(field_name, "") != sqlite_row.get(field_name, ""):
                fields[field_name] = {
                    "current": current_row.get(field_name, ""),
                    "sqlite": sqlite_row.get(field_name, ""),
                }
        if fields:
            mismatches.append(
                {
                    "canonical_url": key,
                    "card_id": current_row.get("card_id") or sqlite_row.get("card_id") or "",
                    "fields": fields,
                }
            )

    sample_limit = _coerce_compare_sample_limit(sample_limit)
    perfect_match_count = len(matched_keys) - len(mismatches)
    return {
        "summary": {
            "current_rows": len(current_index),
            "sqlite_rows": len(sqlite_index),
            "matched_rows": len(matched_keys),
            "perfect_match_rows": perfect_match_count,
            "mismatch_rows": len(mismatches),
            "missing_in_sqlite": len(missing_in_sqlite_keys),
            "missing_in_current": len(missing_in_current_keys),
        },
        "samples": {
            "missing_in_sqlite": [
                {
                    "canonical_url": key,
                    "card_id": current_index[key].get("card_id", ""),
                    "pricing_status": current_index[key].get("pricing_status", ""),
                }
                for key in missing_in_sqlite_keys[:sample_limit]
            ],
            "missing_in_current": [
                {
                    "canonical_url": key,
                    "card_id": sqlite_index[key].get("card_id", ""),
                    "pricing_status": sqlite_index[key].get("pricing_status", ""),
                    "sheet_sync_status": sqlite_index[key].get("sheet_sync_status", ""),
                }
                for key in missing_in_current_keys[:sample_limit]
            ],
            "field_mismatches": mismatches[:sample_limit],
        },
    }


init_state_db_schema(MIRU_STATE_DB_PATH)
_sync_card_url_map_to_state_db()


def _enrich_item(item, last_prices, next_last_prices):
    raw_name = (item.get("name") or item.get("tcgplayer_name") or "").strip()
    code = _find_code(raw_name, item.get("code"))

    img_path = choose_image_path(raw_name, code)
    display_name, set_name = get_watchlist_display_name(raw_name, code)
    set_code = _set_from_code(code)

    price_f = _parse_float(item.get("price", None))
    target_f = _parse_float(item.get("target", 0))
    pricing_status = _infer_pricing_status(
        item.get("url", ""), item.get("pricing_status", "")
    )
    needs_link = pricing_status == "missing_link"

    price_txt = (
        f"${price_f:.2f}"
        if isinstance(price_f, (int, float))
        else ("No data" if needs_link else "Pending scan")
    )
    target_txt = f"${target_f:.2f}" if isinstance(target_f, (int, float)) else ""

    hit, pct, pct_label, tier = calc_deal(price_f, target_f)

    last_ts = int(item.get("last_checked_ts", 0) or 0)
    url = (item.get("url", "") or "").strip()
    key = url or code or display_name
    delta_txt, delta_cls = _build_price_delta(price_f, last_prices.get(key))

    if isinstance(price_f, (int, float)) and key:
        next_last_prices[key] = round(float(price_f), 4)

    insight_summary = {}
    if code:
        try:
            insight_summary = _resolve_watchlist_card_insight(code)
        except Exception:
            app.logger.warning("Watchlist insight lookup failed for %s", code, exc_info=True)

    return {
        "it": item,
        "code": code,
        "set": _set_from_code(code),
        "display_name": display_name,
        "img_path": img_path,
        "price_txt": price_txt,
        "target_txt": target_txt,
        "hit": hit,
        "pct": pct if pct is not None else -9999.0,
        "pct_label": pct_label,
        "tier": tier,
        "last_ts": last_ts,
        "has_img": 1 if img_path else 0,
        "has_target": 1 if (isinstance(target_f, (int, float)) and target_f > 0) else 0,
        "delta_txt": delta_txt,
        "delta_cls": delta_cls,
        "url": url,
        "pricing_status": pricing_status,
        "needs_link": 1 if needs_link else 0,
        "open_label": "Buy on TCGplayer",
        "set_code": set_code,
        "set_name": set_name,
        "target_input": str(item.get("target_price") or item.get("target") or "").strip(),
        "cooldown_minutes": str(item.get("cooldown_minutes") or "").strip(),
        "notes": str(item.get("notes") or "").strip(),
        "insight_summary": str(insight_summary.get("summary_short") or "").strip(),
        "insight_voice_line": str(insight_summary.get("voice_line") or "").strip(),
        "insight_confidence_label": str(insight_summary.get("confidence_label") or "").strip(),
        "insight_evidence_posture": str(insight_summary.get("evidence_posture") or "").strip(),
        "insight_cache_status": str(insight_summary.get("cache_status") or "").strip(),
    }


def _sort_enriched_item(item):
    hit_bucket = 0 if item["hit"] else 1
    deal_rank = -item["pct"] if item["hit"] else 0
    recency = -item["last_ts"]
    name = (item["display_name"] or "").lower()
    return (hit_bucket, deal_rank, recency, name)


def _render_card_html(item, index):
    src_path = _thumb_path(item["img_path"]) or item["img_path"]
    is_hero = index < 8
    loading = "eager" if is_hero else "lazy"
    fetchpri = "high" if is_hero else "low"
    card_class = f"card {item['tier']}".strip()
    price_class = "priceMain buy" if item["hit"] else "priceMain"
    data_hit = "1" if item["hit"] else "0"

    img_tag = (
        f'<img class="thumb" src="/img/{src_path}?v={BUILD_ID}" loading="{loading}" '
        f'fetchpriority="{fetchpri}" decoding="async" alt="">'
        if src_path
        else '<div class="ph">No image</div>'
    )

    badge = ""
    delta_html = (
        f'<div class="priceDelta {item.get("delta_cls", "")}">{item["delta_txt"]}</div>'
        if item.get("delta_txt")
        else ""
    )

    code = item["code"]
    display_name = item["display_name"]
    set_code = _safe_html(item.get("set_code") or "")
    set_name = _safe_html(item.get("set_name") or "")
    url = _safe_attr(item.get("url"))
    data_set = _safe_attr(item.get("set"))
    data_card_id = _safe_attr(item.get("code"))
    data_target = _safe_attr(item.get("target_input"))
    data_cooldown = _safe_attr(item.get("cooldown_minutes"))
    data_notes = _safe_attr(item.get("notes"))
    data_insight_summary = _safe_attr(item.get("insight_summary"))
    data_insight_voice = _safe_attr(item.get("insight_voice_line"))
    link_badge = (
        '<div class="statusBadge needsLink">Needs product link</div>'
        if item.get("pricing_status") == "missing_link"
        else ""
    )
    open_label = _safe_html(item.get("open_label") or "Open")
    target_line = (
        f'<div class="marketTarget">target {item["target_txt"]}</div>'
        if item.get("target_txt")
        else ""
    )
    insight_line = (
        f'<div class="insightMini">{_safe_html(item.get("insight_voice_line") or item.get("insight_summary"))}</div>'
        if item.get("insight_summary") or item.get("insight_voice_line")
        else ""
    )

    return f"""
        <div class="{card_class}"
             data-url="{url}"
             data-set="{data_set}"
             data-cardid="{data_card_id}"
             data-code="{_safe_attr(code)}"
             data-name="{_safe_attr(display_name)}"
             data-hit="{data_hit}"
             data-pct="{item["pct"]}"
             data-ts="{item["last_ts"]}"
             data-hasimg="{item["has_img"]}"
             data-hastarget="{item["has_target"]}"
             data-target="{data_target}"
             data-cooldown="{data_cooldown}"
             data-notes="{data_notes}"
             data-insightsummary="{data_insight_summary}"
             data-insightvoice="{data_insight_voice}"
             data-pricingstatus="{_safe_attr(item.get("pricing_status"))}">
          <div class="row">
            <div class="left">{img_tag}</div>
            <div class="middle">
              <div class="metaTop">
                <div class="codebar">
                  <span class="code">{code}</span>
                  {badge}
                </div>
                {link_badge}
              </div>
              <div class="title">{display_name}</div>
              <div class="subtitle">{set_name or set_code}</div>
              {insight_line}
            </div>
            <div class="market">
              <div class="{price_class}">{item["price_txt"]}</div>
              {delta_html}
              {target_line}
              <a class="openbtn" href="{url}" target="_blank" rel="noopener">{open_label}</a>
              <div class="miniActions">
                <button class="editbtn" type="button">Edit</button>
                <button class="removebtn" type="button" data-url="{url}">Remove</button>
              </div>
              <div class="hiddenCardActions" aria-hidden="true">
                <a class="openbtn" href="{url}" target="_blank" rel="noopener">{open_label}</a>
                <button class="editbtn" type="button">Edit Target</button>
                <button class="removebtn" type="button" data-url="{url}">Remove</button>
              </div>
            </div>
          </div>
        </div>
        """


def _build_watchlist_card_response(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url:
        return {}

    row = {
        "url": url,
        "card_id": (payload.get("card_id") or "").strip().upper(),
        "target_price": (payload.get("target_price") or "").strip(),
        "cooldown_minutes": (payload.get("cooldown_minutes") or "").strip(),
        "notes": (payload.get("notes") or "").strip(),
        "tcgplayer_name": (payload.get("tcgplayer_name") or "").strip(),
        "pricing_status": _infer_pricing_status(url, payload.get("pricing_status") or ""),
    }
    items = _build_watchlist_items([row], build_prices_by_url())
    if not items:
        return {}

    enriched = _enrich_item(items[0], {}, {})
    contextual_opportunities = []
    card_code = str(enriched.get("code") or "").strip().upper()
    if card_code:
        try:
            typed_bundle = _resolve_card_typed_intelligence_bundle(
                card_code,
                context_prefix="watchlist_single_card_response",
            )
            dossier = _get_miru_dossier_store()
            snapshot = dict(dossier.fetch_card_snapshot(card_code) or {})
            contextual_opportunities = _build_contextual_opportunities_for_card(
                card_code=card_code,
                snapshot=snapshot,
                typed_bundle=typed_bundle,
                watchlist_context={
                    "current_price": enriched.get("it", {}).get("price"),
                    "target_price": enriched.get("target_input"),
                    "card_code": card_code,
                },
                context_tag="watchlist:single_card_contextual",
            )
        except Exception:
            app.logger.warning("Watchlist contextual opportunities failed for %s", card_code, exc_info=True)
            contextual_opportunities = []
    return {
        "card_html": _render_card_html(enriched, 9999),
        "card_url": url,
        "card_id": enriched.get("code", ""),
        "pricing_status": enriched.get("pricing_status", ""),
        "link_status": enriched.get("pricing_status", ""),
        "insight_summary": enriched.get("insight_summary", ""),
        "insight_voice_line": enriched.get("insight_voice_line", ""),
        "insight_confidence_label": enriched.get("insight_confidence_label", ""),
        "contextual_opportunities": contextual_opportunities,
    }


def _render_top_deals_html(top_deals):
    if not top_deals:
        return ""

    rows = []
    for deal in top_deals[:3]:
        rows.append(
            f"""
                <div class="dealItem" data-url="{_safe_attr(deal.get("url"))}">
                  <div class="dealLeft">
                    <div class="dealName">{_safe_html(deal.get("code"))} \u2022 {_safe_html(deal.get("display_name"))}</div>
                    <div class="dealMeta">{_safe_html(deal.get("pct_label"))}</div>
                  </div>
                  <div class="dealRight">Deal</div>
                </div>
                """
        )

    return f"""
        <div class="dealsBox" id="topDeals">
          <div class="dealList">
            {''.join(rows)}
          </div>
        </div>
        """


def _render_toast_html(msg):
    if not msg:
        return ""
    return f'<div class="toast" id="toast">{_safe_html(msg)}</div>'


@app.get("/api/watchlist")
def api_watchlist():
    mode = (request.args.get("mode") or "enabled").strip()
    ok, msg, rows = fetch_watchlist(mode=mode)
    return jsonify(
        {
            "ok": ok,
            "msg": msg,
            "rows": rows,
            "authority_mode": WATCHLIST_AUTHORITY_MODE,
            "legacy_fallback_enabled": False,
        }
    )


@app.get("/api/debug/watchlist_compare")
def api_debug_watchlist_compare():
    mode = (request.args.get("mode") or "enabled").strip().lower()
    if mode not in {"enabled", "all"}:
        mode = "enabled"
    sample_limit = _coerce_compare_sample_limit(request.args.get("sample") or "20")

    current_ok = False
    current_msg = ""
    current_rows = []
    sqlite_ok = False
    sqlite_msg = "ok"
    sqlite_rows = []
    authority_ok = False
    authority_msg = ""
    authority_rows = []
    authority_status = {}

    try:
        authority_ok, authority_msg, authority_rows = fetch_watchlist(mode=mode)
        authority_status = get_watchlist_runtime_status(db_path=MIRU_STATE_DB_PATH)
    except Exception as exc:
        authority_msg = str(exc)
        app.logger.warning("Watchlist compare authority-path failure: %s", authority_msg)

    try:
        current_rows = list_watchlist_runtime_rows(mode=mode, db_path=MIRU_STATE_DB_PATH)
        current_ok = True
        current_msg = "local_runtime_export"
    except Exception as exc:
        current_msg = str(exc)
        app.logger.warning("Watchlist compare current-path failure: %s", current_msg)

    try:
        sqlite_rows = list_watchlist_entries(db_path=MIRU_STATE_DB_PATH)
        if mode == "enabled":
            sqlite_rows = [row for row in sqlite_rows if int(row.get("enabled") or 0) == 1]
        sqlite_ok = True
    except Exception as exc:
        sqlite_msg = str(exc)
        app.logger.warning("Watchlist compare sqlite failure: %s", sqlite_msg)

    report = _build_watchlist_compare_report(
        current_rows if current_ok else [],
        sqlite_rows if sqlite_ok else [],
        sample_limit=sample_limit,
    )
    summary = report["summary"]
    app.logger.info(
        "Watchlist compare mode=%s current=%s sqlite=%s matched=%s mismatches=%s missing_sqlite=%s missing_current=%s",
        mode,
        summary["current_rows"],
        summary["sqlite_rows"],
        summary["matched_rows"],
        summary["mismatch_rows"],
        summary["missing_in_sqlite"],
        summary["missing_in_current"],
    )

    return jsonify(
        {
            "ok": current_ok and sqlite_ok,
            "mode": mode,
            "sample_limit": sample_limit,
            "authority_path": {
                "mode": WATCHLIST_AUTHORITY_MODE,
                "ok": authority_ok,
                "msg": authority_msg or ("ok" if authority_ok else "watchlist authority fetch failed"),
                "rows": len(authority_rows),
            },
            "readiness": authority_status,
            "current_path": {
                "ok": current_ok,
                "msg": current_msg or ("ok" if current_ok else "local watchlist runtime export failed"),
            },
            "sqlite_path": {
                "ok": sqlite_ok,
                "msg": sqlite_msg,
            },
            **report,
        }
    )


@app.get("/api/debug/cache_metrics")
def api_debug_cache_metrics():
    metrics = get_insight_cache_metrics_snapshot()
    metrics["authority_mode"] = WATCHLIST_AUTHORITY_MODE
    metrics["cache_db_path"] = MIRU_INSIGHT_CACHE_DB_PATH
    persistent_rollup = {}
    latest_effectiveness_report = {}
    latest_backfill_report = {}
    try:
        persistent_rollup = dict(get_persistent_insight_cache_rollup_snapshot() or {})
    except Exception:
        persistent_rollup = {}
    if load_cache_effectiveness_report is not None:
        try:
            latest_effectiveness_report = dict(load_cache_effectiveness_report() or {})
        except Exception:
            latest_effectiveness_report = {}
    if load_backfill_queue_report is not None:
        try:
            latest_backfill_report = dict(load_backfill_queue_report() or {})
        except Exception:
            latest_backfill_report = {}
    return jsonify(
        {
            "ok": True,
            "metrics": metrics,
            "persistent_rollup": persistent_rollup,
            "latest_effectiveness_report": latest_effectiveness_report,
            "latest_backfill_report": latest_backfill_report,
        }
    )


@app.get("/api/debug/cache_hotspots")
def api_debug_cache_hotspots():
    report = {}
    if load_cache_effectiveness_report is not None:
        try:
            report = dict(load_cache_effectiveness_report() or {})
        except Exception:
            report = {}
    if not report:
        report = {
            "generated_at": "",
            "hotspots": {},
            "msg": "No persisted maintenance hotspot report found yet. Run a maintenance cycle to generate one.",
        }
    return jsonify({"ok": True, "report": report})


@app.get("/api/debug/backfill_queue")
def api_debug_backfill_queue():
    report = {}
    if load_backfill_queue_report is not None:
        try:
            report = dict(load_backfill_queue_report() or {})
        except Exception:
            report = {}
    if not report:
        report = {
            "generated_at": "",
            "plan": {},
            "last_apply": {},
            "msg": "No backfill queue report found yet. Run maintenance cycle in plan/apply mode.",
        }
    return jsonify({"ok": True, "report": report})


@app.get("/api/debug/contextual_insights")
def api_debug_contextual_insights():
    card_code = (request.args.get("card_code") or "").strip().upper()
    if not card_code:
        return jsonify({"ok": False, "msg": "Missing card_code"}), 400

    dossier = _get_miru_dossier_store()
    snapshot = dict(dossier.fetch_card_snapshot(card_code) or {})
    if not snapshot:
        return jsonify({"ok": False, "msg": "Card not found in local dossier", "card_code": card_code}), 404

    typed_bundle = _resolve_card_typed_intelligence_bundle(
        card_code,
        dossier=dossier,
        insight_cache=_get_miru_insight_cache(),
        context_prefix="debug_contextual_insights",
    )
    opportunities = _build_contextual_opportunities_for_card(
        card_code=card_code,
        snapshot=snapshot,
        typed_bundle=typed_bundle,
        watchlist_context={
            "target_price": request.args.get("target_price") or "",
            "current_price": request.args.get("current_price") or "",
            "card_code": card_code,
        },
        context_tag="debug:contextual_insights",
    )
    context_snapshot = {}
    if MIRU_CONTEXT_WINDOW is not None:
        try:
            context_snapshot = dict(MIRU_CONTEXT_WINDOW.snapshot() or {})
        except Exception:
            context_snapshot = {}
    return jsonify(
        {
            "ok": True,
            "card_code": card_code,
            "context_snapshot": context_snapshot,
            "opportunities": opportunities,
        }
    )


@app.post("/api/upsert_json")
def api_upsert_json():
    url = (request.values.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "Missing URL"}), 400

    pricing_status = _infer_pricing_status(url)
    payload = {
        "url": url,
        "enabled": True,
        "target_price": (request.values.get("target_price") or "").strip(),
        "cooldown_minutes": (request.values.get("cooldown_minutes") or "").strip(),
        "notes": (request.values.get("notes") or "").strip(),
        "pricing_status": pricing_status,
    }

    card_id = (request.values.get("card_id") or "").strip().upper()
    if card_id:
        payload["card_id"] = card_id

    replaced_url = ""
    if card_id and _is_direct_tcgplayer_product_url(url):
        wl_ok, _wl_msg, watchlist_rows = fetch_watchlist(mode="enabled")
        if wl_ok:
            conflict = _find_card_id_conflict(card_id, url, watchlist_rows)
            if conflict:
                if conflict.get("is_exact"):
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "msg": (
                                    f"{card_id} already exists with a different URL "
                                    f"({conflict['url']}). Update or remove the older entry first "
                                    "to avoid duplicates."
                                ),
                            }
                        ),
                        409,
                    )

                replaced_url = conflict["url"]
                payload["replace_url"] = replaced_url
                _disable_watchlist_payload_in_state_db(
                    replaced_url,
                    source_kind="dashboard-disable-replaced-link",
                    sheet_sync_status="retired",
                )
                update_cached_watchlist_row({"url": replaced_url, "enabled": False})
                _disable_watchlist_payload_in_state_db(
                    replaced_url,
                    source_kind="dashboard-disable-replaced-link",
                    sheet_sync_status="retired",
                )

    local_saved = _upsert_watchlist_payload_to_state_db(
        payload,
        source_kind="dashboard-upsert",
        sheet_sync_status="retired",
    )
    if card_id and _is_direct_tcgplayer_product_url(url):
        save_confirmed_card_url(card_id, url)
    update_cached_watchlist_row(payload)
    local_saved = _upsert_watchlist_payload_to_state_db(
        payload,
        source_kind="dashboard-upsert",
        sheet_sync_status="retired",
    ) or local_saved

    if replaced_url:
        msg = f"Updated {card_id} with an exact product URL."
    elif pricing_status == "missing_link":
        msg = "Added to watchlist. Needs exact product link for live pricing."
    else:
        msg = "Added/updated locally"

    if local_saved:
        msg = msg.rstrip() + " Local authority updated."
    else:
        msg = msg.rstrip() + " Local authority write needs attention."

    response = {
        "ok": bool(local_saved),
        "msg": msg,
        "authority_ok": bool(local_saved),
        "authority_mode": WATCHLIST_AUTHORITY_MODE,
        "sync_ok": False,
        "sync_warning": "",
        "sync_status": "retired",
        "sync_failure_kind": "",
    }
    response.update(_build_watchlist_card_response(payload))
    if replaced_url:
        response["replaced_url"] = replaced_url
    return jsonify(response)


@app.post("/api/disable_json")
def api_disable_json():
    url = (request.values.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "Missing URL"}), 400

    payload = {"url": url, "enabled": False}

    local_ok = _disable_watchlist_payload_in_state_db(
        url,
        source_kind="dashboard-disable",
        sheet_sync_status="retired",
    )
    if local_ok:
        update_cached_watchlist_row(payload)

    _disable_watchlist_payload_in_state_db(
        url,
        source_kind="dashboard-disable",
        sheet_sync_status="retired",
    )
    _WL_CACHE["ts"] = 0

    if local_ok:
        msg = "Removed locally."
    else:
        msg = "Remove failed locally."

    return jsonify(
        {
            "ok": bool(local_ok),
            "msg": msg,
            "authority_ok": bool(local_ok),
            "authority_mode": WATCHLIST_AUTHORITY_MODE,
            "sync_ok": False,
            "sync_failure_kind": "",
        }
    )


def _set_from_code(code: str) -> str:
    c = (code or "").upper().strip()
    if not c:
        return ""
    if c.startswith("P-"):
        return "P"
    m = re.match(r"^(OP|EB|ST|PRB)(\d{2})-", c)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return ""


@app.get("/api/images")
def api_images():
    return jsonify({"ok": True, "images": IMAGE_API_CACHE.get("payload") or []})


@app.get("/api/resolve_card_url")
def api_resolve_card_url():
    code = (request.args.get("code") or "").strip().upper()
    variant = (request.args.get("variant") or "").strip()
    ok, msg, data = resolve_card_url(code, variant=variant)
    return jsonify({"ok": ok, "msg": msg, **data})


@app.post("/api/confirm_card_url")
def api_confirm_card_url():
    card_id = (request.values.get("card_id") or "").strip().upper()
    url = (request.values.get("url") or "").strip()
    variant = (request.values.get("variant") or "").strip()
    saved_url = save_confirmed_card_url(card_id, url, variant=variant)
    if not saved_url:
        return jsonify({"ok": False, "msg": "A strict direct TCGplayer product URL is required"}), 400
    return jsonify(
        {
            "ok": True,
            "msg": (
                f"Saved confirmed link for {card_id} ({_build_variant_info(variant).get('display')})"
                if _build_variant_info(variant).get("display")
                else f"Saved confirmed link for {card_id}"
            ),
            "card_id": card_id,
            "url": saved_url,
            "link_status": "confirmed_exact",
        }
    )


@app.get("/api/find_card_candidates")
def api_find_card_candidates():
    filename = (request.args.get("filename") or "").strip()
    code = (request.args.get("code") or "").strip()
    variant = (request.args.get("variant") or "").strip()
    card_input = filename or code
    data = find_card_candidates(card_input=card_input, variant=variant)
    ok = data.get("match_status") != "unresolved"
    return jsonify({"ok": ok, **data})


@app.get("/api/search_card_url")
def api_search_card_url():
    set_name = (request.args.get("set_name") or "").strip()
    card_number = (request.args.get("card_number") or "").strip()
    ok, msg, data = search_tcgplayer_url(set_name, card_number)
    return jsonify({"ok": ok, "msg": msg, **data})


@app.get("/api/catalog/search")
def api_catalog_search():
    q = (request.args.get("q") or "").strip()
    set_code = (request.args.get("set") or "").strip().upper()
    rows = search_catalog_cards(query=q, set_code=set_code, db_path=CATALOG_DB_PATH)
    dossier = _get_miru_dossier_store()
    insight_cache = _get_miru_insight_cache()
    summary_cache: dict[str, dict] = {}
    bundle_cache: dict[str, dict] = {}
    for row in rows:
        image_path = (row.get("image_path") or "").strip()
        row["thumb_path"] = _thumb_path(image_path) or image_path
        canonical_code = (row.get("canonical_code") or row.get("card_code") or "").strip().upper()
        if canonical_code and len(rows) <= 24:
            try:
                record_contextual_view_metric(context_tag="context_view:catalog_search")
            except Exception:
                pass
            if MIRU_CONTEXT_WINDOW is not None:
                try:
                    MIRU_CONTEXT_WINDOW.record_card_view(
                        canonical_code,
                        is_leader=str(row.get("card_type") or "").strip().lower() == "leader",
                    )
                except Exception:
                    pass
            if canonical_code not in summary_cache:
                try:
                    summary_cache[canonical_code] = _resolve_card_intelligence_summary(
                        canonical_code,
                        dossier=dossier,
                        insight_cache=insight_cache,
                        context_tag="catalog_search:card_intelligence_summary",
                    )
                except Exception:
                    app.logger.warning("Catalog insight cache lookup failed for %s", canonical_code, exc_info=True)
                    summary_cache[canonical_code] = {}
            row["miru_card_summary"] = dict(summary_cache.get(canonical_code) or {})
            if canonical_code not in bundle_cache:
                try:
                    bundle_cache[canonical_code] = _resolve_card_typed_intelligence_bundle(
                        canonical_code,
                        dossier=dossier,
                        insight_cache=insight_cache,
                        context_prefix="catalog_search",
                    )
                except Exception:
                    app.logger.warning("Catalog typed insight lookup failed for %s", canonical_code, exc_info=True)
                    bundle_cache[canonical_code] = {}
            bundle = dict(bundle_cache.get(canonical_code) or {})
            row["miru_quick_insights"] = {
                "usage_insight": dict(bundle.get("usage_insight") or {}),
                "strategy_insight": dict(bundle.get("strategy_insight") or {}),
                "meta_insight": dict(bundle.get("meta_insight") or {}),
                "verified_loop_card_summary": dict(bundle.get("verified_loop_card_summary") or {}),
                "voice_primary": str(bundle.get("voice_primary") or ""),
            }
        else:
            row["miru_card_summary"] = {}
            row["miru_quick_insights"] = {}
    return jsonify({"ok": True, "rows": rows})


@app.get("/api/miru/insight/<card_code>")
def api_miru_insight(card_code: str):
    payload = _build_miru_insight_response(card_code)
    status_code = 200 if payload.get("ok") else 404
    return jsonify(payload), status_code


@app.get("/catalog")
@app.get("/cards")
def cards_page():
    refresh_requested = request.args.get("refresh", "").strip() == "1"
    metadata_requested = request.args.get("metadata", "").strip() == "1"
    force_requested = request.args.get("force", "").strip() == "1"
    metadata_result = None

    if refresh_requested:
        refresh_local_indexes()
        refresh_catalog_index()

    should_import_metadata = metadata_requested or (
        refresh_requested and os.path.isfile(CATALOG_METADATA_PATH)
    )

    if should_import_metadata:
        overwrite_metadata = force_requested if metadata_requested else False
        try:
            metadata_result = refresh_catalog_metadata(overwrite=overwrite_metadata)
        except Exception as exc:
            app.logger.exception(
                "Catalog metadata import failed from %s",
                CATALOG_METADATA_PATH,
            )
            metadata_result = {
                "source_path": CATALOG_METADATA_PATH,
                "source_exists": os.path.isfile(CATALOG_METADATA_PATH),
                "overwrite": overwrite_metadata,
                "rows_read": 0,
                "rows_valid": 0,
                "rows_invalid": 0,
                "rows_updated": 0,
                "rows_skipped": 0,
                "rows_unmatched": 0,
                "error": str(exc),
            }

    return render_template(
        "catalog.html",
        **_project_page_context(
            "cards",
            refresh_requested=refresh_requested,
            metadata_requested=metadata_requested,
            force_requested=force_requested,
            metadata_result=metadata_result,
            metadata_path=CATALOG_METADATA_PATH,
            set_options=sorted(SET_CODE_TO_NAME.keys()),
        ),
    )


@app.get("/intelligence")
@app.get("/insights")
def intelligence_page():
    insight_rows = _fetch_insight_rows(limit=18)
    return render_template(
        "intelligence.html",
        **_project_page_context(
            "insights",
            insight_rows=insight_rows,
        ),
    )


@app.get("/sets")
def sets_page():
    set_rows = _fetch_set_summaries()
    return render_template(
        "sets.html",
        **_project_page_context(
            "sets",
            set_rows=set_rows,
        ),
    )


@app.get("/leaders")
def leaders_page():
    leader_rows = _fetch_leader_rows(limit=180)
    return render_template(
        "leaders.html",
        **_project_page_context(
            "leaders",
            leader_rows=leader_rows,
        ),
    )


@app.get("/verified")
def verified_page():
    snapshot = _fetch_verified_snapshot()
    return render_template(
        "verified.html",
        **_project_page_context(
            "verified",
            verified_snapshot=snapshot,
        ),
    )


@app.get("/status")
def status_page():
    status_snapshot = _fetch_status_snapshot()
    return render_template(
        "status.html",
        **_project_page_context(
            "status",
            status_snapshot=status_snapshot,
        ),
    )


@app.get("/")
def index():
    if request.args.get("reindex", "").strip() == "1":
        refresh_local_indexes()
        refresh_catalog_index()

    msg = (request.args.get("msg") or "").strip()
    wl_ok, watchlist_rows = get_cached_watchlist(mode="enabled")
    items = _build_items(wl_ok, watchlist_rows)

    last_prices = load_last_prices()
    next_last_prices = dict(last_prices)
    enriched = [_enrich_item(item, last_prices, next_last_prices) for item in items]
    enriched.sort(key=_sort_enriched_item)
    save_last_prices(next_last_prices)

    top_deals = [x for x in enriched if x["hit"]]
    top_deals.sort(key=lambda x: x["pct"], reverse=True)
    top_deals = top_deals[:5]
    cards_html = [_render_card_html(item, i) for i, item in enumerate(enriched)]
    toast = _render_toast_html(msg)
    top_html = _render_top_deals_html(top_deals)

    # ALWAYS define the grid HTML
    grid_html = (
        "".join(cards_html) if cards_html else '<div class="card">No data yet.</div>'
    )

    return render_template(
        "index.html",
        **_project_page_context(
            "home",
            toast=toast,
            top_html=top_html,
            cards_html=grid_html,
        ),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
