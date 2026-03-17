#!/usr/bin/env python
from __future__ import annotations

from contextlib import closing
import argparse
import ctypes
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.exceptions import HTTPException

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.miru_ai_onepiece import (
    initialize_fallback_catalog_db,
    inspect_fallback_catalog_db,
    normalize_card_code,
)
from tools.miru_env import build_pushover_status_message, inspect_pushover_env, load_project_env
from tools.miru_dossier_store import MiruDossierStore, inspect_miru_dossier_store
from tools.miru_learner_config import (
    get_learner_mode,
    learner_status as get_learner_status,
    set_learner_mode,
)
from tools.miru_learning_engine import load_learning_engine_status
from tools.miru_learning_notifications import (
    build_learning_notification,
    save_learning_notification_baseline,
)
from tools.miru_network_trust import is_trusted_private_client
from tools.miru_pushover import send_pushover_notification
from tools.miru_project_sync import load_card_validation_audit, list_validation_audit_insights
from tools.miru_runtime_preflight import build_runtime_preflight_report


TOOL_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = TOOL_ROOT / "templates"
STATIC_DIR = TOOL_ROOT / "static"
PROJECT_ROOT = TOOL_ROOT.parent
SCRIPT_PATH = TOOL_ROOT / "miru_ai.py"
KNOWLEDGE_CACHE_PATH = PROJECT_ROOT / "data" / "miru_ai_onepiece_knowledge.json"
FALLBACK_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
LEARNING_QUEUE_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_queue.db"
LEARNING_STATUS_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_log.db"
LEARNING_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
LIMITS_STATUS_PATH = PROJECT_ROOT / "data" / "miru_limits_status.json"
CSS_PATH = STATIC_DIR / "miru_ai.css"
JS_PATH = STATIC_DIR / "miru_ai.js"
TEMPLATE_PATH = TEMPLATE_DIR / "miru_ai.html"
DEFAULT_TIMEOUT_SECONDS = 180
RUN_API_PATH = "/api/run"
APP_NAME = "Miru AI"
APP_TAGLINE = "A One Piece Card Intelligence System"
PROJECT_ENV_LOAD = load_project_env()
_PUSHOVER_STATUS_LOCK = Lock()
_PUSHOVER_STATUS_LOGGED = False
CARD_REFERENCE_RE = re.compile(r"\b(?:OP|EB|ST|PRB)\d{2}-\d{3}[A-Z]?|P-\d{3}\b", re.I)
DIRECT_KNOWLEDGE_HINTS = (
    "what is the effect",
    "effect of",
    "what does",
    "who drew",
    "what variants exist",
    "what is the rarity",
    "rarity of",
    "what attribute",
    "attribute of",
    "what card type",
    "card type of",
    "what is the trigger",
    "trigger of",
    "what is op",
    "what is eb",
)
SET_CATALOG_HINTS = (
    "what cards are in",
    "what is op",
    "what is eb",
    "what is st",
    "set",
    "catalog",
    "fields are known",
    "fields are missing",
    "what is still missing",
    "represented in the catalog",
    "card information is still missing",
)
TRAINING_HINTS = (
    "what is still missing",
    "what does miru still need to learn",
    "what should miru learn next",
    "which fields are known",
    "which fields are missing",
    "card information is still missing",
    "still missing for",
    "knowledge gap",
    "missing data",
    "missing field",
    "represented in the catalog",
)
GAMEPLAY_HINTS = (
    "don!!",
    "counter",
    "trigger",
    "blocker",
    "rush",
    "banish",
    "double attack",
)
MATCHING_HINTS = (
    "tell me about",
    "which one is",
    "which card is",
    "identify",
    "from filename",
    "from filenames",
    "filename",
)
EXPLICIT_DEVELOPMENT_HINTS = (
    "codex prompt",
    "implementation plan",
    "development brief",
    "debug ",
    "review ",
    "fix ",
    "implement ",
    "update ",
    "refactor ",
    "edit ",
    "patch ",
    "app.py",
    "dashboard",
    "repository",
    "repo",
    "flask",
    "server",
    "ui",
    "bug ",
    "bug:",
)

MODE_CONFIGS = (
    {
        "key": "card lookup",
        "label": "Card Lookup",
        "caption": "Exact card questions and direct card facts.",
        "hint": "Best for exact card codes, card names, and direct card-detail questions.",
        "use_case": "Use this when you want to know what one specific card is and what Miru already knows about it.",
        "answer_shape": "You get the resolved card, the known details, any missing fields, and a few good next questions.",
        "request_help": "Ask about one card code or card name. Miru AI will return the card it found, the known card fields, and any missing or uncertain details clearly.",
        "request_placeholder": "Example: What is OP09-001?",
        "request_example": "Good fit: card lookups, rarity checks, artist questions, illustration questions, and field-completeness checks for one card.",
        "result_hint": "Use the answer as a card fact sheet first. Missing fields should stay visible so Miru can learn what the catalog still lacks.",
    },
    {
        "key": "card analysis",
        "label": "Card Analysis",
        "caption": "Effects, keywords, and game mechanics in plain language.",
        "hint": "Best for effect text, trigger text, and game-mechanic questions.",
        "use_case": "Use this when you want to know what a card does or what a One Piece rule term means.",
        "answer_shape": "You get mechanic notes, effect text context, and follow-up questions for deeper learning.",
        "request_help": "Ask what a card effect means, what a mechanic does, or how several gameplay terms relate to each other.",
        "request_placeholder": "Example: What effects does OP04-061 have?",
        "request_example": "Good fit: effect text, trigger text, DON!!, Counter, Trigger, and card-by-card gameplay explanations.",
        "result_hint": "Read the effect analysis first, then use the follow-up suggestions to compare that mechanic with other cards or sets.",
    },
    {
        "key": "variant & print analysis",
        "label": "Variant & Print Analysis",
        "caption": "Alt arts, promos, SP cards, manga cards, and print confusion.",
        "hint": "Best for variant questions, print families, and rough card references that need careful identification.",
        "use_case": "Use this when you want to know which print or variant a card reference most likely means.",
        "answer_shape": "You get likely matches, variant notes, ambiguity warnings, and follow-up questions.",
        "request_help": "Ask about promos, alt arts, SP cards, manga cards, reprints, filenames, or same-name cards that need clearer print handling.",
        "request_placeholder": "Example: What variants exist for EB04-031 King?",
        "request_example": "Good fit: variant families, print comparisons, filename-based identification, and ambiguous same-name card questions.",
        "result_hint": "Use the result to see which print details are known, which are uncertain, and what Miru still needs to learn about that card family.",
    },
    {
        "key": "set & catalog knowledge",
        "label": "Set & Catalog Knowledge",
        "caption": "Set codes, set contents, and catalog coverage.",
        "hint": "Best for set-level questions, coverage checks, and catalog completeness questions.",
        "use_case": "Use this when you want to know what is in a set or how complete the current catalog knowledge looks.",
        "answer_shape": "You get set references, sample card coverage, missing data notes, and follow-up questions.",
        "request_help": "Ask what a set is, what cards are in it, which fields are known, or what the current catalog still needs to learn.",
        "request_placeholder": "Example: What cards are in OP09?",
        "request_example": "Good fit: set lookup, catalog completeness, missing-field questions, and how a card or set should be represented in the catalog.",
        "result_hint": "Use the answer to see what Miru already knows about the set and what knowledge gaps still matter for the catalog.",
    },
    {
        "key": "knowledge training",
        "label": "Knowledge Training",
        "caption": "What Miru knows, what is missing, and what it should learn next.",
        "hint": "Best for field completeness checks and catalog-coverage questions.",
        "use_case": "Use this when your main goal is improving Miru's card knowledge instead of only reading one fact.",
        "answer_shape": "You get known fields, missing fields, uncertainty notes, and suggested next questions.",
        "request_help": "Ask what Miru still needs to learn, which fields are missing, or how a card should be represented so the catalog becomes more complete over time.",
        "request_placeholder": "Example: What card information is still missing for OP09-001?",
        "request_example": "Good fit: missing-field checks, unknown-field checks, completeness questions, and what Miru should learn next.",
        "result_hint": "Use the answer to spot missing knowledge quickly and decide what Miru should learn next about the card or set.",
    },
)
MODE_LABELS = {config["key"]: config["label"] for config in MODE_CONFIGS}
LEGACY_MODE_LABELS = {
    "card knowledge": "Card Knowledge",
    "matching": "Matching / Identification",
    "codex prompt": "Codex Prompt",
    "development": "Miru Development",
    "plan": "Plan",
    "debug": "Debug",
    "review": "Review",
}
ALL_MODE_LABELS = {**MODE_LABELS, **LEGACY_MODE_LABELS}

PRESETS = (
    {
        "label": "What is OP09-001?",
        "mode": "card lookup",
        "request_text": "What is OP09-001?",
    },
    {
        "label": "What effects does OP04-061 have?",
        "mode": "card analysis",
        "request_text": "What effects does OP04-061 have?",
    },
    {
        "label": "What variants exist for EB04-031 King?",
        "mode": "variant & print analysis",
        "request_text": "What variants exist for EB04-031 King?",
    },
    {
        "label": "What set is OP05-067 from?",
        "mode": "set & catalog knowledge",
        "request_text": "What set is OP05-067 from?",
    },
    {
        "label": "Does this card have trigger text?",
        "mode": "card analysis",
        "request_text": "Does OP04-061 have trigger text?",
    },
    {
        "label": "What facts are still missing for P-088?",
        "mode": "knowledge training",
        "request_text": "What card information is still missing for P-088?",
    },
)

NAV_ITEMS = (
    {"endpoint": "index", "label": "Home"},
    {"endpoint": "ask_page", "label": "Ask Miru"},
    {"endpoint": "dossiers_page", "label": "Dossiers"},
    {"endpoint": "gaps_page", "label": "Gaps"},
    {"endpoint": "training_page", "label": "Training"},
    {"endpoint": "status_page", "label": "Status"},
    {"endpoint": "dev_page", "label": "Dev Monitor"},
)

ROADMAP_SECTIONS = (
    {
        "title": "What Miru AI knows now",
        "items": (
            "Card lookup",
            "Variant and print analysis",
            "Verified dossier-backed answers",
            "Local source and citation tracking",
            "Official snapshot ingestion",
        ),
    },
    {
        "title": "What Miru AI is learning next",
        "items": (
            "Richer official field coverage",
            "Snapshot refresh and update flow",
            "Better missing-field and conflict visibility",
            "More practical card question coverage",
        ),
    },
    {
        "title": "What is planned later",
        "items": (
            "Deck intelligence",
            "Relationship graphing",
            "Player lens",
            "Market lens",
            "Semantic and vector retrieval support",
        ),
    },
)

DOSSIER_PANELS = (
    {
        "eyebrow": "Identity",
        "title": "Resolve the exact card first",
        "body": "Miru dossiers are built around exact One Piece card identity: code, name, set, rarity, color, type, and print family when known.",
    },
    {
        "eyebrow": "Sources",
        "title": "Keep facts tied to evidence",
        "body": "Answers should show where a fact came from, what is verified, and what still needs a better source instead of pretending certainty.",
    },
    {
        "eyebrow": "Variants",
        "title": "Track prints without hiding ambiguity",
        "body": "Alt arts, promos, SP cards, and same-name cards can stay separate so Miru can explain what is clear and what still needs cleanup.",
    },
)

GAP_PANELS = (
    {
        "eyebrow": "Missing fields",
        "title": "Keep unknowns visible",
        "body": "Miru should show missing effect text, attributes, image identity, or source coverage directly so the next enrichment pass knows what to fix.",
    },
    {
        "eyebrow": "Conflicts",
        "title": "Record disagreement instead of flattening it",
        "body": "When a lower-tier source disagrees with an official record, the conflict stays visible instead of getting silently collapsed into one answer.",
    },
    {
        "eyebrow": "Next question",
        "title": "Turn gaps into useful follow-ups",
        "body": "Good gap tracking should tell you the next practical card question to ask, not just that the data is incomplete.",
    },
)

TRAINING_PANELS = (
    {
        "eyebrow": "Verified loop",
        "title": "Miru learns card by card",
        "body": "The sidecar pipeline ingests trusted source records, compares facts, records conflicts, and updates reusable card dossiers without changing the main runtime.",
    },
    {
        "eyebrow": "Refresh",
        "title": "Official snapshots can be updated safely",
        "body": "Miru can refresh official-style snapshots over time, compare what changed, and keep a clean audit trail for what was added, updated, or left unresolved.",
    },
)

INTELLIGENCE_STAGE_BLUEPRINT = (
    {
        "key": "card_recognition",
        "label": "Card Recognition",
        "voyage_arc": "East Blue",
        "summary": "Miru can identify cards and their basic reference data.",
        "what_this_means": "Miru is building a reliable foundation for identifying One Piece cards. It can match names, codes, sets, rarity, and images, but it is not yet ready to interpret full card details or reason about decks.",
        "can_do_now": (
            "Recognize card names, codes, sets, and rarity",
            "Track card identity and basic variant information",
            "Match images to known card records",
            "Prepare the card catalog for deeper learning",
        ),
        "still_learning": (
            "Core stats, traits, and effects",
            "Deck synergy and strategy",
            "Tournament and matchup analysis",
            "Verified multi-source card intelligence",
        ),
        "next_stage_detail": "Miru will begin learning how to understand individual card details such as stats, traits, and effects.",
    },
    {
        "key": "card_understanding",
        "label": "Card Understanding",
        "voyage_arc": "Alabasta",
        "summary": "Miru can understand individual card facts, but not full deck or meta reasoning yet.",
        "what_this_means": "Miru is currently strongest at understanding individual One Piece cards. It can identify cards and interpret core card details, but it is not yet advanced enough to fully understand decks, tournament trends, or the competitive meta.",
        "can_do_now": (
            "Recognize card names, codes, sets, and rarity",
            "Read core stats, traits, and effects",
            "Track card identity and variant information",
            "Use dossier-style card records where they already exist",
        ),
        "still_learning": (
            "Deck synergy and strategy",
            "Tournament and matchup analysis",
            "Market reasoning tied to competitive play",
            "Broader verified multi-source card intelligence across the catalog",
        ),
        "next_stage_detail": "Miru will begin learning how cards function together inside real decklists.",
    },
    {
        "key": "deck_understanding",
        "label": "Deck Understanding",
        "voyage_arc": "Water 7",
        "summary": "Miru will learn how individual cards combine into real decks and roles.",
        "what_this_means": "Miru will move from understanding single cards to understanding how cards function together inside complete decklists, including roles, synergy, and common play patterns.",
        "can_do_now": (
            "Relate cards to common deck roles",
            "Recognize simple synergy patterns",
            "Follow real decklist structure",
            "Explain why certain cards are paired together",
        ),
        "still_learning": (
            "Tournament-level matchup reasoning",
            "Meta trend tracking",
            "Price reasoning tied to play patterns",
            "Full verified dossier coverage across the catalog",
        ),
        "next_stage_detail": "Miru will begin learning which decks, cards, and strategies are actually performing well in the live meta.",
    },
    {
        "key": "meta_understanding",
        "label": "Meta Understanding",
        "voyage_arc": "Dressrosa",
        "summary": "Miru will learn which decks and strategies are performing well.",
        "what_this_means": "Miru will start connecting deck knowledge to competitive results so it can explain which decks, cards, and strategies are currently strong and why they matter.",
        "can_do_now": (
            "Track deck performance trends",
            "Spot common matchups and pressure points",
            "Explain why certain cards rise in importance",
            "Summarize competitive shifts over time",
        ),
        "still_learning": (
            "Market reasoning tied to demand",
            "Broader verified intelligence across trusted sources",
        ),
        "next_stage_detail": "Miru will begin connecting market movement and demand to deck usage and competitive trends.",
    },
    {
        "key": "market_intelligence",
        "label": "Market Intelligence",
        "voyage_arc": "Wano",
        "summary": "Miru will connect card prices and demand to how the game is actually being played.",
        "what_this_means": "Miru will start relating price movement and buyer demand to deck usage, set relevance, and competitive shifts instead of treating market data as a separate system.",
        "can_do_now": (
            "Track price movement against deck usage",
            "Explain demand spikes tied to play trends",
            "Connect metagame shifts to market pressure",
            "Surface cards whose value is moving for gameplay reasons",
        ),
        "still_learning": (
            "Full verified multi-source card intelligence across the product",
        ),
        "next_stage_detail": "Miru will begin answering from broad, verified dossier-style knowledge gathered from trusted sources.",
    },
    {
        "key": "verified_card_intelligence",
        "label": "Verified Card Intelligence",
        "voyage_arc": "Egghead",
        "summary": "Miru will answer from broad, verified, dossier-style card knowledge.",
        "what_this_means": "Miru will be able to answer from verified dossier-style knowledge gathered from trusted sources across the product, with stronger evidence, broader coverage, and clearer confidence about what is known versus still uncertain.",
        "can_do_now": (
            "Answer from trusted, verified dossier records",
            "Keep source-backed card knowledge organized and reusable",
            "Surface clearer evidence and confidence boundaries",
            "Support more reliable product experiences across card, deck, meta, and market workflows",
        ),
        "still_learning": (
            "Ongoing coverage expansion and answer quality improvements",
        ),
        "next_stage_detail": "Miru will keep improving coverage, freshness, and answer quality across the full card intelligence stack.",
    },
)

HOME_HIGHLIGHTS = (
    {
        "title": "Verified card answers",
        "body": "Ask about a card, variant, set, or mechanic and Miru answers from structured knowledge instead of bluffing.",
    },
    {
        "title": "Gaps stay honest",
        "body": "If a field is missing, uncertain, or conflicting, Miru should say so clearly and point to the next useful follow-up.",
    },
)

VOYAGE_ISLANDS = (
    {"key": "east_blue", "name": "East Blue", "short_name": "East Blue", "sprite": "islands/island_east_blue.png", "stage": "East Blue", "map_x": 10, "map_y": 75},
    {"key": "reverse_mountain", "name": "Reverse Mountain", "short_name": "Reverse Mountain", "sprite": "islands/island_reverse_mountain.png", "stage": "Grand Line Approach", "map_x": 23, "map_y": 57},
    {"key": "alabasta", "name": "Alabasta", "short_name": "Alabasta", "sprite": "islands/island_alabasta.png", "stage": "Grand Line", "map_x": 35, "map_y": 66},
    {"key": "skypiea", "name": "Skypiea", "short_name": "Skypiea", "sprite": "islands/island_skypiea.png", "stage": "Grand Line", "map_x": 45, "map_y": 42},
    {"key": "water_7", "name": "Water 7", "short_name": "Water 7", "sprite": "islands/island_water_7.png", "stage": "Grand Line", "map_x": 56, "map_y": 56},
    {"key": "thriller_bark", "name": "Thriller Bark", "short_name": "Thriller Bark", "sprite": "islands/island_thriller_bark.png", "stage": "Grand Line", "map_x": 66, "map_y": 42},
    {"key": "fishman_island", "name": "Fishman Island", "short_name": "Fishman Island", "sprite": "islands/island_fishman_island.png", "stage": "New World", "map_x": 74, "map_y": 64},
    {"key": "dressrosa", "name": "Dressrosa", "short_name": "Dressrosa", "sprite": "islands/island_dressrosa.png", "stage": "New World", "map_x": 83, "map_y": 48},
    {"key": "whole_cake", "name": "Whole Cake", "short_name": "Whole Cake", "sprite": "islands/island_whole_cake.png", "stage": "New World", "map_x": 90, "map_y": 62},
    {"key": "wano", "name": "Wano", "short_name": "Wano", "sprite": "islands/island_wano.png", "stage": "New World", "map_x": 86, "map_y": 29},
    {"key": "egghead", "name": "Egghead", "short_name": "Egghead", "sprite": "islands/island_egghead.png", "stage": "Final Voyage", "map_x": 69, "map_y": 15},
    {"key": "laugh_tale", "name": "Laugh Tale", "short_name": "Laugh Tale", "sprite": "islands/island_laugh_tale.png", "stage": "Final Voyage", "map_x": 50, "map_y": 11},
)

VOYAGE_BOSSES = (
    {"name": "Alvida", "sprite": "bosses/boss_alvida.png", "island_key": "east_blue"},
    {"name": "Kuro", "sprite": "bosses/boss_kuro.png", "island_key": "east_blue"},
    {"name": "Krieg", "sprite": "bosses/boss_krieg.png", "island_key": "east_blue"},
    {"name": "Buggy", "sprite": "bosses/boss_buggy.png", "island_key": "east_blue"},
    {"name": "Arlong", "sprite": "bosses/boss_arlong.png", "island_key": "east_blue"},
    {"name": "Crocodile", "sprite": "bosses/boss_crocodile.png", "island_key": "alabasta"},
    {"name": "Enel", "sprite": "bosses/boss_enel.png", "island_key": "skypiea"},
    {"name": "Lucci", "sprite": "bosses/boss_lucci.png", "island_key": "water_7"},
    {"name": "Moria", "sprite": "bosses/boss_moria.png", "island_key": "thriller_bark"},
    {"name": "Doflamingo", "sprite": "bosses/boss_doflamingo.png", "island_key": "dressrosa"},
    {"name": "Katakuri", "sprite": "bosses/boss_katakuri.png", "island_key": "whole_cake"},
    {"name": "Big Mom", "sprite": "bosses/boss_big_mom.png", "island_key": "whole_cake"},
    {"name": "Kaido", "sprite": "bosses/boss_kaido.png", "island_key": "wano"},
    {"name": "Five Elders", "sprite": "bosses/boss_five_elders.png", "island_key": "egghead"},
    {"name": "Imu", "sprite": "bosses/boss_imu.png", "island_key": "egghead"},
    {"name": "Blackbeard", "sprite": "bosses/boss_blackbeard.png", "island_key": "laugh_tale"},
)

VOYAGE_ROUTE_MARKERS = {
    "completed": "routes/route_completed_marker.png",
    "current": "routes/route_current_ship_marker.png",
    "next": "routes/route_next_destination_marker.png",
    "planned": "routes/route_checkpoint_marker.png",
    "finish": "routes/route_finish_marker.png",
}

VOYAGE_SHARED_ASSETS = {
    "ship": "ships/polar_tang_sail_01.png",
    "ship_alt": "ships/polar_tang_sail_02.png",
    "travel_ship": "travel/travel_ship_move_01.png",
    "wake_primary": "travel/travel_wake_01.png",
    "wake_secondary": "travel/travel_long_wake_01.png",
    "captain_log": "ui/ui_log_pose.png",
    "compass": "ui/ui_compass_open.png",
    "vivre": "ui/ui_vivre_card.png",
    "celebration": "characters/barto_victory.png",
    "celebration_alt": "characters/barto_fanboy.png",
    "sparkle": "effects/effect_glow_sparkle.png",
    "confetti": "effects/effect_confetti.png",
}

TRAINING_STAGE_BLUEPRINT = (
    ("catalog_ready", "Catalog Ready", "Local card catalog is indexed and ready for lookup."),
    ("dossiers_building", "Dossiers Building", "Structured dossiers are being created card by card."),
    ("verification_expanding", "Verification Expanding", "Verified dossiers are growing with trusted evidence."),
    ("relationship_graph_later", "Relationship Graph Later", "Card and variant relationships will deepen later."),
    ("deck_intelligence_later", "Deck Intelligence Later", "Deck-level intelligence comes after card knowledge is solid."),
)


def read_port_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


PROJECT_MIRU_DEV_PORT = read_port_env("PROJECT_MIRU_DEV_PORT", 18080)
PROJECT_MIRU_PORT = read_port_env("PROJECT_MIRU_PORT", 8080)
ACTIVITY_RECENT_WINDOW_SECONDS = 300

DEV_ACTIVITY_BLUEPRINT = (
    {"key": "sleeping", "title": "Sleeping", "description": "Miru is idle and ready for the next card question.", "visual": "sleeping"},
    {"key": "setting_sail", "title": "Setting Sail", "description": "Miru has real training data and verified coverage is still growing.", "visual": "sailing"},
    {"key": "gathering_crew", "title": "Gathering the Crew", "description": "Miru is actively answering a live request right now.", "visual": "crew"},
    {"key": "storm_warning", "title": "Storm Warning", "description": "A recent Miru request hit a problem and needs attention.", "visual": "storm"},
)

MIRU_ACTIVITY_LOCK = Lock()
MIRU_ACTIVITY_STATE = {
    "active_runs": 0,
    "last_started_at": "",
    "last_finished_at": "",
    "last_mode": "",
    "last_request_text": "",
    "last_error": "",
}

def compute_asset_version() -> str:
    candidates = [Path(__file__)]
    if TEMPLATE_DIR.exists():
        candidates.extend(path for path in TEMPLATE_DIR.rglob("*") if path.is_file())
    if STATIC_DIR.exists():
        candidates.extend(path for path in STATIC_DIR.rglob("*") if path.is_file())
    latest_mtime = max(int(path.stat().st_mtime_ns) for path in candidates)
    return str(latest_mtime)


def format_mode_label(mode_key: str) -> str:
    return ALL_MODE_LABELS.get(mode_key, mode_key.title())



def format_count(value: int) -> str:
    return f"{int(max(value, 0)):,}"


def safe_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((max(numerator, 0) / denominator) * 100, 1)


def ensure_fallback_catalog_status() -> dict[str, Any]:
    status = inspect_fallback_catalog_db(FALLBACK_CATALOG_DB_PATH)
    if status["usable"]:
        return status
    return initialize_fallback_catalog_db(
        db_path=FALLBACK_CATALOG_DB_PATH,
        cache_path=KNOWLEDGE_CACHE_PATH,
    )


def inspect_dossier_db(path: Path | None = None) -> dict[str, Any]:
    return inspect_miru_dossier_store(Path(path or DOSSIER_DB_PATH))


def fetch_dossier_snapshot(card_code: str, path: Path | None = None) -> dict[str, Any] | None:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_snapshot(card_code)


def fetch_dossier_verified_facts(card_code: str, *, fact_type: str = "", path: Path | None = None) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_verified_facts(card_code, fact_type=fact_type)


def fetch_dossier_effects(card_code: str, *, effect_type: str = "", path: Path | None = None) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_effects(card_code, effect_type=effect_type)


def fetch_dossier_answer_context(card_code: str, path: Path | None = None) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_answer_context(card_code)


def fetch_dossier_card_usage(card_code: str, *, leader_code: str = "", archetype_label: str = "", path: Path | None = None) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_usage(card_code, leader_code=leader_code, archetype_label=archetype_label)


def fetch_dossier_leader_links(card_code: str, path: Path | None = None) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_leader_links(card_code)


def fetch_dossier_usage_context(card_code: str, path: Path | None = None) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_usage_context(card_code)


def fetch_dossier_leader_intelligence(leader_code: str, path: Path | None = None) -> dict[str, Any] | None:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_leader_intelligence(leader_code)


def fetch_dossier_leader_context(leader_code: str, path: Path | None = None) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_leader_context(leader_code)


def fetch_dossier_strategy_intel(
    card_code: str,
    *,
    leader_code: str = "",
    path: Path | None = None,
) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_strategy_intel(card_code, leader_code=leader_code)


def fetch_dossier_strategy_context(
    card_code: str,
    *,
    leader_code: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_strategy_context(card_code, leader_code=leader_code)


def fetch_dossier_leader_strategy(
    leader_code: str,
    *,
    role_label: str = "",
    path: Path | None = None,
) -> list[dict[str, Any]]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_strategy_by_leader(leader_code, role_label=role_label)


def fetch_dossier_card_meta(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_meta_intel(card_code)


def fetch_dossier_card_meta_posture(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_card_meta_posture(card_code)


def fetch_dossier_leader_meta(
    leader_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_leader_meta_intel(leader_code)


def fetch_dossier_leader_meta_posture(
    leader_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_leader_meta_posture(leader_code)


def fetch_dossier_card_insight_summary(
    card_code: str,
    *,
    leader_code: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Return a unified card intelligence summary for the Project Miru UI.

    Stitches identity, usage posture, strategy posture, meta posture, top
    leader association, and evidence breadth into one compact read.  No
    heavy computation at request time; all sub-reads are indexed lookups.
    """
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_card_intelligence_summary(card_code, leader_code=leader_code)


def fetch_dossier_leader_insight_summary(
    leader_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return a unified leader intelligence summary for the Project Miru UI.

    Stitches leader pattern intelligence, meta posture, role-grouped cards,
    and strategy records into one compact read.  No heavy computation at
    request time; all sub-reads are indexed lookups on stored structures.
    """
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_leader_intelligence_summary(leader_code)


def fetch_dossier_integrated_card_insight(
    card_code: str,
    *,
    leader_code: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Return the unified Miru Insights object for a card.

    Combines all verified intelligence layers (usage, strategy, meta,
    rulings, synergy) into one structured read.  Includes readiness
    signal, primary insight, additional insights, gameplay tip, and
    optional lore context.  All sub-reads are lightweight indexed
    lookups on stored structures; no heavy aggregation at request time.
    """
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_integrated_card_insight(card_code, leader_code=leader_code)


def fetch_dossier_publication_eligibility(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return stored publication eligibility for a card (lightweight indexed read).

    Fails closed: no audit record yields publish_allowed=False.
    """
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.get_publication_eligibility(card_code)


def fetch_dossier_compliance_summary(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return compact compliance summary for a card (for Dev/UI)."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_compliance_summary(card_code)


def fetch_dossier_synergy_intel(
    card_code: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return stored synergy records for a card (lightweight indexed read)."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_synergy_intel(card_code)


def fetch_dossier_synergy_posture(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return structured synergy posture including caution/reassurance notes."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_synergy_posture(card_code)


def fetch_dossier_synergy_context(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return synergy posture + top synergy records in a single compact read."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_synergy_context(card_code)


def fetch_dossier_synergy_by_leader(
    leader_code: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return synergy records for a leader context (lightweight indexed read)."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_synergy_by_leader(leader_code)


def fetch_dossier_rulings_intel(
    card_code: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return stored ruling intelligence records for a card (lightweight indexed read)."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.fetch_card_rulings_intel(card_code)


def fetch_dossier_ruling_posture(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return structured ruling posture for a card including caution/reassurance notes."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_ruling_posture(card_code)


def fetch_dossier_ruling_context(
    card_code: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return ruling posture + top ruling records in a single compact read."""
    store = MiruDossierStore(Path(path or DOSSIER_DB_PATH))
    return store.build_ruling_context(card_code)


def determine_training_stage(total_cards: int, dossiers_created: int, verified_dossiers: int) -> str:
    if total_cards <= 0:
        return "catalog_ready"
    if dossiers_created <= 0:
        return "dossiers_building"
    if verified_dossiers < total_cards:
        return "verification_expanding"
    if verified_dossiers >= total_cards:
        return "relationship_graph_later"
    return "deck_intelligence_later"


def build_training_stage_rows(training_stage: str) -> list[dict[str, str]]:
    active_seen = False
    rows = []
    for key, label, detail in TRAINING_STAGE_BLUEPRINT:
        if key == training_stage:
            state = "active"
            active_seen = True
        elif active_seen:
            state = "upcoming"
        else:
            state = "complete"
        rows.append({"key": key, "label": label, "detail": detail, "state": state})
    return rows


def determine_intelligence_stage_index(training_status: dict[str, Any]) -> int:
    capability_flags = training_status.get("capability_flags")
    if isinstance(capability_flags, dict):
        for index, key in (
            (5, "verified_card_intelligence"),
            (4, "market_intelligence"),
            (3, "meta_understanding"),
            (2, "deck_understanding"),
        ):
            if capability_flags.get(key):
                return index

    total_cards = int(training_status.get("total_cards") or 0)
    dossiers_created = int(training_status.get("dossiers_created") or 0)
    verified_dossiers = int(training_status.get("verified_dossiers") or 0)

    if total_cards <= 0:
        return 0
    if verified_dossiers > 0 or dossiers_created > 0:
        return 1
    return 0


def build_intelligence_progress(training_status: dict[str, Any]) -> dict[str, Any]:
    current_index = determine_intelligence_stage_index(training_status)
    total_stages = len(INTELLIGENCE_STAGE_BLUEPRINT)
    current_stage = dict(INTELLIGENCE_STAGE_BLUEPRINT[current_index])
    next_stage = dict(INTELLIGENCE_STAGE_BLUEPRINT[current_index + 1]) if current_index + 1 < total_stages else None

    for stage in (current_stage, next_stage):
        if isinstance(stage, dict):
            stage["number"] = 1 + next(
                index for index, entry in enumerate(INTELLIGENCE_STAGE_BLUEPRINT) if entry["key"] == stage["key"]
            )

    stage_rows = []
    for index, stage in enumerate(INTELLIGENCE_STAGE_BLUEPRINT):
        if index < current_index:
            state = "complete"
        elif index == current_index:
            state = "active"
        else:
            state = "upcoming"
        stage_rows.append(
            {
                "key": stage["key"],
                "number": index + 1,
                "label": stage["label"],
                "voyage_arc": stage["voyage_arc"],
                "summary": stage["summary"],
                "state": state,
            }
        )

    total_cards = int(training_status.get("total_cards") or 0)
    verified_dossiers = int(training_status.get("verified_dossiers") or 0)
    progress_percent = float(training_status.get("progress_percent") or 0.0)
    coverage_note = (
        f"{format_count(verified_dossiers)} of {format_count(total_cards)} cards currently have a verified dossier ({progress_percent:.1f}%). "
        "This measures card knowledge coverage, not deck, meta, or market intelligence."
        if total_cards > 0
        else "Miru is still building its local card catalog, so intelligence progress is early and conservative."
    )

    return {
        "total_stages": total_stages,
        "current_stage": current_stage,
        "next_stage": next_stage,
        "stage_rows": stage_rows,
        "coverage_note": coverage_note,
        "voyage_note": "The voyage arc is supporting flavor for the current intelligence stage, not a claim that Miru is near endgame knowledge.",
    }


def build_training_status() -> dict[str, Any]:
    catalog_status = ensure_fallback_catalog_status()
    dossier_status = inspect_dossier_db(DOSSIER_DB_PATH)

    total_cards = int(catalog_status["cards"]) if catalog_status["usable"] else 0
    dossiers_created = min(int(dossier_status["dossiers_created"]), total_cards) if total_cards else int(dossier_status["dossiers_created"])
    verified_dossiers = min(int(dossier_status["verified_dossiers"]), dossiers_created) if dossiers_created else 0
    remaining_gaps = max(total_cards - verified_dossiers, 0)

    catalog_coverage_percent = 100.0 if total_cards > 0 else 0.0
    dossier_coverage_percent = safe_percent(dossiers_created, total_cards)
    verified_coverage_percent = safe_percent(verified_dossiers, total_cards)
    progress_percent = verified_coverage_percent
    training_stage = determine_training_stage(total_cards, dossiers_created, verified_dossiers)

    ring_metrics = [
        {
            "label": "Catalog Coverage",
            "percent": catalog_coverage_percent,
            "value": format_count(total_cards),
            "detail": "Indexed card identities available locally.",
        },
        {
            "label": "Dossier Coverage",
            "percent": dossier_coverage_percent,
            "value": format_count(dossiers_created),
            "detail": "Cards with dossier records created.",
        },
        {
            "label": "Verified Coverage",
            "percent": verified_coverage_percent,
            "value": format_count(verified_dossiers),
            "detail": "Cards with verified dossier state.",
        },
    ]

    verified_summary = f"{format_count(verified_dossiers)} verified dossiers out of {format_count(total_cards)} catalog cards"
    remaining_summary = f"{format_count(remaining_gaps)} cards still need verified dossier coverage."

    ring_metrics_clear = [
        {"label": "Catalog size", "percent": catalog_coverage_percent, "value": format_count(total_cards), "detail": "Indexed card identities available locally."},
        {"label": "Dossiers in store", "percent": dossier_coverage_percent, "value": format_count(dossiers_created), "detail": "Cards with dossier records in the verified store."},
        {"label": "Verified", "percent": verified_coverage_percent, "value": format_count(verified_dossiers), "detail": "Cards with verified dossier state."},
    ]
    stats_clear = (
        {"label": "Catalog size", "value": format_count(total_cards), "detail": "Cards in local catalog."},
        {"label": "Dossiers in verified store", "value": format_count(dossiers_created), "detail": "Card profiles in the main verified store."},
        {"label": "Verified in store", "value": format_count(verified_dossiers), "detail": "Dossiers marked verified."},
        {"label": "Still to verify", "value": format_count(remaining_gaps), "detail": "Catalog cards without a verified dossier."},
    )

    training_status = {
        "total_cards": total_cards,
        "dossiers_created": dossiers_created,
        "verified_dossiers": verified_dossiers,
        "remaining_gaps": remaining_gaps,
        "progress_percent": progress_percent,
        "catalog_coverage_percent": catalog_coverage_percent,
        "dossier_coverage_percent": dossier_coverage_percent,
        "verified_coverage_percent": verified_coverage_percent,
        "training_stage": training_stage,
        "stage_rows": build_training_stage_rows(training_stage),
        "ring_metrics": ring_metrics,
        "catalog_status": catalog_status,
        "dossier_status": dossier_status,
        "verified_summary": verified_summary,
        "remaining_summary": remaining_summary,
        "stats": (
            {
                "label": "Total Cards",
                "value": format_count(total_cards),
                "detail": "Local fallback catalog rows indexed for card lookup.",
            },
            {
                "label": "Dossiers Created",
                "value": format_count(dossiers_created),
                "detail": "Structured card profiles written to the dossier store.",
            },
            {
                "label": "Verified Dossiers",
                "value": format_count(verified_dossiers),
                "detail": "Dossiers that currently report a verified overall state.",
            },
            {
                "label": "Remaining Knowledge Gaps",
                "value": format_count(remaining_gaps),
                "detail": "Catalog cards that still need verified dossier coverage.",
            },
        ),
    }
    training_status["training_progress"] = {
        "catalog_cards_total": total_cards,
        "verified_store_dossiers_count": dossiers_created,
        "verified_store_verified_count": verified_dossiers,
        "verified_store_gaps_count": remaining_gaps,
        "training_progress_percent": progress_percent,
        "training_stage": training_stage,
        "stage_rows": build_training_stage_rows(training_stage),
        "ring_metrics": ring_metrics_clear,
        "verified_summary": verified_summary,
        "remaining_summary": remaining_summary,
        "stats": stats_clear,
    }
    training_status["intelligence_progress"] = build_intelligence_progress(training_status)
    training_status["voyage"] = ensure_voyage_state(training_status)
    return training_status


def voyage_asset_url(relative_path: str) -> str:
    return url_for("static", filename=f"icons/miru_voyage/{relative_path}")


def safe_voyage_asset_url(relative_path: str | None, fallback_relative_path: str = "ui/ui_log_pose.png") -> str:
    voyage_root = STATIC_DIR / "icons" / "miru_voyage"
    candidate = voyage_root / str(relative_path or "")
    if relative_path and candidate.is_file():
        return voyage_asset_url(relative_path)
    fallback_candidate = voyage_root / fallback_relative_path
    if fallback_candidate.is_file():
        return voyage_asset_url(fallback_relative_path)
    return url_for("static", filename="icons/miru-fruit.png")


def voyage_shared_asset_url(key: str, fallback_relative_path: str = "ui/ui_log_pose.png") -> str:
    return safe_voyage_asset_url(VOYAGE_SHARED_ASSETS.get(key), fallback_relative_path)


def build_voyage_assets() -> dict[str, str]:
    fallbacks = {
        "ship": "ships/polar_tang_idle.png",
        "ship_alt": "ships/polar_tang_sail_01.png",
        "travel_ship": "ships/polar_tang_sail_01.png",
        "wake_primary": "effects/effect_wave_loop.png",
        "wake_secondary": "effects/effect_wave_loop.png",
        "captain_log": "ui/ui_log_pose.png",
        "compass": "ui/ui_compass_open.png",
        "vivre": "ui/ui_vivre_card.png",
        "celebration": "characters/barto_idle.png",
        "celebration_alt": "characters/barto_fanboy.png",
        "sparkle": "effects/effect_glow_sparkle.png",
        "confetti": "effects/effect_confetti.png",
    }
    return {
        key: voyage_shared_asset_url(key, fallback_relative_path=fallback)
        for key, fallback in fallbacks.items()
    }


def build_voyage_location(island: dict[str, str], status: str) -> dict[str, str]:
    return {
        "key": island.get("key", "planned"),
        "name": island.get("name", "Planned"),
        "short_name": island.get("short_name", island.get("name", "Planned")),
        "stage": island.get("stage", "Planned"),
        "status": status,
        "map_x": int(island.get("map_x", 0) or 0),
        "map_y": int(island.get("map_y", 0) or 0),
        "sprite_url": safe_voyage_asset_url(island.get("sprite"), "islands/island_east_blue.png"),
    }


def build_voyage_boss(boss: dict[str, str], status: str) -> dict[str, str]:
    return {
        "name": boss.get("name", "Planned"),
        "island_key": boss.get("island_key", ""),
        "status": status,
        "sprite_url": safe_voyage_asset_url(boss.get("sprite"), "characters/barto_idle.png"),
    }


def build_voyage_log_entries(
    current_island_index: int,
    defeated_boss_count: int,
    next_island: dict[str, str] | None,
) -> list[dict[str, str]]:
    assets = build_voyage_assets()
    entries: list[dict[str, str]] = []
    if defeated_boss_count > 0:
        last_boss = VOYAGE_BOSSES[defeated_boss_count - 1]
        entries.append(
            {
                "message": f"Defeated {last_boss['name']}",
                "tone": "victory",
                "icon_url": assets["confetti"],
            }
        )
    if current_island_index > 0:
        entries.append(
            {
                "message": f"Entered {VOYAGE_ISLANDS[current_island_index]['name']}",
                "tone": "travel",
                "icon_url": assets["compass"],
            }
        )
        entries.append(
            {
                "message": f"Left {VOYAGE_ISLANDS[current_island_index - 1]['name']}",
                "tone": "travel",
                "icon_url": assets["captain_log"],
            }
        )
    if current_island_index >= 6:
        entries.append(
            {
                "message": "Entered the New World",
                "tone": "travel",
                "icon_url": assets["compass"],
            }
        )
    elif current_island_index >= 1:
        entries.append(
            {
                "message": "Entered the Grand Line",
                "tone": "travel",
                "icon_url": assets["compass"],
            }
        )
    if current_island_index >= 10:
        entries.append(
            {
                "message": "Left Wano",
                "tone": "travel",
                "icon_url": assets["captain_log"],
            }
        )
    if next_island:
        entries.append(
            {
                "message": f"Charted course for {next_island['name']}",
                "tone": "planned",
                "icon_url": assets["vivre"],
            }
        )
    return entries[:5]


def build_voyage_state(training_status: dict[str, Any]) -> dict[str, Any]:
    progress_percent = float(training_status.get("progress_percent") or 0.0)
    island_total = len(VOYAGE_ISLANDS)
    boss_total = len(VOYAGE_BOSSES)

    island_index = min(island_total - 1, int((progress_percent / 100.0) * island_total)) if island_total else 0
    if progress_percent >= 100.0 and island_total:
        island_index = island_total - 1
    defeated_boss_count = min(boss_total, int((progress_percent / 100.0) * boss_total))

    current_island = build_voyage_location(VOYAGE_ISLANDS[island_index], "current")
    next_island = build_voyage_location(VOYAGE_ISLANDS[island_index + 1], "next") if island_index + 1 < island_total else None
    next_boss = build_voyage_boss(VOYAGE_BOSSES[defeated_boss_count], "next") if defeated_boss_count < boss_total else None
    defeated_bosses = [build_voyage_boss(boss, "defeated") for boss in VOYAGE_BOSSES[:defeated_boss_count]]
    bosses_by_island: dict[str, list[dict[str, str]]] = {}
    for boss_index, boss in enumerate(VOYAGE_BOSSES):
        if boss_index < defeated_boss_count:
            status = "completed"
        elif boss_index == defeated_boss_count:
            status = "next"
        else:
            status = "planned"
        island_key = boss.get("island_key", "")
        bosses_by_island.setdefault(island_key, []).append(build_voyage_boss(boss, status))

    route_nodes = []
    for index, island in enumerate(VOYAGE_ISLANDS):
        if index < island_index:
            status = "completed"
        elif index == island_index:
            status = "current"
        elif index == island_index + 1:
            status = "next"
        elif index == island_total - 1:
            status = "finish"
        else:
            status = "planned"
        route_nodes.append(
            {
                "key": island.get("key", f"island-{index}"),
                "name": island.get("name", "Planned"),
                "short_name": island.get("short_name", island.get("name", "Planned")),
                "status": status,
                "map_x": int(island.get("map_x", 0) or 0),
                "map_y": int(island.get("map_y", 0) or 0),
                "sprite_url": safe_voyage_asset_url(island.get("sprite"), "islands/island_east_blue.png"),
                "marker_url": safe_voyage_asset_url(VOYAGE_ROUTE_MARKERS.get(status), "routes/route_checkpoint_marker.png"),
                "bosses": bosses_by_island.get(island.get("key", ""), []),
            }
        )

    recent_log = build_voyage_log_entries(
        current_island_index=island_index,
        defeated_boss_count=defeated_boss_count,
        next_island=next_island,
    )
    assets = build_voyage_assets()

    if next_boss is None and defeated_bosses:
        recent_log.insert(
            0,
            {
                "message": f"Defeated {defeated_bosses[-1]['name']}",
                "tone": "victory",
                "icon_url": assets["confetti"],
            }
        )

    stage_label = current_island["stage"]
    route_progress = f"{current_island['name']} to {next_island['name']}" if next_island else "Final approach to Laugh Tale"
    boss_summary = (
        f"{defeated_boss_count} of {boss_total} bosses defeated"
        if boss_total
        else "Boss progression not started"
    )
    route_polyline = " ".join(f"{node['map_x']},{node['map_y']}" for node in route_nodes)
    ship_position = {
        "x": int(current_island.get("map_x", 0) or 0),
        "y": int(current_island.get("map_y", 0) or 0),
    }

    return {
        "source_label": "Estimated from verified dossier progress",
        "learning_label": "In progress" if progress_percent < 100.0 else "Complete",
        "learning_stage": str(training_status.get("training_stage") or "planned"),
        "stage": stage_label,
        "sea_label": stage_label,
        "route_progress": route_progress,
        "boss_summary": boss_summary,
        "current_island": current_island,
        "next_island": next_island,
        "next_boss": next_boss,
        "progress_percent": progress_percent,
        "defeated_boss_count": defeated_boss_count,
        "boss_total": boss_total,
        "defeated_bosses": defeated_bosses,
        "defeated_boss_names": [boss["name"] for boss in defeated_bosses],
        "recent_log": recent_log,
        "route_nodes": route_nodes,
        "route_polyline": route_polyline,
        "ship_position": ship_position,
        "assets": assets,
        "celebration_state": "victory" if recent_log and recent_log[0]["tone"] == "victory" else "voyage",
    }


def ensure_voyage_state(training_status: dict[str, Any] | None, voyage_state: dict[str, Any] | None = None) -> dict[str, Any]:
    training_status = dict(training_status or {})
    base_voyage = build_voyage_state(training_status)
    candidate = voyage_state if isinstance(voyage_state, dict) else training_status.get("voyage")
    if not isinstance(candidate, dict):
        return base_voyage

    merged = dict(base_voyage)
    for key in ("source_label", "learning_label", "learning_stage", "stage", "sea_label", "route_progress", "boss_summary", "progress_percent", "defeated_boss_count", "boss_total", "celebration_state", "route_polyline"):
        if candidate.get(key) not in (None, ""):
            merged[key] = candidate[key]
    for key in ("current_island", "next_island", "next_boss", "ship_position"):
        if isinstance(candidate.get(key), dict):
            merged[key] = {**merged.get(key, {}), **candidate[key]}
    if isinstance(candidate.get("assets"), dict):
        merged["assets"] = {**merged["assets"], **{k: v for k, v in candidate["assets"].items() if v}}
    if isinstance(candidate.get("recent_log"), list) and candidate["recent_log"]:
        merged["recent_log"] = candidate["recent_log"]
    if isinstance(candidate.get("route_nodes"), list) and candidate["route_nodes"]:
        merged["route_nodes"] = candidate["route_nodes"]
    if isinstance(candidate.get("defeated_bosses"), list):
        merged["defeated_bosses"] = candidate["defeated_bosses"]
    if isinstance(candidate.get("defeated_boss_names"), list):
        merged["defeated_boss_names"] = candidate["defeated_boss_names"]
    return merged


def current_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def seconds_since(timestamp: str) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = time.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(time.time() - time.mktime(parsed), 0.0)


def summarize_activity_request(request_text: str) -> str:
    text = re.sub(r"\s+", " ", request_text or "").strip()
    if len(text) <= 84:
        return text
    return text[:81].rstrip() + "..."


def note_miru_run_started(selected_mode: str, request_text: str) -> None:
    with MIRU_ACTIVITY_LOCK:
        MIRU_ACTIVITY_STATE["active_runs"] += 1
        MIRU_ACTIVITY_STATE["last_started_at"] = current_timestamp()
        MIRU_ACTIVITY_STATE["last_mode"] = selected_mode
        MIRU_ACTIVITY_STATE["last_request_text"] = summarize_activity_request(request_text)
        MIRU_ACTIVITY_STATE["last_error"] = ""


def note_miru_run_finished(selected_mode: str, request_text: str, ok: bool, message: str) -> None:
    with MIRU_ACTIVITY_LOCK:
        MIRU_ACTIVITY_STATE["active_runs"] = max(int(MIRU_ACTIVITY_STATE["active_runs"]) - 1, 0)
        MIRU_ACTIVITY_STATE["last_finished_at"] = current_timestamp()
        MIRU_ACTIVITY_STATE["last_mode"] = selected_mode
        MIRU_ACTIVITY_STATE["last_request_text"] = summarize_activity_request(request_text)
        MIRU_ACTIVITY_STATE["last_error"] = "" if ok else summarize_activity_request(message)


def build_activity_states(current_key: str) -> list[dict[str, Any]]:
    return [{**item, "active": item["key"] == current_key} for item in DEV_ACTIVITY_BLUEPRINT]


def build_miru_activity(training_status: dict[str, Any]) -> dict[str, Any]:
    with MIRU_ACTIVITY_LOCK:
        snapshot = dict(MIRU_ACTIVITY_STATE)

    current_key = "sleeping"
    detail = "Miru is waiting for the next question to arrive."

    if snapshot["active_runs"] > 0:
        current_key = "gathering_crew"
        detail = (
            f"Live request: {snapshot['last_request_text']}"
            if snapshot.get("last_request_text")
            else "Miru is answering a live request right now."
        )
    else:
        recent_error_age = seconds_since(str(snapshot.get("last_finished_at") or ""))
        if snapshot.get("last_error") and recent_error_age is not None and recent_error_age <= ACTIVITY_RECENT_WINDOW_SECONDS:
            current_key = "storm_warning"
            detail = str(snapshot["last_error"])
        elif training_status["dossiers_created"] > 0 and training_status["remaining_gaps"] > 0:
            current_key = "setting_sail"
            detail = training_status["remaining_summary"]

    current = next((item for item in DEV_ACTIVITY_BLUEPRINT if item["key"] == current_key), DEV_ACTIVITY_BLUEPRINT[0])
    return {
        "key": current_key,
        "title": current["title"],
        "description": current["description"],
        "detail": detail,
        "visual": current["visual"],
        "updated_at": snapshot.get("last_finished_at") or snapshot.get("last_started_at") or current_timestamp(),
        "active_runs": int(snapshot.get("active_runs") or 0),
    }


def format_bytes(value: int) -> str:
    size = float(max(int(value), 0))
    units = ("B", "KB", "MB", "GB", "TB")
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    return f"{size:.1f} {units[unit_index]}"


def sample_cpu_usage() -> float | None:
    if psutil is not None:
        try:
            return round(float(psutil.cpu_percent(interval=0.05)), 1)
        except Exception:
            return None
    if hasattr(os, "getloadavg") and os.name != "nt":
        try:
            load = os.getloadavg()[0]
            cpu_count = max(os.cpu_count() or 1, 1)
            return round(min(max((load / cpu_count) * 100, 0.0), 100.0), 1)
        except OSError:
            return None
    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[math]::Round((Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples[0].CookedValue, 1)",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and result.stdout.strip():
            try:
                return round(float(result.stdout.strip().splitlines()[-1]), 1)
            except ValueError:
                return None
    return None


def sample_memory_usage() -> dict[str, float | int] | None:
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            return {"percent": float(memory.percent), "used": int(memory.used), "total": int(memory.total)}
        except Exception:
            return None
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        used = int(status.ullTotalPhys - status.ullAvailPhys)
        return {"percent": float(status.dwMemoryLoad), "used": used, "total": int(status.ullTotalPhys)}
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    total = page_size * total_pages
    used = total - (page_size * available_pages)
    percent = round((used / total) * 100, 1) if total else 0.0
    return {"percent": percent, "used": used, "total": total}


def sample_gpu_usage() -> dict[str, float | int] | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    first_line = result.stdout.strip().splitlines()[0]
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 3:
        return None
    try:
        percent = float(parts[0])
        memory_used_mb = int(float(parts[1]))
        memory_total_mb = int(float(parts[2]))
    except ValueError:
        return None
    return {"percent": percent, "memory_used_mb": memory_used_mb, "memory_total_mb": memory_total_mb}


def build_resource_metrics() -> list[dict[str, Any]]:
    cpu_percent = sample_cpu_usage()
    memory = sample_memory_usage()
    gpu = sample_gpu_usage()
    disk_total, disk_used, disk_free = shutil.disk_usage(str(PROJECT_ROOT.anchor or PROJECT_ROOT))
    return [
        {
            "key": "cpu",
            "label": "CPU",
            "value": f"{cpu_percent:.1f}%" if cpu_percent is not None else "Unavailable",
            "detail": "Current processor use." if cpu_percent is not None else "CPU usage is unavailable on this machine.",
            "percent": cpu_percent if cpu_percent is not None else 0.0,
            "available": cpu_percent is not None,
        },
        {
            "key": "memory",
            "label": "Memory",
            "value": f"{format_bytes(int(memory['used']))} / {format_bytes(int(memory['total']))}" if memory else "Unavailable",
            "detail": f"{float(memory['percent']):.1f}% in use." if memory else "Memory usage is unavailable on this machine.",
            "percent": float(memory["percent"]) if memory else 0.0,
            "available": memory is not None,
        },
        {
            "key": "gpu",
            "label": "GPU",
            "value": f"{float(gpu['percent']):.1f}%" if gpu else "Unavailable",
            "detail": f"{int(gpu['memory_used_mb'])} MB of {int(gpu['memory_total_mb'])} MB used." if gpu else "GPU stats are unavailable on this machine.",
            "percent": float(gpu["percent"]) if gpu else 0.0,
            "available": gpu is not None,
        },
        {
            "key": "storage",
            "label": "Storage",
            "value": f"{format_bytes(disk_free)} free",
            "detail": f"{format_bytes(disk_used)} used of {format_bytes(disk_total)}.",
            "percent": round((disk_used / disk_total) * 100, 1) if disk_total else 0.0,
            "available": True,
        },
    ]


def build_route_url(path: str) -> str:
    route_path = path if path.startswith("/") else f"/{path}"
    return f"{request.url_root.rstrip('/')}" + route_path


def build_companion_url(port: int, path: str = "/") -> str:
    route_path = path if path.startswith("/") else f"/{path}"
    host = request.host.split(":", 1)[0]
    return f"{request.scheme}://{host}:{port}{route_path}"


def inspect_local_http_route(url: str) -> dict[str, Any]:
    try:
        with closing(urlopen(url, timeout=1.0)) as response:
            return {"reachable": True, "status_code": int(response.getcode() or 200), "detail": f"HTTP {int(response.getcode() or 200)}"}
    except HTTPError as exc:
        return {"reachable": True, "status_code": int(exc.code), "detail": f"HTTP {int(exc.code)}"}
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"reachable": False, "status_code": 0, "detail": f"{exc.__class__.__name__}: {reason}"}


def build_issue_card(label: str, issues: list[str], *, ok_detail: str, warn_detail: str) -> dict[str, Any]:
    if issues:
        return {"label": label, "status": issues[0], "tone": "warn", "detail": warn_detail, "items": issues}
    return {"label": label, "status": "No issues detected", "tone": "good", "detail": ok_detail, "items": []}


def build_learning_engine_activity(
    learning_status: dict[str, Any],
    fallback_activity: dict[str, Any],
) -> dict[str, Any]:
    worker_status = dict(learning_status.get("worker_status") or {})
    worker_state = str(worker_status.get("status") or "").strip().lower()
    current_state = str(learning_status.get("current_state") or "").lower()
    current_source_id = str(learning_status.get("current_source_id") or "").strip()
    current_image_task = str(learning_status.get("current_image_task") or "").strip()
    queue_length = int(learning_status.get("queue_length") or 0)
    processed_count = int(learning_status.get("processed_count") or 0)
    dossier_count = int(learning_status.get("dossier_count") or 0)
    task_label = str(learning_status.get("current_task_label") or "").strip()
    last_error = str(learning_status.get("last_error") or "").strip()

    if worker_state == "stale":
        return {
            "key": "storm_warning",
            "title": "Worker Stale",
            "description": "The learning worker stopped reporting fresh heartbeats.",
            "detail": str(worker_status.get("detail") or "Last heartbeat is stale."),
            "visual": "storm",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": 0,
        }

    if worker_state == "no_heartbeat":
        return {
            "key": "sleeping",
            "title": "No Heartbeat Yet",
            "description": "The learning worker has not reported a heartbeat yet.",
            "detail": str(worker_status.get("detail") or "Start the worker to begin background learning."),
            "visual": "sleeping",
            "updated_at": fallback_activity["updated_at"],
            "active_runs": 0,
        }

    if current_state in {"processing", "running", "starting"} or worker_state == "running" or int(learning_status.get("running_count") or 0) > 0:
        return {
            "key": "gathering_crew",
            "title": "Gathering the Crew",
            "description": "Miru is actively running a learning task.",
            "detail": (
                f"{current_image_task} via {current_source_id}"
                if current_image_task and current_source_id
                else (
                    f"{task_label} via {current_source_id}"
                    if current_source_id and task_label
                    else (current_image_task or task_label or "Miru is processing a queued learning task.")
                )
            ),
            "visual": "crew",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": int(learning_status.get("running_count") or 1),
        }

    if last_error:
        return {
            "key": "storm_warning",
            "title": "Storm Warning",
            "description": "The learning engine hit a recent problem.",
            "detail": last_error,
            "visual": "storm",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": 0,
        }

    if worker_state == "idle":
        return {
            "key": "sleeping",
            "title": "Idle",
            "description": "The learning worker is alive and waiting for the next task.",
            "detail": str(worker_status.get("detail") or "Worker heartbeat is fresh."),
            "visual": "sleeping",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": 0,
        }

    if worker_state == "stopped":
        return {
            "key": "sleeping",
            "title": "Stopped",
            "description": "The learning worker is not currently running.",
            "detail": str(worker_status.get("detail") or "Start the worker to resume background learning."),
            "visual": "sleeping",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": 0,
        }

    if queue_length > 0 or processed_count > 0 or dossier_count > 0:
        detail = (
            f"Queued: {format_count(queue_length)} | Processed: {format_count(processed_count)} | "
            f"Learning dossiers: {format_count(dossier_count)}"
        )
        return {
            "key": "setting_sail",
            "title": "Setting Sail",
            "description": "Miru's learning engine is running independently in the sidecar boundary.",
            "detail": detail,
            "visual": "sailing",
            "updated_at": learning_status.get("last_heartbeat") or fallback_activity["updated_at"],
            "active_runs": 0,
        }

    return fallback_activity


def build_issue_detection(
    training_status: dict[str, Any],
    project_status: dict[str, Any],
    learning_status: dict[str, Any],
) -> dict[str, Any]:
    miru_issues = list(runtime_issue_messages())
    if not training_status["catalog_status"].get("usable"):
        miru_issues.append("Fallback catalog missing")
    dossier_status = training_status["dossier_status"]
    if not dossier_status.get("exists"):
        miru_issues.append("Training DB not initialized")
    elif dossier_status.get("error"):
        miru_issues.append(str(dossier_status["error"]))
    worker_status = dict(learning_status.get("worker_status") or {})
    worker_state = str(worker_status.get("status") or "").strip().lower()
    if worker_state == "stale":
        miru_issues.append("Learning worker heartbeat is stale")
    elif worker_state in {"stopped", "no_heartbeat"} and int(learning_status.get("queue_length") or 0) > 0:
        miru_issues.append("Learning worker is not reporting a fresh heartbeat")
    if learning_status.get("status_db_exists") and learning_status.get("last_error"):
        miru_issues.append("Learning engine reported an error")

    with MIRU_ACTIVITY_LOCK:
        recent_error = str(MIRU_ACTIVITY_STATE.get("last_error") or "")
        recent_finished = str(MIRU_ACTIVITY_STATE.get("last_finished_at") or "")
    error_age = seconds_since(recent_finished)
    if recent_error and error_age is not None and error_age <= ACTIVITY_RECENT_WINDOW_SECONDS:
        miru_issues.append("Latest Ask run needs attention")

    project_issues = []
    if not project_status.get("reachable"):
        project_issues.append("Project Miru route unreachable")

    return {
        "miru_ai": build_issue_card(
            "Miru AI",
            miru_issues,
            ok_detail="Ask flow, fallback catalog, and training DB all look ready.",
            warn_detail="Miru AI needs attention before relying on this testing surface.",
        ),
        "project_miru": build_issue_card(
            "Project Miru",
            project_issues,
            ok_detail=f"Project Miru answered on port {PROJECT_MIRU_DEV_PORT}.",
            warn_detail=f"Project Miru did not answer on port {PROJECT_MIRU_DEV_PORT}.",
        ),
    }


def build_learning_engine_metrics(learning_status: dict[str, Any]) -> list[dict[str, Any]]:
    """Build learning-engine-only metrics (queue, throughput, sidecar) with clear keys and labels."""
    return [
        {"key": "queue_queued_count", "label": "Queue: waiting", "value": format_count(learning_status.get("queue_length", 0)), "detail": "Learning tasks waiting in the queue."},
        {"key": "queue_running_count", "label": "Queue: running", "value": format_count(learning_status.get("running_count", 0)), "detail": "Tasks currently running."},
        {"key": "queue_failed_count", "label": "Queue: failed", "value": format_count(learning_status.get("failed_count", 0)), "detail": "Tasks that permanently failed."},
        {"key": "queue_completed_count", "label": "Queue: completed", "value": format_count(learning_status.get("completed_count", 0)), "detail": "Tasks completed (all time)."},
        {"key": "queue_backlog", "label": "Queue backlog", "value": format_count(learning_status.get("queue_backlog", 0)), "detail": "Queued learning work still waiting to run."},
        {"key": "parallel_workers", "label": "Parallel validations", "value": format_count(learning_status.get("max_parallel_validations", 1)), "detail": "Configured safe concurrency limit for validation work."},
        {"key": "engine_processed_count", "label": "Tasks run", "value": format_count(learning_status.get("processed_count", 0)), "detail": "Total task attempts processed."},
        {"key": "engine_success_count", "label": "Succeeded", "value": format_count(learning_status.get("success_count", 0)), "detail": "Tasks completed without error."},
        {"key": "engine_error_count", "label": "Failed", "value": format_count(learning_status.get("error_count", 0)), "detail": "Task attempts that ended in error."},
        {"key": "validated_cards_total", "label": "Validated cards", "value": format_count(learning_status.get("validated_card_count", 0)), "detail": "Cards that completed the verified field validation step."},
        {"key": "cards_learned_per_hour", "label": "Cards learned/hr", "value": format_count(learning_status.get("cards_learned_per_hour", 0)), "detail": "Verified cards completed during the last rolling hour."},
        {"key": "validation_success_rate", "label": "Validation success", "value": f"{float(learning_status.get('validation_success_rate', 0.0)):.1f}%", "detail": "Share of validation tasks that finished successfully."},
        {"key": "average_validation_seconds", "label": "Avg validation time", "value": f"{float(learning_status.get('average_validation_seconds', 0.0)):.2f}s", "detail": "Average runtime for completed validation tasks."},
        {"key": "engine_source_success_count", "label": "Source: success", "value": format_count(learning_status.get("source_success_count", 0)), "detail": "Source-backed tasks completed successfully."},
        {"key": "engine_source_error_count", "label": "Source: errors", "value": format_count(learning_status.get("source_error_count", 0)), "detail": "Source-backed tasks that ended in error."},
        {"key": "engine_image_success_count", "label": "Images: success", "value": format_count(learning_status.get("image_success_count", 0)), "detail": "Image ingestion tasks completed successfully."},
        {"key": "engine_image_error_count", "label": "Images: errors", "value": format_count(learning_status.get("image_error_count", 0)), "detail": "Image ingestion tasks that ended in error."},
        {"key": "discovery_candidates", "label": "Source candidates", "value": format_count(learning_status.get("discovery_candidate_count", 0)), "detail": "Potential source sites discovered locally."},
        {"key": "discovery_pending_review", "label": "Source review queue", "value": format_count(learning_status.get("discovery_pending_review_count", 0)), "detail": "Discovered source candidates still waiting for operator review."},
        {"key": "sidecar_dossiers_count", "label": "Sidecar dossiers", "value": format_count(learning_status.get("dossier_count", 0)), "detail": "Profiles in the learning engine store."},
        {"key": "sidecar_images_tracked_count", "label": "Sidecar images: tracked", "value": format_count(learning_status.get("images_tracked", 0)), "detail": "Image registry entries tracked."},
        {"key": "sidecar_images_verified_count", "label": "Sidecar images: verified", "value": format_count(learning_status.get("images_verified", 0)), "detail": "Image registry entries marked verified."},
        {"key": "sidecar_images_missing_count", "label": "Sidecar images: missing", "value": format_count(learning_status.get("images_missing", 0)), "detail": "Catalog cards missing a sidecar image entry."},
        {"key": "last_image_update", "label": "Last image update", "value": learning_status.get("last_image_update") or "—", "detail": "Most recent image ingestion update timestamp."},
    ]


def build_learning_metrics(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
) -> list[dict[str, Any]]:
    total_cards = int(training_status["total_cards"] or 0)
    dossier_status = training_status["dossier_status"]
    variants_tracked = int(training_status["catalog_status"].get("variants") or 0)
    image_coverage_cards = int(dossier_status.get("image_coverage_cards") or 0)
    image_coverage_percent = safe_percent(image_coverage_cards, total_cards)
    images_tracked = int(learning_status.get("images_tracked") or 0)
    images_verified = int(learning_status.get("images_verified") or 0)
    images_missing = int(learning_status.get("images_missing") or 0)
    metrics = [
        {"key": "queue_length", "label": "Queued tasks", "value": format_count(learning_status["queue_length"]), "detail": "Learning tasks waiting in the SQLite queue."},
        {"key": "processed_count", "label": "Processed tasks", "value": format_count(learning_status["processed_count"]), "detail": "Total task attempts processed by the learning engine."},
        {"key": "success_count", "label": "Successful tasks", "value": format_count(learning_status["success_count"]), "detail": "Learning tasks completed without error."},
        {"key": "error_count", "label": "Errored tasks", "value": format_count(learning_status["error_count"]), "detail": "Learning task attempts that ended in an error."},
        {"key": "source_success_count", "label": "Source fetch success", "value": format_count(learning_status["source_success_count"]), "detail": "Source-backed tasks completed successfully."},
        {"key": "source_error_count", "label": "Source fetch errors", "value": format_count(learning_status["source_error_count"]), "detail": "Source-backed tasks that ended in error."},
        {"key": "image_success_count", "label": "Image fetch success", "value": format_count(learning_status.get("image_success_count", 0)), "detail": "Image ingestion tasks completed successfully."},
        {"key": "image_error_count", "label": "Image fetch errors", "value": format_count(learning_status.get("image_error_count", 0)), "detail": "Image ingestion tasks that ended in error."},
        {"key": "images_tracked", "label": "Images tracked", "value": format_count(images_tracked), "detail": "Image registry entries tracked by the learning engine."},
        {"key": "images_verified", "label": "Images verified", "value": format_count(images_verified), "detail": "Image registry entries marked as verified."},
        {"key": "images_missing", "label": "Images missing", "value": format_count(images_missing), "detail": "Catalog cards missing an image registry entry."},
        {"key": "last_image_update", "label": "Last image update", "value": learning_status.get("last_image_update") or "—", "detail": "Most recent image ingestion update timestamp."},
        {"key": "learning_dossiers", "label": "Learning dossiers", "value": format_count(learning_status["dossier_count"]), "detail": "Bootstrap dossier rows managed by the learning engine."},
        {"key": "total_cards", "label": "Total cards", "value": format_count(total_cards), "detail": "Catalog identities loaded locally."},
        {"key": "dossiers_created", "label": "Dossiers created", "value": format_count(training_status["dossiers_created"]), "detail": "Structured card profiles in the training store."},
        {"key": "verified_dossiers", "label": "Verified dossiers", "value": format_count(training_status["verified_dossiers"]), "detail": "Cards that currently read as verified."},
        {"key": "remaining_gaps", "label": "Remaining gaps", "value": format_count(training_status["remaining_gaps"]), "detail": "Cards still missing verified dossier coverage."},
        {"key": "variants_tracked", "label": "Variants tracked", "value": format_count(variants_tracked), "detail": "Variant rows currently indexed in the local catalog."},
        {"key": "image_coverage", "label": "Image coverage", "value": format_count(image_coverage_cards), "detail": f"{image_coverage_percent:.1f}% of catalog cards have a stored image identity."},
    ]
    return metrics


def build_image_coverage_by_set(
    *,
    catalog_db_path: Path = FALLBACK_CATALOG_DB_PATH,
    dossier_db_path: Path = LEARNING_DOSSIER_DB_PATH,
) -> list[dict[str, Any]]:
    if not Path(catalog_db_path).is_file() or not Path(dossier_db_path).is_file():
        return []

    query = """
        SELECT
            c.set_code AS set_code,
            COUNT(DISTINCT c.canonical_code) AS total_cards,
            COUNT(DISTINCT i.card_code) AS images_tracked,
            COUNT(DISTINCT CASE WHEN i.verification_state = 'verified' THEN i.card_code END) AS images_verified
        FROM catalog.cards c
        LEFT JOIN learning_dossier_images i
            ON i.card_code = c.canonical_code
        WHERE trim(coalesce(c.set_code, '')) != ''
        GROUP BY c.set_code
        ORDER BY c.set_code ASC
    """

    coverage: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(dossier_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("ATTACH DATABASE ? AS catalog", (str(catalog_db_path),))
            for row in conn.execute(query):
                total_cards = int(row["total_cards"] or 0)
                images_tracked = int(row["images_tracked"] or 0)
                images_verified = int(row["images_verified"] or 0)
                images_missing = max(total_cards - images_tracked, 0)
                coverage_percent = (images_tracked / total_cards * 100.0) if total_cards else 0.0
                if images_tracked == 0:
                    milestone_stage = 0
                    milestone_label = "not_started"
                elif images_verified == 0:
                    milestone_stage = 1
                    milestone_label = "discovered"
                elif images_verified < total_cards:
                    milestone_stage = 2
                    milestone_label = "tracked"
                else:
                    milestone_stage = 3
                    milestone_label = "verified"
                coverage.append(
                    {
                        "set_code": str(row["set_code"] or ""),
                        "total_cards": total_cards,
                        "images_tracked": images_tracked,
                        "images_verified": images_verified,
                        "images_missing": images_missing,
                        "coverage_percent": round(coverage_percent, 2),
                        "milestone_stage": milestone_stage,
                        "milestone_label": milestone_label,
                    }
                )
    except sqlite3.OperationalError:
        return []

    return coverage


def _load_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _humanize_image_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for prefix in ("phase3-", "phase2-", "phase1-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.replace("_", " ").replace("-", " ").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _selection_scope_label(value: Any) -> str:
    mapping = {
        "card_default": "Card default",
        "print_default": "Print default",
        "gallery_preferred": "Gallery preferred",
    }
    key = str(value or "").strip()
    return mapping.get(key, key.replace("_", " ").title() or "Selection")


def _required_image_margin(summary: dict[str, Any]) -> float:
    relationship = str(summary.get("runner_up_duplicate_relationship") or "").strip()
    if relationship == "same-family-cautious":
        return 8.0
    if relationship in {"same-art-different-crop-or-treatment", "exact-duplicate"}:
        return 10.0
    return 6.0


def _match_runtime_variant(
    variants: list[dict[str, Any]],
    *,
    print_id: str,
    variant_key: str,
) -> dict[str, Any] | None:
    normalized_variant = str(variant_key or "").strip().lower()
    for row in variants:
        if str(row.get("print_id") or "").strip() == print_id and print_id:
            return row
    if not normalized_variant:
        for row in variants:
            if str(row.get("variant_key") or "").strip().lower() in {"", "base"}:
                return row
    for row in variants:
        if str(row.get("variant_key") or "").strip().lower() == normalized_variant:
            return row
    return variants[0] if variants else None


def _build_image_runtime_entry(
    *,
    card_code: str,
    card_name: str,
    selection_scope: str,
    print_id: str,
    variant_key: str,
    selected_identifier: str,
    source_label: str,
    winner_verified: bool,
    display_policy: str,
    origin_language: str,
    english_print_exists: bool,
    display_provisional: bool,
    runtime_synced: bool,
    runtime_state: str,
    runtime_label: str,
    status_tone: str,
    summary: str,
    change_summary: str,
    synced_by_verified_logic: bool,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "card_code": card_code,
        "card_name": card_name,
        "selection_scope": selection_scope,
        "selection_scope_label": _selection_scope_label(selection_scope),
        "print_id": print_id,
        "variant_key": variant_key,
        "selected_identifier": selected_identifier,
        "source_label": source_label,
        "winner_verified": bool(winner_verified),
        "display_policy": display_policy or "english-first",
        "origin_language": origin_language or "en",
        "english_print_exists": bool(english_print_exists),
        "display_provisional": bool(display_provisional),
        "runtime_synced": bool(runtime_synced),
        "synced_by_verified_logic": bool(synced_by_verified_logic),
        "runtime_state": runtime_state,
        "runtime_label": runtime_label,
        "status_tone": status_tone,
        "summary": summary,
        "change_summary": change_summary,
        "updated_at": updated_at,
    }


def build_image_runtime_status(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
    dossier_db_path: Path = LEARNING_DOSSIER_DB_PATH,
    limit: int = 8,
) -> list[dict[str, Any]]:
    project_path = Path(project_db_path)
    dossier_path = Path(dossier_db_path)
    if not project_path.is_file() or not dossier_path.is_file():
        return []

    limit = max(1, min(int(limit or 8), 16))
    selections: list[dict[str, Any]] = []
    blocked_candidates: list[dict[str, Any]] = []
    cards_by_code: dict[str, dict[str, Any]] = {}
    variants_by_card_id: dict[int, list[dict[str, Any]]] = {}

    project_conn: sqlite3.Connection | None = None
    dossier_conn: sqlite3.Connection | None = None
    try:
        project_conn = sqlite3.connect(project_path)
        project_conn.row_factory = sqlite3.Row
        card_rows = project_conn.execute("SELECT id, canonical_code, card_name FROM cards").fetchall()
        variant_rows = project_conn.execute(
            """
            SELECT id, card_id, variant_key, print_id, image_path, image_url, sync_status, unresolved_reason, source_attribution_json
            FROM card_variants
            """
        ).fetchall()
        cards_by_code = {
            str(row["canonical_code"] or "").strip().upper(): {key: row[key] for key in row.keys()}
            for row in card_rows
        }
        for row in variant_rows:
            item = {key: row[key] for key in row.keys()}
            variants_by_card_id.setdefault(int(item.get("card_id") or 0), []).append(item)

        dossier_conn = sqlite3.connect(dossier_path)
        dossier_conn.row_factory = sqlite3.Row
        selections = [
            {key: row[key] for key in row.keys()}
            for row in dossier_conn.execute(
                """
                SELECT
                    s.card_code,
                    s.print_id,
                    s.variant_key,
                    s.selection_scope,
                    s.image_candidate_id,
                    s.selection_confidence,
                    s.selection_reason,
                    s.origin_language,
                    s.english_print_exists,
                    s.display_policy,
                    s.provisional_language_display,
                    s.selected_at,
                    s.reviewed_at,
                    s.comparison_summary_json,
                    i.source_id,
                    i.source_reference,
                    i.verification_state,
                    i.local_path,
                    i.source_url
                FROM learning_image_selections s
                LEFT JOIN learning_dossier_images i ON i.id = s.image_candidate_id
                ORDER BY COALESCE(s.reviewed_at, s.selected_at) DESC, s.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        blocked_candidates = [
            {key: row[key] for key in row.keys()}
            for row in dossier_conn.execute(
                """
                SELECT
                    i.card_code,
                    i.print_id,
                    i.variant_key,
                    i.source_id,
                    i.source_reference,
                    i.verification_state,
                    i.origin_language,
                    i.english_print_exists,
                    i.display_policy,
                    i.provisional_language_display,
                    i.last_reviewed_at
                FROM learning_dossier_images i
                WHERE i.verification_state = 'verified'
                  AND lower(coalesce(i.origin_language, 'en')) != 'en'
                  AND coalesce(i.english_print_exists, 1) = 1
                ORDER BY COALESCE(i.last_reviewed_at, '') DESC, i.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        if dossier_conn is not None:
            dossier_conn.close()
        if project_conn is not None:
            project_conn.close()

    results: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for item in selections:
        card_code = str(item.get("card_code") or "").strip().upper()
        print_id = str(item.get("print_id") or "").strip()
        selection_scope = str(item.get("selection_scope") or "").strip()
        variant_key = str(item.get("variant_key") or "").strip().lower()
        seen_keys.add((card_code, print_id, selection_scope))
        card_row = cards_by_code.get(card_code, {})
        card_name = str(card_row.get("card_name") or "").strip()
        runtime_variant = _match_runtime_variant(
            variants_by_card_id.get(int(card_row.get("id") or 0), []),
            print_id=print_id,
            variant_key=variant_key,
        )
        runtime_payload = _load_json_object((runtime_variant or {}).get("source_attribution_json") or "{}")
        mirrored_selection = _load_json_object(runtime_payload.get("miru_image_selection") or {})
        comparison_summary = _load_json_object(item.get("comparison_summary_json") or "{}")
        score_margin = float(comparison_summary.get("score_margin") or 0.0)
        runner_up_present = bool(int(comparison_summary.get("runner_up_candidate_id") or 0))
        stable_threshold = _required_image_margin(comparison_summary)
        stable_winner_note = ""
        if runner_up_present and score_margin < stable_threshold:
            stable_winner_note = (
                f"Stable winner kept. Challenger gain ({score_margin:.2f}) did not clear the {stable_threshold:.0f}-point threshold."
            )

        origin_language = str(item.get("origin_language") or mirrored_selection.get("origin_language") or "en").strip().lower() or "en"
        english_print_exists = bool(int(item.get("english_print_exists") or mirrored_selection.get("english_print_exists") or 0))
        display_policy = str(item.get("display_policy") or mirrored_selection.get("display_policy") or "english-first").strip() or "english-first"
        provisional_display = bool(
            int(item.get("provisional_language_display") or mirrored_selection.get("provisional_language_display") or 0)
        )
        winner_verified = str(item.get("verification_state") or "").strip().lower() == "verified"
        runtime_sync_status = str((runtime_variant or {}).get("sync_status") or "").strip()
        synced_by_verified_logic = bool(mirrored_selection) or runtime_sync_status.startswith("miru-image-")

        runtime_state = "selected_pending_runtime"
        runtime_label = "Selected"
        status_tone = "neutral"
        summary = f"{_selection_scope_label(selection_scope)} winner selected in the sidecar."
        if synced_by_verified_logic and provisional_display:
            runtime_state = "provisional_non_english"
            runtime_label = "Provisional non-English"
            status_tone = "warn"
            summary = "Verified translated-origin image is displayed provisionally because no English print exists yet."
        elif synced_by_verified_logic:
            runtime_state = "verified_standard"
            runtime_label = "Verified"
            status_tone = "good"
            summary = "Verified image-selection logic is driving the current runtime display."
        elif origin_language != "en" and english_print_exists:
            runtime_state = "blocked_english_preferred"
            runtime_label = "English kept"
            status_tone = "warn"
            summary = "English-first policy blocked a non-English image from replacing the current runtime display."
        elif stable_winner_note:
            runtime_state = "deferred_small_gain"
            runtime_label = "Stable winner kept"
            status_tone = "neutral"
            summary = "A challenger existed, but Miru kept the current winner because the improvement was too small."

        reason_label = _humanize_image_reason(item.get("selection_reason") or mirrored_selection.get("selection_reason") or "")
        change_summary = stable_winner_note or (f"Winner reason: {reason_label}." if reason_label else "")
        source_identifier = str(item.get("source_reference") or "").strip() or str(item.get("local_path") or "").strip() or str(item.get("source_url") or "").strip()
        source_label = f"{str(item.get('source_id') or '').strip() or 'unknown source'} · {source_identifier or 'selected image'}"
        results.append(
            _build_image_runtime_entry(
                card_code=card_code,
                card_name=card_name or "Unknown card",
                selection_scope=selection_scope,
                print_id=print_id,
                variant_key=variant_key,
                selected_identifier=source_identifier,
                source_label=source_label,
                winner_verified=winner_verified,
                display_policy=display_policy,
                origin_language=origin_language,
                english_print_exists=english_print_exists,
                display_provisional=provisional_display,
                runtime_synced=synced_by_verified_logic,
                runtime_state=runtime_state,
                runtime_label=runtime_label,
                status_tone=status_tone,
                summary=summary,
                change_summary=change_summary,
                synced_by_verified_logic=synced_by_verified_logic,
                updated_at=str(item.get("reviewed_at") or item.get("selected_at") or ""),
            )
        )

    if len(results) < limit:
        for item in blocked_candidates:
            card_code = str(item.get("card_code") or "").strip().upper()
            print_id = str(item.get("print_id") or "").strip()
            key = (card_code, print_id, "card_default")
            if key in seen_keys:
                continue
            card_name = str(cards_by_code.get(card_code, {}).get("card_name") or "Unknown card")
            source_identifier = str(item.get("source_reference") or "").strip() or "candidate"
            results.append(
                _build_image_runtime_entry(
                    card_code=card_code,
                    card_name=card_name,
                    selection_scope="card_default",
                    print_id=print_id,
                    variant_key=str(item.get("variant_key") or "").strip().lower(),
                    selected_identifier=source_identifier,
                    source_label=f"{str(item.get('source_id') or '').strip() or 'unknown source'} · {source_identifier}",
                    winner_verified=str(item.get("verification_state") or "").strip().lower() == "verified",
                    display_policy=str(item.get("display_policy") or "english-first"),
                    origin_language=str(item.get("origin_language") or "en").strip().lower() or "en",
                    english_print_exists=bool(int(item.get("english_print_exists") or 0)),
                    display_provisional=bool(int(item.get("provisional_language_display") or 0)),
                    runtime_synced=False,
                    runtime_state="blocked_english_preferred",
                    runtime_label="English kept",
                    status_tone="warn",
                    summary="English-first policy is deferring a verified non-English candidate because an English print is available.",
                    change_summary="Runtime sync stayed deferred under the English-first policy.",
                    synced_by_verified_logic=False,
                    updated_at=str(item.get("last_reviewed_at") or ""),
                )
            )
            if len(results) >= limit:
                break

    return results[:limit]


def load_limits_status() -> list[dict[str, Any]]:
    """Load limits data from data/miru_limits_status.json. Returns a list of provider entries; empty on missing/invalid file."""
    path = Path(LIMITS_STATUS_PATH)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict) and entry.get("provider")]


def build_pushover_runtime_state() -> dict[str, Any]:
    state = dict(inspect_pushover_env())
    state["env_path"] = PROJECT_ENV_LOAD.get("env_path")
    state["env_exists"] = bool(PROJECT_ENV_LOAD.get("exists"))
    state["env_parser"] = PROJECT_ENV_LOAD.get("parser")
    state["loaded_keys"] = list(PROJECT_ENV_LOAD.get("loaded_keys") or [])
    state["skipped_existing_keys"] = list(PROJECT_ENV_LOAD.get("skipped_existing_keys") or [])
    state["server_script_path"] = str(Path(__file__).resolve())
    state["project_root"] = str(PROJECT_ROOT)
    state["test_endpoint"] = build_route_url("/api/dev/test-pushover")
    return state


def build_legacy_dev_usage() -> dict[str, Any]:
    dev_status = build_dev_status()
    return {
        "ok": True,
        "updated_at": dev_status["updated_at"],
        "limits_status": dev_status.get("limits_status", []),
        "limits_by_provider": dev_status.get("limits_by_provider", {}),
        "pushover": dev_status.get("pushover", {}),
    }


def build_legacy_validation_insights() -> dict[str, Any]:
    dev_status = build_dev_status()
    return {
        "ok": True,
        "updated_at": dev_status["updated_at"],
        "validation_audit": dev_status.get("validation_audit", {}),
        "validation_audit_url_base": dev_status.get("validation_audit_url_base", ""),
    }


def build_dev_status(training_status: dict[str, Any] | None = None) -> dict[str, Any]:
    training_status = training_status or build_training_status()
    training_status["voyage"] = ensure_voyage_state(training_status)
    project_status = inspect_local_http_route(f"http://127.0.0.1:{PROJECT_MIRU_DEV_PORT}/")
    preflight = build_runtime_preflight_report(
        target="all",
        check_server_port_available=False,
        check_worker_lock_available=False,
    )
    learning_status = load_learning_engine_status(
        queue_db_path=LEARNING_QUEUE_DB_PATH,
        status_db_path=LEARNING_STATUS_DB_PATH,
        dossier_db_path=LEARNING_DOSSIER_DB_PATH,
        total_cards=int(training_status.get("total_cards") or 0),
    )
    activity = build_learning_engine_activity(learning_status, build_miru_activity(training_status))
    links = {
        "miru_ai": build_route_url("/"),
        "project_miru": build_companion_url(PROJECT_MIRU_DEV_PORT, "/"),
        "training": build_route_url("/training"),
        "status": build_route_url("/status"),
    }
    image_coverage_by_set = build_image_coverage_by_set(
        catalog_db_path=FALLBACK_CATALOG_DB_PATH,
        dossier_db_path=LEARNING_DOSSIER_DB_PATH,
    )
    training_progress = training_status.get("training_progress")
    limits_status = load_limits_status()
    limits_by_provider = {e["provider"]: e for e in limits_status if e.get("provider")}
    validation_audit = list_validation_audit_insights(project_db_path=FALLBACK_CATALOG_DB_PATH)
    learner = get_learner_status(PROJECT_ROOT)
    # learning_engine: include learner_mode so worktree can read learning_engine.learner_mode
    learning_engine_payload = {**learning_status, "learner_mode": learner["mode"]}
    return {
        "updated_at": current_timestamp(),
        "links": links,
        "activity": activity,
        "activity_states": build_activity_states(activity["key"]),
        "limits_status": limits_status,
        "limits_by_provider": limits_by_provider,
        "training": {
            "progress_percent": float(training_status["progress_percent"]),
            "summary": training_status["verified_summary"],
            "detail": training_status["remaining_summary"],
            "stage": training_status["training_stage"],
        },
        "training_progress": training_progress,
        "voyage": ensure_voyage_state(training_status, training_status.get("voyage")),
        "learning_metrics": build_learning_engine_metrics(learning_status),
        "validation_audit": validation_audit,
        "validation_audit_url_base": build_route_url("/api/dev/card-validation"),
        "image_coverage_by_set": image_coverage_by_set,
        "image_runtime_status": build_image_runtime_status(
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            dossier_db_path=LEARNING_DOSSIER_DB_PATH,
        ),
        "resource_metrics": build_resource_metrics(),
        "issues": build_issue_detection(training_status, project_status, learning_status),
        "preflight": {
            "ok": bool(preflight.get("ok")),
            "summary": dict(preflight.get("summary") or {}),
        },
        "catalog_status": training_status["catalog_status"],
        "dossier_status": training_status["dossier_status"],
        "learning_engine": learning_engine_payload,
        "pushover": build_pushover_runtime_state(),
        "project_miru": {
            "reachable": bool(project_status.get("reachable")),
            "status_code": int(project_status.get("status_code") or 0),
            "detail": project_status.get("detail") or "Unavailable",
            "url": links["project_miru"],
        },
        "learner": learner,
    }


def log_pushover_startup_status(logger: Any) -> None:
    global _PUSHOVER_STATUS_LOGGED
    with _PUSHOVER_STATUS_LOCK:
        if _PUSHOVER_STATUS_LOGGED:
            return
        message = build_pushover_status_message(
            env_load=PROJECT_ENV_LOAD,
            pushover=inspect_pushover_env(),
        )
        pushover = inspect_pushover_env()
        if pushover["enabled"] and pushover["configured"]:
            logger.info(message)
        else:
            logger.warning(message)
        _PUSHOVER_STATUS_LOGGED = True


def is_local_request() -> bool:
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr:
        return False
    return is_trusted_private_client(remote_addr)

def resolve_cli_mode(selected_mode: str) -> str:
    mapping = {
        "card lookup": "knowledge",
        "card analysis": "analysis",
        "variant & print analysis": "matching",
        "set & catalog knowledge": "knowledge",
        "knowledge training": "knowledge",
        "card knowledge": "knowledge",
        "matching": "matching",
        "codex prompt": "codex-prompt",
        "development": "development",
        "plan": "plan",
        "debug": "debug",
        "review": "review",
    }
    return mapping.get(selected_mode, selected_mode)


def normalize_request_text(request_text: str) -> str:
    return re.sub(r"\s+", " ", (request_text or "").strip().lower())


def is_explicit_development_request(request_text: str) -> bool:
    normalized = normalize_request_text(request_text)
    return any(token in normalized for token in EXPLICIT_DEVELOPMENT_HINTS)


def infer_knowledge_mode(request_text: str) -> str:
    normalized = normalize_request_text(request_text)
    has_card_reference = bool(CARD_REFERENCE_RE.search(request_text or ""))
    has_gameplay_hint = any(token in normalized for token in GAMEPLAY_HINTS)

    if any(token in normalized for token in TRAINING_HINTS):
        return "knowledge training"
    if any(token in normalized for token in SET_CATALOG_HINTS):
        return "set & catalog knowledge"
    if any(token in normalized for token in MATCHING_HINTS):
        return "variant & print analysis"
    if has_gameplay_hint or any(token in normalized for token in DIRECT_KNOWLEDGE_HINTS):
        return "card analysis" if ("effect" in normalized or "what does" in normalized or has_gameplay_hint) else "card lookup"
    if has_card_reference:
        return "card lookup"
    return "card lookup"


def resolve_effective_mode(selected_mode: str, request_text: str) -> str:
    if selected_mode not in {"development", "codex prompt"}:
        return selected_mode
    if is_explicit_development_request(request_text):
        return selected_mode
    return infer_knowledge_mode(request_text)


def collect_runtime_dependencies() -> list[dict[str, Any]]:
    catalog_status = ensure_fallback_catalog_status()
    knowledge_ready = KNOWLEDGE_CACHE_PATH.is_file() or catalog_status["usable"]
    return [
        {
            "label": "Helper script",
            "path": str(SCRIPT_PATH),
            "exists": SCRIPT_PATH.is_file(),
            "required": True,
            "detail": "Subprocess entrypoint for the Ask Miru flow.",
        },
        {
            "label": "One Piece knowledge cache",
            "path": str(KNOWLEDGE_CACHE_PATH),
            "exists": KNOWLEDGE_CACHE_PATH.is_file(),
            "required": not FALLBACK_CATALOG_DB_PATH.is_file(),
            "detail": "Primary structured knowledge cache used by tools/miru_ai_onepiece.py.",
        },
        {
            "label": "Fallback catalog database",
            "path": str(FALLBACK_CATALOG_DB_PATH),
            "exists": catalog_status["exists"],
            "required": False,
            "detail": (
                f"Secondary local catalog source. "
                f"Openable: {'yes' if catalog_status['openable'] else 'no'}. "
                f"Cards indexed: {catalog_status['cards']}. "
                f"Variants indexed: {catalog_status['variants']}."
                + (
                    f" Issue: {catalog_status['error']}"
                    if catalog_status["error"]
                    else ""
                )
            ),
            "openable": catalog_status["openable"],
            "usable": catalog_status["usable"],
            "cards": catalog_status["cards"],
            "variants": catalog_status["variants"],
            "sets": catalog_status["sets"],
            "error": catalog_status["error"],
        },
        {
            "label": "Ask template",
            "path": str(TEMPLATE_PATH),
            "exists": TEMPLATE_PATH.is_file(),
            "required": True,
            "detail": "Miru AI HTML template.",
        },
        {
            "label": "Miru AI stylesheet",
            "path": str(CSS_PATH),
            "exists": CSS_PATH.is_file(),
            "required": True,
            "detail": "Miru AI CSS asset.",
        },
        {
            "label": "Miru AI script",
            "path": str(JS_PATH),
            "exists": JS_PATH.is_file(),
            "required": True,
            "detail": "Miru AI browser behavior asset.",
        },
        {
            "label": "Ask runtime data",
            "path": f"{KNOWLEDGE_CACHE_PATH} or {FALLBACK_CATALOG_DB_PATH}",
            "exists": knowledge_ready,
            "required": True,
            "detail": "At least one local card-knowledge source must be available.",
        },
    ]


def runtime_issue_messages() -> list[str]:
    issues = []
    for dependency in collect_runtime_dependencies():
        if dependency["required"] and not dependency["exists"]:
            issues.append(f"{dependency['label']} is missing: {dependency['path']}")
    return issues


def startup_issues() -> list[str]:
    return runtime_issue_messages()


def validate_inputs(selected_mode: str, request_text: str, file_path: str) -> list[str]:
    errors = []

    if selected_mode not in ALL_MODE_LABELS:
        errors.append("Choose a valid mode before you run Miru AI.")
    if not SCRIPT_PATH.is_file():
        errors.append(f"Required helper script is missing: {SCRIPT_PATH}")
    if resolve_cli_mode(selected_mode) == "review":
        if not file_path.strip():
            errors.append("Review needs a readable file path.")
    elif not request_text.strip():
        errors.append("Request text is required for this mode.")

    return errors


def build_command(selected_mode: str, request_text: str, file_path: str) -> list[str]:
    cli_mode = resolve_cli_mode(selected_mode)
    command = [sys.executable, str(SCRIPT_PATH), cli_mode]

    if cli_mode == "review":
        command.append(file_path.strip())
    else:
        command.append(request_text.strip())

    return command


def build_command_preview(selected_mode: str, request_text: str, file_path: str) -> str:
    cli_mode = resolve_cli_mode(selected_mode)
    if cli_mode == "review":
        target = file_path.strip() or "<file path required>"
    else:
        target = "<request text>" if request_text.strip() else "<request text required>"

    return f"{Path(sys.executable).name} {SCRIPT_PATH.name} {cli_mode} {target}"


def build_command_summary(selected_mode: str) -> str:
    cli_mode = resolve_cli_mode(selected_mode)
    return f"{Path(sys.executable).name} {SCRIPT_PATH.name} {cli_mode}"


def run_miru_ai(selected_mode: str, request_text: str, file_path: str) -> tuple[bool, str]:
    dependency_issues = runtime_issue_messages()
    if dependency_issues:
        diagnostic = " | ".join(dependency_issues)
        print(f"[miru_ai_server] Runtime dependency issue before /api/run: {diagnostic}", file=sys.stderr)
        return False, f"Runtime dependency issue: {diagnostic}"

    command = build_command(selected_mode, request_text, file_path)
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
    except OSError as exc:
        print(f"[miru_ai_server] Failed to start subprocess: {exc}", file=sys.stderr)
        return False, f"Failed to start subprocess: {exc}"
    except subprocess.TimeoutExpired:
        print("[miru_ai_server] Subprocess timed out while waiting for miru_ai.py.", file=sys.stderr)
        return False, "Subprocess timed out while waiting for miru_ai.py."
    except Exception as exc:
        print(f"[miru_ai_server] Unexpected subprocess failure: {exc}", file=sys.stderr)
        traceback.print_exc()
        return False, f"Unexpected subprocess failure: {exc}"

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        details = stderr or stdout or f"Exited with status {result.returncode}."
        print(f"[miru_ai_server] miru_ai.py failed with status {result.returncode}: {details}", file=sys.stderr)
        return False, f"miru_ai.py failed: {details}"

    if not stdout:
        print("[miru_ai_server] miru_ai.py completed without any output.", file=sys.stderr)
        return False, "miru_ai.py completed without any output."

    return True, stdout


def build_inline_logo_data_uri() -> str:
    svg = """
    <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128' role='img' aria-label='Miru logo'>
      <defs>
        <linearGradient id='miru-bg' x1='0%' y1='0%' x2='100%' y2='100%'>
          <stop offset='0%' stop-color='#d7c8ff'/>
          <stop offset='55%' stop-color='#8b5cf6'/>
          <stop offset='100%' stop-color='#4c1d95'/>
        </linearGradient>
        <linearGradient id='miru-leaf' x1='0%' y1='0%' x2='100%' y2='100%'>
          <stop offset='0%' stop-color='#facc15'/>
          <stop offset='100%' stop-color='#fb7185'/>
        </linearGradient>
      </defs>
      <rect x='10' y='10' width='108' height='108' rx='30' fill='url(#miru-bg)'/>
      <path d='M39 30c13 0 24 6 31 17c8-11 19-17 32-17c-4 10-14 19-29 25c-11 4-23 6-34 5c2-18 12-30 30-30Z' fill='url(#miru-leaf)' opacity='0.95'/>
      <circle cx='64' cy='73' r='28' fill='rgba(8,5,15,0.22)'/>
      <path d='M46 86V52h11l10 15l10-15h11v34H77V69L67 83h-6L51 69v17H46Z' fill='#fffaf5'/>
    </svg>
    """.strip()
    return f"data:image/svg+xml;charset=utf-8,{quote(svg)}"


def find_static_asset(*relative_paths: str) -> str | None:
    for relative_path in relative_paths:
        if (STATIC_DIR / relative_path).is_file():
            return relative_path
    return None


def build_brand_assets() -> dict[str, str | None]:
    fallback_logo = build_inline_logo_data_uri()
    logo_asset = find_static_asset(
        "icons/miru-fruit.png",
        "icons/miru-fruit-512.png",
        "icons/miru-fruit-192.png",
        "icons/miru-fruit-180.png",
        "miru-logo.svg",
        "miru-logo.png",
        "miru.png",
    )
    favicon_asset = find_static_asset(
        "icons/favicon.ico",
        "icons/miru-fruit-192.png",
        "icons/miru-fruit.png",
    )
    apple_icon_asset = find_static_asset(
        "icons/miru-fruit-192.png",
        "icons/miru-fruit-512.png",
        "icons/miru-fruit.png",
    )
    manifest_asset = find_static_asset("manifest.webmanifest")

    def asset_url(relative_path: str | None) -> str | None:
        if not relative_path:
            return None
        return url_for("static", filename=relative_path)

    logo_url = asset_url(logo_asset) or fallback_logo
    return {
        "logo_url": logo_url,
        "favicon_url": asset_url(favicon_asset) or logo_url,
        "apple_icon_url": asset_url(apple_icon_asset) or logo_url,
        "manifest_url": asset_url(manifest_asset),
        "uses_inline_logo": logo_asset is None,
    }


def build_nav(current_endpoint: str) -> list[dict[str, str | bool]]:
    nav = []
    for item in NAV_ITEMS:
        href = url_for(item["endpoint"])
        nav.append(
            {
                "label": item["label"],
                "href": href,
                "active": item["endpoint"] == current_endpoint,
            }
        )
    return nav


def build_status_snapshot() -> list[dict[str, str]]:
    runtime_dependencies = collect_runtime_dependencies()
    catalog_status = inspect_fallback_catalog_db(FALLBACK_CATALOG_DB_PATH)
    dependency_rows = []
    for dependency in runtime_dependencies[:4]:
        detail = dependency["path"]
        if dependency["label"] == "Fallback catalog database":
            detail = (
                f"{dependency['path']} | cards={catalog_status['cards']} | "
                f"variants={catalog_status['variants']} | openable={'yes' if catalog_status['openable'] else 'no'}"
            )
            if catalog_status["error"]:
                detail += f" | {catalog_status['error']}"
        dependency_rows.append(
            {
                "label": dependency["label"],
                "value": (
                    "Ready"
                    if dependency["label"] != "Fallback catalog database" and dependency["exists"]
                    else (
                        "Ready"
                        if catalog_status["usable"]
                        else ("Present but unavailable" if catalog_status["exists"] else "Missing")
                    )
                ),
                "tone": (
                    "good"
                    if dependency["label"] != "Fallback catalog database" and dependency["exists"]
                    else (
                        "good"
                        if catalog_status["usable"]
                        else "warn"
                    )
                ),
                "detail": detail,
            }
        )

    dependency_rows.extend(
        [
            {
                "label": "Ask flow mode",
                "value": "Local knowledge",
                "tone": "neutral",
                "detail": "The visible Ask Miru modes resolve through local One Piece knowledge and do not require an API key.",
            },
            {
                "label": "Knowledge routes",
                "value": "7 pages",
                "tone": "neutral",
                "detail": "Home, Ask Miru, Dossiers, Gaps, Training, Status, and Dev Monitor.",
            },
        ]
    )
    return dependency_rows


def render_page(page_key: str, current_endpoint: str):
    issues = startup_issues()
    brand_assets = build_brand_assets()
    training_status = build_training_status()
    training_status["voyage"] = ensure_voyage_state(training_status, training_status.get("voyage"))
    dev_status = build_dev_status(training_status) if page_key == "dev" else None
    return render_template(
        "miru_ai.html",
        app_name=APP_NAME,
        app_tagline=APP_TAGLINE,
        page_key=page_key,
        nav_items=build_nav(current_endpoint),
        home_highlights=HOME_HIGHLIGHTS,
        dossier_panels=DOSSIER_PANELS,
        gap_panels=GAP_PANELS,
        training_panels=TRAINING_PANELS,
        training_status=training_status,
        roadmap_sections=ROADMAP_SECTIONS,
        status_snapshot=build_status_snapshot(),
        runtime_dependencies=collect_runtime_dependencies(),
        runtime_issues=runtime_issue_messages(),
        mode_configs=MODE_CONFIGS,
        presets=PRESETS,
        default_mode=MODE_CONFIGS[0]["key"],
        startup_issues=issues,
        run_disabled=bool(issues),
        asset_version=compute_asset_version(),
        logo_url=brand_assets["logo_url"],
        favicon_url=brand_assets["favicon_url"],
        apple_icon_url=brand_assets["apple_icon_url"],
        manifest_url=brand_assets["manifest_url"],
        uses_inline_logo=brand_assets["uses_inline_logo"],
        health_url=url_for("health"),
        run_url=RUN_API_PATH,
        ask_url=url_for("ask_page"),
        dossiers_url=url_for("dossiers_page"),
        gaps_url=url_for("gaps_page"),
        training_url=url_for("training_page"),
        status_url=url_for("status_page"),
        dev_status=dev_status,
        dev_status_url=url_for("dev_status"),
    )


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["PYTHON_NAME"] = Path(sys.executable).name
    log_pushover_startup_status(app.logger)

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": f"{exc.code} {exc.name}: {exc.description}"}), exc.code
            return exc
        print(f"[miru_ai_server] Unhandled error while serving {request.path}: {exc}", file=sys.stderr)
        traceback.print_exc()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": f"Miru AI server error: {exc.__class__.__name__}: {exc}"}), 500
        return (
            "Miru AI server error. Check the server console for the exact traceback and dependency diagnostics.",
            500,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.get("/")
    def index():
        return render_page("home", "index")

    @app.get("/ask")
    def ask_page():
        return render_page("ask", "ask_page")

    @app.get("/dossiers")
    def dossiers_page():
        return render_page("dossiers", "dossiers_page")

    @app.get("/gaps")
    def gaps_page():
        return render_page("gaps", "gaps_page")

    @app.get("/training")
    def training_page():
        return render_page("training", "training_page")

    @app.get("/status")
    def status_page():
        return render_page("status", "status_page")

    @app.get("/dev")
    def dev_page():
        return render_page("dev", "dev_page")

    @app.get("/api/health")
    def health():
        runtime_dependencies = collect_runtime_dependencies()
        catalog_status = inspect_fallback_catalog_db(FALLBACK_CATALOG_DB_PATH)
        preflight = build_runtime_preflight_report(
            target="all",
            check_server_port_available=False,
            check_worker_lock_available=False,
        )
        return jsonify(
            {
                "status": "ok" if preflight.get("ok") else "warn",
                "app_name": APP_NAME,
                "helper_script_ready": SCRIPT_PATH.is_file(),
                "api_key_ready": bool(os.getenv("OPENAI_API_KEY", "").strip()),
                "default_mode": MODE_CONFIGS[0]["key"],
                "pages": ["/", "/ask", "/dossiers", "/gaps", "/training", "/status", "/dev"],
                "runtime_dependencies": runtime_dependencies,
                "runtime_issues": runtime_issue_messages(),
                "fallback_catalog": catalog_status,
                "training_status": build_training_status(),
                "preflight": preflight,
            }
        )

    @app.get("/api/training-status")
    def training_status():
        return jsonify(build_training_status())

    @app.get("/api/dev-status")
    def dev_status():
        return jsonify(build_dev_status())

    @app.get("/api/dev/status")
    def legacy_dev_status():
        return jsonify(build_dev_status())

    @app.get("/dev-status")
    def legacy_dev_status_root():
        return jsonify(build_dev_status())

    @app.get("/api/dev/usage")
    def legacy_dev_usage():
        return jsonify(build_legacy_dev_usage())

    @app.get("/api/dev/validation_insights")
    def legacy_validation_insights():
        return jsonify(build_legacy_validation_insights())

    @app.get("/api/dev/debug-routes")
    def dev_debug_routes():
        if not is_local_request():
            return jsonify({"ok": False, "error": "debug-routes is limited to localhost."}), 403
        server_file = Path(__file__).resolve()
        try:
            mtime = server_file.stat().st_mtime
        except OSError:
            mtime = None
        routes = [
            {"rule": str(rule.rule), "methods": list(rule.methods - {"HEAD", "OPTIONS"})}
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
        ]
        return jsonify({
            "server_file": str(server_file),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "server_mtime": mtime,
            "routes": routes,
        })

    @app.post("/api/dev/set-learner-mode")
    def dev_set_learner_mode():
        if not is_local_request():
            return jsonify({"ok": False, "error": "set-learner-mode is limited to localhost."}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form or {}
        mode = str(payload.get("mode") or payload.get("learner_mode") or "").strip()
        effective = set_learner_mode(mode, PROJECT_ROOT)
        return jsonify({
            "ok": True,
            "learner_mode": effective,
            "learner": get_learner_status(PROJECT_ROOT),
        })

    @app.post("/api/dev/test-pushover")
    def dev_test_pushover():
        if not (app.config.get("TESTING") or is_local_request()):
            return jsonify({"ok": False, "error": "Pushover test endpoint is limited to localhost."}), 403

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form
        use_learning_summary = str(payload.get("use_learning_summary", "")).strip().lower() in {"1", "true", "yes", "on"} or not str(payload.get("message", "")).strip()
        dry_run = str(payload.get("dry_run", "")).strip().lower() in {"1", "true", "yes", "on"}
        learning_preview: dict[str, Any] | None = None
        if use_learning_summary:
            learning_status = load_learning_engine_status(
                queue_db_path=LEARNING_QUEUE_DB_PATH,
                status_db_path=LEARNING_STATUS_DB_PATH,
                dossier_db_path=LEARNING_DOSSIER_DB_PATH,
            )
            learning_preview = build_learning_notification(
                learning_status=learning_status,
                catalog_db_path=FALLBACK_CATALOG_DB_PATH,
                dossier_db_path=DOSSIER_DB_PATH,
            )
        title = str(payload.get("title", "")).strip() or (
            str(learning_preview["title"]) if learning_preview else "Miru AI Test"
        )
        message = str(payload.get("message", "")).strip() or (
            str(learning_preview["message"])
            if learning_preview
            else f"Miru AI test notification from {PROJECT_ROOT} at {current_timestamp()}."
        )
        priority_raw = str(payload.get("priority", "")).strip()
        priority: int | None = None
        if priority_raw:
            try:
                priority = int(priority_raw)
            except ValueError:
                return jsonify({"ok": False, "error": f"Invalid priority value: {priority_raw}"}), 400

        pushover_status = inspect_pushover_env()
        if dry_run:
            result = {
                "ok": True,
                "enabled": bool(pushover_status.get("enabled")),
                "configured": bool(pushover_status.get("configured")),
                "missing_required_keys": list(pushover_status.get("missing_required_keys") or []),
                "endpoint": "dry-run",
                "status_code": 200,
                "error": "",
                "response_json": {"dry_run": True},
                "response_text": "dry-run",
            }
        else:
            result = send_pushover_notification(
                title=title,
                message=message,
                priority=priority,
                logger=app.logger,
            )
            if bool(result.get("ok")) and learning_preview is not None:
                save_learning_notification_baseline(dict(learning_preview["snapshot"]))
        response_body = {
            "ok": bool(result["ok"]),
            "pushover": build_pushover_runtime_state(),
            "learning_preview": learning_preview,
            "send_result": {
                "ok": bool(result["ok"]),
                "dry_run": dry_run,
                "enabled": bool(result["enabled"]),
                "configured": bool(result["configured"]),
                "missing_required_keys": list(result["missing_required_keys"]),
                "endpoint": result["endpoint"],
                "status_code": result["status_code"],
                "error": result["error"],
                "response_json": result["response_json"],
                "response_text": str(result["response_text"] or "")[:500],
            },
        }
        return jsonify(response_body), (200 if result["ok"] else 502)

    @app.get("/api/dev/card-validation/<card_code>")
    def dev_card_validation(card_code: str):
        normalized = normalize_card_code(card_code)
        canonical_code = (normalized["canonical_code"] or card_code or "").strip().upper()
        audit = load_card_validation_audit(canonical_code, project_db_path=FALLBACK_CATALOG_DB_PATH)
        if audit is None:
            return jsonify({"ok": False, "card_code": canonical_code, "error": "Validation audit not found."}), 404
        return jsonify({"ok": True, "card_code": canonical_code, "audit": audit})

    @app.post("/api/run")
    @app.post("/api/run/")
    def run_request():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form

        requested_mode = str(payload.get("mode", MODE_CONFIGS[0]["key"])).strip().lower()
        request_text = str(payload.get("request_text", "")).strip()
        file_path = str(payload.get("file_path", "")).strip()
        selected_mode = resolve_effective_mode(requested_mode, request_text)
        cli_mode = resolve_cli_mode(selected_mode)
        command_preview = build_command_preview(selected_mode, request_text, file_path)
        command_summary = build_command_summary(selected_mode)

        errors = validate_inputs(selected_mode, request_text, file_path)
        if errors:
            return jsonify(
                {
                    "ok": False,
                    "command": command_preview,
                    "command_summary": command_summary,
                    "mode": selected_mode,
                    "requested_mode": requested_mode,
                    "cli_mode": cli_mode,
                    "mode_label": format_mode_label(selected_mode),
                    "error": " ".join(errors),
                }
            ), 400

        note_miru_run_started(selected_mode, request_text)
        ok = False
        output = ""
        try:
            ok, output = run_miru_ai(selected_mode, request_text, file_path)
        finally:
            note_miru_run_finished(selected_mode, request_text, ok, output)
        status_code = 200 if ok else 502
        response_body = {
            "ok": ok,
            "command": command_preview,
            "command_summary": command_summary,
            "mode": selected_mode,
            "requested_mode": requested_mode,
            "cli_mode": cli_mode,
            "mode_label": format_mode_label(selected_mode),
        }
        if ok:
            response_body["output"] = output
        else:
            response_body["error"] = output

        return jsonify(response_body), status_code

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightweight Flask sidecar for running tools/miru_ai.py from a phone or browser."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the Flask app to.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind the Flask app to.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the Flask development server in debug mode.",
    )
    return parser.parse_args()


def ensure_server_port_available(host: str, port: int) -> None:
    if psutil is not None:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status != psutil.CONN_LISTEN or not conn.laddr or int(conn.laddr.port) != int(port):
                    continue
                pid = int(conn.pid or 0)
                raise SystemExit(
                    f"Refusing to start Miru AI server: port {port} is already in use."
                    + (f" Listener PID: {pid}." if pid else "")
                )
        except RuntimeError:
            raise
        except Exception:
            pass
    bind_host = host if host not in {"0.0.0.0", ""} else "0.0.0.0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind((bind_host, int(port)))
        except OSError as exc:
            raise SystemExit(f"Refusing to start Miru AI server: port {port} is already in use or unavailable ({exc}).") from exc


def main() -> None:
    args = parse_args()
    ensure_server_port_available(args.host, args.port)
    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
