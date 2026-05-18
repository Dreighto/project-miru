#!/usr/bin/env python
from __future__ import annotations

import argparse
import atexit
import copy
import csv
import ctypes
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from contextlib import closing, suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread, Timer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import HTTPException

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from miru_ai.core.ai_onepiece import (
    initialize_fallback_catalog_db,
    inspect_fallback_catalog_db,
    normalize_card_code,
)
from miru_ai.core.local_helper import (
    draft_review_note,
    explain_elevation,
    helper_status,
    set_helper_runtime_override,
    suggest_correction_detail,
    summarize_candidate_rationale,
)
from miru_ai.dev_training_review import (
    build_training_review_queue_payload,
    op01_throughput_stats,
    persist_training_review_row,
    verify_action_preflight,
)
from miru_ai.dev_training_review import (
    configure as configure_dev_training_review,
)
from miru_ai.evidence_collectors import (
    collect_evidence_for_review,
)
from miru_ai.evidence_collectors import (
    configure as configure_evidence_collectors,
)
from miru_ai.evidence_watchdog import (
    configure as configure_evidence_watchdog,
)
from miru_ai.evidence_watchdog import (
    init_evidence_schema,
)
from miru_ai.evidence_watchdog import (
    watchdog_tick as evidence_watchdog_tick,
)
from miru_ai.governance.action_governance import (
    _log_action_history,
    build_action_governance_snapshot,
    build_publication_batch_summary,
    execute_governed_action,
    load_publication_batch_summary,
    load_publication_stage_summary,
    load_review_queue_summary,
    resolve_review_queue_item,
    update_review_queue_item,
)
from miru_ai.governance.mcp_governance import (
    McpInvocationError,
    build_mcp_governance_summary,
    list_research_review_leads,
    run_governed_research,
    sync_catalog_snapshot,
)
from miru_ai.operator_price_context import (
    build_operator_price_snapshot,
    refresh_operator_price_from_tcgcsv,
)
from miru_ai.recurrence import (
    build_candidate_queue_payload,
    init_recurrence_schema,
    seed_recurrence_from_history,
)
from miru_ai.recurrence import (
    configure as configure_recurrence,
)
from miru_ai.workers.image_fetcher import fetch_all_missing
from miru_ai.workers.learning_engine import (
    build_engine_from_args,
    load_learning_engine_status,
)
from miru_ai.workers.learning_engine import (
    build_parser as build_learner_parser,
)
from shared.env import (
    build_pushover_status_message,
    inspect_pushover_env,
    load_project_env,
)
from shared.pushover import send_pushover_notification
from tools.miru_learner_config import LEARNER_MODES, get_learner_mode, set_learner_mode
from tools.miru_operator_handoff_resolution import (
    clear_operator_handoff_resolution,
    compute_operator_handoff_need_fingerprint,
    is_operator_handoff_acknowledged_for_fingerprint,
    save_operator_handoff_resolution,
)
from tools.miru_project_sync import (
    MiruProjectDbSync,
    connect_catalog_db,
    ensure_catalog_sync_schema,
    list_validation_audit_insights,
    load_card_validation_audit,
    run_publish_ready_insight_sync,
    run_worktree_card_insight_sync,
)
from tools.miru_self_report import build_self_report
from tools.miru_source_adapters import NormalizedSourceRecord

TOOL_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = TOOL_ROOT / "templates"
STATIC_DIR = TOOL_ROOT / "static"
PROJECT_ROOT = TOOL_ROOT.parent
SCRIPT_PATH = TOOL_ROOT / "core" / "ai.py"
KNOWLEDGE_CACHE_PATH = PROJECT_ROOT / "data" / "miru_ai_onepiece_knowledge.json"
FALLBACK_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
DECK_INTEL_DB_PATH = PROJECT_ROOT / "data" / "miru_deck_intel.db"
LEARNING_QUEUE_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_queue.db"
LEARNING_STATUS_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_log.db"
LEARNING_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
PRICES_PATH = PROJECT_ROOT / "data" / "prices.json"
CHAPTER19_11_INSTALL_PLAN_CSV = PROJECT_ROOT / "data" / "overlays" / "chapter19_11_install_plan.csv"
CHAPTER19_11C_INSTALL_PANEL_MANIFEST = (
    PROJECT_ROOT / "data" / "overlays" / "chapter19_11c_install_panel_image_manifest.json"
)
CHAPTER19_12_PRICE_HYDRATION_CSV = (
    PROJECT_ROOT / "data" / "overlays" / "chapter19_12_price_hydration.csv"
)
LIMITS_STATUS_PATH = PROJECT_ROOT / "data" / "miru_limits_status.json"
PUSHOVER_LEARNING_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "miru_pushover_learning_snapshot.json"
WORKTREE_LEARNER_PID_DIR = PROJECT_ROOT / "data" / "startup-logs"
WORKTREE_LEARNER_PID_FILE = WORKTREE_LEARNER_PID_DIR / "miru_learner_worktree.pid"
MIRU_AI_DEV_PID_FILE = WORKTREE_LEARNER_PID_DIR / "miru_ai_worktree.pid"
WORKER_LAST_RUN_PATH = PROJECT_ROOT / "data" / "miru_worker_last_run.json"
GOVERNED_AUTOPILOT_DATA_DIR = PROJECT_ROOT / "data"
WORKTREE_LEARNER_STDOUT_LOG = WORKTREE_LEARNER_PID_DIR / "miru_learner_worktree_stdout.log"
WORKTREE_LEARNER_STDERR_LOG = WORKTREE_LEARNER_PID_DIR / "miru_learner_worktree_stderr.log"
CSS_PATH = STATIC_DIR / "miru_ai.css"
JS_PATH = STATIC_DIR / "miru_ai.js"
TEMPLATE_PATH = TEMPLATE_DIR / "miru_ai.html"
MIRU_ASSETS_ROOT = Path(r"D:\Miru_Assets")
MIRU_FETCH_LOG_PATH = MIRU_ASSETS_ROOT / "fetch_log.txt"
OCR_AUDIT_PARALLEL_REPRINT_PATH = MIRU_ASSETS_ROOT / "ocr_audit_parallel_reprint.txt"
IMAGE_REVIEW_DECISIONS_PATH = MIRU_ASSETS_ROOT / "image_review_decisions.json"
_IMAGE_REVIEW_DECISIONS_LOCK = Lock()
IMAGE_REVIEW_STAGED_PATH = MIRU_ASSETS_ROOT / "image_review_staged.json"
_IMAGE_REVIEW_STAGED_LOCK = Lock()
DEFAULT_TIMEOUT_SECONDS = 180
RUN_API_PATH = "/api/run"
APP_NAME = "Miru AI"
APP_TAGLINE = "A One Piece Card Intelligence System"
PROJECT_ENV_LOAD = load_project_env()
_PUSHOVER_STATUS_LOCK = Lock()
_PUSHOVER_STATUS_LOGGED = False
CURRENT_SERVER_PORT = 18765
_SERVER_STARTED_AT: str = ""  # ISO-8601 UTC; set in main() before create_app()
_TTL_CACHE_LOCK = Lock()
_TTL_CACHE: dict[str, dict[str, Any]] = {}
_RUNTIME_TRUTH_CACHE_LOCK = Lock()
_RUNTIME_TRUTH_CACHE: dict[str, dict[str, Any]] = {}
_LAST_INSIGHT_SYNC_LOCK = Lock()
_LAST_INSIGHT_SYNC_REPORT: dict[str, Any] = {}
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
    {"endpoint": "leader_hub", "label": "Leader Hub", "path": "/leader/OP10-001"},
    {"endpoint": "dev_page", "label": "Dev"},
    {"endpoint": "dev_monitor_page", "label": "Dev · Full", "path": "/dev/monitor"},
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
        "still_learning": ("Full verified multi-source card intelligence across the product",),
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
        "still_learning": ("Ongoing coverage expansion and answer quality improvements",),
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
    {
        "key": "east_blue",
        "name": "East Blue",
        "short_name": "East Blue",
        "sprite": "islands/island_east_blue.png",
        "stage": "East Blue",
        "map_x": 10,
        "map_y": 75,
    },
    {
        "key": "reverse_mountain",
        "name": "Reverse Mountain",
        "short_name": "Reverse Mountain",
        "sprite": "islands/island_reverse_mountain.png",
        "stage": "Grand Line Approach",
        "map_x": 23,
        "map_y": 57,
    },
    {
        "key": "alabasta",
        "name": "Alabasta",
        "short_name": "Alabasta",
        "sprite": "islands/island_alabasta.png",
        "stage": "Grand Line",
        "map_x": 35,
        "map_y": 66,
    },
    {
        "key": "skypiea",
        "name": "Skypiea",
        "short_name": "Skypiea",
        "sprite": "islands/island_skypiea.png",
        "stage": "Grand Line",
        "map_x": 45,
        "map_y": 42,
    },
    {
        "key": "water_7",
        "name": "Water 7",
        "short_name": "Water 7",
        "sprite": "islands/island_water_7.png",
        "stage": "Grand Line",
        "map_x": 56,
        "map_y": 56,
    },
    {
        "key": "thriller_bark",
        "name": "Thriller Bark",
        "short_name": "Thriller Bark",
        "sprite": "islands/island_thriller_bark.png",
        "stage": "Grand Line",
        "map_x": 66,
        "map_y": 42,
    },
    {
        "key": "fishman_island",
        "name": "Fishman Island",
        "short_name": "Fishman Island",
        "sprite": "islands/island_fishman_island.png",
        "stage": "New World",
        "map_x": 74,
        "map_y": 64,
    },
    {
        "key": "dressrosa",
        "name": "Dressrosa",
        "short_name": "Dressrosa",
        "sprite": "islands/island_dressrosa.png",
        "stage": "New World",
        "map_x": 83,
        "map_y": 48,
    },
    {
        "key": "whole_cake",
        "name": "Whole Cake",
        "short_name": "Whole Cake",
        "sprite": "islands/island_whole_cake.png",
        "stage": "New World",
        "map_x": 90,
        "map_y": 62,
    },
    {
        "key": "wano",
        "name": "Wano",
        "short_name": "Wano",
        "sprite": "islands/island_wano.png",
        "stage": "New World",
        "map_x": 86,
        "map_y": 29,
    },
    {
        "key": "egghead",
        "name": "Egghead",
        "short_name": "Egghead",
        "sprite": "islands/island_egghead.png",
        "stage": "Final Voyage",
        "map_x": 69,
        "map_y": 15,
    },
    {
        "key": "laugh_tale",
        "name": "Laugh Tale",
        "short_name": "Laugh Tale",
        "sprite": "islands/island_laugh_tale.png",
        "stage": "Final Voyage",
        "map_x": 50,
        "map_y": 11,
    },
)

VOYAGE_BOSSES = (
    {"name": "Alvida", "sprite": "bosses/boss_alvida.png", "island_key": "east_blue"},
    {"name": "Kuro", "sprite": "bosses/boss_kuro.png", "island_key": "east_blue"},
    {"name": "Krieg", "sprite": "bosses/boss_krieg.png", "island_key": "east_blue"},
    {"name": "Buggy", "sprite": "bosses/boss_buggy.png", "island_key": "east_blue"},
    {"name": "Arlong", "sprite": "bosses/boss_arlong.png", "island_key": "east_blue"},
    {
        "name": "Crocodile",
        "sprite": "bosses/boss_crocodile.png",
        "island_key": "alabasta",
    },
    {"name": "Enel", "sprite": "bosses/boss_enel.png", "island_key": "skypiea"},
    {"name": "Lucci", "sprite": "bosses/boss_lucci.png", "island_key": "water_7"},
    {"name": "Moria", "sprite": "bosses/boss_moria.png", "island_key": "thriller_bark"},
    {
        "name": "Doflamingo",
        "sprite": "bosses/boss_doflamingo.png",
        "island_key": "dressrosa",
    },
    {
        "name": "Katakuri",
        "sprite": "bosses/boss_katakuri.png",
        "island_key": "whole_cake",
    },
    {
        "name": "Big Mom",
        "sprite": "bosses/boss_big_mom.png",
        "island_key": "whole_cake",
    },
    {"name": "Kaido", "sprite": "bosses/boss_kaido.png", "island_key": "wano"},
    {
        "name": "Five Elders",
        "sprite": "bosses/boss_five_elders.png",
        "island_key": "egghead",
    },
    {"name": "Imu", "sprite": "bosses/boss_imu.png", "island_key": "egghead"},
    {
        "name": "Blackbeard",
        "sprite": "bosses/boss_blackbeard.png",
        "island_key": "laugh_tale",
    },
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
    (
        "catalog_ready",
        "Catalog Ready",
        "Local card catalog is indexed and ready for lookup.",
    ),
    (
        "dossiers_building",
        "Dossiers Building",
        "Structured dossiers are being created card by card.",
    ),
    (
        "verification_expanding",
        "Verification Expanding",
        "Verified dossiers are growing with trusted evidence.",
    ),
    (
        "relationship_graph_later",
        "Relationship Graph Later",
        "Card and variant relationships will deepen later.",
    ),
    (
        "deck_intelligence_later",
        "Deck Intelligence Later",
        "Deck-level intelligence comes after card knowledge is solid.",
    ),
)


def read_port_env(name: str, default: int) -> int:
    raw_value = str(os.getenv(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


PROJECT_MIRU_PORT = read_port_env("PROJECT_MIRU_PORT", 8080)
PROJECT_MIRU_DEV_PORT = read_port_env("PROJECT_MIRU_DEV_PORT", 18080)
RUNTIME_MONITOR_PORT = read_port_env("MIRU_RUNTIME_PORT", 18765)
RUNTIME_MONITOR_STATUS_URL = str(os.getenv("MIRU_RUNTIME_STATUS_URL", "") or "").strip()
ACTIVITY_RECENT_WINDOW_SECONDS = 300


def path_signature(path_like: str | Path | None) -> tuple[bool, int, int]:
    try:
        path = Path(path_like) if path_like is not None else None
    except Exception:
        path = None
    if path is None or not path.exists():
        return (False, 0, 0)
    stat = path.stat()
    return (True, int(stat.st_mtime_ns), int(stat.st_size))


def get_ttl_cached_value(
    key: str,
    *,
    ttl_seconds: float,
    builder: Callable[[], Any],
    signature: Any = None,
) -> Any:
    now = time.time()
    with _TTL_CACHE_LOCK:
        entry = _TTL_CACHE.get(key)
        if entry and entry.get("expires_at", 0.0) > now and entry.get("signature") == signature:
            return copy.deepcopy(entry["value"])
    value = builder()
    with _TTL_CACHE_LOCK:
        _TTL_CACHE[key] = {
            "expires_at": now + max(float(ttl_seconds), 0.0),
            "signature": signature,
            "value": copy.deepcopy(value),
        }
    return value


def invalidate_ttl_cache(key: str) -> None:
    """Remove a key from the TTL cache so the next get_ttl_cached_value rebuilder runs."""
    with _TTL_CACHE_LOCK:
        _TTL_CACHE.pop(key, None)


DEV_ACTIVITY_BLUEPRINT = (
    {
        "key": "sleeping",
        "title": "Idle",
        "description": "Miru is online but not working on a learning task right now.",
        "visual": "sleeping",
    },
    {
        "key": "setting_sail",
        "title": "Running",
        "description": "Miru is online and moving through structured learning work.",
        "visual": "sailing",
    },
    {
        "key": "gathering_crew",
        "title": "Working Now",
        "description": "Miru is actively processing a live learning task.",
        "visual": "crew",
    },
    {
        "key": "storm_warning",
        "title": "Needs Attention",
        "description": "The learning engine reported a recent problem that may need help.",
        "visual": "storm",
    },
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

# Bumped once per server process so ?v= on miru_ai.js / miru_ai.css changes on every restart
# (avoids mobile 304 loops when mtimes did not move).
_MIRU_ASSET_VERSION: str = ""


def compute_asset_version() -> str:
    """Return a cache-bust query value stable for this process, new on each server start."""
    global _MIRU_ASSET_VERSION
    if not _MIRU_ASSET_VERSION:
        _MIRU_ASSET_VERSION = str(int(time.time() * 1000))
    return _MIRU_ASSET_VERSION


def format_mode_label(mode_key: str) -> str:
    return ALL_MODE_LABELS.get(mode_key, mode_key.title())


def format_count(value: int) -> str:
    return f"{int(max(value, 0)):,}"


def format_compact_count(value: int | float) -> str:
    number = float(value or 0)
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= threshold:
            compact = absolute / threshold
            return f"{sign}{compact:.1f}".rstrip("0").rstrip(".") + suffix
    if absolute.is_integer():
        return f"{sign}{int(absolute)}"
    return f"{sign}{absolute:.1f}".rstrip("0").rstrip(".")


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
    dossier_path = Path(path or DOSSIER_DB_PATH)
    status = {
        "path": str(dossier_path),
        "exists": dossier_path.is_file(),
        "openable": False,
        "usable": False,
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
        with closing(sqlite3.connect(dossier_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            status["openable"] = True

            if "cards" in tables:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
                status["dossiers_created"] = int(
                    conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                )
                if "overall_state" in columns:
                    status["verified_dossiers"] = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM cards WHERE lower(coalesce(overall_state, '')) = 'verified'"
                        ).fetchone()[0]
                    )
                elif "verification_state" in columns:
                    status["verified_dossiers"] = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM cards WHERE lower(coalesce(verification_state, '')) = 'verified'"
                        ).fetchone()[0]
                    )
                elif "confidence_records" in tables:
                    status["verified_dossiers"] = int(
                        conn.execute(
                            """
                            SELECT COUNT(DISTINCT card_id)
                            FROM confidence_records
                            WHERE lower(coalesce(verification_state, '')) = 'verified'
                              AND (
                                    lower(coalesce(scope, '')) = 'card'
                                 OR lower(coalesce(scope_key, '')) = 'overall'
                              )
                            """
                        ).fetchone()[0]
                    )
                if "card_variants" in tables:
                    status["variant_records"] = int(
                        conn.execute("SELECT COUNT(*) FROM card_variants").fetchone()[0]
                    )
                if "image_identity" in columns:
                    status["image_coverage_cards"] = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM cards WHERE trim(coalesce(image_identity, '')) != ''"
                        ).fetchone()[0]
                    )
                elif "card_facts" in tables:
                    status["image_coverage_cards"] = int(
                        conn.execute(
                            """
                            SELECT COUNT(DISTINCT card_id)
                            FROM card_facts
                            WHERE field_name = 'image_identity'
                              AND trim(coalesce(value_text, '')) != ''
                            """
                        ).fetchone()[0]
                    )
            elif "dossiers" in tables:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(dossiers)").fetchall()}
                status["dossiers_created"] = int(
                    conn.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0]
                )
                verification_column = None
                for candidate in ("overall_state", "verification_state", "status"):
                    if candidate in columns:
                        verification_column = candidate
                        break
                if verification_column:
                    status["verified_dossiers"] = int(
                        conn.execute(
                            f"SELECT COUNT(*) FROM dossiers WHERE lower(coalesce({verification_column}, '')) = 'verified'"
                        ).fetchone()[0]
                    )
            else:
                status["error"] = (
                    "Miru dossier database opened, but no recognized dossier tables were found."
                )
                return status

            status["usable"] = (
                status["dossiers_created"] > 0 or "cards" in tables or "dossiers" in tables
            )
            if not status["usable"] and not status["error"]:
                status["error"] = "Miru dossier database opened, but contains no dossier rows yet."
    except sqlite3.Error as exc:
        status["error"] = f"{exc.__class__.__name__}: {exc}"

    return status


def determine_training_stage(
    total_cards: int, dossiers_created: int, verified_dossiers: int
) -> str:
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
    next_stage = (
        dict(INTELLIGENCE_STAGE_BLUEPRINT[current_index + 1])
        if current_index + 1 < total_stages
        else None
    )

    for stage in (current_stage, next_stage):
        if isinstance(stage, dict):
            stage["number"] = 1 + next(
                index
                for index, entry in enumerate(INTELLIGENCE_STAGE_BLUEPRINT)
                if entry["key"] == stage["key"]
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
    cache_signature = (
        path_signature(FALLBACK_CATALOG_DB_PATH),
        path_signature(DOSSIER_DB_PATH),
    )
    cached = get_ttl_cached_value(
        "training_status",
        ttl_seconds=10.0,
        signature=cache_signature,
        builder=lambda: _build_training_status_uncached(),
    )
    return cached


def _build_training_status_uncached() -> dict[str, Any]:
    catalog_status = ensure_fallback_catalog_status()
    dossier_status = inspect_dossier_db(DOSSIER_DB_PATH)

    total_cards = int(catalog_status["cards"]) if catalog_status["usable"] else 0
    dossiers_created = (
        min(int(dossier_status["dossiers_created"]), total_cards)
        if total_cards
        else int(dossier_status["dossiers_created"])
    )
    verified_dossiers = (
        min(int(dossier_status["verified_dossiers"]), dossiers_created) if dossiers_created else 0
    )
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
    remaining_summary = (
        f"{format_count(remaining_gaps)} cards still need verified dossier coverage."
    )

    ring_metrics_clear = [
        {
            "label": "Catalog size",
            "percent": catalog_coverage_percent,
            "value": format_count(total_cards),
            "detail": "Indexed card identities available locally.",
        },
        {
            "label": "Dossiers in store",
            "percent": dossier_coverage_percent,
            "value": format_count(dossiers_created),
            "detail": "Cards with dossier records in the verified store.",
        },
        {
            "label": "Verified",
            "percent": verified_coverage_percent,
            "value": format_count(verified_dossiers),
            "detail": "Cards with verified dossier state.",
        },
    ]
    stats_clear = (
        {
            "label": "Catalog size",
            "value": format_count(total_cards),
            "detail": "Cards in local catalog.",
        },
        {
            "label": "Dossiers in verified store",
            "value": format_count(dossiers_created),
            "detail": "Card profiles in the main verified store.",
        },
        {
            "label": "Verified in store",
            "value": format_count(verified_dossiers),
            "detail": "Dossiers marked verified.",
        },
        {
            "label": "Still to verify",
            "value": format_count(remaining_gaps),
            "detail": "Catalog cards without a verified dossier.",
        },
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


def safe_voyage_asset_url(
    relative_path: str | None, fallback_relative_path: str = "ui/ui_log_pose.png"
) -> str:
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


def build_voyage_state(
    training_status: dict[str, Any],
    learning_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    progress_percent = float(training_status.get("progress_percent") or 0.0)
    island_total = len(VOYAGE_ISLANDS)
    reverse_mountain_index = next(
        (
            index
            for index, island in enumerate(VOYAGE_ISLANDS)
            if island.get("key") == "reverse_mountain"
        ),
        0,
    )
    next_island_index = min(reverse_mountain_index + 1, island_total - 1) if island_total else 0

    current_island = build_voyage_location(VOYAGE_ISLANDS[reverse_mountain_index], "current")
    next_island = (
        build_voyage_location(VOYAGE_ISLANDS[next_island_index], "next") if island_total else None
    )
    route_nodes = []
    for index, island in enumerate(VOYAGE_ISLANDS):
        if index == reverse_mountain_index:
            status = "current"
        elif index == next_island_index and next_island_index != reverse_mountain_index:
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
                "sprite_url": safe_voyage_asset_url(
                    island.get("sprite"), "islands/island_east_blue.png"
                ),
                "marker_url": safe_voyage_asset_url(
                    VOYAGE_ROUTE_MARKERS.get(status),
                    "routes/route_checkpoint_marker.png",
                ),
                "bosses": [],
            }
        )

    learning_phase = describe_learning_engine_phase(dict(learning_status or {}))
    queue_length = int((learning_status or {}).get("queue_length") or 0)
    running_count = int((learning_status or {}).get("running_count") or 0)
    assets = build_voyage_assets()
    stage_label = "Approaching Reverse Mountain"
    route_progress = learning_phase["description"]
    stage_detail = "Miru is in early structured learning: collecting, checking, and organizing card knowledge without claiming advanced intelligence."
    stage_meaning = (
        "This voyage position means Miru has moved beyond basic setup and is learning in a structured way, "
        "but it is still far from advanced deck, matchup, or endgame reasoning."
    )

    if learning_phase["title"] == "Verifying Knowledge":
        can_do_now = "Check card facts against real sources"
        can_do_now_detail = (
            "Miru can compare queued card facts with source material and lock in trusted details."
        )
    elif learning_phase["title"] == "Writing Dossiers":
        can_do_now = "Write structured card notes"
        can_do_now_detail = (
            "Miru can turn trusted facts into cleaner dossier records for later lookup."
        )
    elif learning_phase["title"] == "Scanning Sources":
        can_do_now = "Collect source material for verification"
        can_do_now_detail = (
            "Miru can gather new source pages and queue them for the next verification pass."
        )
    elif learning_phase["title"] == "Processing Images":
        can_do_now = "Track and verify card images"
        can_do_now_detail = (
            "Miru can fetch or review card images so the learning archive stays usable."
        )
    else:
        can_do_now = "Build dependable card knowledge"
        can_do_now_detail = (
            "Miru can work through source-backed card tasks and keep structured learning moving."
        )

    next_title = "Steadier trusted coverage"
    if queue_length > 0:
        next_detail = f"{format_count(queue_length)} queued task{'s' if queue_length != 1 else ''} still need attention after the current step."
    elif running_count > 0:
        next_detail = "The next step is finishing the work already in flight and turning it into trusted coverage."
    else:
        next_detail = "The next step is widening trusted coverage and keeping the queue healthy."

    route_polyline = " ".join(f"{node['map_x']},{node['map_y']}" for node in route_nodes)
    ship_position = {
        "x": int(current_island.get("map_x", 0) or 0),
        "y": int(current_island.get("map_y", 0) or 0),
    }

    return {
        "source_label": "Live learning engine telemetry",
        "learning_label": learning_phase["title"],
        "learning_stage": str(training_status.get("training_stage") or "planned"),
        "stage": stage_label,
        "sea_label": "Early structured learning",
        "route_progress": route_progress,
        "boss_summary": learning_phase["detail"] or stage_detail,
        "stage_title": stage_label,
        "stage_detail": stage_detail,
        "stage_meaning": stage_meaning,
        "can_do_now": can_do_now,
        "can_do_now_detail": can_do_now_detail,
        "still_learning": "Broader trusted coverage",
        "still_learning_detail": "Miru still needs much more verified coverage before higher-level strategic reasoning would be trustworthy.",
        "next_title": next_title,
        "next_detail": next_detail,
        "current_island": current_island,
        "next_island": next_island,
        "next_boss": None,
        "progress_percent": progress_percent,
        "defeated_boss_count": 0,
        "boss_total": 0,
        "defeated_bosses": [],
        "defeated_boss_names": [],
        "recent_log": [],
        "route_nodes": route_nodes,
        "route_polyline": route_polyline,
        "ship_position": ship_position,
        "assets": assets,
        "celebration_state": "voyage",
    }


def ensure_voyage_state(
    training_status: dict[str, Any] | None,
    voyage_state: dict[str, Any] | None = None,
    learning_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    training_status = dict(training_status or {})
    base_voyage = build_voyage_state(training_status, learning_status=learning_status)
    candidate = voyage_state if isinstance(voyage_state, dict) else training_status.get("voyage")
    if not isinstance(candidate, dict):
        return base_voyage

    merged = dict(base_voyage)
    for key in (
        "source_label",
        "learning_label",
        "learning_stage",
        "stage",
        "sea_label",
        "route_progress",
        "boss_summary",
        "stage_title",
        "stage_detail",
        "stage_meaning",
        "can_do_now",
        "can_do_now_detail",
        "still_learning",
        "still_learning_detail",
        "next_title",
        "next_detail",
        "progress_percent",
        "defeated_boss_count",
        "boss_total",
        "celebration_state",
        "route_polyline",
    ):
        if candidate.get(key) not in (None, ""):
            merged[key] = candidate[key]
    for key in ("current_island", "next_island", "next_boss", "ship_position"):
        if isinstance(candidate.get(key), dict):
            merged[key] = {**merged.get(key, {}), **candidate[key]}
    if isinstance(candidate.get("assets"), dict):
        merged["assets"] = {
            **merged["assets"],
            **{k: v for k, v in candidate["assets"].items() if v},
        }
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


def parse_bool_flag(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def seconds_since(timestamp: str) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = time.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(time.time() - time.mktime(parsed), 0.0)


# Heartbeat age above this is never treated as "Running" while a PID looks alive; aligns dev-status with live process truth.
LEARNER_HEARTBEAT_STALE_SECONDS = 120.0


def compute_learner_state_and_freshness(
    learning_status: dict[str, Any],
) -> dict[str, str]:
    """Compute learner_state (Offline/Starting/Running/Idle/Stale/Error) and heartbeat_freshness (fresh/stale/none)."""
    current_state = str(learning_status.get("current_state") or "").strip().lower()
    last_heartbeat = str(learning_status.get("last_heartbeat") or "").strip()
    last_error = str(learning_status.get("last_error") or "").strip()
    queue_waiting = int(learning_status.get("queue_length") or 0)
    queue_running = int(learning_status.get("running_count") or 0)
    active_states = {
        "processing",
        "running",
        "validating",
        "learning",
        "syncing",
        "discovering",
        "starting",
    }
    heartbeat_age = seconds_since(last_heartbeat) if last_heartbeat else None
    status_db_exists = bool(learning_status.get("status_db_exists"))

    if last_error or current_state == "error":
        heartbeat_freshness = (
            "stale"
            if heartbeat_age is not None and heartbeat_age <= LEARNER_HEARTBEAT_STALE_SECONDS
            else ("none" if heartbeat_age is None else "stale")
        )
        return {"learner_state": "Error", "heartbeat_freshness": heartbeat_freshness}
    if not status_db_exists and heartbeat_age is None:
        return {"learner_state": "Offline", "heartbeat_freshness": "none"}
    if heartbeat_age is not None and heartbeat_age > 600:
        return {"learner_state": "Offline", "heartbeat_freshness": "stale"}
    if (
        heartbeat_age is not None
        and heartbeat_age > LEARNER_HEARTBEAT_STALE_SECONDS
        and (queue_waiting > 0 or queue_running > 0 or current_state in active_states)
    ):
        return {"learner_state": "Stale", "heartbeat_freshness": "stale"}
    if current_state == "starting":
        return {
            "learner_state": "Starting",
            "heartbeat_freshness": (
                "fresh"
                if heartbeat_age is not None and heartbeat_age <= LEARNER_HEARTBEAT_STALE_SECONDS
                else "stale"
            ),
        }
    if queue_running > 0 or current_state in active_states:
        return {
            "learner_state": "Running",
            "heartbeat_freshness": (
                "fresh"
                if heartbeat_age is not None and heartbeat_age <= LEARNER_HEARTBEAT_STALE_SECONDS
                else "stale"
            ),
        }
    if last_heartbeat:
        return {
            "learner_state": "Idle",
            "heartbeat_freshness": (
                "fresh"
                if heartbeat_age is not None and heartbeat_age <= LEARNER_HEARTBEAT_STALE_SECONDS
                else "stale"
            ),
        }
    return {"learner_state": "Offline", "heartbeat_freshness": "none"}


def humanize_snake_label(value: str) -> str:
    parts = [part for part in re.split(r"[_\s]+", str(value or "").strip()) if part]
    if not parts:
        return ""
    return " ".join(part.capitalize() for part in parts)


def classify_learning_task(
    task_type: str,
    *,
    current_image_task: str = "",
) -> tuple[str, str]:
    normalized = str(task_type or "").strip().lower()
    if current_image_task or normalized in {
        "fetch_card_image",
        "verify_card_image",
        "refresh_card_image",
        "inspect_missing_image",
    }:
        return (
            "Processing Images",
            "Miru is fetching or checking card images for the learning archive.",
        )
    if normalized in {
        "discover_sources",
        "discover_set_cards",
        "fetch_official_source",
        "refresh_from_source",
    }:
        return (
            "Scanning Sources",
            "Miru is collecting source material before it verifies new card facts.",
        )
    if normalized in {"verify_official_fields", "verify_card_image"}:
        return (
            "Verifying Knowledge",
            "Miru is checking card facts against source material before saving them.",
        )
    if normalized in {"bootstrap_dossier", "sync_missing_fields", "refresh_progress"}:
        return (
            "Writing Dossiers",
            "Miru is updating structured card notes from the facts it already trusts.",
        )
    return (
        "Processing Queue",
        "Miru is working through queued learning tasks.",
    )


def build_learning_task_detail(
    *,
    task_label: str,
    task_type: str,
    current_source_id: str,
    current_image_task: str,
    queue_length: int,
) -> str:
    parts: list[str] = []
    primary = current_image_task or task_label or humanize_snake_label(task_type)
    if primary:
        parts.append(primary)
    if current_source_id:
        parts.append(f"Source: {current_source_id}")
    if queue_length > 0:
        waiting_label = "task" if queue_length == 1 else "tasks"
        parts.append(f"{format_count(queue_length)} {waiting_label} waiting")
    if parts:
        return " | ".join(parts)
    return "Miru is working through queued learning tasks."


def load_pushover_learning_snapshot(
    path: Path = PUSHOVER_LEARNING_SNAPSHOT_PATH,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_pushover_learning_snapshot(
    snapshot: dict[str, Any],
    *,
    title: str = "",
    message: str = "",
    path: Path = PUSHOVER_LEARNING_SNAPSHOT_PATH,
) -> None:
    payload = dict(snapshot or {})
    if title:
        payload["_notification_title"] = str(title)
    if message:
        payload["_notification_message"] = str(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_learning_notification_snapshot(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": current_timestamp(),
        "verified_dossiers": int(training_status.get("verified_dossiers") or 0),
        "verified_coverage_percent": round(
            float(training_status.get("verified_coverage_percent") or 0.0), 1
        ),
        "queue_length": int(learning_status.get("queue_length") or 0),
        "running_count": int(learning_status.get("running_count") or 0),
        "failed_count": int(learning_status.get("failed_count") or 0),
        "processed_count": int(learning_status.get("processed_count") or 0),
        "source_success_count": int(learning_status.get("source_success_count") or 0),
        "source_error_count": int(learning_status.get("source_error_count") or 0),
        "image_success_count": int(learning_status.get("image_success_count") or 0),
        "image_error_count": int(learning_status.get("image_error_count") or 0),
        "current_state": str(learning_status.get("current_state") or "").strip().lower(),
        "current_task_type": str(learning_status.get("current_task_type") or "").strip().lower(),
        "current_task_label": str(learning_status.get("current_task_label") or "").strip(),
        "current_card_code": str(learning_status.get("current_card_code") or "").strip(),
        "current_source_id": str(learning_status.get("current_source_id") or "").strip(),
        "last_completed_task": str(learning_status.get("last_completed_task") or "")
        .strip()
        .lower(),
        "last_completed_card": str(learning_status.get("last_completed_card") or "").strip(),
        "last_error": str(learning_status.get("last_error") or "").strip(),
        "last_heartbeat": str(learning_status.get("last_heartbeat") or "").strip(),
    }


def describe_learning_notification_engine_state(snapshot: dict[str, Any]) -> str:
    current_state = str(snapshot.get("current_state") or "").strip().lower()
    task_type = str(snapshot.get("current_task_type") or "").strip().lower()
    task_label = str(snapshot.get("current_task_label") or "").strip()
    current_card = str(snapshot.get("current_card_code") or "").strip()
    source_id = str(snapshot.get("current_source_id") or "").strip()
    queue_length = int(snapshot.get("queue_length") or 0)

    if current_state == "error" or snapshot.get("last_error"):
        return "stuck"
    if current_state in {"processing", "starting"}:
        if "image" in task_type:
            return "checking images"
        if (
            "source" in task_type
            or task_type.startswith("verify_")
            or task_type.startswith("discover_")
        ):
            return "searching"
        return "learning"
    if queue_length > 0:
        return "queued"
    if current_state in {"sleeping", "idle"}:
        return "idle"
    if task_label or current_card or source_id:
        return "active"
    return "idle"


def describe_learning_notification_task(snapshot: dict[str, Any]) -> str:
    task_label = str(snapshot.get("current_task_label") or "").strip()
    task_type = str(snapshot.get("current_task_type") or "").strip().lower()
    current_card = str(snapshot.get("current_card_code") or "").strip()
    source_id = str(snapshot.get("current_source_id") or "").strip()
    if task_label:
        return task_label
    parts: list[str] = []
    if task_type:
        parts.append(humanize_snake_label(task_type))
    if current_card:
        parts.append(current_card)
    if source_id:
        parts.append(f"from {source_id}")
    return " ".join(parts).strip()


def describe_learning_notification_queue(snapshot: dict[str, Any]) -> str:
    queue_length = int(snapshot.get("queue_length") or 0)
    running_count = int(snapshot.get("running_count") or 0)
    failed_count = int(snapshot.get("failed_count") or 0)
    parts: list[str] = []
    if queue_length > 0:
        parts.append(f"{format_compact_count(queue_length)} waiting")
    else:
        parts.append("clear")
    if running_count > 0:
        parts.append(f"{format_compact_count(running_count)} running")
    if failed_count > 0:
        parts.append(f"{format_compact_count(failed_count)} failed")
    return "Queue is " + ", ".join(parts) + "."


def build_learning_notification_payload(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = build_learning_notification_snapshot(training_status, learning_status)
    baseline = previous_snapshot if isinstance(previous_snapshot, dict) else None

    previous_verified = int(baseline.get("verified_dossiers") or 0) if baseline else 0
    previous_coverage = (
        round(float(baseline.get("verified_coverage_percent") or 0.0), 1) if baseline else 0.0
    )
    verified_delta = (snapshot["verified_dossiers"] - previous_verified) if baseline else 0
    coverage_delta = (
        round(snapshot["verified_coverage_percent"] - previous_coverage, 1) if baseline else 0.0
    )
    processed_delta = (
        (snapshot["processed_count"] - int(baseline.get("processed_count") or 0)) if baseline else 0
    )
    source_success_delta = (
        (snapshot["source_success_count"] - int(baseline.get("source_success_count") or 0))
        if baseline
        else 0
    )
    image_success_delta = (
        (snapshot["image_success_count"] - int(baseline.get("image_success_count") or 0))
        if baseline
        else 0
    )
    error_delta = (
        (
            snapshot["source_error_count"]
            + snapshot["image_error_count"]
            - int(baseline.get("source_error_count") or 0)
            - int(baseline.get("image_error_count") or 0)
        )
        if baseline
        else 0
    )

    engine_state = describe_learning_notification_engine_state(snapshot)
    task_summary = describe_learning_notification_task(snapshot)
    queue_summary = describe_learning_notification_queue(snapshot)
    meaningful_gain = bool(baseline) and (verified_delta > 0 or coverage_delta > 0)
    api_permission_required = bool(learning_status.get("api_permission_events"))

    if api_permission_required:
        title = "Miru: API permission required"
        first_sentence = "A source requires API or permission; Miru has not used it automatically. Check the Dev page."
        coverage_sentence = f"Verified coverage is {snapshot['verified_coverage_percent']:.1f}%."
    elif meaningful_gain:
        title = "Miru learning improved"
        if verified_delta > 0:
            first_sentence = (
                f"Miru verified +{format_compact_count(verified_delta)} card dossier"
                f"{'' if verified_delta == 1 else 's'} since the last report."
            )
        else:
            first_sentence = "Miru improved verified coverage since the last report."
        coverage_sentence = f"Coverage rose from {previous_coverage:.1f}% to {snapshot['verified_coverage_percent']:.1f}%."
    elif not baseline:
        title = "Miru learning snapshot"
        first_sentence = f"Miru currently has {format_compact_count(snapshot['verified_dossiers'])} verified card dossiers."
        coverage_sentence = f"Verified coverage is {snapshot['verified_coverage_percent']:.1f}%."
    elif snapshot.get("last_error") or engine_state == "stuck" or error_delta > 0:
        title = "Miru may be stuck"
        first_sentence = "Miru retried work but did not add new verified learning this cycle."
        coverage_sentence = (
            f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
        )
    elif engine_state == "idle" and snapshot["queue_length"] <= 0:
        title = "Miru is idle"
        first_sentence = "Miru is online but idle. No queued learning work right now."
        coverage_sentence = f"Verified coverage is {snapshot['verified_coverage_percent']:.1f}%."
    elif engine_state == "searching":
        title = "Miru is searching"
        first_sentence = "Miru scanned sources but has not added new verified learning yet."
        coverage_sentence = (
            f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
        )
    elif engine_state == "checking images":
        title = "Miru is checking images"
        first_sentence = "Miru processed image work but has not added new verified learning yet."
        coverage_sentence = (
            f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
        )
    elif (
        processed_delta > 0
        or source_success_delta > 0
        or image_success_delta > 0
        or snapshot["queue_length"] > 0
    ):
        title = "Miru is working"
        first_sentence = "Miru is active but has not added new verified learning yet."
        coverage_sentence = (
            f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
        )
    else:
        title = "Miru learning steady"
        first_sentence = "Miru has not added meaningful new verified learning this cycle."
        coverage_sentence = (
            f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
        )

    engine_sentence = f"Engine is {engine_state}" + (
        f" on {task_summary}." if task_summary and engine_state not in {"idle", "stuck"} else "."
    )
    if engine_state == "stuck" and snapshot.get("last_error"):
        engine_sentence = f"Engine is stuck. {str(snapshot['last_error'])[:120]}."

    message = " ".join(
        part for part in (first_sentence, coverage_sentence, queue_summary, engine_sentence) if part
    )
    return {
        "title": title,
        "message": message.strip(),
        "meaningful_gain": meaningful_gain,
        "api_permission_required": api_permission_required,
        "engine_state": engine_state,
        "queue_summary": queue_summary,
        "task_summary": task_summary,
        "verified_delta": verified_delta if baseline else None,
        "coverage_delta": coverage_delta if baseline else None,
        "snapshot": snapshot,
        "has_baseline": bool(baseline),
    }


def describe_learning_engine_phase(learning_status: dict[str, Any]) -> dict[str, Any]:
    current_state = str(learning_status.get("current_state") or "").strip().lower()
    current_source_id = str(learning_status.get("current_source_id") or "").strip()
    current_image_task = str(learning_status.get("current_image_task") or "").strip()
    task_label = str(learning_status.get("current_task_label") or "").strip()
    task_type = str(learning_status.get("current_task_type") or "").strip().lower()
    last_error = str(learning_status.get("last_error") or "").strip()
    last_heartbeat = str(learning_status.get("last_heartbeat") or "").strip()
    queue_length = int(learning_status.get("queue_length") or 0)
    running_count = int(learning_status.get("running_count") or 0)
    processed_count = int(learning_status.get("processed_count") or 0)
    dossier_count = int(learning_status.get("dossier_count") or 0)
    status_db_exists = bool(learning_status.get("status_db_exists"))

    updated_at = last_heartbeat or current_timestamp()
    if current_state == "error" and last_error:
        return {
            "key": "storm_warning",
            "title": "Needs Attention",
            "description": "The learning engine hit a recent problem and may need operator help.",
            "detail": last_error,
            "visual": "storm",
            "updated_at": updated_at,
            "active_runs": 0,
        }

    if current_state == "starting":
        return {
            "key": "setting_sail",
            "title": "Starting Up",
            "description": "Miru is bringing the learning engine online.",
            "detail": "Preparing the queue and learning stores for live work.",
            "visual": "sailing",
            "updated_at": updated_at,
            "active_runs": max(running_count, 0),
        }

    if current_state in {"processing", "running"} or running_count > 0:
        title, description = classify_learning_task(
            task_type, current_image_task=current_image_task
        )
        return {
            "key": "gathering_crew",
            "title": title,
            "description": description,
            "detail": build_learning_task_detail(
                task_label=task_label,
                task_type=task_type,
                current_source_id=current_source_id,
                current_image_task=current_image_task,
                queue_length=queue_length,
            ),
            "visual": "crew",
            "updated_at": updated_at,
            "active_runs": max(running_count, 1),
        }

    if current_state in {"sleeping", "idle"}:
        if queue_length > 0:
            return {
                "key": "setting_sail",
                "title": "Processing Queue",
                "description": "Miru is online with more learning work lined up.",
                "detail": f"{format_count(queue_length)} queued task{'s' if queue_length != 1 else ''} waiting to run.",
                "visual": "sailing",
                "updated_at": updated_at,
                "active_runs": 0,
            }
        return {
            "key": "sleeping",
            "title": "Idle",
            "description": "Miru is online but not working on a learning task right now.",
            "detail": (
                f"Queue clear. {format_count(dossier_count)} learning dossiers tracked."
                if (processed_count > 0 or dossier_count > 0)
                else "No live learning task is running."
            ),
            "visual": "sleeping",
            "updated_at": updated_at,
            "active_runs": 0,
        }

    if last_error:
        return {
            "key": "storm_warning",
            "title": "Needs Attention",
            "description": "The learning engine reported a recent problem.",
            "detail": last_error,
            "visual": "storm",
            "updated_at": updated_at,
            "active_runs": 0,
        }

    if status_db_exists or queue_length > 0 or processed_count > 0 or dossier_count > 0:
        return {
            "key": "setting_sail",
            "title": "Running",
            "description": "Miru's learning engine is online and tracking structured card knowledge.",
            "detail": (
                f"Queue: {format_count(queue_length)} waiting | "
                f"Processed: {format_count(processed_count)} | "
                f"Dossiers: {format_count(dossier_count)}"
            ),
            "visual": "sailing",
            "updated_at": updated_at,
            "active_runs": 0,
        }

    return {
        "key": "sleeping",
        "title": "Offline",
        "description": "Miru's learning engine is not reporting live status yet.",
        "detail": "No live learning status has been recorded yet.",
        "visual": "sleeping",
        "updated_at": updated_at,
        "active_runs": 0,
    }


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
        if (
            snapshot.get("last_error")
            and recent_error_age is not None
            and recent_error_age <= ACTIVITY_RECENT_WINDOW_SECONDS
        ):
            current_key = "storm_warning"
            detail = str(snapshot["last_error"])
        elif training_status["dossiers_created"] > 0 and training_status["remaining_gaps"] > 0:
            current_key = "setting_sail"
            detail = training_status["remaining_summary"]

    current = next(
        (item for item in DEV_ACTIVITY_BLUEPRINT if item["key"] == current_key),
        DEV_ACTIVITY_BLUEPRINT[0],
    )
    return {
        "key": current_key,
        "title": current["title"],
        "description": current["description"],
        "detail": detail,
        "visual": current["visual"],
        "updated_at": snapshot.get("last_finished_at")
        or snapshot.get("last_started_at")
        or current_timestamp(),
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
            return {
                "percent": float(memory.percent),
                "used": int(memory.used),
                "total": int(memory.total),
            }
        except Exception:
            return None
    if os.name == "nt":

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [  # noqa: RUF012
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
        return {
            "percent": float(status.dwMemoryLoad),
            "used": used,
            "total": int(status.ullTotalPhys),
        }
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
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
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
    return {
        "percent": percent,
        "memory_used_mb": memory_used_mb,
        "memory_total_mb": memory_total_mb,
    }


def build_resource_metrics() -> list[dict[str, Any]]:
    cpu_percent = sample_cpu_usage()
    memory = sample_memory_usage()
    gpu = sample_gpu_usage()
    disk_total, disk_used, disk_free = shutil.disk_usage(str(PROJECT_ROOT.anchor or PROJECT_ROOT))
    return [
        {
            "key": "cpu",
            "label": "CPU",
            "value": (f"{cpu_percent:.1f}%" if cpu_percent is not None else "Unavailable"),
            "detail": (
                "Current processor use."
                if cpu_percent is not None
                else "CPU usage is unavailable on this machine."
            ),
            "percent": cpu_percent if cpu_percent is not None else 0.0,
            "available": cpu_percent is not None,
        },
        {
            "key": "memory",
            "label": "Memory",
            "value": (
                f"{format_bytes(int(memory['used']))} / {format_bytes(int(memory['total']))}"
                if memory
                else "Unavailable"
            ),
            "detail": (
                f"{float(memory['percent']):.1f}% in use."
                if memory
                else "Memory usage is unavailable on this machine."
            ),
            "percent": float(memory["percent"]) if memory else 0.0,
            "available": memory is not None,
        },
        {
            "key": "gpu",
            "label": "GPU",
            "value": f"{float(gpu['percent']):.1f}%" if gpu else "Unavailable",
            "detail": (
                f"{int(gpu['memory_used_mb'])} MB of {int(gpu['memory_total_mb'])} MB used."
                if gpu
                else "GPU stats are unavailable on this machine."
            ),
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


def _hub_pipeline_counts() -> dict[str, Any]:
    """Read pipeline table counts from the catalog DB for the hub dashboard."""
    out: dict[str, Any] = {
        "image_variant_analysis_count": 0,
        "market_prices_count": 0,
        "review_queue_count": 0,
        "publication_stage_count": 0,
    }
    db_path = FALLBACK_CATALOG_DB_PATH
    if not db_path.is_file():
        return out
    try:
        with closing(sqlite3.connect(str(db_path), timeout=5)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for key, table in (
                ("image_variant_analysis_count", "image_variant_analysis"),
                ("market_prices_count", "market_prices"),
                ("review_queue_count", "miru_review_queue"),
                ("publication_stage_count", "miru_publication_stage"),
            ):
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()
                    out[key] = int(row[0]) if row else 0
                except sqlite3.Error:
                    pass
    except sqlite3.Error:
        pass
    return out


def build_hub_summary_payload() -> dict[str, Any]:
    """Aggregate all hub dashboard metrics from existing data sources."""
    training = build_training_status()
    catalog_status = ensure_fallback_catalog_status()
    resources = build_resource_metrics()
    throughput = op01_throughput_stats()
    issues = runtime_issue_messages()
    pipeline = _hub_pipeline_counts()

    return {
        "catalog": {
            "total_cards": int(catalog_status.get("cards", 0)),
            "total_variants": int(catalog_status.get("variants", 0)),
            "usable": bool(catalog_status.get("usable")),
        },
        "dossier": {
            "dossiers_created": int(training.get("dossiers_created", 0)),
            "verified_dossiers": int(training.get("verified_dossiers", 0)),
            "remaining_gaps": int(training.get("remaining_gaps", 0)),
            "verified_coverage_percent": float(training.get("verified_coverage_percent", 0)),
            "dossier_coverage_percent": float(training.get("dossier_coverage_percent", 0)),
        },
        "pipeline": pipeline,
        "throughput": {
            "today_reviews": int(throughput.get("today_reviews", 0)),
            "total_reviews": int(throughput.get("total_reviews", 0)),
            "distinct_cards_reviewed": int(throughput.get("distinct_cards_reviewed", 0)),
        },
        "resources": resources,
        "health": {
            "issues": issues,
            "issue_count": len(issues),
        },
        "server_started_at": _SERVER_STARTED_AT,
        "updated_at": current_timestamp(),
    }


def build_route_url(path: str) -> str:
    route_path = path if path.startswith("/") else f"/{path}"
    return f"{request.url_root.rstrip('/')}" + route_path


def build_companion_url(port: int, path: str = "/") -> str:
    route_path = path if path.startswith("/") else f"/{path}"
    host = request.host.split(":", 1)[0]
    return f"{request.scheme}://{host}:{port}{route_path}"


def inspect_local_http_route(url: str, *, timeout_seconds: float = 0.35) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        req = Request(url, headers={"User-Agent": "Miru-Runtime-Probe/1"})
        with closing(urlopen(req, timeout=timeout_seconds)) as response:
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
            return {
                "reachable": True,
                "status_code": int(response.getcode() or 200),
                "detail": f"HTTP {int(response.getcode() or 200)}",
                "timeout_seconds": timeout_seconds,
                "elapsed_ms": elapsed_ms,
            }
    except HTTPError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return {
            "reachable": True,
            "status_code": int(exc.code),
            "detail": f"HTTP {int(exc.code)}",
            "timeout_seconds": timeout_seconds,
            "elapsed_ms": elapsed_ms,
        }
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return {
            "reachable": False,
            "status_code": 0,
            "detail": f"{exc.__class__.__name__}: {reason}",
            "timeout_seconds": timeout_seconds,
            "elapsed_ms": elapsed_ms,
        }


def build_runtime_status_payload() -> dict[str, Any]:
    fast_probe = inspect_local_http_route(
        f"http://127.0.0.1:{PROJECT_MIRU_DEV_PORT}/", timeout_seconds=0.35
    )
    confirm_probe: dict[str, Any] | None = None
    project_state = "confirmed_healthy"
    project_certainty = "confirmed"
    project_detail = str(fast_probe.get("detail") or "")
    project_status_value = "ok"
    fast_ok = bool(fast_probe.get("reachable")) and int(fast_probe.get("status_code") or 0) == 200
    if fast_ok:
        project_detail = (
            f"Fast runtime probe returned HTTP 200 in {fast_probe.get('elapsed_ms', 0)}ms."
        )
    else:
        confirm_probe = inspect_local_http_route(
            f"http://127.0.0.1:{PROJECT_MIRU_DEV_PORT}/", timeout_seconds=1.5
        )
        confirm_ok = (
            bool(confirm_probe.get("reachable"))
            and int(confirm_probe.get("status_code") or 0) == 200
        )
        if confirm_ok:
            project_state = "direct_check_healthy"
            project_certainty = "degraded"
            project_status_value = "ok"
            project_detail = (
                "Fast runtime probe was inconclusive, but a confirmatory direct check returned HTTP 200 "
                f"in {confirm_probe.get('elapsed_ms', 0)}ms."
            )
        else:
            project_state = "confirmed_unhealthy"
            project_certainty = "confirmed"
            project_status_value = "unhealthy"
            project_detail = str((confirm_probe or fast_probe).get("detail") or "")
    main_port = int(PROJECT_MIRU_PORT)
    main_probe = inspect_local_http_route(f"http://127.0.0.1:{main_port}/", timeout_seconds=0.45)
    main_code = int(main_probe.get("status_code") or 0)
    main_ok = bool(main_probe.get("reachable")) and main_code in (
        200,
        204,
        301,
        302,
        304,
        403,
        404,
    )
    main_site_value = "ok" if main_ok else "unhealthy"

    payload: dict[str, Any] = {
        "18765": "ok",
        "18080": project_status_value,
        str(main_port): main_site_value,
        "worktree": True,
        "project_detail": project_detail,
        "project_probe_state": project_state,
        "project_probe_certainty": project_certainty,
        "project_fast_probe": fast_probe,
        "project_confirm_probe": confirm_probe or {},
        "main_site_probe": main_probe,
        "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return payload


def load_runtime_status_payload(*, force: bool = False) -> dict[str, Any]:
    if force:
        return build_runtime_status_payload()
    return get_ttl_cached_value(
        "runtime_status_payload",
        ttl_seconds=5.0,
        signature=(PROJECT_MIRU_DEV_PORT, PROJECT_MIRU_PORT),
        builder=build_runtime_status_payload,
    )


def build_issue_card(
    label: str, issues: list[str], *, ok_detail: str, warn_detail: str
) -> dict[str, Any]:
    if issues:
        return {
            "label": label,
            "status": issues[0],
            "tone": "warn",
            "detail": warn_detail,
            "items": issues,
        }
    return {
        "label": label,
        "status": "No issues detected",
        "tone": "good",
        "detail": ok_detail,
        "items": [],
    }


def build_learning_engine_activity(
    learning_status: dict[str, Any],
    fallback_activity: dict[str, Any],
) -> dict[str, Any]:
    phase = describe_learning_engine_phase(learning_status)
    if not phase.get("updated_at"):
        phase["updated_at"] = fallback_activity.get("updated_at") or current_timestamp()
    return phase


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
    if learning_status.get("status_db_exists") and learning_status.get("last_error"):
        miru_issues.append("Learning engine reported an error")
    for ev in learning_status.get("api_permission_events") or []:
        msg = ev.get("message") or ev.get("event_type") or "API/permission event"
        miru_issues.append(f"Learner: {msg}")
    for warning in (learning_status.get("snapshot_inputs") or {}).get("required_warnings") or []:
        w = str(warning or "").strip()
        if w:
            miru_issues.append(w)

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


def build_learning_engine_metrics(
    learning_status: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build learning-engine-only metrics (queue, throughput, sidecar) with clear keys and labels."""
    return [
        {
            "key": "queue_queued_count",
            "label": "Queue: waiting",
            "value": format_count(learning_status.get("queue_length", 0)),
            "detail": "Learning tasks waiting in the queue.",
        },
        {
            "key": "queue_running_count",
            "label": "Queue: running",
            "value": format_count(learning_status.get("running_count", 0)),
            "detail": "Tasks currently running.",
        },
        {
            "key": "queue_failed_count",
            "label": "Queue: failed",
            "value": format_count(learning_status.get("failed_count", 0)),
            "detail": "Tasks that permanently failed.",
        },
        {
            "key": "queue_completed_count",
            "label": "Queue: completed",
            "value": format_count(learning_status.get("completed_count", 0)),
            "detail": "Tasks completed (all time).",
        },
        {
            "key": "queue_backlog",
            "label": "Queue backlog",
            "value": format_count(learning_status.get("queue_backlog", 0)),
            "detail": "Queued learning work still waiting to run.",
        },
        {
            "key": "parallel_workers",
            "label": "Parallel validations",
            "value": format_count(learning_status.get("max_parallel_validations", 1)),
            "detail": "Configured safe concurrency limit for validation work.",
        },
        {
            "key": "engine_processed_count",
            "label": "Tasks run",
            "value": format_count(learning_status.get("processed_count", 0)),
            "detail": "Total task attempts processed.",
        },
        {
            "key": "engine_success_count",
            "label": "Succeeded",
            "value": format_count(learning_status.get("success_count", 0)),
            "detail": "Tasks completed without error.",
        },
        {
            "key": "engine_error_count",
            "label": "Failed",
            "value": format_count(learning_status.get("error_count", 0)),
            "detail": "Task attempts that ended in error.",
        },
        {
            "key": "validated_cards_total",
            "label": "Validated cards",
            "value": format_count(learning_status.get("validated_card_count", 0)),
            "detail": "Cards that completed the verified field validation step.",
        },
        {
            "key": "cards_learned_per_hour",
            "label": "Cards learned/hr",
            "value": format_count(learning_status.get("cards_learned_per_hour", 0)),
            "detail": "Verified cards completed during the last rolling hour.",
        },
        {
            "key": "validation_success_rate",
            "label": "Validation success",
            "value": f"{float(learning_status.get('validation_success_rate', 0.0)):.1f}%",
            "detail": "Share of validation tasks that finished successfully.",
        },
        {
            "key": "average_validation_seconds",
            "label": "Avg validation time",
            "value": f"{float(learning_status.get('average_validation_seconds', 0.0)):.2f}s",
            "detail": "Average runtime for completed validation tasks.",
        },
        {
            "key": "engine_source_success_count",
            "label": "Source: success",
            "value": format_count(learning_status.get("source_success_count", 0)),
            "detail": "Source-backed tasks completed successfully.",
        },
        {
            "key": "engine_source_error_count",
            "label": "Source: errors",
            "value": format_count(learning_status.get("source_error_count", 0)),
            "detail": "Source-backed tasks that ended in error.",
        },
        {
            "key": "engine_image_success_count",
            "label": "Images: success",
            "value": format_count(learning_status.get("image_success_count", 0)),
            "detail": "Image ingestion tasks completed successfully.",
        },
        {
            "key": "engine_image_error_count",
            "label": "Images: errors",
            "value": format_count(learning_status.get("image_error_count", 0)),
            "detail": "Image ingestion tasks that ended in error.",
        },
        {
            "key": "discovery_candidates",
            "label": "Source candidates",
            "value": format_count(learning_status.get("discovery_candidate_count", 0)),
            "detail": "Potential source sites discovered locally.",
        },
        {
            "key": "discovery_pending_review",
            "label": "Source review queue",
            "value": format_count(learning_status.get("discovery_pending_review_count", 0)),
            "detail": "Discovered source candidates still waiting for operator review.",
        },
        {
            "key": "approved_sources_loaded",
            "label": "Approved sources (config)",
            "value": format_count(learning_status.get("approved_sources_loaded_count", 0)),
            "detail": "Sources loaded from worktree config/miru_approved_sources.json (allowlist).",
        },
        {
            "key": "approved_sources_config_errors",
            "label": "Config load errors",
            "value": format_count(len(learning_status.get("approved_sources_config_errors") or [])),
            "detail": "Invalid or skipped entries in approved-sources config (see status payload for messages).",
        },
        {
            "key": "sidecar_dossiers_count",
            "label": "Sidecar dossiers",
            "value": format_count(learning_status.get("dossier_count", 0)),
            "detail": "Profiles in the learning engine store.",
        },
        {
            "key": "dossier_verified_count",
            "label": "Dossiers: verified",
            "value": format_count(learning_status.get("dossier_verified_count", 0)),
            "detail": "Learning dossiers with verification_state = verified.",
        },
        {
            "key": "dossier_source_backed_count",
            "label": "Dossiers: source-backed",
            "value": format_count(learning_status.get("dossier_source_backed_count", 0)),
            "detail": "Learning dossiers with verification_state = source-backed (used for insight sync).",
        },
        {
            "key": "sidecar_images_tracked_count",
            "label": "Sidecar images: tracked",
            "value": format_count(learning_status.get("images_tracked", 0)),
            "detail": "Image registry entries tracked.",
        },
        {
            "key": "sidecar_images_verified_count",
            "label": "Sidecar images: verified",
            "value": format_count(learning_status.get("images_verified", 0)),
            "detail": "Image registry entries marked verified.",
        },
        {
            "key": "sidecar_images_missing_count",
            "label": "Sidecar images: missing",
            "value": format_count(learning_status.get("images_missing", 0)),
            "detail": "Catalog cards missing a sidecar image entry.",
        },
        {
            "key": "last_image_update",
            "label": "Last image update",
            "value": learning_status.get("last_image_update") or "—",
            "detail": "Most recent image ingestion update timestamp.",
        },
    ]


def format_relative_age(seconds_value: float | None) -> str:
    if seconds_value is None:
        return "time unavailable"
    seconds_int = max(int(seconds_value), 0)
    if seconds_int < 60:
        return f"{seconds_int}s ago"
    minutes = seconds_int // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def load_monitor_validation_counts(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
    recent_window_seconds: int = 3600,
) -> dict[str, int]:
    snapshot = {
        "miru_validations_count": 0,
        "recent_validation_writes_count": 0,
    }
    path = Path(project_db_path)
    if not path.is_file():
        return snapshot

    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - max(recent_window_seconds, 0))
    )
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    SUM(
                        CASE
                            WHEN trim(coalesce(verified_at, '')) != ''
                             AND verified_at >= ?
                            THEN 1
                            ELSE 0
                        END
                    ) AS recent_rows
                FROM miru_validations
                """,
                (cutoff,),
            ).fetchone()
    except sqlite3.Error:
        return snapshot

    if row is not None:
        snapshot["miru_validations_count"] = int(row["total_rows"] or 0)
        snapshot["recent_validation_writes_count"] = int(row["recent_rows"] or 0)
    return snapshot


def build_monitor_activity_item_from_log(row: sqlite3.Row) -> dict[str, Any] | None:
    event_type = str(row["event_type"] or "").strip().lower()
    task_type = str(row["task_type"] or "").strip().lower()
    card_code = str(row["card_code"] or "").strip().upper()
    message = str(row["message"] or "").strip()
    created_at = str(row["created_at"] or "").strip()

    if event_type == "card_synced":
        return {
            "kind": event_type,
            "title": "Card synced",
            "detail": message or f"{card_code} reached Project Miru.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "good",
        }
    if event_type == "card_sync_queued":
        return {
            "kind": event_type,
            "title": "Sync queued",
            "detail": message or f"{card_code} is waiting to reach Project Miru.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "neutral",
        }
    if event_type == "card_sync_failed":
        return {
            "kind": event_type,
            "title": "Sync failed",
            "detail": message or f"{card_code} failed to reach Project Miru.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "warn",
        }
    if event_type == "task_completed":
        task_label = humanize_snake_label(task_type) or "Task"
        return {
            "kind": event_type,
            "title": f"{task_label} completed",
            "detail": message or "The task completed successfully.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "good",
        }
    if event_type == "task_failed":
        task_label = humanize_snake_label(task_type) or "Task"
        return {
            "kind": event_type,
            "title": f"{task_label} failed",
            "detail": message or "The task ended in error.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "warn",
        }
    if event_type == "task_started":
        task_label = humanize_snake_label(task_type) or "Task"
        return {
            "kind": event_type,
            "title": f"{task_label} started",
            "detail": message or "Miru started a new learning task.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "neutral",
        }
    if event_type == "engine_started":
        return {
            "kind": event_type,
            "title": "Engine started",
            "detail": message or "Continuous learning started.",
            "timestamp": created_at,
            "card_code": "",
            "tone": "neutral",
        }
    if event_type == "engine_stopped":
        return {
            "kind": event_type,
            "title": "Engine stopped",
            "detail": message or "Continuous learning stopped.",
            "timestamp": created_at,
            "card_code": "",
            "tone": "warn",
        }
    if event_type in {"source_not_registered", "source_not_in_registry"}:
        return {
            "kind": event_type,
            "title": "Blocked: source not registered",
            "detail": message or "Miru ignored a source that is not in the allowed registry.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "warn",
        }
    if event_type in {
        "api_required_source_detected",
        "access_policy_unclear",
        "permission_required",
    }:
        return {
            "kind": event_type,
            "title": "API permission required",
            "detail": message
            or "A source requires API or permission; Miru did not use it automatically.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "warn",
        }
    if event_type == "publish_blocked":
        return {
            "kind": event_type,
            "title": "Publish blocked by mode",
            "detail": message or "Miru did not publish; mode is dry run or review required.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "neutral",
        }
    if event_type == "publish_success":
        return {
            "kind": event_type,
            "title": "Queued for publish",
            "detail": message or f"{card_code} queued for Project Miru.",
            "timestamp": created_at,
            "card_code": card_code,
            "tone": "good",
        }
    if event_type == "seed_queue":
        return {
            "kind": event_type,
            "title": "Queue seeded",
            "detail": message or "Learning tasks were added to the queue.",
            "timestamp": created_at,
            "card_code": "",
            "tone": "neutral",
        }
    # Fallback: include all events for control room visibility
    title = humanize_snake_label(event_type) or event_type or "Event"
    return {
        "kind": event_type,
        "title": title,
        "detail": message or "Miru activity.",
        "timestamp": created_at,
        "card_code": card_code,
        "tone": "neutral",
    }


def load_pending_approvals(
    *,
    status_db_path: Path = LEARNING_STATUS_DB_PATH,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Load items from learner_review_queue (review_required). Worktree-only; no schema change."""
    path = Path(status_db_path)
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, card_code, source_id, confidence, reason, created_at
                FROM learner_review_queue
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": int(row["id"]),
            "card_code": str(row["card_code"] or "").strip(),
            "source_id": str(row["source_id"] or "").strip(),
            "task_type": "verify_official_fields",
            "confidence": float(row["confidence"] or 0.0),
            "reason": str(row["reason"] or "").strip(),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def load_publication_review_queue(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load pending operator approval/review items from miru_review_queue (catalog DB)."""
    path = Path(project_db_path)
    if not path.is_file():
        return []
    limit = max(int(limit), 1)
    sql = """
        SELECT
            rq.item_key,
            rq.target_id AS card_code,
            rq.status,
            rq.approval_state,
            rq.readiness_state,
            rq.review_reason,
            rq.guardrail_label,
            rq.confidence_score,
            rq.risk_level,
            rq.recommended_next_step,
            rq.summary_text,
            rq.updated_at,
            rq.created_at,
            c.card_name,
            c.set_code,
            c.set_name
        FROM miru_review_queue rq
        INNER JOIN cards c ON upper(trim(c.canonical_code)) = upper(trim(rq.target_id))
        INNER JOIN card_intelligence ci ON ci.card_id = c.id
        WHERE rq.status = 'pending'
          AND lower(trim(coalesce(ci.publish_status, ''))) = 'publish_requires_review'
        ORDER BY
            CASE WHEN rq.approval_state = 'pending_review' THEN 0
                 WHEN rq.approval_state = '' THEN 1
                 ELSE 2
            END,
            rq.confidence_score DESC,
            rq.updated_at DESC
        LIMIT ?
    """
    try:
        with closing(sqlite3.connect(str(path))) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (limit,)).fetchall()
    except sqlite3.Error:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["card_code"] or "").strip().upper()
        preview_src = (
            str(row["summary_text"] or "").strip() or str(row["review_reason"] or "").strip()
        )
        preview = preview_src.replace("\n", " ").strip()
        if len(preview) > 220:
            preview = preview[:217] + "…"
        conf: float
        try:
            conf = float(row["confidence_score"] or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0
        out.append(
            {
                "queue_kind": "publication",
                "item_key": str(row["item_key"] or "").strip(),
                "card_code": code,
                "status": str(row["status"] or "").strip(),
                "approval_state": str(row["approval_state"] or "").strip(),
                "readiness_state": str(row["readiness_state"] or "").strip(),
                "review_reason": str(row["review_reason"] or "").strip(),
                "guardrail_label": str(row["guardrail_label"] or "").strip(),
                "confidence_score": conf,
                "risk_level": str(row["risk_level"] or "").strip(),
                "recommended_next_step": str(row["recommended_next_step"] or "").strip(),
                "summary_text": str(row["summary_text"] or "").strip(),
                "updated_at": str(row["updated_at"] or "").strip(),
                "created_at": str(row["created_at"] or "").strip(),
                "card_name": str(row["card_name"] or "").strip(),
                "set_code": str(row["set_code"] or "").strip(),
                "set_name": str(row["set_name"] or "").strip(),
                "id": code,
                "insight_type": str(row["guardrail_label"] or "").strip() or "—",
                "confidence": conf,
                "insight_preview": preview or "—",
            }
        )
    return out


_TASK_QUEUE_LOCK = Lock()


def _task_queue_path() -> Path:
    return PROJECT_ROOT / "data" / "task_queue.json"


def _read_task_queue_items() -> list[dict[str, Any]]:
    path = _task_queue_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def _write_task_queue_items(items: list[dict[str, Any]]) -> None:
    path = _task_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, sort_keys=False), encoding="utf-8")


def count_publication_review_rows(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
) -> int:
    path = Path(project_db_path).resolve()
    if not path.is_file():
        return 0
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM card_intelligence
                WHERE lower(trim(coalesce(publish_status, ''))) = 'publish_requires_review'
                """
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row is not None else 0


def compute_catalog_publish_pulse_coverage_percent(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
) -> float | None:
    """Live % for /dev System Pulse: distinct card_intelligence.card_id in publish pipeline / 2527."""
    path = Path(project_db_path).resolve()
    if not path.is_file():
        return None
    sql = """
        SELECT COUNT(DISTINCT card_id) AS n
        FROM card_intelligence
        WHERE coalesce(publish_status, '') != ''
          AND publish_status IN (
            'publish_ready',
            'approved_for_candidate',
            'publish_requires_review',
            'publish_blocked'
          )
    """
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            row = conn.execute(sql).fetchone()
        n = int(row[0] or 0) if row else 0
        return round((n / 2527.0) * 100, 1)
    except sqlite3.Error:
        return None


def update_publication_review_status(
    card_code: str,
    new_status: str,
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
    require_review_state: bool = True,
) -> bool:
    """
    Set card_intelligence.publish_status for one card. Only publication field is written.
    When require_review_state is True, only updates rows currently in publish_requires_review.
    """
    code = str(card_code or "").strip().upper()
    status = str(new_status or "").strip()
    if not code or status not in {"publish_ready", "publish_deferred"}:
        return False
    path = Path(project_db_path)
    if not path.is_file():
        return False
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    review_clause = (
        "AND lower(trim(coalesce(publish_status, ''))) = 'publish_requires_review'"
        if require_review_state
        else ""
    )
    try:
        with closing(sqlite3.connect(path)) as conn:
            cur = conn.execute(
                f"""
                UPDATE card_intelligence
                SET publish_status = ?, publish_updated_at = ?
                WHERE card_id = (SELECT id FROM cards WHERE upper(trim(canonical_code)) = ? LIMIT 1)
                {review_clause}
                """,
                (status, ts, code),
            )
            conn.commit()
            return bool(cur.rowcount and cur.rowcount > 0)
    except sqlite3.Error:
        return False


def dismiss_image_variant_sp_operator_review(
    card_code: str,
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
) -> bool:
    """
    Dismiss SP-label triage for a card when there is no publication row to defer or the
    review-queue row is missing. Only ``review_status`` on ``image_variant_analysis`` is
    updated (to ``reviewed_not_sp``).
    """
    code = str(card_code or "").strip().upper()
    if not code:
        return False
    path = Path(project_db_path).resolve()
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path)) as conn:
            cur = conn.execute(
                """
                UPDATE image_variant_analysis
                SET review_status = 'reviewed_not_sp'
                WHERE upper(trim(canonical_code)) = ?
                  AND COALESCE(sp_marker_detected, 0) = 1
                  AND lower(trim(review_status)) IN ('queued_sp_review', 'review_required')
                """,
                (code,),
            )
            conn.commit()
            return bool(cur.rowcount and cur.rowcount > 0)
    except sqlite3.Error:
        return False


def approve_all_publication_review(
    *,
    project_db_path: Path = FALLBACK_CATALOG_DB_PATH,
) -> int:
    """Move every publish_requires_review row to publish_ready. Returns rows updated."""
    path = Path(project_db_path)
    if not path.is_file():
        return 0
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with closing(sqlite3.connect(path)) as conn:
            cur = conn.execute(
                """
                UPDATE card_intelligence
                SET publish_status = 'publish_ready', publish_updated_at = ?
                WHERE lower(trim(coalesce(publish_status, ''))) = 'publish_requires_review'
                """,
                (ts,),
            )
            conn.commit()
            return int(cur.rowcount or 0)
    except sqlite3.Error:
        return 0


def load_monitor_engine_events(
    *,
    status_db_path: Path = LEARNING_STATUS_DB_PATH,
    recent_window_seconds: int = 3600,
    churn_window_seconds: int = 1800,
    limit: int = 80,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "recent_activity": [],
        "recent_synced_cards_count": 0,
        "recent_failure_count": 0,
    }
    path = Path(status_db_path)
    if not path.is_file():
        return snapshot

    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT level, event_type, message, card_code, task_type, created_at
                FROM engine_log
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
    except sqlite3.Error:
        return snapshot

    items: list[dict[str, Any]] = []
    synced_count = 0
    failure_count = 0
    for row in rows:
        event_type = str(row["event_type"] or "").strip().lower()
        created_at = str(row["created_at"] or "").strip()
        if (
            event_type == "card_synced"
            and seconds_since(created_at) is not None
            and seconds_since(created_at) <= recent_window_seconds
        ):
            synced_count += 1
        if (
            event_type in {"task_failed", "card_sync_failed"}
            and seconds_since(created_at) is not None
            and seconds_since(created_at) <= churn_window_seconds
        ):
            failure_count += 1
        item = build_monitor_activity_item_from_log(row)
        if item is not None:
            items.append(item)

    snapshot["recent_activity"] = items
    snapshot["recent_synced_cards_count"] = synced_count
    snapshot["recent_failure_count"] = failure_count
    return snapshot


def fetch_remote_runtime_dev_status(
    url: str, *, timeout_seconds: float = 1.0
) -> dict[str, Any] | None:
    target = str(url or "").strip()
    if not target:
        return None
    try:
        with urlopen(target, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except (
        HTTPError,
        URLError,
        OSError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
    return payload if isinstance(payload, dict) else None


def cache_runtime_truth_snapshot(target: str, payload: dict[str, Any]) -> None:
    cache_key = str(target or "").strip()
    if not cache_key or not isinstance(payload, dict):
        return
    entry = {
        "payload": copy.deepcopy(payload),
        "fetched_at": time.time(),
    }
    with _RUNTIME_TRUTH_CACHE_LOCK:
        _RUNTIME_TRUTH_CACHE[cache_key] = entry


def load_cached_runtime_truth_snapshot(target: str) -> dict[str, Any] | None:
    cache_key = str(target or "").strip()
    if not cache_key:
        return None
    with _RUNTIME_TRUTH_CACHE_LOCK:
        entry = copy.deepcopy(_RUNTIME_TRUTH_CACHE.get(cache_key) or {})
    return entry or None


def clear_runtime_truth_cache() -> None:
    """Clear the runtime truth cache so the next Dev status fetch hits the main runtime."""
    with _RUNTIME_TRUTH_CACHE_LOCK:
        _RUNTIME_TRUTH_CACHE.clear()


def build_runtime_truth_source_descriptor(
    *,
    mode: str,
    status_url: str,
    fetched_at: float | None = None,
) -> dict[str, Any]:
    age_label = ""
    if fetched_at is not None:
        age_seconds = max(time.time() - float(fetched_at), 0.0)
        age_label = format_relative_age(age_seconds)
    if mode == "main_runtime_live":
        label = "Live main runtime"
        detail = "Using the live main-runtime snapshot."
        if age_label and age_label != "just now":
            detail = f"Using the live main-runtime snapshot refreshed {age_label}."
    elif mode == "main_runtime_cached":
        label = "Cached main-runtime snapshot"
        detail = "Using the last good main-runtime snapshot while live access catches up."
        if age_label:
            detail = f"Using the last good main-runtime snapshot from {age_label} while live access catches up."
    else:
        label = "Local fallback"
        detail = (
            "Using local worktree status data because the main runtime is not currently reachable."
        )
    return {
        "mode": mode,
        "label": label,
        "detail": detail,
        "status_url": status_url,
        "fetched_at": fetched_at,
        "age_label": age_label,
    }


def fetch_truth_source_dev_status_result(
    *,
    summary_only: bool = False,
    include_heavy: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    remote_status_url = resolve_runtime_monitor_status_url()
    if not remote_status_url:
        return None
    query_parts: list[str] = []
    if summary_only:
        query_parts.append("view=summary")
    if include_heavy:
        query_parts.append("include=heavy")
    target = remote_status_url
    if query_parts:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{'&'.join(query_parts)}"

    cached_entry = load_cached_runtime_truth_snapshot(target)
    if cached_entry and not force_refresh:
        cached_age = max(time.time() - float(cached_entry.get("fetched_at") or 0.0), 0.0)
        fresh_cache_window = 8.0 if summary_only and not include_heavy else 4.0
        if cached_age <= fresh_cache_window:
            source = build_runtime_truth_source_descriptor(
                mode="main_runtime_live",
                status_url=target,
                fetched_at=cached_entry.get("fetched_at"),
            )
            payload = dict(cached_entry.get("payload") or {})
            if payload:
                payload["truth_source"] = dict(source)
                return {
                    "payload": payload,
                    "source": source,
                    "status_url": target,
                }

    if force_refresh:
        timeout_plan = (2.5, 6.0)
    elif summary_only and not include_heavy:
        timeout_plan = (1.0, 2.0)
    else:
        timeout_plan = (2.0, 4.5)
    live_payload = None
    for timeout_seconds in timeout_plan:
        live_payload = fetch_remote_runtime_dev_status(target, timeout_seconds=timeout_seconds)
        if live_payload:
            break
    if live_payload:
        cache_runtime_truth_snapshot(target, live_payload)
        source = build_runtime_truth_source_descriptor(
            mode="main_runtime_live",
            status_url=target,
            fetched_at=time.time(),
        )
        payload = dict(live_payload)
        payload["truth_source"] = dict(source)
        return {
            "payload": payload,
            "source": source,
            "status_url": target,
        }

    if cached_entry:
        source = build_runtime_truth_source_descriptor(
            mode="main_runtime_cached",
            status_url=target,
            fetched_at=cached_entry.get("fetched_at"),
        )
        payload = dict(cached_entry.get("payload") or {})
        if payload:
            payload["truth_source"] = dict(source)
            return {
                "payload": payload,
                "source": source,
                "status_url": target,
            }
    return None


def fetch_truth_source_dev_status(
    *,
    summary_only: bool = False,
    include_heavy: bool = False,
) -> dict[str, Any] | None:
    result = fetch_truth_source_dev_status_result(
        summary_only=summary_only,
        include_heavy=include_heavy,
    )
    return dict(result.get("payload") or {}) if result else None


def _ensure_dev_bootstrap_display_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload or {})
    out.setdefault(
        "activity",
        {
            "key": "status_trimmed",
            "title": "Status trimmed",
            "description": "The legacy monitor hero activity was removed from the live API payload.",
            "detail": "Use the activity feed and health cards for current runtime state.",
            "visual": "status",
        },
    )
    return out


def build_dev_bootstrap_status() -> dict[str, Any]:
    remote_status_url = resolve_runtime_monitor_status_url()
    if remote_status_url:
        target = f"{remote_status_url}{'&' if '?' in remote_status_url else '?'}view=summary"
        cached_entry = load_cached_runtime_truth_snapshot(target)
        if cached_entry:
            payload = dict(cached_entry.get("payload") or {})
            if payload:
                source = build_runtime_truth_source_descriptor(
                    mode="main_runtime_cached",
                    status_url=target,
                    fetched_at=cached_entry.get("fetched_at"),
                )
                payload["truth_source"] = dict(source)
                payload["dev_environment"] = build_dev_environment_descriptor()
                payload = ensure_governed_autopilot_payload(payload)
                _apply_worker_heartbeat_fallback(payload)
                return _ensure_dev_bootstrap_display_payload(
                    ensure_control_layer_payload(payload, force_runtime_probe=True)
                )

    training_status = build_training_status()
    signature = (
        path_signature(FALLBACK_CATALOG_DB_PATH),
        path_signature(DOSSIER_DB_PATH),
        path_signature(LEARNING_QUEUE_DB_PATH),
        path_signature(LEARNING_STATUS_DB_PATH),
        path_signature(LEARNING_DOSSIER_DB_PATH),
        path_signature(WORKER_LAST_RUN_PATH),
    )
    return get_ttl_cached_value(
        "dev_bootstrap_status_local",
        ttl_seconds=600.0,
        signature=signature,
        builder=lambda: _ensure_dev_bootstrap_display_payload(
            build_dev_status(
                training_status,
                lightweight=True,
                fetch_truth_source=False,
            )
        ),
    )


def resolve_runtime_monitor_status_url() -> str:
    """Remote runtime status URL. Non-empty only when explicitly set via MIRU_RUNTIME_STATUS_URL.
    Default is local: worktree (18765) uses worktree data.
    Set MIRU_RUNTIME_STATUS_URL (e.g. http://127.0.0.1:18765/api/dev-status) to make this
    server proxy status and control to that URL instead of using local runtime."""
    return str(RUNTIME_MONITOR_STATUS_URL or "").strip()


def _is_worktree_runtime() -> bool:
    """True when this server is the worktree instance (e.g. 18765), not main, and not proxying."""
    current = int(CURRENT_SERVER_PORT or 0)
    if current == 0 or current == int(RUNTIME_MONITOR_PORT or 18765):
        return False
    return not bool(str(resolve_runtime_monitor_status_url() or "").strip())


def _worktree_learner_pid_record(pid: int) -> dict[str, Any]:
    """Build PID file record for worktree learner."""
    return {
        "pid": pid,
        "repo_root": str(PROJECT_ROOT),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "learner": "miru_learning_engine",
        "mode": "continuous",
    }


def _miru_ai_dev_pid_record(pid: int, port: int) -> dict[str, Any]:
    """Build PID file record for the worktree Dev server on 18765."""
    return {
        "pid": pid,
        "miru_ai_port": port,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "repo_root": str(PROJECT_ROOT),
    }


def _write_miru_ai_dev_pid_record(pid: int, port: int) -> None:
    """Write the authoritative PID file for the worktree Dev server."""
    WORKTREE_LEARNER_PID_DIR.mkdir(parents=True, exist_ok=True)
    MIRU_AI_DEV_PID_FILE.write_text(
        json.dumps(_miru_ai_dev_pid_record(pid, port), indent=2),
        encoding="utf-8",
    )


def _clear_miru_ai_dev_pid_record_if_owned(pid: int) -> None:
    """Clear the Dev PID file only when it still belongs to this process."""
    if not MIRU_AI_DEV_PID_FILE.is_file():
        return
    try:
        raw = MIRU_AI_DEV_PID_FILE.read_text(encoding="utf-8").strip()
        record = json.loads(raw) if raw else {}
        record_pid = int(record.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        record_pid = pid
    if record_pid and record_pid != pid:
        return
    with suppress(OSError):
        MIRU_AI_DEV_PID_FILE.unlink()


def _register_miru_ai_dev_pid_lifecycle(port: int) -> None:
    """Keep the 18765 PID file aligned with the actual Dev server process."""
    if int(port or 0) != 18765:
        return
    pid = os.getpid()
    _write_miru_ai_dev_pid_record(pid, int(port))
    atexit.register(_clear_miru_ai_dev_pid_record_if_owned, pid)


def _read_worktree_learner_pid() -> dict[str, Any] | None:
    """Read worktree learner PID file. Returns None if missing or invalid."""
    path = WORKTREE_LEARNER_PID_FILE
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int | float):
            return None
        return {"pid": int(data["pid"]), "repo_root": str(data.get("repo_root") or "")}
    except (OSError, ValueError, TypeError):
        return None


def _is_worktree_learner_process_alive(pid: int) -> bool:
    """True if pid exists and is the worktree learner (miru_learning_engine --mode continuous)."""
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            cmd = " ".join(proc.cmdline() or [])
            if "miru_learning_engine" not in cmd or "continuous" not in cmd:
                return False
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception as e:
            if psutil is not None and isinstance(e, psutil.NoSuchProcess | psutil.AccessDenied):
                return False
            raise
    try:
        if sys.platform == "win32":
            os.kill(pid, 0)
        else:
            os.kill(pid, 0)
        return True
    except OSError:
        return False


def _normalize_path_variants(path: Path) -> set[str]:
    """Lower-cased path string variants for resilient command-line matching."""
    raw = str(path)
    normalized = os.path.normpath(raw)
    variants = {
        raw.lower(),
        normalized.lower(),
        raw.replace("\\", "/").lower(),
        normalized.replace("\\", "/").lower(),
    }
    return {item for item in variants if item}


def _looks_like_worktree_learner_command(cmdline: list[str] | str | None) -> bool:
    """True when a command line is this worktree's continuous learner process."""
    if cmdline is None:
        return False
    # On Windows psutil may return cmdline as list or in some contexts as single string
    if isinstance(cmdline, str):
        cmd_text = cmdline.lower()
    else:
        if not cmdline:
            return False
        cmd_text = " ".join(str(part) for part in cmdline).lower()
    if "miru_learning_engine" not in cmd_text or "continuous" not in cmd_text:
        return False
    expected_path_variants = (
        _normalize_path_variants(LEARNING_QUEUE_DB_PATH),
        _normalize_path_variants(LEARNING_STATUS_DB_PATH),
        _normalize_path_variants(LEARNING_DOSSIER_DB_PATH),
        _normalize_path_variants(FALLBACK_CATALOG_DB_PATH),
    )
    for variants in expected_path_variants:
        if not any(candidate in cmd_text for candidate in variants):
            return False
    return True


def _list_worktree_learner_process_ids() -> list[int]:
    """List running continuous learner process IDs that target this worktree's data paths."""
    if psutil is None:
        return []
    process_ids: set[int] = set()
    for proc in psutil.process_iter(attrs=["pid", "cmdline", "status"]):
        try:
            cmdline = proc.info.get("cmdline")
            # Pass as-is: list on Unix, sometimes list or str on Windows; avoid list(str) -> chars
            if not _looks_like_worktree_learner_command(cmdline if cmdline is not None else []):
                continue
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                process_ids.add(int(proc.pid))
        except Exception as e:
            if isinstance(e, psutil.NoSuchProcess | psutil.AccessDenied):
                continue
            raise
    return sorted(process_ids)


def _write_worktree_learner_pid(pid: int) -> None:
    """Write worktree learner PID file."""
    WORKTREE_LEARNER_PID_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREE_LEARNER_PID_FILE.write_text(
        json.dumps(_worktree_learner_pid_record(pid), indent=2),
        encoding="utf-8",
    )


def _clear_worktree_learner_pid() -> None:
    """Remove worktree learner PID file."""
    try:
        if WORKTREE_LEARNER_PID_FILE.is_file():
            WORKTREE_LEARNER_PID_FILE.unlink()
    except OSError:
        pass


def _start_worktree_learner_process() -> tuple[bool, str, int | None]:
    """Launch the worktree learner (miru_learning_engine --mode continuous). Returns (ok, message, pid_or_none)."""
    running_pids = _list_worktree_learner_process_ids()
    if running_pids:
        adopted_pid = int(running_pids[0])
        _write_worktree_learner_pid(adopted_pid)
        duplicate_count = len(running_pids)
        suffix = (
            f" ({duplicate_count} matching process(es) detected)." if duplicate_count > 1 else ""
        )
        return (
            True,
            f"Worktree learner already running; adopted PID {adopted_pid}.{suffix}",
            adopted_pid,
        )

    cmd = [
        sys.executable,
        "-m",
        "miru_ai.workers.learning_engine",
        "--mode",
        "continuous",
        "--queue-db",
        str(LEARNING_QUEUE_DB_PATH),
        "--status-db",
        str(LEARNING_STATUS_DB_PATH),
        "--dossier-db",
        str(LEARNING_DOSSIER_DB_PATH),
        "--catalog-db",
        str(FALLBACK_CATALOG_DB_PATH),
    ]
    env = os.environ.copy()
    kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    WORKTREE_LEARNER_PID_DIR.mkdir(parents=True, exist_ok=True)
    try:
        stderr_file = open(WORKTREE_LEARNER_STDERR_LOG, "a", encoding="utf-8")  # noqa: SIM115
        stdout_file = open(WORKTREE_LEARNER_STDOUT_LOG, "a", encoding="utf-8")  # noqa: SIM115
        stderr_file.write(
            f"\n[worktree] learner start requested at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC\n"
        )
        stderr_file.flush()
    except OSError:
        stderr_file = subprocess.DEVNULL
        stdout_file = subprocess.DEVNULL
    kwargs["stdout"] = stdout_file
    kwargs["stderr"] = stderr_file
    try:
        proc = subprocess.Popen(cmd, **kwargs)
        pid = proc.pid
        if pid is None:
            for f in (stdout_file, stderr_file):
                if f != subprocess.DEVNULL and hasattr(f, "close"):
                    with suppress(Exception):
                        f.close()
            return False, "Learner process failed to start (no PID).", None
        _write_worktree_learner_pid(pid)
        return True, "Worktree learner started.", pid
    except OSError as e:
        for f in (stdout_file, stderr_file):
            if f != subprocess.DEVNULL and hasattr(f, "close"):
                with suppress(Exception):
                    f.close()
        return False, f"Failed to start learner process: {e}", None
    except Exception as e:
        for f in (stdout_file, stderr_file):
            if f != subprocess.DEVNULL and hasattr(f, "close"):
                with suppress(Exception):
                    f.close()
        return False, f"Unexpected error starting learner: {e}", None


def _stop_worktree_learner_process() -> tuple[bool, str]:
    """Stop the worktree learner process tracked by the PID file. Returns (ok, message)."""
    record = _read_worktree_learner_pid()
    running_pids = _list_worktree_learner_process_ids()
    if record:
        record_pid = int(record["pid"])
        if _is_worktree_learner_process_alive(record_pid) and record_pid not in running_pids:
            running_pids.append(record_pid)
    running_pids = sorted({int(pid) for pid in running_pids if int(pid) > 0})
    if not running_pids:
        _clear_worktree_learner_pid()
        return (
            True,
            "No worktree learner process found (already stopped or not started by this runtime).",
        )

    if psutil is None:
        pid = int(running_pids[0])
        try:
            sigterm = getattr(__import__("signal", fromlist=["SIGTERM"]), "SIGTERM", 15)
            os.kill(pid, sigterm)
            time.sleep(2)
        except OSError as e:
            _clear_worktree_learner_pid()
            return False, f"Error stopping learner: {e}"
        _clear_worktree_learner_pid()
        return True, "Worktree learner stopped."

    stopped_count = 0
    for pid in running_pids:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception as e:
                if not isinstance(e, psutil.TimeoutExpired):
                    raise
                proc.kill()
                proc.wait(timeout=5)
            stopped_count += 1
        except Exception as e:
            if isinstance(e, psutil.NoSuchProcess | psutil.AccessDenied):
                continue
            raise

    _clear_worktree_learner_pid()
    if stopped_count <= 0:
        return (
            True,
            "No worktree learner process found (already stopped or not started by this runtime).",
        )
    if stopped_count == 1:
        return True, "Worktree learner stopped."
    return True, f"Stopped {stopped_count} worktree learner processes."


def _get_worktree_learner_control_status() -> dict[str, Any]:
    """Return current worktree learner process control status (pid, managed, state) for dev-status."""
    if not _is_worktree_runtime():
        return {}
    running_pids = _list_worktree_learner_process_ids()
    if running_pids:
        primary_pid = int(running_pids[0])
        _write_worktree_learner_pid(primary_pid)
        return {
            "learner_managed_by_worktree": True,
            "learner_pid": primary_pid,
            "learner_pid_stale": False,
            "learner_process_count": len(running_pids),
        }
    record = _read_worktree_learner_pid()
    if not record:
        return {"learner_managed_by_worktree": False, "learner_pid": None}
    pid = int(record["pid"])
    alive = _is_worktree_learner_process_alive(pid)
    return {
        "learner_managed_by_worktree": True,
        "learner_pid": pid if alive else None,
        "learner_pid_stale": not alive,
    }


def _get_last_insight_sync_report() -> dict[str, Any]:
    """Return a copy of the last worktree card insight sync report (from post-stop sync) for Dev status."""
    with _LAST_INSIGHT_SYNC_LOCK:
        return dict(_LAST_INSIGHT_SYNC_REPORT)


def _apply_worktree_learner_state_override(
    learning_engine: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    """When worktree learner process is alive but DB says Idle, show Running (waiting). Mutates activity title for consistency."""
    if not learning_engine.get("learner_managed_by_worktree"):
        return learning_engine
    if learning_engine.get("learner_pid") is None:
        return learning_engine
    if learning_engine.get("learner_state") != "Idle":
        return learning_engine
    learning_engine = dict(learning_engine)
    learning_engine["learner_state"] = "Running (waiting)"
    if activity.get("title") == "Idle":
        activity["title"] = "Running (waiting)"
    return learning_engine


def _reconcile_learner_status_with_process_truth(
    learning_engine: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    """
    Process truth first: stale/missing PID => Offline; live PID + old heartbeat => Stale (never Running).
    DB-only signals cannot upgrade a dead learner to Running.
    """
    if not _is_worktree_runtime():
        return _apply_worktree_learner_state_override(learning_engine, activity)
    le = dict(learning_engine)
    pid = le.get("learner_pid")
    pid_stale = bool(le.get("learner_pid_stale"))
    has_live_pid = (
        pid is not None
        and str(pid).strip() != ""
        and not pid_stale
        and bool(le.get("learner_managed_by_worktree"))
    )
    hb = str(le.get("last_heartbeat") or "").strip()
    hb_age = seconds_since(hb) if hb else None

    if not has_live_pid:
        le = dict(le)
        le["learner_state"] = "Offline"
        if hb_age is None:
            le["heartbeat_freshness"] = "none"
        else:
            le["heartbeat_freshness"] = "stale"
        if activity.get("title") in ("Idle", "Running (waiting)", "Running"):
            activity["title"] = "Idle"
        return le

    if hb_age is not None and hb_age > LEARNER_HEARTBEAT_STALE_SECONDS:
        le = dict(le)
        prev = str(le.get("learner_state") or "")
        if prev in (
            "Running",
            "Starting",
            "Running (waiting)",
            "Idle",
        ):
            le["learner_state"] = "Stale"
        le["heartbeat_freshness"] = "stale"
        return le

    return _apply_worktree_learner_state_override(le, activity)


def build_dev_environment_descriptor() -> dict[str, Any]:
    """Label and ports for the Dev page so environment (worktree vs main) and runtime target are explicit."""
    current = int(CURRENT_SERVER_PORT or 0)
    truth_port = int(RUNTIME_MONITOR_PORT or 18765)
    is_worktree = current != 0 and current != truth_port
    remote_url = resolve_runtime_monitor_status_url()
    if remote_url:
        runtime_target = "remote/main"
        truth_source_label = "Remote main runtime (proxied via MIRU_RUNTIME_STATUS_URL)"
    else:
        runtime_target = "local/worktree" if is_worktree else "local/main"
        truth_source_label = (
            f"Worktree runtime (port {current})"
            if is_worktree
            else f"Main runtime (port {truth_port})"
        )
    return {
        "environment": "worktree" if is_worktree else "main",
        "runtime_target": runtime_target,
        "label": "Worktree Preview" if is_worktree else "Main runtime",
        "current_port": current,
        "truth_port": truth_port,
        "truth_source_label": truth_source_label,
    }


def build_worktree_update_summary(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    validation_audit: dict[str, Any],
    dev_environment: dict[str, Any],
) -> dict[str, Any] | None:
    """Lightweight summary for worktree Dev Monitor: verified updates and awaiting-review state.
    Only returned when environment is worktree. Data is from worktree-local runtime."""
    if str(dev_environment.get("environment") or "").strip().lower() != "worktree":
        return None
    recently_validated = list(validation_audit.get("recently_validated") or [])[:10]
    rejected = list(validation_audit.get("rejected_evidence") or [])
    lowest_conf = list(validation_audit.get("lowest_confidence") or [])
    review_queue_count = int(learning_status.get("review_queue_count") or 0)
    verified_dossiers = int(training_status.get("verified_dossiers") or 0)
    cards_recently_added = len(recently_validated)
    awaiting_review = review_queue_count > 0 or len(rejected) > 0 or len(lowest_conf) > 0
    has_verified_updates = cards_recently_added > 0 or verified_dossiers > 0

    if awaiting_review and has_verified_updates:
        status = "ready_for_review"
        message = "Verified update ready for review on worktree Project Miru site."
    elif has_verified_updates:
        status = "updated"
        message = "Worktree Project Miru site updated with verified data."
    elif awaiting_review:
        status = "ready_for_review"
        message = "Items awaiting review on worktree site."
    else:
        status = "none"
        message = "No verified updates on worktree site yet."

    recent_additions = [
        {
            "card_code": str(item.get("card_code") or "").strip().upper(),
            "card_name": str(item.get("card_name") or "Unknown card").strip(),
            "verified_at": str(item.get("verified_at") or "").strip(),
        }
        for item in recently_validated
        if item.get("card_code")
    ][:6]

    return {
        "show": True,
        "environment": "worktree",
        "status": status,
        "message": message,
        "awaiting_review": awaiting_review,
        "review_count": review_queue_count,
        "dossier_count": verified_dossiers,
        "cards_updated": cards_recently_added,
        "recent_additions": recent_additions,
        "insight_bundles_ready": cards_recently_added,
    }


def build_monitor_source(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    validation_audit: dict[str, Any],
) -> dict[str, Any]:
    local_project_db_path = Path(FALLBACK_CATALOG_DB_PATH)
    local_status_db_path = Path(LEARNING_STATUS_DB_PATH)
    local_queue_db_path = Path(LEARNING_QUEUE_DB_PATH)
    local_dossier_db_path = Path(LEARNING_DOSSIER_DB_PATH)
    local_source = {
        "mode": "worktree_runtime",
        "label": "Monitoring worktree runtime",
        "detail": f"Reading worktree data from {local_status_db_path.parent}.",
        "status_url": "",
        "queue_db_path": str(local_queue_db_path),
        "status_db_path": str(local_status_db_path),
        "dossier_db_path": str(local_dossier_db_path),
        "project_db_path": str(local_project_db_path),
        "catalog_total": int(training_status.get("total_cards") or 0),
        "learning_status": dict(learning_status or {}),
        "validation_audit": dict(validation_audit or {}),
    }

    remote_status_url = resolve_runtime_monitor_status_url()
    if not remote_status_url:
        return local_source

    truth_result = fetch_truth_source_dev_status_result(force_refresh=False)
    if not truth_result:
        fallback = dict(local_source)
        fallback_truth = build_runtime_truth_source_descriptor(
            mode="local_fallback",
            status_url=remote_status_url,
        )
        fallback["label"] = str(fallback_truth.get("label") or fallback["label"])
        fallback["detail"] = str(fallback_truth.get("detail") or fallback["detail"])
        fallback["status_url"] = remote_status_url
        return fallback

    remote_payload = dict(truth_result.get("payload") or {})
    truth_source = dict(truth_result.get("source") or {})
    remote_learning = dict(remote_payload.get("learning_engine") or {})
    queue_db_raw = str(remote_learning.get("queue_db_path") or "").strip()
    status_db_raw = str(remote_learning.get("status_db_path") or "").strip()
    dossier_db_raw = str(remote_learning.get("dossier_db_path") or "").strip()
    runtime_queue_db = Path(queue_db_raw) if queue_db_raw else None
    runtime_status_db = Path(status_db_raw) if status_db_raw else None
    runtime_dossier_db = Path(dossier_db_raw) if dossier_db_raw else None
    runtime_root = (
        runtime_status_db.parent
        if runtime_status_db
        else (runtime_queue_db.parent if runtime_queue_db else None)
    )
    runtime_project_db = runtime_root / "card_catalog.db" if runtime_root else None

    if (
        runtime_queue_db
        and runtime_status_db
        and runtime_dossier_db
        and runtime_project_db
        and runtime_project_db.is_file()
    ):
        runtime_catalog_status = inspect_fallback_catalog_db(runtime_project_db)
        runtime_total_cards = int(
            runtime_catalog_status.get("cards")
            or (remote_payload.get("training_progress") or {}).get("catalog_cards_total")
            or training_status.get("total_cards")
            or 0
        )
        truth_mode = str(truth_source.get("mode") or "")
        return {
            "mode": (
                "main_runtime" if truth_mode == "main_runtime_live" else "main_runtime_cached"
            ),
            "label": str(truth_source.get("label") or "Live main runtime"),
            "detail": (
                f"{truth_source.get('detail') or 'Using main-runtime truth.'!s}"
                f" Runtime data paths are available for richer monitoring."
            ),
            "status_url": remote_status_url,
            "queue_db_path": str(runtime_queue_db),
            "status_db_path": str(runtime_status_db),
            "dossier_db_path": str(runtime_dossier_db),
            "project_db_path": str(runtime_project_db),
            "catalog_total": runtime_total_cards,
            "fetched_at": truth_source.get("fetched_at"),
            "age_label": str(truth_source.get("age_label") or ""),
            "learning_status": load_learning_engine_status(
                queue_db_path=runtime_queue_db,
                status_db_path=runtime_status_db,
                dossier_db_path=runtime_dossier_db,
                total_cards=runtime_total_cards,
            ),
            "validation_audit": list_validation_audit_insights(project_db_path=runtime_project_db),
        }

    fallback_remote = dict(local_source)
    truth_mode = str(truth_source.get("mode") or "")
    fallback_remote["mode"] = (
        "main_runtime_api" if truth_mode == "main_runtime_live" else "main_runtime_api_cached"
    )
    fallback_remote["label"] = str(truth_source.get("label") or "Live main runtime")
    fallback_remote["detail"] = (
        f"{truth_source.get('detail') or 'Using main-runtime truth.'!s} "
        "Direct runtime DB paths were unavailable, so validation and sync activity uses the runtime API snapshot."
    )
    fallback_remote["status_url"] = remote_status_url
    fallback_remote["queue_db_path"] = queue_db_raw
    fallback_remote["status_db_path"] = status_db_raw
    fallback_remote["dossier_db_path"] = dossier_db_raw
    fallback_remote["project_db_path"] = str(runtime_project_db) if runtime_project_db else ""
    fallback_remote["fetched_at"] = truth_source.get("fetched_at")
    fallback_remote["age_label"] = str(truth_source.get("age_label") or "")
    fallback_remote["catalog_total"] = int(
        (remote_payload.get("training_progress") or {}).get("catalog_cards_total")
        or training_status.get("total_cards")
        or 0
    )
    fallback_remote["learning_status"] = remote_learning or dict(learning_status or {})
    fallback_remote["validation_audit"] = dict(remote_payload.get("validation_audit") or {})
    return fallback_remote


def build_monitor_payload(
    training_status: dict[str, Any],
    monitor_source: dict[str, Any],
) -> dict[str, Any]:
    learning_status = dict(monitor_source.get("learning_status") or {})
    validation_audit = dict(monitor_source.get("validation_audit") or {})
    activity = build_learning_engine_activity(learning_status, build_miru_activity(training_status))
    project_db_path_raw = str(monitor_source.get("project_db_path") or "").strip()
    status_db_path_raw = str(monitor_source.get("status_db_path") or "").strip()
    project_db_path = Path(project_db_path_raw) if project_db_path_raw else None
    status_db_path = Path(status_db_path_raw) if status_db_path_raw else None
    validation_counts = (
        load_monitor_validation_counts(project_db_path=project_db_path)
        if project_db_path and project_db_path.is_file()
        else {"miru_validations_count": 0, "recent_validation_writes_count": 0}
    )
    engine_events = (
        load_monitor_engine_events(status_db_path=status_db_path)
        if status_db_path and status_db_path.is_file()
        else {
            "recent_activity": [],
            "recent_synced_cards_count": 0,
            "recent_failure_count": 0,
        }
    )
    queue_length = int(learning_status.get("queue_length") or 0)
    running_count = int(learning_status.get("running_count") or 0)
    failed_count = int(learning_status.get("failed_count") or 0)
    cards_per_hour = int(learning_status.get("cards_learned_per_hour") or 0)
    validation_success_rate = float(learning_status.get("validation_success_rate") or 0.0)
    catalog_total = int(
        monitor_source.get("catalog_total") or training_status.get("total_cards") or 0
    )
    recent_validation_writes = int(validation_counts["recent_validation_writes_count"] or 0)
    recent_synced_cards = int(engine_events["recent_synced_cards_count"] or 0)
    recent_failure_count = int(engine_events["recent_failure_count"] or 0)
    source_mode = str(monitor_source.get("mode") or "worktree_runtime")
    if source_mode in {
        "main_runtime",
        "main_runtime_api",
        "main_runtime_cached",
        "main_runtime_api_cached",
    }:
        verified_dossiers = int(
            validation_counts["miru_validations_count"]
            or learning_status.get("validated_card_count")
            or 0
        )
    else:
        verified_dossiers = int(training_status.get("verified_dossiers") or 0)
    verified_coverage = safe_percent(verified_dossiers, catalog_total)
    heartbeat = str(learning_status.get("last_heartbeat") or "").strip()
    heartbeat_age = seconds_since(heartbeat)
    heartbeat_stale = heartbeat_age is not None and heartbeat_age > 180
    current_state = str(learning_status.get("current_state") or "").strip().lower()
    task_label = str(learning_status.get("current_task_label") or "").strip()
    task_type = str(learning_status.get("current_task_type") or "").strip().lower()
    task_type_label = humanize_snake_label(task_type) or "Waiting"

    state_label = str(activity.get("title") or "Idle")
    state_tone = "neutral"
    if heartbeat_stale and (
        running_count > 0 or queue_length > 0 or current_state in {"processing", "running"}
    ):
        state_label = "Stuck"
        state_tone = "warn"
    elif str(activity.get("key") or "") == "storm_warning":
        state_tone = "warn"
    elif str(activity.get("key") or "") in {"setting_sail", "gathering_crew"}:
        state_tone = "good"

    if recent_failure_count >= 3 and recent_validation_writes <= 0 and recent_synced_cards <= 0:
        progress_label = "Churning"
        progress_tone = "warn"
        meaningful_progress = False
        progress_summary = (
            "Miru is active, but the recent loop looks more like churn than new verified gains."
        )
        progress_detail = f"{format_count(recent_failure_count)} recent failures and no fresh validation or sync writes in the last 30 minutes."
    elif recent_validation_writes > 0 and recent_synced_cards > 0:
        progress_label = "Improving"
        progress_tone = "good"
        meaningful_progress = True
        progress_summary = "Miru is adding verified knowledge and Project Miru is receiving it."
        progress_detail = f"{format_count(recent_validation_writes)} validation write(s) and {format_count(recent_synced_cards)} sync(s) landed in the last hour."
    elif recent_validation_writes > 0:
        progress_label = "Improving"
        progress_tone = "neutral"
        meaningful_progress = True
        progress_summary = (
            "Miru is adding verified knowledge, but Project Miru sync looks quiet right now."
        )
        progress_detail = f"{format_count(recent_validation_writes)} validation write(s) landed in the last hour while recent sync count stayed at {format_count(recent_synced_cards)}."
    elif cards_per_hour > 0 or queue_length > 0 or running_count > 0:
        progress_label = "Working"
        progress_tone = "neutral"
        meaningful_progress = False
        progress_summary = "Miru is busy, but fresh verified gains are not visible yet."
        progress_detail = f"Throughput is {format_count(cards_per_hour)} cards/hr with {format_count(queue_length)} queued and {format_count(running_count)} running."
    else:
        progress_label = "Idle"
        progress_tone = "neutral"
        meaningful_progress = False
        progress_summary = "Miru is not showing meaningful new verified progress right now."
        progress_detail = "Queue is clear and no recent validation or sync writes are visible."

    warnings: list[dict[str, Any]] = []
    if heartbeat_stale:
        warnings.append(
            {
                "tone": "warn",
                "title": "Stale heartbeat",
                "detail": f"The last learning heartbeat was {format_relative_age(heartbeat_age)}.",
            }
        )
    if recent_failure_count >= 3 and recent_validation_writes <= 0:
        warnings.append(
            {
                "tone": "warn",
                "title": "High churn",
                "detail": "Recent task failures are piling up without fresh verified gains.",
            }
        )
    if queue_length <= 0 and running_count <= 0:
        warnings.append(
            {
                "tone": "neutral",
                "title": "Empty queue",
                "detail": "No queued or running learning tasks are visible right now.",
            }
        )
    if validation_success_rate > 0 and validation_success_rate < 70.0:
        warnings.append(
            {
                "tone": "warn",
                "title": "Low validation success",
                "detail": f"Recent validation success is only {validation_success_rate:.1f}%.",
            }
        )
    if recent_validation_writes > 0 and recent_synced_cards <= 0:
        warnings.append(
            {
                "tone": "warn",
                "title": "Sync not progressing",
                "detail": "Fresh validation writes are landing, but no recent Project Miru sync completed.",
            }
        )
    if not warnings:
        warnings.append(
            {
                "tone": "good",
                "title": "No active warnings",
                "detail": "Heartbeat looks fresh and no obvious blockage is showing right now.",
            }
        )

    validation_events = [
        {
            "kind": "validation_written",
            "title": "Validation written",
            "detail": (
                f"{str(item.get('card_code') or '').upper()} · "
                f"{item.get('card_name') or 'Unknown card'!s} saved to miru_validations."
            ),
            "timestamp": str(item.get("verified_at") or ""),
            "card_code": str(item.get("card_code") or "").upper(),
            "tone": "good",
        }
        for item in (validation_audit.get("recently_validated") or [])[:6]
        if item.get("card_code")
    ]
    recent_activity = sorted(
        [*validation_events, *(engine_events.get("recent_activity") or [])],
        key=lambda item: str(item.get("timestamp") or ""),
        reverse=True,
    )[:8]

    heartbeat_detail = "No heartbeat reported yet."
    if heartbeat:
        heartbeat_detail = f"{heartbeat} ({format_relative_age(heartbeat_age)})"
        if heartbeat_stale:
            heartbeat_detail += " - stale"

    gains = [
        {
            "key": "verified_coverage",
            "label": "Verified coverage",
            "value": f"{verified_coverage:.1f}%",
            "detail": "Share of catalog cards with verified dossier coverage.",
        },
        {
            "key": "verified_dossiers",
            "label": "Verified card dossiers",
            "value": format_count(verified_dossiers),
            "detail": "Cards currently covered by verified dossier records.",
        },
        {
            "key": "miru_validations_rows",
            "label": "Validation ledger rows",
            "value": format_count(validation_counts["miru_validations_count"]),
            "detail": "Rows currently stored in Project Miru's miru_validations table.",
        },
        {
            "key": "recent_validation_writes",
            "label": "Recent validation writes",
            "value": format_count(recent_validation_writes),
            "detail": "Rows written to miru_validations during the last hour.",
        },
        {
            "key": "recent_synced_cards",
            "label": "Recent synced cards",
            "value": format_count(recent_synced_cards),
            "detail": "Cards that recently reached Project Miru during the last hour.",
        },
        {
            "key": "cards_learned_per_hour",
            "label": "Cards learned per hour",
            "value": format_count(cards_per_hour),
            "detail": "Rolling hour of completed verify_official_fields tasks.",
        },
    ]

    return {
        "source": {
            "mode": source_mode,
            "label": str(monitor_source.get("label") or "Monitoring worktree runtime"),
            "detail": str(monitor_source.get("detail") or ""),
            "status_url": str(monitor_source.get("status_url") or ""),
            "queue_db_path": str(monitor_source.get("queue_db_path") or ""),
            "status_db_path": str(monitor_source.get("status_db_path") or ""),
            "dossier_db_path": str(monitor_source.get("dossier_db_path") or ""),
            "project_db_path": str(monitor_source.get("project_db_path") or ""),
        },
        "state": {
            "label": state_label,
            "tone": state_tone,
            "description": str(activity.get("description") or "Miru is online and waiting."),
            "task_label": task_label or "No active task",
            "task_type_label": task_type_label,
            "queue_status": (
                f"{format_count(queue_length)} waiting, "
                f"{format_count(running_count)} running, "
                f"{format_count(failed_count)} failed"
            ),
            "heartbeat": heartbeat_detail,
        },
        "progress": {
            "label": progress_label,
            "tone": progress_tone,
            "meaningful_progress": meaningful_progress,
            "summary": progress_summary,
            "detail": progress_detail,
        },
        "gains": gains,
        "recent_activity": recent_activity,
        "warnings": warnings,
    }


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
        {
            "key": "queue_length",
            "label": "Queued tasks",
            "value": format_count(learning_status["queue_length"]),
            "detail": "Learning tasks waiting in the SQLite queue.",
        },
        {
            "key": "processed_count",
            "label": "Processed tasks",
            "value": format_count(learning_status["processed_count"]),
            "detail": "Total task attempts processed by the learning engine.",
        },
        {
            "key": "success_count",
            "label": "Successful tasks",
            "value": format_count(learning_status["success_count"]),
            "detail": "Learning tasks completed without error.",
        },
        {
            "key": "error_count",
            "label": "Errored tasks",
            "value": format_count(learning_status["error_count"]),
            "detail": "Learning task attempts that ended in an error.",
        },
        {
            "key": "source_success_count",
            "label": "Source fetch success",
            "value": format_count(learning_status["source_success_count"]),
            "detail": "Source-backed tasks completed successfully.",
        },
        {
            "key": "source_error_count",
            "label": "Source fetch errors",
            "value": format_count(learning_status["source_error_count"]),
            "detail": "Source-backed tasks that ended in error.",
        },
        {
            "key": "image_success_count",
            "label": "Image fetch success",
            "value": format_count(learning_status.get("image_success_count", 0)),
            "detail": "Image ingestion tasks completed successfully.",
        },
        {
            "key": "image_error_count",
            "label": "Image fetch errors",
            "value": format_count(learning_status.get("image_error_count", 0)),
            "detail": "Image ingestion tasks that ended in error.",
        },
        {
            "key": "images_tracked",
            "label": "Images tracked",
            "value": format_count(images_tracked),
            "detail": "Image registry entries tracked by the learning engine.",
        },
        {
            "key": "images_verified",
            "label": "Images verified",
            "value": format_count(images_verified),
            "detail": "Image registry entries marked as verified.",
        },
        {
            "key": "images_missing",
            "label": "Images missing",
            "value": format_count(images_missing),
            "detail": "Catalog cards missing an image registry entry.",
        },
        {
            "key": "last_image_update",
            "label": "Last image update",
            "value": learning_status.get("last_image_update") or "—",
            "detail": "Most recent image ingestion update timestamp.",
        },
        {
            "key": "learning_dossiers",
            "label": "Learning dossiers",
            "value": format_count(learning_status["dossier_count"]),
            "detail": "Bootstrap dossier rows managed by the learning engine.",
        },
        {
            "key": "total_cards",
            "label": "Total cards",
            "value": format_count(total_cards),
            "detail": "Catalog identities loaded locally.",
        },
        {
            "key": "dossiers_created",
            "label": "Dossiers created",
            "value": format_count(training_status["dossiers_created"]),
            "detail": "Structured card profiles in the training store.",
        },
        {
            "key": "verified_dossiers",
            "label": "Verified dossiers",
            "value": format_count(training_status["verified_dossiers"]),
            "detail": "Cards that currently read as verified.",
        },
        {
            "key": "remaining_gaps",
            "label": "Remaining gaps",
            "value": format_count(training_status["remaining_gaps"]),
            "detail": "Cards still missing verified dossier coverage.",
        },
        {
            "key": "variants_tracked",
            "label": "Variants tracked",
            "value": format_count(variants_tracked),
            "detail": "Variant rows currently indexed in the local catalog.",
        },
        {
            "key": "image_coverage",
            "label": "Image coverage",
            "value": format_count(image_coverage_cards),
            "detail": f"{image_coverage_percent:.1f}% of catalog cards have a stored image identity.",
        },
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


def _format_timestamp_readable(ts: str | None) -> str:
    """Format a timestamp string (ISO with Z/+ or YYYY-MM-DD HH:MM:SS) to a short local-time display. Returns '—' if missing or unparseable."""
    if not ts or not str(ts).strip():
        return "—"
    s = str(ts).strip()
    try:
        if "T" in s or "+" in s or s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            local = dt.astimezone()
        else:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            local = dt
        return local.strftime("%b %d, %I:%M %p").replace(" 0", " ").strip()
    except Exception:
        return s[:20] if len(s) > 20 else s


def _learner_state_display(learning_engine: dict[str, Any]) -> str:
    """Single label for Dev strip: Idle, Running, Waiting for work, Learning, Error, Needs attention. Avoid 'Stopped' for healthy no-work state."""
    state = str(learning_engine.get("learner_state") or "").strip()
    pid = learning_engine.get("learner_pid")
    has_pid = pid is not None and str(pid).strip() != ""
    if state in ("Running", "Starting"):
        return "Learning"
    if state == "Running (waiting)" or (state == "Idle" and has_pid):
        return "Waiting for work"
    if state == "Idle":
        return "Idle"
    if state == "Offline":
        return "Offline"
    if state == "Error":
        return "Error"
    if state == "Stale":
        return "Needs attention"
    if state and state != "—":
        return state
    return "Idle" if not has_pid else "Waiting for work"


def _worker_action_display(action: str, data: dict[str, Any]) -> str:
    """Human-readable scheduled worker action for Dev display. Use Idle/Ready so post-governed state feels alive, not blocked."""
    a = (action or "").strip().lower()
    if a == "overlap_blocked":
        return "Idle (run in progress)"
    if a == "no_new_work":
        return "Idle (no new work)"
    if a == "growth":
        return "Growth"
    if a == "bulk_growth_and_sync":
        return "Growth + sync"
    if a == "sync_only":
        return "Sync only"
    if a == "error":
        return "Error"
    if a == "no_run_recorded":
        return "—"
    return humanize_snake_label(action or "—")


def _enrich_learning_engine_display(learning_engine: dict[str, Any]) -> dict[str, Any]:
    """Add learner_state_display for Dev strip (truthful, readable label)."""
    out = dict(learning_engine)
    out["learner_state_display"] = _learner_state_display(out)
    return out


def load_governed_autopilot_rollup() -> dict[str, Any]:
    """Load today's governed autopilot rollup from worktree data/. Returns empty dict if missing."""
    date_key = datetime.now(UTC).strftime("%Y-%m-%d")
    path = GOVERNED_AUTOPILOT_DATA_DIR / f"governed_autopilot_rollup_{date_key}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_last_governed_report_cards(rollup: dict[str, Any]) -> dict[str, list[str]]:
    """Load card identifiers from the last governed batch report (auto_applied and skipped). Truthful, read-only."""
    out: dict[str, list[str]] = {"auto_applied": [], "skipped": []}
    last_ts = str(rollup.get("last_run_ts") or "").strip()
    if not last_ts:
        return out
    path = GOVERNED_AUTOPILOT_DATA_DIR / f"governed_batch_report_{last_ts}.json"
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return out
        for r in (data.get("auto_applied") or [])[:12]:
            ident = str((r or {}).get("item_identifier") or "").strip()
            if ident:
                out["auto_applied"].append(ident)
        for r in (data.get("skipped") or [])[:12]:
            ident = str((r or {}).get("item_identifier") or "").strip()
            if ident:
                out["skipped"].append(ident)
    except Exception:
        pass
    return out


def _build_governed_recent_activity(rollup: dict[str, Any]) -> list[dict[str, str]]:
    """Build a short list of recent-activity messages from the governed rollup for the Dev 'Recently touched cards' section."""
    activities: list[dict[str, str]] = []
    if not rollup or not isinstance(rollup, dict):
        return activities
    runs = int(rollup.get("runs") or 0)
    if runs <= 0:
        return activities
    total_auto = int(rollup.get("total_auto_applied") or 0)
    total_skipped = int(rollup.get("total_skipped") or 0)
    open_items = rollup.get("open_review_items") or []
    n_review = len(open_items)
    parts = [f"{runs} run{'s' if runs != 1 else ''} today"]
    if total_auto > 0:
        parts.append(f"{total_auto} safe update{'s' if total_auto != 1 else ''} applied")
    if total_skipped > 0:
        parts.append(f"{total_skipped} skipped (no change)")
    if n_review > 0:
        parts.append(f"{n_review} need{'s' if n_review == 1 else ''} your review")
    activities.append(
        {
            "message": "Governed batch: " + ", ".join(parts) + ".",
            "label": "Governed batch",
        }
    )
    report_cards = _load_last_governed_report_cards(rollup)
    if report_cards["auto_applied"]:
        codes = ", ".join(report_cards["auto_applied"][:8])
        activities.append({"message": f"Safely updated: {codes}.", "label": "Safe update"})
    if report_cards["skipped"]:
        codes = ", ".join(report_cards["skipped"][:8])
        activities.append({"message": f"Checked (no change): {codes}.", "label": "Checked"})
    for item in open_items[:4]:
        ident = str(item.get("item_identifier") or "").strip()
        state = str(item.get("proposed_state") or "").strip()
        title = str(item.get("simple_title") or "Item for review").strip()
        if ident:
            activities.append(
                {
                    "message": (f"{ident} ({state}): {title}" if state else f"{ident}: {title}"),
                    "label": "Needs review",
                }
            )
    return activities


def ensure_governed_autopilot_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure governed_autopilot and official_rules summary are present in Dev payloads."""
    out = dict(payload or {})
    rollup = load_governed_autopilot_rollup()
    out["governed_autopilot"] = rollup
    out["governed_recent_activity"] = _build_governed_recent_activity(rollup)
    try:
        from tools.miru_official_rules import (
            DEFAULT_RULES_DB_PATH,
            get_official_rules_summary,
        )

        out["official_rules"] = get_official_rules_summary(DEFAULT_RULES_DB_PATH)
    except Exception:
        out["official_rules"] = {
            "current_notices": 0,
            "upcoming_notices": 0,
            "upcoming_legality_count": 0,
        }
    return out


def _enrich_worker_last_run_display(worker_last_run: dict[str, Any]) -> dict[str, Any]:
    """Add action_display and timestamp_display for Dev scheduled worker block."""
    out = dict(worker_last_run)
    out["action_display"] = _worker_action_display(out.get("action", ""), out)
    out["timestamp_display"] = _format_timestamp_readable(out.get("timestamp"))
    return out


def _parse_worker_timestamp_age(ts: str) -> tuple[float | None, str]:
    """Return (age_seconds, formatted_display) for worker timestamp (ISO). (None, '') if unparseable."""
    if not ts or not isinstance(ts, str):
        return None, ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        age = (now - dt).total_seconds()
        formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
        return age, formatted
    except Exception:
        return None, ""


def _apply_worker_heartbeat_fallback(payload: dict[str, Any]) -> None:
    """When learner heartbeat is stale but worker ran recently (overlap_blocked/no_new_work), use worker time so Dev page shows fresh."""
    le = payload.get("learning_engine")
    wr = payload.get("worker_last_run")
    if not le or not wr or le.get("heartbeat_freshness") != "stale":
        return
    if wr.get("action") not in ("overlap_blocked", "no_new_work"):
        return
    ts = wr.get("timestamp")
    age, formatted = _parse_worker_timestamp_age(ts)
    if age is not None and 0 <= age <= 600 and formatted:
        le["last_heartbeat"] = formatted
        le["heartbeat_freshness"] = "fresh"


def load_worker_last_run() -> dict[str, Any]:
    """Read latest worker run from data/miru_worker_last_run.json. File-read only; no DB. Returns {"action": "no_run_recorded"} if missing."""
    if not WORKER_LAST_RUN_PATH.is_file():
        return {"action": "no_run_recorded"}
    try:
        raw = WORKER_LAST_RUN_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"action": "no_run_recorded"}
    except (OSError, json.JSONDecodeError):
        return {"action": "no_run_recorded"}


def build_pushover_runtime_state(
    *,
    training_status: dict[str, Any] | None = None,
    learning_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = dict(inspect_pushover_env())
    state["env_path"] = PROJECT_ENV_LOAD.get("env_path")
    state["env_exists"] = bool(PROJECT_ENV_LOAD.get("exists"))
    state["env_parser"] = PROJECT_ENV_LOAD.get("parser")
    state["loaded_keys"] = list(PROJECT_ENV_LOAD.get("loaded_keys") or [])
    state["skipped_existing_keys"] = list(PROJECT_ENV_LOAD.get("skipped_existing_keys") or [])
    state["server_script_path"] = str(Path(__file__).resolve())
    state["project_root"] = str(PROJECT_ROOT)
    state["test_endpoint"] = build_route_url("/api/dev/test-pushover")
    if training_status is not None and learning_status is not None:
        preview = build_learning_notification_payload(
            training_status,
            learning_status,
            previous_snapshot=load_pushover_learning_snapshot(),
        )
        state["learning_preview"] = {
            "title": preview["title"],
            "message": preview["message"],
            "meaningful_gain": bool(preview["meaningful_gain"]),
            "engine_state": preview["engine_state"],
            "has_baseline": bool(preview["has_baseline"]),
            "verified_delta": preview["verified_delta"],
            "coverage_delta": preview["coverage_delta"],
        }
    return state


def build_dev_intelligence_status(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    pushover_state: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    total_cards = int(training_status.get("total_cards") or 0)
    dossiers_created = int(training_status.get("dossiers_created") or 0)
    verified_dossiers = int(training_status.get("verified_dossiers") or 0)
    validated_cards = int(learning_status.get("validated_card_count") or 0)
    images_tracked = int(learning_status.get("images_tracked") or 0)
    images_verified = int(learning_status.get("images_verified") or 0)
    queue_waiting = int(learning_status.get("queue_length") or 0)
    queue_running = int(learning_status.get("running_count") or 0)
    queue_failed = int(learning_status.get("failed_count") or 0)
    queue_completed = int(learning_status.get("completed_count") or 0)
    current_state = str(learning_status.get("current_state") or "").strip().lower()
    last_heartbeat = str(learning_status.get("last_heartbeat") or "").strip()
    heartbeat_age = seconds_since(last_heartbeat)
    active_state_keys = {
        "processing",
        "running",
        "validating",
        "learning",
        "syncing",
        "discovering",
    }

    if not bool(learning_status.get("status_db_exists")) and heartbeat_age is None:
        worker_label = "Offline"
        worker_tone = "warn"
        worker_detail = "No worker heartbeat or status store is visible from this server."
    elif heartbeat_age is not None and heartbeat_age > 600:
        worker_label = "Offline"
        worker_tone = "warn"
        worker_detail = f"Last worker heartbeat was {format_relative_age(heartbeat_age)}."
    elif (
        heartbeat_age is not None
        and heartbeat_age > 180
        and (queue_waiting > 0 or queue_running > 0 or current_state in active_state_keys)
    ):
        worker_label = "Stalled"
        worker_tone = "warn"
        worker_detail = f"Work is queued or active, but the heartbeat is stale at {format_relative_age(heartbeat_age)}."
    elif queue_waiting > 0 or queue_running > 0 or current_state in active_state_keys:
        worker_label = "Running"
        worker_tone = "good"
        worker_detail = "The learning worker is actively processing or holding queued work."
    else:
        worker_label = "Idle"
        worker_tone = "neutral"
        worker_detail = "The worker is online, but no learning tasks are waiting right now."

    verified_coverage = float(training_status.get("verified_coverage_percent") or 0.0)
    dossier_coverage = float(training_status.get("dossier_coverage_percent") or 0.0)
    validation_coverage = safe_percent(validated_cards, total_cards)
    image_coverage = safe_percent(max(images_verified, images_tracked), total_cards)

    last_signal_timestamp = ""
    last_signal_label = "No recent meaningful learning signal"
    last_signal_detail = (
        "No recent verified, source, or image completion timestamp is available yet."
    )
    for timestamp, label, detail in (
        (
            str(learning_status.get("last_source_update") or "").strip(),
            "Recent source learning",
            "Most recent source-backed learning signal recorded by the worker.",
        ),
        (
            str(learning_status.get("last_image_update") or "").strip(),
            "Recent image learning",
            "Most recent image-learning signal recorded by the worker.",
        ),
        (
            last_heartbeat,
            "Recent worker heartbeat",
            f"Best local signal after the latest '{humanize_snake_label(str(learning_status.get('last_completed_task') or 'learning update')) or 'learning update'}'.",
        ),
    ):
        if timestamp:
            last_signal_timestamp = timestamp
            age = seconds_since(timestamp)
            last_signal_label = format_relative_age(age) if age is not None else timestamp
            last_signal_detail = f"{label}. {detail}"
            break

    preview = dict(pushover_state.get("learning_preview") or {})
    pushover_enabled = bool(pushover_state.get("enabled"))
    pushover_configured = bool(pushover_state.get("configured"))
    if not pushover_enabled:
        pushover_label = "Quiet because disabled"
        pushover_tone = "neutral"
        pushover_detail = "Pushover is turned off for this environment."
    elif not pushover_configured:
        pushover_label = "Quiet because unavailable/error"
        pushover_tone = "warn"
        pushover_detail = "Pushover is enabled, but credentials are incomplete or unavailable."
    elif bool(preview.get("meaningful_gain")):
        pushover_label = "Sending normally"
        pushover_tone = "good"
        pushover_detail = str(
            preview.get("message") or "Meaningful verified gains are ready to notify."
        )
    elif worker_label == "Idle" and queue_waiting <= 0 and queue_running <= 0:
        pushover_label = "Quiet because idle"
        pushover_tone = "neutral"
        pushover_detail = str(
            preview.get("message") or "Miru is online, but there is no queued learning work."
        )
    elif worker_label in {"Offline", "Stalled"}:
        pushover_label = "Quiet because unavailable/error"
        pushover_tone = "warn"
        pushover_detail = str(
            preview.get("message")
            or "Worker health needs attention before notification timing can be trusted."
        )
    else:
        pushover_label = "Quiet because threshold not reached"
        pushover_tone = "neutral"
        pushover_detail = str(
            preview.get("message")
            or "Miru is active, but this cycle has not reached a meaningful learning threshold yet."
        )

    if worker_label == "Running":
        status_sentence = "Miru is processing queued card tasks."
    elif worker_label == "Idle":
        status_sentence = "Miru is idle. No learning tasks are waiting."
    elif worker_label == "Stalled":
        status_sentence = "Miru is running, but the worker heartbeat looks stalled."
    else:
        status_sentence = "Miru looks offline from this Dev server."

    if worker_label == "Running" and pushover_label == "Quiet because threshold not reached":
        status_sentence = "Miru is running, but no new notification threshold has been reached yet."

    stage_rows = [
        {
            "label": "Card awareness",
            "status": "Grounded" if total_cards > 0 else "Early",
            "tone": "good" if total_cards > 0 else "neutral",
            "detail": (
                f"{format_compact_count(total_cards)} local card records are available for lookup."
                if total_cards > 0
                else "The local card catalog is still not populated."
            ),
        },
        {
            "label": "Verified card knowledge",
            "status": "Active" if verified_dossiers > 0 else "Early",
            "tone": "good" if verified_dossiers > 0 else "neutral",
            "detail": (
                f"{verified_coverage:.1f}% verified coverage across {format_compact_count(verified_dossiers)} cards."
                if verified_dossiers > 0
                else "Verified dossier coverage has not started yet."
            ),
        },
        {
            "label": "Image intelligence",
            "status": (
                "Building" if images_tracked > 0 or images_verified > 0 else "Not built yet"
            ),
            "tone": "neutral",
            "detail": (
                f"{format_compact_count(images_verified)} verified image records and {format_compact_count(images_tracked)} tracked."
                if images_tracked > 0 or images_verified > 0
                else "No stored image-learning coverage is visible yet."
            ),
        },
        {
            "label": "Deck intelligence",
            "status": "Not built yet",
            "tone": "neutral",
            "detail": "Deck-level reasoning is not part of the live verified card pipeline yet.",
        },
        {
            "label": "Meta intelligence",
            "status": "Not built yet",
            "tone": "neutral",
            "detail": "Meta interpretation is not currently tracked as a live capability.",
        },
        {
            "label": "Market intelligence",
            "status": "Not built yet",
            "tone": "neutral",
            "detail": "Market reasoning is not currently part of this Miru worker state.",
        },
    ]

    return {
        "status_sentence": status_sentence,
        "worker": {
            "label": worker_label,
            "tone": worker_tone,
            "detail": worker_detail,
        },
        "queue": {
            "waiting": queue_waiting,
            "running": queue_running,
            "failed": queue_failed,
            "completed": queue_completed,
            "summary": (
                f"{format_compact_count(queue_waiting)} pending, {format_compact_count(queue_running)} running, "
                f"{format_compact_count(queue_completed)} completed"
            ),
            "detail": f"{format_compact_count(queue_failed)} failed tasks remain on the ledger.",
        },
        "coverages": [
            {
                "key": "verified",
                "label": "Verified card coverage",
                "value": f"{verified_coverage:.1f}%",
                "detail": f"{format_compact_count(verified_dossiers)} verified dossiers in the main store.",
            },
            {
                "key": "dossiers",
                "label": "Dossier coverage",
                "value": f"{dossier_coverage:.1f}%",
                "detail": f"{format_compact_count(dossiers_created)} dossiers created across the catalog.",
            },
            {
                "key": "validation",
                "label": "Validation coverage",
                "value": f"{validation_coverage:.1f}%",
                "detail": f"{format_compact_count(validated_cards)} cards finished the validation step.",
            },
            {
                "key": "images",
                "label": "Image coverage",
                "value": f"{image_coverage:.1f}%",
                "detail": (
                    f"{format_compact_count(images_verified)} verified image records, "
                    f"{format_compact_count(images_tracked)} tracked total."
                ),
            },
        ],
        "last_meaningful_activity": {
            "label": last_signal_label,
            "detail": last_signal_detail,
            "timestamp": last_signal_timestamp or "Unavailable",
        },
        "pushover": {
            "label": pushover_label,
            "tone": pushover_tone,
            "detail": pushover_detail,
        },
        "stages": stage_rows,
        "current_stage_label": str(
            (training_status.get("intelligence_progress") or {})
            .get("current_stage", {})
            .get("label")
            or "Card awareness"
        ),
        "activity_hint": str(activity.get("description") or status_sentence),
    }


def humanize_operator_truth_source(monitor_source: dict[str, Any]) -> dict[str, str]:
    mode = str(monitor_source.get("mode") or "").strip().lower()
    age_label = str(monitor_source.get("age_label") or "").strip()
    if mode in {"main_runtime", "main_runtime_api"}:
        label = "Live main runtime"
        detail = "Using the live main-runtime snapshot."
        if age_label and age_label != "just now":
            detail = f"Using the live main-runtime snapshot refreshed {age_label}."
    elif mode in {"main_runtime_cached", "main_runtime_api_cached"}:
        label = "Cached main-runtime snapshot"
        detail = "Using the last good main-runtime snapshot while live access catches up."
        if age_label:
            detail = f"Using the last good main-runtime snapshot from {age_label} while live access catches up."
    else:
        label = "Local fallback"
        detail = (
            "Using local worktree status data because the main runtime is not currently reachable."
        )
    detail = str(monitor_source.get("detail") or detail)
    return {"label": label, "detail": detail}


def build_operator_recent_activity(
    *,
    learning_status: dict[str, Any],
    monitor_source: dict[str, Any],
    validation_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    last_completed_task = (
        humanize_snake_label(str(learning_status.get("last_completed_task") or ""))
        or "No completed task recorded"
    )
    last_heartbeat = str(learning_status.get("last_heartbeat") or "").strip()
    if last_completed_task:
        items.append(
            {
                "title": "Latest completed task",
                "detail": last_completed_task,
                "timestamp": last_heartbeat,
                "tone": (
                    "good" if last_completed_task != "No completed task recorded" else "neutral"
                ),
            }
        )

    latest_validation = next(iter(list(validation_audit.get("recently_validated") or [])), None)
    if latest_validation:
        items.append(
            {
                "title": "Latest validation signal",
                "detail": f"{str(latest_validation.get('card_code') or '').upper()} · {latest_validation.get('card_name') or 'Unknown card'!s}",
                "timestamp": str(latest_validation.get("verified_at") or "").strip(),
                "tone": "good",
            }
        )

    last_image_update = str(learning_status.get("last_image_update") or "").strip()
    if last_image_update:
        items.append(
            {
                "title": "Latest image signal",
                "detail": "Miru last updated its image-learning state here.",
                "timestamp": last_image_update,
                "tone": "neutral",
            }
        )

    queue_waiting = int(learning_status.get("queue_length") or 0)
    queue_running = int(learning_status.get("running_count") or 0)
    items.append(
        {
            "title": "Queue state",
            "detail": (
                "No learning tasks are waiting right now."
                if queue_waiting <= 0 and queue_running <= 0
                else f"{format_compact_count(queue_waiting)} waiting and {format_compact_count(queue_running)} running."
            ),
            "timestamp": last_heartbeat,
            "tone": "neutral" if queue_waiting <= 0 and queue_running <= 0 else "good",
        }
    )

    return items[:4]


def build_set_progress_snapshot(
    set_code: str, *, catalog_db_path: Path, dossier_db_path: Path | None
) -> dict[str, Any] | None:
    normalized = str(set_code or "").strip().upper()
    if not re.fullmatch(r"(?:OP|EB|ST|PRB)\d{2}", normalized):
        return None
    if not catalog_db_path.is_file():
        return None

    catalog_total = 0
    verified_count = 0
    dossier_count = 0
    try:
        with closing(sqlite3.connect(catalog_db_path)) as conn:
            catalog_total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE upper(coalesce(set_code, '')) = ?",
                    (normalized,),
                ).fetchone()[0]
            )
    except sqlite3.Error:
        return None

    if dossier_db_path and dossier_db_path.is_file():
        try:
            with closing(sqlite3.connect(dossier_db_path)) as conn:
                dossier_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM cards WHERE upper(coalesce(set_code, '')) = ?",
                        (normalized,),
                    ).fetchone()[0]
                )
                verified_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM cards
                        WHERE upper(coalesce(set_code, '')) = ?
                          AND lower(coalesce(overall_state, '')) = 'verified'
                        """,
                        (normalized,),
                    ).fetchone()[0]
                )
        except sqlite3.Error:
            dossier_count = 0
            verified_count = 0

    return {
        "set_code": normalized,
        "catalog_total": catalog_total,
        "dossier_count": dossier_count,
        "verified_count": verified_count,
        "verified_percent": safe_percent(verified_count, catalog_total),
        "dossier_percent": safe_percent(dossier_count, catalog_total),
    }


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


def load_cached_validation_audit() -> dict[str, Any]:
    signature = path_signature(FALLBACK_CATALOG_DB_PATH)
    return get_ttl_cached_value(
        "validation_audit",
        ttl_seconds=20.0,
        signature=signature,
        builder=lambda: list_validation_audit_insights(project_db_path=FALLBACK_CATALOG_DB_PATH),
    )


def load_cached_image_coverage_by_set() -> list[dict[str, Any]]:
    signature = (
        path_signature(FALLBACK_CATALOG_DB_PATH),
        path_signature(LEARNING_DOSSIER_DB_PATH),
    )
    return get_ttl_cached_value(
        "image_coverage_by_set",
        ttl_seconds=45.0,
        signature=signature,
        builder=lambda: build_image_coverage_by_set(
            catalog_db_path=FALLBACK_CATALOG_DB_PATH,
            dossier_db_path=LEARNING_DOSSIER_DB_PATH,
        ),
    )


def load_cached_resource_metrics() -> list[dict[str, Any]]:
    return get_ttl_cached_value(
        "resource_metrics",
        ttl_seconds=5.0,
        builder=build_resource_metrics,
    )


def load_cached_monitor_source(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    validation_audit: dict[str, Any],
) -> dict[str, Any]:
    signature = (
        path_signature(LEARNING_QUEUE_DB_PATH),
        path_signature(LEARNING_STATUS_DB_PATH),
        path_signature(LEARNING_DOSSIER_DB_PATH),
        path_signature(FALLBACK_CATALOG_DB_PATH),
        int(training_status.get("verified_dossiers") or 0),
        int(learning_status.get("queue_length") or 0),
        int(learning_status.get("running_count") or 0),
        str(learning_status.get("current_state") or ""),
    )
    return get_ttl_cached_value(
        "monitor_source",
        ttl_seconds=10.0,
        signature=signature,
        builder=lambda: build_monitor_source(training_status, learning_status, validation_audit),
    )


def build_deferred_monitor_payload(
    training_status: dict[str, Any],
    learning_status: dict[str, Any],
    activity: dict[str, Any],
) -> dict[str, Any]:
    total_cards = int(training_status.get("total_cards") or 0)
    verified_dossiers = int(training_status.get("verified_dossiers") or 0)
    coverage_percent = safe_percent(verified_dossiers, total_cards)
    state_label = str(activity.get("title") or "Loading live monitor")
    state_description = str(activity.get("description") or "Miru telemetry is loading.")
    state_detail = str(  # noqa: F841
        activity.get("detail") or "Detailed runtime data will appear after the first refresh."
    )
    queue_waiting = int(learning_status.get("queue_length") or 0)
    queue_running = int(learning_status.get("running_count") or 0)
    queue_failed = int(learning_status.get("failed_count") or 0)
    heartbeat = "Waiting for live runtime refresh."
    heartbeat_seconds = seconds_since(learning_status.get("last_heartbeat"))
    if heartbeat_seconds is not None:
        heartbeat = f"Last local heartbeat {format_relative_age(heartbeat_seconds)}."
    return {
        "source": {
            "label": "Loading runtime monitor",
            "detail": "Heavy runtime details load after first paint so the page can open faster.",
        },
        "state": {
            "label": state_label,
            "tone": str(activity.get("tone") or "neutral"),
            "description": state_description,
            "task_label": str(learning_status.get("current_task_label") or "Loading active task"),
            "task_type_label": humanize_snake_label(
                str(learning_status.get("current_task_type") or "")
            )
            or "Waiting",
            "queue_status": f"{queue_waiting} waiting, {queue_running} running, {queue_failed} failed",
            "heartbeat": heartbeat,
        },
        "progress": {
            "label": "Loading live detail",
            "tone": "neutral",
            "summary": f"{coverage_percent:.1f}% verified card coverage currently cached.",
            "detail": "Recent gains and sync activity appear after the first live refresh.",
        },
        "gains": [],
        "recent_activity": [],
        "warnings": [],
    }


def build_monitor_panel_payload(
    training_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    training_status = training_status or build_training_status()
    learning_status = load_learning_engine_status(
        queue_db_path=LEARNING_QUEUE_DB_PATH,
        status_db_path=LEARNING_STATUS_DB_PATH,
        dossier_db_path=LEARNING_DOSSIER_DB_PATH,
        total_cards=int(training_status.get("total_cards") or 0),
    )
    validation_audit = load_cached_validation_audit()
    monitor_source = load_cached_monitor_source(training_status, learning_status, validation_audit)
    return {
        "updated_at": current_timestamp(),
        "monitor": build_monitor_payload(training_status, monitor_source),
    }


def build_image_coverage_payload() -> dict[str, Any]:
    return {
        "updated_at": current_timestamp(),
        "image_coverage_by_set": load_cached_image_coverage_by_set(),
    }


def build_validation_audit_payload() -> dict[str, Any]:
    return {
        "updated_at": current_timestamp(),
        "validation_audit": load_cached_validation_audit(),
        "validation_audit_url_base": build_route_url("/api/dev/card-validation"),
    }


def build_resource_metrics_payload() -> dict[str, Any]:
    return {
        "updated_at": current_timestamp(),
        "resource_metrics": load_cached_resource_metrics(),
    }


def _build_control_observation(
    *,
    key: str,
    label: str,
    detail: str,
    source: str,
    confirmed: bool = False,
    failed: bool = False,
) -> dict[str, Any]:
    if failed:
        status = "FAILED"
        tone = "warn"
    elif confirmed:
        status = "CONFIRMED WORKING"
        tone = "good"
    else:
        status = "INCONCLUSIVE"
        tone = "neutral"
    return {
        "key": key,
        "label": label,
        "status": status,
        "tone": tone,
        "detail": detail,
        "source": source,
    }


def _build_control_recent_issues(payload: dict[str, Any]) -> dict[str, Any]:
    issues = payload.get("issues") or {}
    learning_engine = payload.get("learning_engine") or {}
    intelligence = payload.get("intelligence_status") or {}
    governed = payload.get("governed_autopilot") or {}
    worker_last_run = payload.get("worker_last_run") or {}
    items: list[dict[str, Any]] = []

    miru_issue = issues.get("miru_ai") or {}
    if str(miru_issue.get("tone") or "") == "warn":
        items.append(
            {
                "title": "Miru AI warning",
                "detail": str(miru_issue.get("detail") or "Miru AI reported a degraded state."),
                "tone": "warn",
                "source": "Issue detection from /api/dev-status.",
            }
        )

    project_issue = issues.get("project_miru") or {}
    if str(project_issue.get("tone") or "") == "warn":
        items.append(
            {
                "title": "Project Miru warning",
                "detail": str(
                    project_issue.get("detail") or "Project Miru is not answering normally."
                ),
                "tone": "warn",
                "source": "Project Miru reachability check from /api/dev-status.",
            }
        )

    worker_status = learning_engine.get("worker_status") or {}
    worker_label = str(
        worker_status.get("label") or (intelligence.get("worker") or {}).get("label") or ""
    ).strip()
    worker_detail = str(
        worker_status.get("detail") or (intelligence.get("worker") or {}).get("detail") or ""
    ).strip()
    if worker_label and worker_label.lower() in {"stale", "offline", "stalled"}:
        items.append(
            {
                "title": f"Worker heartbeat {worker_label.lower()}",
                "detail": worker_detail
                or "The learning worker freshness signal needs a closer look.",
                "tone": "warn",
                "source": "Learning engine heartbeat and worker status.",
            }
        )

    open_review_items = governed.get("open_review_items") or []
    if int(governed.get("newly_surfaced_review") or 0) > 0 or open_review_items:
        first_item = open_review_items[0] if open_review_items else {}
        review_title = str(first_item.get("simple_title") or "Governed review item needs attention")
        review_detail = str(
            first_item.get("simple_reason")
            or "Governed autopilot surfaced an item for operator review."
        )
        items.append(
            {
                "title": review_title,
                "detail": review_detail,
                "tone": "warn",
                "source": "Governed autopilot rollup for today.",
            }
        )

    worker_action = str(worker_last_run.get("action") or "").strip().lower()
    worker_blocker = str(
        worker_last_run.get("blocker") or worker_last_run.get("no_new_work_reason") or ""
    ).strip()
    if worker_action in {"overlap_blocked", "blocked", "error"} and worker_blocker:
        items.append(
            {
                "title": humanize_snake_label(worker_action) or "Scheduled worker blocked",
                "detail": worker_blocker,
                "tone": "warn",
                "source": "Scheduled worker last-run record.",
            }
        )

    if items:
        return {
            "status": (
                "FAILED"
                if any(item["title"].lower().startswith("project miru") for item in items)
                else "INCONCLUSIVE"
            ),
            "tone": "warn",
            "headline": items[0]["title"],
            "summary": items[0]["detail"],
            "items": items[:4],
            "source": "Issue detection, worker heartbeat, governed rollup, and scheduled worker history.",
        }
    return {
        "status": "CONFIRMED WORKING",
        "tone": "good",
        "headline": "No active issue surfaced",
        "summary": "The current Dev snapshot did not surface an active warning or review blocker.",
        "items": [],
        "source": "Issue detection, worker heartbeat, governed rollup, and scheduled worker history.",
    }


def _build_control_recent_activity(payload: dict[str, Any]) -> list[dict[str, Any]]:
    activity_feed = payload.get("activity_feed") or []
    governed_recent = payload.get("governed_recent_activity") or []
    worker_last_run = payload.get("worker_last_run") or {}
    items: list[dict[str, Any]] = []

    for entry in activity_feed[:4]:
        items.append(
            {
                "title": str(entry.get("title") or "Runtime event"),
                "detail": str(entry.get("detail") or ""),
                "timestamp": str(entry.get("timestamp") or ""),
                "tone": str(entry.get("tone") or "neutral"),
                "source": "Learning engine event log",
            }
        )

    worker_action = str(
        worker_last_run.get("action_display") or worker_last_run.get("action") or ""
    ).strip()
    if worker_action and str(worker_last_run.get("action") or "") != "no_run_recorded":
        detail_parts = []
        if worker_last_run.get("blocker"):
            detail_parts.append(str(worker_last_run["blocker"]))
        elif worker_last_run.get("no_new_work_reason"):
            detail_parts.append(str(worker_last_run["no_new_work_reason"]))
        if worker_last_run.get("insight_count_after") is not None:
            detail_parts.append(f"{int(worker_last_run['insight_count_after'])} insights after run")
        items.append(
            {
                "title": worker_action,
                "detail": " ".join(part for part in detail_parts if part).strip()
                or "Latest scheduled worker record.",
                "timestamp": str(worker_last_run.get("timestamp") or ""),
                "tone": "neutral" if "idle" in worker_action.lower() else "good",
                "source": "Scheduled worker last-run record",
            }
        )

    for entry in governed_recent[:2]:
        items.append(
            {
                "title": str(entry.get("label") or "Governed batch"),
                "detail": str(entry.get("message") or ""),
                "timestamp": "",
                "tone": (
                    "warn" if "review" in str(entry.get("label") or "").lower() else "neutral"
                ),
                "source": "Governed autopilot rollup",
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item["title"], item["detail"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:6]


def _judgment_tone_for_category(category: str) -> str:
    normalized = str(category or "").strip().lower()
    if normalized in {"warning", "failure", "needs_review"}:
        return "warn"
    if normalized == "info":
        return "good"
    return "neutral"


def _judgment_guardrail_for_action(action_class: str) -> str:
    normalized = str(action_class or "").strip().lower()
    if normalized == "user_review_required":
        return "Review required"
    if normalized in {"safe_check", "investigate", "worker_prompt"}:
        return "Safe action"
    return "Read-only"


def _judgment_retry_guidance(action_class: str) -> str:
    normalized = str(action_class or "").strip().lower()
    if normalized == "user_review_required":
        return "Do not retry this path until a human review is complete."
    if normalized == "observe_only":
        return "No retry is needed right now."
    if normalized == "safe_check":
        return "Safe to retry after the recommended check confirms the state."
    if normalized == "worker_prompt":
        return "Safe to hand off with a worker prompt before retrying."
    return "Investigate before retrying any related action."


def _judgment_priority(item: dict[str, Any]) -> tuple[int, int, int]:
    category = str(item.get("category") or "").strip().lower()
    recommendation = item.get("recommendation") or {}
    risk = str(recommendation.get("risk") or "").strip().lower()
    confidence = str(recommendation.get("confidence") or "").strip().lower()
    category_rank = {
        "needs_review": 0,
        "failure": 1,
        "warning": 2,
        "watch": 3,
        "info": 4,
    }.get(category, 9)
    risk_rank = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }.get(risk, 3)
    confidence_rank = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }.get(confidence, 3)
    return (category_rank, risk_rank, confidence_rank)


def _build_judgment_item(
    *,
    key: str,
    title: str,
    summary: str,
    category: str,
    issue_type: str,
    source: str,
    action_class: str,
    rationale: str,
    confidence: str,
    risk: str,
    route_task: str = "",
    detail: str = "",
) -> dict[str, Any]:
    category_normalized = str(category or "").strip().lower() or "info"
    issue_type_normalized = str(issue_type or "").strip().lower() or "environment"
    action_normalized = str(action_class or "").strip().lower() or "observe_only"
    tone = _judgment_tone_for_category(category_normalized)
    recommendation = {
        "action_class": action_normalized,
        "label": humanize_snake_label(action_normalized) or "Observe only",
        "rationale": rationale,
        "confidence": str(confidence or "medium").strip().lower() or "medium",
        "risk": str(risk or "medium").strip().lower() or "medium",
        "guardrail_label": _judgment_guardrail_for_action(action_normalized),
        "retry_guidance": _judgment_retry_guidance(action_normalized),
        "route_task": route_task.strip(),
    }
    return {
        "key": key,
        "title": title.strip() or "Miru judgment",
        "summary": summary.strip() or "Miru surfaced a state that needs interpretation.",
        "detail": detail.strip() or summary.strip(),
        "category": category_normalized,
        "category_label": humanize_snake_label(category_normalized) or category_normalized.title(),
        "issue_type": issue_type_normalized,
        "issue_type_label": humanize_snake_label(issue_type_normalized)
        or issue_type_normalized.title(),
        "tone": tone,
        "source": source.strip() or "Current Dev status signals.",
        "recommendation": recommendation,
    }


def _build_control_judgment(
    payload: dict[str, Any],
    *,
    system_health: list[dict[str, Any]],
    recent_issues: dict[str, Any],
    recent_activity: list[dict[str, Any]],
) -> dict[str, Any]:
    issues = payload.get("issues") or {}
    governed = payload.get("governed_autopilot") or {}
    truth_source = payload.get("truth_source") or {}
    learning_engine = payload.get("learning_engine") or {}
    worker_last_run = payload.get("worker_last_run") or {}
    items: list[dict[str, Any]] = []

    project_health = next(
        (item for item in system_health if str(item.get("key") or "") == "project_miru"),
        {},
    )
    miru_health = next(
        (item for item in system_health if str(item.get("key") or "") == "miru_ai"), {}
    )
    worker_health = next(
        (item for item in system_health if str(item.get("key") or "") == "worker"), {}
    )

    open_review_items = governed.get("open_review_items") or []
    if open_review_items:
        first_item = open_review_items[0] if open_review_items else {}
        review_title = str(first_item.get("simple_title") or "Governed review item needs attention")
        review_summary = str(
            first_item.get("simple_reason")
            or "Governed autopilot surfaced an item for human review."
        )
        suggested_action = str(
            first_item.get("suggested_action")
            or "Review the item before applying any related change."
        )
        items.append(
            _build_judgment_item(
                key="governed_review",
                title=review_title,
                summary=review_summary,
                detail=suggested_action,
                category="needs_review",
                issue_type="approval",
                source="Governed autopilot rollup for today.",
                action_class="user_review_required",
                rationale="A governed or conflicting item is waiting for a human decision before Miru should treat it as safe.",
                confidence="high",
                risk="high",
            )
        )

    project_issue = issues.get("project_miru") or {}
    if (
        str(project_health.get("status") or "").strip().upper() == "FAILED"
        or str(project_issue.get("tone") or "") == "warn"
    ):
        items.append(
            _build_judgment_item(
                key="project_runtime",
                title="Project Miru runtime needs attention",
                summary=str(
                    project_health.get("detail")
                    or project_issue.get("detail")
                    or "Project Miru is not answering normally from the Dev surface."
                ),
                detail=f"Observed from {project_health.get('source') or 'Project Miru runtime health.'}",
                category="failure",
                issue_type="runtime",
                source=str(
                    project_health.get("source") or "Observed via Project Miru runtime health."
                ),
                action_class="investigate",
                rationale="If the user-facing site is not healthy, retries or follow-on checks can mislead you until the runtime path is understood.",
                confidence="high",
                risk="high",
                route_task="Investigate the Project Miru runtime path that 18765 is observing, confirm why it is unhealthy, and report the exact live verification evidence without changing any 18080 user-facing files.",
            )
        )

    if str(miru_health.get("status") or "").strip().upper() == "FAILED":
        items.append(
            _build_judgment_item(
                key="dev_runtime",
                title="Miru Dev surface needs attention",
                summary=str(miru_health.get("detail") or "The Dev runtime is not healthy."),
                detail="The control surface itself is degraded, so every downstream signal is less trustworthy until the runtime path is understood.",
                category="failure",
                issue_type="environment",
                source=str(miru_health.get("source") or "Observed from Dev runtime status."),
                action_class="investigate",
                rationale="The Dev surface is the authority for these signals, so runtime instability here should be investigated before acting on the page.",
                confidence="high",
                risk="high",
                route_task="Investigate why the Miru Dev surface on 18765 is degraded, verify the failing runtime path live, and explain the safest next step without adding new background actions.",
            )
        )

    worker_status = learning_engine.get("worker_status") or {}
    worker_label = str(worker_status.get("label") or "").strip().lower()
    worker_detail = str(worker_health.get("detail") or worker_status.get("detail") or "").strip()
    if worker_label in {"stale", "offline", "stalled"}:
        items.append(
            _build_judgment_item(
                key="worker_heartbeat",
                title="Worker heartbeat needs investigation",
                summary=worker_detail or "The learner heartbeat looks stale or offline.",
                detail=str(
                    worker_health.get("source") or "Derived from the learning engine heartbeat."
                ),
                category="warning",
                issue_type="worker",
                source="Learning engine heartbeat and worker status.",
                action_class="worker_prompt",
                rationale="Miru can still answer from cached state, but a stale worker signal can hide blocked learning or an idle process that needs confirmation.",
                confidence="high" if worker_label in {"stale", "stalled"} else "medium",
                risk="medium",
                route_task="Inspect the Miru learner heartbeat and worker state on 18765, explain why the signal is stale, and verify whether the worker is safely idle or blocked before recommending any retry.",
            )
        )

    worker_action = str(worker_last_run.get("action") or "").strip().lower()
    worker_blocker = str(
        worker_last_run.get("blocker") or worker_last_run.get("no_new_work_reason") or ""
    ).strip()
    if worker_action in {"overlap_blocked", "blocked", "error"} and worker_blocker:
        issue_type = (
            "source_freshness"
            if "snapshot" in worker_blocker.lower() or "overlap" in worker_action
            else "intelligence"
        )
        items.append(
            _build_judgment_item(
                key="worker_last_run",
                title=humanize_snake_label(worker_action) or "Scheduled worker blocked",
                summary=worker_blocker,
                detail=str(
                    worker_last_run.get("exact_snapshot_needed")
                    or worker_last_run.get("timestamp_display")
                    or ""
                ),
                category="watch" if worker_action == "overlap_blocked" else "warning",
                issue_type=issue_type,
                source="Scheduled worker last-run record.",
                action_class="safe_check",
                rationale="A read-only source or overlap check can confirm whether the worker is blocked by incomplete inputs before you try anything else.",
                confidence="medium",
                risk="low" if worker_action == "overlap_blocked" else "medium",
                route_task="Check the scheduled worker blocker that Miru is surfacing on 18765, confirm whether the snapshot or overlap state is incomplete, and report the safest next action without changing 18080.",
            )
        )

    truth_mode = str(truth_source.get("mode") or "").strip().lower()
    if truth_mode == "local_fallback":
        items.append(
            _build_judgment_item(
                key="truth_source",
                title="Using local worktree truth source",
                summary=str(
                    truth_source.get("detail") or "Miru is using local worktree data for this view."
                ),
                detail="The page is grounded in local worktree signals rather than a remote truth source.",
                category="info",
                issue_type="environment",
                source="Truth-source descriptor.",
                action_class="observe_only",
                rationale="This is informative rather than broken, but it matters when comparing what you see here to another runtime.",
                confidence="high",
                risk="low",
            )
        )

    if not items:
        items.append(
            _build_judgment_item(
                key="stable",
                title="Miru looks stable from this Dev surface",
                summary="The current runtime, worker, and governed signals do not surface an active failure or review blocker.",
                detail="Keep observing from the current Dev control layer.",
                category="info",
                issue_type="runtime",
                source="Rules-based interpretation of current control-layer signals.",
                action_class="observe_only",
                rationale="No surfaced signal currently justifies a retry, intervention, or human review step.",
                confidence="medium",
                risk="low",
            )
        )

    ranked_items = sorted(items, key=_judgment_priority)
    primary = ranked_items[0]
    recommendation = primary.get("recommendation") or {}
    primary_category = str(primary.get("category") or "").strip().lower()
    primary_type = str(primary.get("issue_type") or "").strip().lower()

    if primary_category == "needs_review":
        what_i_think = (
            "Miru looks operational, but a governed item is waiting for a human decision."
        )
    elif primary_type == "worker":
        what_i_think = "Miru is up, but the learning worker signal looks stale or blocked."
    elif primary_type == "runtime":
        what_i_think = "One of the observed runtime paths needs attention before Miru should trust follow-on actions."
    elif primary_type == "source_freshness":
        what_i_think = (
            "Miru is up, but one of the supporting source inputs looks incomplete or stale."
        )
    else:
        what_i_think = (
            "Miru is interpreting the current worktree signals without surfacing a hard failure."
        )

    what_matters = str(
        primary.get("summary") or recent_issues.get("summary") or "No dominant concern surfaced."
    )
    action_label = str(recommendation.get("label") or "Observe only")
    if str(recommendation.get("guardrail_label") or "") == "Review required":
        what_next = (
            f"{action_label}. Human review should happen before any related retry or apply step."
        )
    else:
        what_next = f"{action_label}. {recommendation.get('rationale') or 'Use the current judgment to choose the next safe step.'}"

    return {
        "status": primary_category or "info",
        "status_label": str(primary.get("category_label") or "Info"),
        "tone": str(primary.get("tone") or "neutral"),
        "headline": str(primary.get("title") or "Miru judgment"),
        "what_i_think": what_i_think,
        "what_matters_most": what_matters,
        "what_i_recommend_next": what_next,
        "guardrail_label": str(recommendation.get("guardrail_label") or "Read-only"),
        "retry_guidance": str(
            recommendation.get("retry_guidance") or "Observe the current state before retrying."
        ),
        "primary": primary,
        "items": ranked_items[:4],
        "secondary": ranked_items[1:4],
        "source": "Rules-based interpretation of runtime health, worker freshness, governed review state, scheduled worker signals, and truth-source context.",
        "activity_hint": (
            str((recent_activity[0] or {}).get("title") or "") if recent_activity else ""
        ),
    }


def build_dev_control_layer(
    payload: dict[str, Any],
    *,
    force_runtime_probe: bool = False,
) -> dict[str, Any]:
    issues = payload.get("issues") or {}
    project = payload.get("project_miru") or {}
    surface_status = payload.get("surface_status") or {}
    learning_engine = payload.get("learning_engine") or {}
    intelligence = payload.get("intelligence_status") or {}
    truth_source = payload.get("truth_source") or {}
    runtime_status = load_runtime_status_payload(force=force_runtime_probe)

    miru_surface = surface_status.get("miru_ai") or {}
    miru_running = str(miru_surface.get("status") or "").strip().lower() == "running"
    miru_issue_warn = str((issues.get("miru_ai") or {}).get("tone") or "") == "warn"
    project_reachable = bool(project.get("reachable"))
    project_status_code = int(project.get("status_code") or 0)  # noqa: F841
    project_runtime_ok = str(runtime_status.get("18080") or "").strip().lower() == "ok"
    project_probe_state = str(runtime_status.get("project_probe_state") or "").strip().lower()
    project_probe_certainty = (
        str(runtime_status.get("project_probe_certainty") or "").strip().lower()
    )
    runtime_checked_at = _format_timestamp_readable(
        str(runtime_status.get("checked_at") or "")
    ) or str(runtime_status.get("checked_at") or "")
    worker_status = learning_engine.get("worker_status") or {}
    worker_state = str(worker_status.get("status") or "").strip().lower()
    worker_detail = str(
        worker_status.get("detail") or (intelligence.get("worker") or {}).get("detail") or ""
    ).strip()
    worker_has_heartbeat = bool(str(learning_engine.get("last_heartbeat") or "").strip())
    worker_store_visible = bool(learning_engine.get("status_db_exists"))

    system_health = [
        _build_control_observation(
            key="miru_ai",
            label="18765 Dev surface",
            detail=str(
                (issues.get("miru_ai") or {}).get("detail") or "Miru AI Dev status is available."
            ),
            source="Observed from the local /api/dev-status worktree payload.",
            confirmed=miru_running and not miru_issue_warn,
            failed=not miru_running,
        ),
        _build_control_observation(
            key="project_miru",
            label="18080 Project Miru",
            detail=(
                f"Observed healthy via local runtime probe ({runtime_checked_at})."
                if project_runtime_ok
                and project_probe_certainty == "confirmed"
                and runtime_checked_at
                else (
                    "Storefront answered the confirmatory direct check, but the fast runtime probe was inconclusive."
                    if project_runtime_ok and project_probe_certainty != "confirmed"
                    else str(
                        project.get("detail")
                        or runtime_status.get("project_detail")
                        or "Project Miru observation is not available yet."
                    )
                )
            ),
            source=(
                "Observed from the cached runtime probe plus a confirmatory direct localhost check when the fast probe is inconclusive."
            ),
            confirmed=project_runtime_ok and project_probe_certainty == "confirmed",
            failed=(not project_runtime_ok)
            and (not project_reachable)
            and project_probe_state == "confirmed_unhealthy",
        ),
        _build_control_observation(
            key="worker",
            label="Learner / worker heartbeat",
            detail=worker_detail or "Worker freshness is not available yet.",
            source=(
                f"Heartbeat from {learning_engine.get('last_heartbeat')}."
                if learning_engine.get("last_heartbeat")
                else "Worker freshness derived from learning engine status."
            ),
            confirmed=worker_state in {"fresh", "ok"}
            or str((intelligence.get("worker") or {}).get("tone") or "") == "good",
            failed=not worker_store_visible and not worker_has_heartbeat,
        ),
    ]

    recent_issues = _build_control_recent_issues(payload)
    recent_activity = _build_control_recent_activity(payload)
    judgment = _build_control_judgment(
        payload,
        system_health=system_health,
        recent_issues=recent_issues,
        recent_activity=recent_activity,
    )
    status_sources = {
        "health": (
            "Health cards reuse /api/dev-status and the same cached local runtime probe used by /api/runtime/status."
            + (f" Last checked {runtime_checked_at}." if runtime_checked_at else "")
        ),
        "judgment": judgment.get("source") or "Rules-based interpretation of current Dev signals.",
        "recent_issues": recent_issues.get("source")
        or "Issue detection and runtime freshness signals.",
        "recent_activity": "Recent activity reuses the learning engine event log, governed autopilot rollup, and scheduled worker run record.",
        "truth": str(truth_source.get("detail") or "Using the current Dev runtime snapshot."),
    }
    summary = (
        f"18765: {system_health[0]['status']}. "
        f"18080: {system_health[1]['status']}. "
        f"Worker: {system_health[2]['status']}. "
        f"Judgment: {judgment.get('headline') or recent_issues['headline']}."
    )
    copy_summary = "\n".join(
        [
            "Miru Control Layer summary",
            summary,
            f"What Miru thinks: {judgment.get('what_i_think') or 'Judgment summary unavailable.'}",
            f"What matters most: {judgment.get('what_matters_most') or recent_issues.get('summary') or 'No dominant concern surfaced.'}",
            f"Recommended next: {judgment.get('what_i_recommend_next') or 'Observe the current state.'}",
            f"Guardrail: {judgment.get('guardrail_label') or 'Read-only'}",
            f"Health source: {status_sources['health']}",
            f"Judgment source: {status_sources['judgment']}",
            f"Issue source: {status_sources['recent_issues']}",
            f"Activity source: {status_sources['recent_activity']}",
            f"Truth source: {status_sources['truth']}",
        ]
    )
    return {
        "summary": summary,
        "judgment": judgment,
        "system_health": system_health,
        "runtime_reliability": {
            "project_miru": {
                "healthy": project_runtime_ok,
                "uncertain": project_runtime_ok and project_probe_certainty != "confirmed",
                "state": project_probe_state
                or ("confirmed_healthy" if project_runtime_ok else "confirmed_unhealthy"),
                "certainty": project_probe_certainty or "confirmed",
                "detail": str(runtime_status.get("project_detail") or ""),
                "fast_probe": dict(runtime_status.get("project_fast_probe") or {}),
                "confirm_probe": dict(runtime_status.get("project_confirm_probe") or {}),
            }
        },
        "recent_issues": recent_issues,
        "recent_activity": recent_activity,
        "status_sources": status_sources,
        "copy_summary": copy_summary,
    }


def ensure_control_layer_payload(
    payload: dict[str, Any] | None,
    *,
    force_runtime_probe: bool = False,
) -> dict[str, Any]:
    out = dict(payload or {})
    out["control_layer"] = build_dev_control_layer(out, force_runtime_probe=force_runtime_probe)
    try:
        out["action_governance"] = build_action_governance_snapshot(
            dev_payload=out,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            runtime_dossier_db_path=LEARNING_DOSSIER_DB_PATH,
            canonical_dossier_db_path=DOSSIER_DB_PATH,
            rules_db_path=PROJECT_ROOT / "data" / "miru_official_rules.db",
            deck_intel_db_path=DECK_INTEL_DB_PATH,
            prices_path=PRICES_PATH,
            persist=False,
        )
    except Exception as exc:
        out["action_governance"] = {
            "generated_at": current_timestamp(),
            "error": str(exc),
            "actions": {
                "all": [],
                "allowed_now": [],
                "allowed_with_review": [],
                "blocked": [],
                "not_applicable": [],
            },
            "publication_readiness": {
                "counts": {},
                "remaining_candidate_count": 0,
                "top_targets": [],
                "target_evaluation": {},
            },
        }
    return out


def load_operator_self_report() -> dict[str, Any]:
    """Cached read-only snapshot from data/ DBs via tools.miru_self_report (insight coverage, gaps, next action)."""
    try:
        sig = (
            path_signature(FALLBACK_CATALOG_DB_PATH),
            path_signature(LEARNING_DOSSIER_DB_PATH),
        )

        def _builder() -> dict[str, Any]:
            return build_self_report(PROJECT_ROOT)

        return get_ttl_cached_value(
            "operator_self_report_v2",
            ttl_seconds=20.0,
            signature=sig,
            builder=_builder,
        )
    except Exception as exc:
        return {
            "error": str(exc),
            "generated_at": None,
            "schema_version": 0,
            "metrics": {},
            "intelligence_surface": {},
        }


def _operator_handoff_resolution_payload(
    *,
    fingerprint: str,
    acknowledged_match: bool,
    underlying_need_still_present: bool,
    resolved_state: dict[str, Any] | None = None,
    self_report_error: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "need_signature_sha256": fingerprint or None,
        "operator_acknowledged_for_signature": acknowledged_match,
        "underlying_need_still_present": underlying_need_still_present,
    }
    if self_report_error:
        out["self_report_error"] = True
    if acknowledged_match and resolved_state:
        ra = resolved_state.get("resolved_at")
        if ra:
            out["resolved_at"] = ra
    return out


def build_operator_handoff_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Ready-to-copy worker prompt grounded in operator_self_report + dev issues.
    Read-only; does not change governance or publication rules.

    When self-report stays \"urgent\" after a worker completes a scoped task, the operator
    can POST /api/dev/operator-handoff/resolve to persist a fingerprint match; while the
    live fingerprint equals the stored one, has_active_handoff is false (backend truth).
    """
    dev_env = payload.get("dev_environment") or {}
    env_label = str(dev_env.get("environment") or "local").strip() or "local"
    runtime_target = str(dev_env.get("runtime_target") or "worktree").strip() or "worktree"
    target_environment = f"{env_label} · {runtime_target}"

    boundaries = (
        "Hard scope: Miru AI / this worktree (port 18765) only. "
        "Do not edit Project Miru site code or the port 18080 implementation. "
        "Do not edit pm/app.py. "
        "Preserve governance, publish gate, truth hierarchy, fail-closed behavior, autonomy rules, and source permissions."
    )

    osr = payload.get("operator_self_report") or {}
    intel = osr.get("intelligence_surface") or {}
    met = osr.get("metrics") or {}
    issues = payload.get("issues") or {}
    miru_issue_tone = str((issues.get("miru_ai") or {}).get("tone") or "good").strip().lower()

    if osr.get("error"):
        err = str(osr.get("error"))
        what = "Restore operator self-report so /dev can read live catalog intelligence metrics."
        why = f"Self-report error: {err}"
        prompt_text = "\n".join(
            [
                "Worker: Cursor",
                f"Target: {target_environment} (Miru AI Dev).",
                "",
                boundaries,
                "",
                "Problem:",
                why,
                "",
                "Task:",
                what,
            ]
        )
        return {
            "schema_version": 1,
            "state": "actionable",
            "has_active_handoff": True,
            "what_miru_needs": what,
            "why": why,
            "recommended_worker": "Cursor",
            "target_environment": target_environment,
            "prompt_text": prompt_text,
            "boundaries": boundaries,
            "resolution": _operator_handoff_resolution_payload(
                fingerprint="",
                acknowledged_match=False,
                underlying_need_still_present=True,
                self_report_error=True,
            ),
        }

    fp = compute_operator_handoff_need_fingerprint(osr, issues)
    ack_match, resolved_state = is_operator_handoff_acknowledged_for_fingerprint(fp)

    cap = str(osr.get("capability_level") or "").strip()
    primary_code = str(intel.get("primary_limitation_code") or "").strip()
    top_blocker = str(osr.get("top_blocker") or "").strip()
    next_priority = str(osr.get("next_priority") or "").strip()
    primary_human = str(intel.get("primary_limitation_human") or "").strip()
    rec = str(intel.get("recommended_next_operator_action") or "").strip()

    cov = met.get("coverage_pct")
    cwi = met.get("cards_with_any_insight")
    strong = met.get("cards_with_strong_insight")

    urgent = (
        primary_code not in ("", "balanced")
        or cap in ("blocked", "minimal")
        or miru_issue_tone == "warn"
    )

    if not urgent:
        # Clear state: no copyable worker prompt; metrics live in self-report / monitor.
        return {
            "schema_version": 1,
            "state": "clear",
            "has_active_handoff": False,
            "what_miru_needs": "No active handoff.",
            "why": "Current self-report shows no urgent limitation and Miru AI issues are not in warn state.",
            "recommended_worker": "Cursor",
            "target_environment": target_environment,
            "prompt_text": "",
            "boundaries": boundaries,
            "resolution": _operator_handoff_resolution_payload(
                fingerprint=fp,
                acknowledged_match=ack_match,
                underlying_need_still_present=False,
                resolved_state=resolved_state if ack_match else None,
            ),
        }

    if ack_match:
        return {
            "schema_version": 1,
            "state": "clear",
            "has_active_handoff": False,
            "what_miru_needs": "No active handoff.",
            "why": (
                "Operator marked this worker handoff resolved for the current need signature. "
                "Self-report may still show a catalog limitation until data catches up; "
                "an active handoff returns automatically if the actionable need signature changes. "
                "Use 'Show handoff again' on /dev if you need to re-open before then."
            ),
            "recommended_worker": "Cursor",
            "target_environment": target_environment,
            "prompt_text": "",
            "boundaries": boundaries,
            "resolution": _operator_handoff_resolution_payload(
                fingerprint=fp,
                acknowledged_match=True,
                underlying_need_still_present=True,
                resolved_state=resolved_state,
            ),
        }

    what = (next_priority or rec or top_blocker or primary_human or "").strip() or (
        "Address the catalog intelligence gap indicated by the current self-report."
    )
    why = (primary_human or top_blocker or "").strip() or (
        "Operator self-report indicates a non-balanced limitation, degraded capability, or Miru AI warn state."
    )

    cov_s = f"{float(cov):.2f}" if cov is not None else "—"
    cwi_s = str(cwi) if cwi is not None else "—"
    strong_s = str(strong) if strong is not None else "—"

    prompt_text = "\n".join(
        [
            "Worker: Cursor",
            f"Target environment: {target_environment} (Miru AI Dev, port 18765).",
            "",
            boundaries,
            "",
            "Current facts (from live operator self-report):",
            f"- Capability: {cap or 'unknown'}",
            f"- Primary limitation ({primary_code or '—'}): {primary_human or '—'}",
            f"- Top blocker: {top_blocker or '—'}",
            f"- Next priority: {next_priority or rec or '—'}",
            f"- Insight coverage: {cov_s}% of catalog with any row ({cwi_s} cards); strong tier: {strong_s}",
            f"- Dev issues (miru_ai tone): {miru_issue_tone}",
            "",
            "Task:",
            what,
            "",
            "Do not weaken governance, publish gate, truth hierarchy, or fail-closed behavior.",
        ]
    )

    return {
        "schema_version": 1,
        "state": "actionable",
        "has_active_handoff": True,
        "what_miru_needs": what,
        "why": why,
        "recommended_worker": "Cursor",
        "target_environment": target_environment,
        "prompt_text": prompt_text,
        "boundaries": boundaries,
        "resolution": _operator_handoff_resolution_payload(
            fingerprint=fp,
            acknowledged_match=False,
            underlying_need_still_present=True,
        ),
    }


def _strip_dev_cockpit_dev_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Smaller JSON for /dev cockpit refresh (view=summary&surface=cockpit).
    Preserves operator_self_report, operator_handoff, issues, learning_engine, runtime fields.
    Read-only trim; does not change governance or stored data.
    """
    out = trim_dev_status_payload(payload)
    for k in (
        "activity_feed",
        "monitor",
        "image_coverage_by_set",
        "resource_metrics",
        "learning_metrics",
        "worktree_update_summary",
    ):
        out.pop(k, None)
    cl = out.get("control_layer")
    if isinstance(cl, dict):
        out["control_layer"] = {
            "system_health": list(cl.get("system_health") or []),
            "runtime_reliability": dict(cl.get("runtime_reliability") or {}),
            "_cockpit_trimmed": True,
        }
    return out


def trim_dev_status_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(payload or {})
    for key in (
        "voyage",
        "training",
        "training_progress",
        "intelligence_progress",
        "operator_console",
        "worker_router",
        "judgment",
        "activity",
        "activity_states",
        "limits_by_provider",
    ):
        out.pop(key, None)
    cl = out.get("control_layer")
    if isinstance(cl, dict):
        out["control_layer"] = {
            "system_health": list(cl.get("system_health") or []),
            "runtime_reliability": dict(cl.get("runtime_reliability") or {}),
        }
    return out


def build_dev_status(
    training_status: dict[str, Any] | None = None,
    *,
    lightweight: bool = False,
    include_heavy_sections: bool = False,
    fetch_truth_source: bool = True,
) -> dict[str, Any]:
    if fetch_truth_source and resolve_runtime_monitor_status_url():
        remote_payload = fetch_truth_source_dev_status(
            summary_only=lightweight,
            include_heavy=include_heavy_sections,
        )
        if remote_payload:
            out = dict(remote_payload)
            out["dev_environment"] = build_dev_environment_descriptor()
            out["operator_self_report"] = load_operator_self_report()
            out["operator_handoff"] = build_operator_handoff_payload(out)
            out = ensure_governed_autopilot_payload(out)
            _apply_worker_heartbeat_fallback(out)
            return trim_dev_status_payload(ensure_control_layer_payload(out))
    training_status = training_status or build_training_status()
    _learning_sig = (
        path_signature(LEARNING_QUEUE_DB_PATH),
        path_signature(LEARNING_STATUS_DB_PATH),
        path_signature(LEARNING_DOSSIER_DB_PATH),
    )
    _total_cards = int(training_status.get("total_cards") or 0)
    learning_status = get_ttl_cached_value(
        "learning_engine_status",
        ttl_seconds=5.0,
        signature=_learning_sig,
        builder=lambda: load_learning_engine_status(
            queue_db_path=LEARNING_QUEUE_DB_PATH,
            status_db_path=LEARNING_STATUS_DB_PATH,
            dossier_db_path=LEARNING_DOSSIER_DB_PATH,
            total_cards=_total_cards,
        ),
    )
    # `/api/dev-status` should always expose a fresh telemetry-based voyage snapshot.
    # Do not merge a previously built voyage dict back in here, because that can
    # preserve older fictional milestone fields from an earlier code path.
    training_status["voyage"] = build_voyage_state(
        training_status,
        learning_status=learning_status,
    )
    activity = build_learning_engine_activity(learning_status, build_miru_activity(training_status))
    pushover_state = build_pushover_runtime_state(
        training_status=training_status,
        learning_status=learning_status,
    )
    links = {
        "miru_ai": build_route_url("/"),
        "project_miru": build_companion_url(PROJECT_MIRU_DEV_PORT, "/"),
    }
    training_progress = training_status.get("training_progress")  # noqa: F841
    if lightweight:
        _rt_probe = build_runtime_status_payload()
        _pm_ok = str(_rt_probe.get("18080") or "").strip().lower() == "ok"
        project_status = {
            "reachable": _pm_ok,
            "status_code": 200 if _pm_ok else 0,
            "detail": str(
                _rt_probe.get("project_detail")
                or (
                    "Project Miru responded to runtime probe."
                    if _pm_ok
                    else "Project Miru not reachable on 18080."
                )
            ),
        }
        limits_status = []
        limits_by_provider = {}
        validation_audit = {
            "recent_conflicts": [],
            "lowest_confidence": [],
            "recently_validated": [],
            "rejected_evidence": [],
        }
        image_coverage_by_set = []
        monitor = build_deferred_monitor_payload(training_status, learning_status, activity)
        resource_metrics = []
    else:
        project_status = get_ttl_cached_value(
            "project_miru_route_status",
            ttl_seconds=5.0,
            signature=PROJECT_MIRU_DEV_PORT,
            builder=lambda: inspect_local_http_route(f"http://127.0.0.1:{PROJECT_MIRU_DEV_PORT}/"),
        )
        limits_status = load_limits_status()
        limits_by_provider = {  # noqa: F841
            e["provider"]: e for e in limits_status if e.get("provider")
        }
        if include_heavy_sections:
            validation_audit = load_cached_validation_audit()
            image_coverage_by_set = load_cached_image_coverage_by_set()
            monitor_source = load_cached_monitor_source(
                training_status, learning_status, validation_audit
            )
            monitor = build_monitor_payload(training_status, monitor_source)
            resource_metrics = load_cached_resource_metrics()
        else:
            validation_audit = {
                "recent_conflicts": [],
                "lowest_confidence": [],
                "recently_validated": [],
                "rejected_evidence": [],
            }
            image_coverage_by_set = []
            monitor = build_deferred_monitor_payload(training_status, learning_status, activity)
            resource_metrics = []
    _updated = current_timestamp()
    payload = {
        "updated_at": _updated,
        "updated_at_display": _format_timestamp_readable(_updated),
        "snapshot_inputs": learning_status.get("snapshot_inputs") or {},
        "links": links,
        "limits_status": limits_status,
        "monitor": monitor,
        "learning_metrics": build_learning_engine_metrics(learning_status),
        "validation_audit": validation_audit,
        "validation_audit_url_base": build_route_url("/api/dev/card-validation"),
        "image_coverage_by_set": image_coverage_by_set,
        "resource_metrics": resource_metrics,
        "issues": build_issue_detection(training_status, project_status, learning_status),
        "catalog_status": training_status["catalog_status"],
        "dossier_status": training_status["dossier_status"],
        "pushover": pushover_state,
        "intelligence_status": build_dev_intelligence_status(
            training_status,
            learning_status,
            pushover_state,
            activity,
        ),
        "project_miru": {
            "reachable": bool(project_status.get("reachable")),
            "status_code": int(project_status.get("status_code") or 0),
            "detail": project_status.get("detail") or "Unavailable",
            "url": links["project_miru"],
        },
        "control_deck": {
            "worktree_site": links["project_miru"],
            "miru_dev_console": build_route_url("/dev"),
            "main_site": build_companion_url(PROJECT_MIRU_PORT, "/"),
        },
        "surface_status": {
            "miru_ai": {
                "port": str(request.environ.get("SERVER_PORT", "18765")),
                "status": "Running",
            },
            "worktree_dashboard": {
                "port": str(PROJECT_MIRU_DEV_PORT),
                "status": ("Running" if project_status.get("reachable") else "Offline"),
            },
        },
        "truth_source": build_runtime_truth_source_descriptor(
            mode="local_fallback",
            status_url=resolve_runtime_monitor_status_url() or "",
        ),
        "worker_last_run": _enrich_worker_last_run_display(load_worker_last_run()),
        "pending_approvals_count": count_publication_review_rows(
            project_db_path=FALLBACK_CATALOG_DB_PATH
        )
        + len(load_pending_approvals(status_db_path=LEARNING_STATUS_DB_PATH)),
        "publication_review_count": count_publication_review_rows(
            project_db_path=FALLBACK_CATALOG_DB_PATH
        ),
        "dev_environment": (dev_environment_descriptor := build_dev_environment_descriptor()),
        "worktree_update_summary": build_worktree_update_summary(
            training_status,
            learning_status,
            validation_audit,
            dev_environment_descriptor,
        ),
        "learning_engine": _enrich_learning_engine_display(
            _reconcile_learner_status_with_process_truth(
                {
                    **learning_status,
                    **compute_learner_state_and_freshness(learning_status),
                    **_get_worktree_learner_control_status(),
                },
                activity,
            ),
        ),
        "activity_feed": load_monitor_engine_events(
            status_db_path=LEARNING_STATUS_DB_PATH,
            limit=20 if lightweight else 40,
        )["recent_activity"],
        "last_insight_sync": _get_last_insight_sync_report(),
        "operator_self_report": load_operator_self_report(),
        "mcp_governance": build_mcp_governance_summary(),
    }
    payload["operator_handoff"] = build_operator_handoff_payload(payload)
    payload = ensure_governed_autopilot_payload(payload)
    _apply_worker_heartbeat_fallback(payload)
    return trim_dev_status_payload(
        ensure_control_layer_payload(payload, force_runtime_probe=lightweight)
    )


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


_TAILSCALE_IPV4_CGNAT = ipaddress.ip_network(
    "100.64.0.0/10"
)  # Tailscale IPv4 (shared CGNAT range; RFC 6598)
_TAILSCALE_IPV6_ULA = ipaddress.ip_network(
    "fd7a:115c:a1e0::/48"
)  # Tailscale IPv6 ULA (matches runtime restart allowlist)


def is_local_request() -> bool:
    """True for loopback, private LAN, and Tailscale (100.64.0.0/10 + TS IPv6 ULA).

    Uses Werkzeug ``REMOTE_ADDR`` only (not X-Forwarded-For). Approve/reject and similar
    dev controls rely on this for localhost-equivalent trust.
    """
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr:
        return False
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        trusted_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            _TAILSCALE_IPV4_CGNAT,
        )
        return any(address in network for network in trusted_networks)
    if isinstance(address, ipaddress.IPv6Address):
        return address in _TAILSCALE_IPV6_ULA
    return False


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
                + (f" Issue: {catalog_status['error']}" if catalog_status["error"] else "")
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
        href = item.get("path") or url_for(item["endpoint"])
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
                    else ("good" if catalog_status["usable"] else "warn")
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


# Leader Hub: read-only load from miru_deck_intel.db; no new intelligence logic.
_LEADER_HUB_FORMAT = ""


def load_card_page_data(card_code: str) -> dict[str, Any]:
    """
    Load card info and usage for the Card Page. Reads only from existing
    catalog, dossiers, and deck intel; no new intelligence computation.
    """
    code = (card_code or "").strip().upper()
    if not code:
        return {
            "card_name": "",
            "card_code": "",
            "set": "",
            "cost": None,
            "type": "",
            "color": "",
            "image_path": None,
            "leaders_using": [],
            "roles": [],
            "miru_insight": None,
        }

    out: dict[str, Any] = {
        "card_name": "",
        "card_code": code,
        "set": "",
        "cost": None,
        "type": "",
        "color": "",
        "image_path": None,
        "leaders_using": [],
        "roles": [],
        "miru_insight": None,
    }

    # Card basics from fallback catalog
    if FALLBACK_CATALOG_DB_PATH.is_file():
        try:
            conn = sqlite3.connect(f"file:{FALLBACK_CATALOG_DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT canonical_code, set_code, card_name, color, card_type, cost FROM cards WHERE canonical_code = ?",
                (code,),
            ).fetchone()
            if row:
                out["card_name"] = (row["card_name"] or "").strip() or code
                out["set"] = (row["set_code"] or "").strip()
                out["color"] = (row["color"] or "").strip()
                out["type"] = (row["card_type"] or "").strip()
                out["cost"] = row["cost"] if row["cost"] is not None else None
            img = conn.execute(
                "SELECT v.image_path FROM card_variants v JOIN cards c ON c.id = v.card_id WHERE c.canonical_code = ? LIMIT 1",
                (code,),
            ).fetchone()
            if img and img["image_path"]:
                out["image_path"] = (img["image_path"] or "").strip()
            conn.close()
        except sqlite3.Error:
            pass

    # Dossiers: fallback for name only
    if not out["card_name"] and DOSSIER_DB_PATH.is_file():
        try:
            dconn = sqlite3.connect(f"file:{DOSSIER_DB_PATH}?mode=ro", uri=True)
            r = dconn.execute(
                "SELECT card_name FROM cards WHERE canonical_code = ?", (code,)
            ).fetchone()
            if r and r[0]:
                out["card_name"] = (r[0] or "").strip()
            dconn.close()
        except sqlite3.Error:
            pass
    if not out["card_name"]:
        out["card_name"] = code

    # Usage: leaders and roles from deck intel
    if DECK_INTEL_DB_PATH.is_file():
        try:
            dconn = sqlite3.connect(f"file:{DECK_INTEL_DB_PATH}?mode=ro", uri=True)
            dconn.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in dconn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                    ("archetype_profile_cards",),
                ).fetchall()
            }
            if "archetype_profile_cards" in tables:
                rows = dconn.execute(
                    """
                    SELECT leader_code, role_label
                    FROM archetype_profile_cards
                    WHERE card_code = ? AND format_code = ?
                    ORDER BY leader_code, role_label
                    """,
                    (code, _LEADER_HUB_FORMAT),
                ).fetchall()
                leaders_seen: set[str] = set()
                role_list: list[dict[str, str]] = []
                for r in rows:
                    leader = (r["leader_code"] or "").strip()
                    role = (r["role_label"] or "tech").lower()
                    if leader:
                        leaders_seen.add(leader)
                        role_list.append({"leader_code": leader, "role_label": role})
                out["leaders_using"] = sorted(leaders_seen)
                out["roles"] = role_list
            dconn.close()
        except sqlite3.Error:
            pass

    # Optional Miru insight from usage only (no new logic)
    out["miru_insight"] = _build_card_page_insight(out)
    return out


def _build_card_page_insight(data: dict[str, Any]) -> dict[str, str] | None:
    """Build a short card insight from existing usage data. Returns None if no meaningful signal."""
    roles = data.get("roles") or []
    leaders = data.get("leaders_using") or []
    if not roles and not leaders:
        return None
    # One sentence from stored usage: core/flex per leader (dedupe by leader)
    core_leaders = sorted({r["leader_code"] for r in roles if r.get("role_label") == "core"})
    flex_leaders = sorted({r["leader_code"] for r in roles if r.get("role_label") == "flex"})
    if core_leaders:
        if len(core_leaders) == 1:
            text = f"I see this card in the core shell for {core_leaders[0]}."
        else:
            text = f"I see this card as core in {len(core_leaders)} leader builds."
    elif flex_leaders:
        if len(flex_leaders) == 1:
            text = f"This card shows up as flex in {flex_leaders[0]} builds."
        else:
            text = f"This card appears as flex in {len(flex_leaders)} leader builds."
    else:
        text = f"This card appears in archetype profiles for {', '.join(leaders[:3])}{'…' if len(leaders) > 3 else ''}."
    return {
        "category": "Strategy Insight",
        "text": text,
        "confidence": "medium" if len(leaders) >= 2 else "low",
    }


def _install_panel_extract_set_code(canonical_code: str) -> str:
    """Match ``miru_ai.workers.image_fetcher._extract_set_code`` (OPxx from OPxx-###)."""
    code = str(canonical_code or "").strip().upper()
    if not code:
        return "UNKNOWN"
    if code.startswith("P-"):
        return "P"
    m = re.match(r"^([A-Z]+\d{2})-", code)
    if m:
        return m.group(1)
    return "UNKNOWN"


def _install_plan_png_relpath(canonical_code: str, variant_family: str) -> str | None:
    """Relative path under ``MIRU_ASSETS_ROOT`` for the card PNG (mirrors image_fetcher layout)."""
    code = str(canonical_code or "").strip().upper()
    if not code:
        return None
    set_code = _install_panel_extract_set_code(code)
    vf = str(variant_family or "").strip().lower().replace("-", "_")

    if vf in ("alt", "alt_art", "alternate_art"):
        return f"{set_code}/alt_art/{code}_alt.png"
    if vf == "base":
        if code.startswith("P-"):
            return f"P/base/{code}.png"
        return f"{set_code}/base/{code}.png"
    if vf == "sp":
        return f"{set_code}/sp/{code}_sp.png"
    if vf == "tr":
        return f"{set_code}/tr/{code}_tr.png"
    if vf == "parallel":
        return f"{set_code}/parallel/{code}_p1.png"
    if vf in ("ir", "illustration_rare"):
        return f"{set_code}/alt_art/{code}_ir.png"
    if vf in ("mr", "manga_rare"):
        return f"{set_code}/alt_art/{code}_mr.png"
    if vf in ("gmr", "golden_manga_rare"):
        return f"{set_code}/alt_art/{code}_gmr.png"
    return None


def _load_chapter19_11c_install_manifest() -> dict[str, str]:
    """Optional Chapter 19.11C manifest: official DOM-resolved PNG relpaths under Miru_Assets."""
    path = CHAPTER19_11C_INSTALL_PANEL_MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        inner = data.get("png_relpath_by_canonical_code")
        if not isinstance(inner, dict):
            return {}
        return {
            str(k).strip().upper(): str(v).strip().replace("\\", "/")
            for k, v in inner.items()
            if str(k).strip() and str(v).strip()
        }
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _install_plan_resolve_install_panel_image(
    canonical_code: str, variant_family: str
) -> dict[str, Any]:
    """
    Resolve PNG + thumb URLs for the dev install panel (18765 ``/img/`` route).

    Thumb prefers a sibling ``.webp`` when present; modal always uses PNG.
    """
    code_u = str(canonical_code or "").strip().upper()
    manifest = _load_chapter19_11c_install_manifest()
    if code_u in manifest:
        rel_png = manifest[code_u]
    else:
        rel_png = _install_plan_png_relpath(canonical_code, variant_family)
    root = MIRU_ASSETS_ROOT.resolve()
    out: dict[str, Any] = {
        "ok": False,
        "png_url": "",
        "thumb_url": "",
        "modal_image_url": "",
        "thumb_is_webp": False,
        "png_exists": False,
        "webp_exists": False,
        "png_relpath_posix": "",
        "thumb_relpath_posix": "",
    }
    if not rel_png:
        out["reason"] = "unknown_variant_family"
        return out

    posix = Path(rel_png).as_posix()
    out["png_relpath_posix"] = posix
    png_abs = (root / rel_png).resolve()
    try:
        png_abs.relative_to(root)
    except ValueError:
        out["reason"] = "path_outside_assets_root"
        return out

    webp_abs = png_abs.with_suffix(".webp")
    out["png_exists"] = png_abs.is_file()
    out["webp_exists"] = webp_abs.is_file()

    out["png_url"] = "/img/" + posix
    if out["webp_exists"]:
        thumb_rel = Path(rel_png).with_suffix(".webp").as_posix()
        out["thumb_relpath_posix"] = thumb_rel
        out["thumb_url"] = "/img/" + thumb_rel
        out["thumb_is_webp"] = True
    else:
        out["thumb_relpath_posix"] = posix
        out["thumb_url"] = out["png_url"]
        out["thumb_is_webp"] = False

    # Lightbox prefers original PNG; WebP-only fallback keeps verification usable.
    if out["png_exists"]:
        out["modal_image_url"] = out["png_url"]
    elif out["webp_exists"]:
        out["modal_image_url"] = out["thumb_url"]
    else:
        out["modal_image_url"] = ""

    out["ok"] = True
    return out


def _load_chapter19_12_price_hydration() -> dict[str, dict[str, str]]:
    """Load the Chapter 19.12 price hydration artifact keyed by printing_id."""
    path = CHAPTER19_12_PRICE_HYDRATION_CSV
    if not path.is_file():
        return {}
    price_fields = (
        "market_price",
        "mid_price",
        "low_price",
        "subtype",
        "prices_updated_at",
    )
    out: dict[str, dict[str, str]] = {}
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                if not raw:
                    continue
                pid = str(raw.get("printing_id") or "").strip()
                if not pid:
                    continue
                out[pid] = {f: str(raw.get(f) or "").strip() for f in price_fields}
    except Exception:
        pass
    return out


def load_op01_conflict_review_panel() -> dict[str, Any]:
    """Load OP01 conflict candidate rows for dual-image visual review.

    Pure read-only: queries image_assets and market_products for side-by-side
    comparison of Miru asset vs TCGPlayer product image.
    """
    CONFLICT_PIDS = (708, 788, 1157, 1158)  # noqa: N806
    REFERENCE_PID = 707  # noqa: N806

    out: dict[str, Any] = {
        "rows": [],
        "conflict_count": 0,
        "reference_count": 0,
        "available": False,
        "error": "",
    }

    try:
        db_path = FALLBACK_CATALOG_DB_PATH
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row

        all_pids = [*CONFLICT_PIDS, REFERENCE_PID]
        placeholders = ",".join("?" * len(all_pids))

        # Fetch printing details with image_assets and candidate market_products
        sql = f"""
            SELECT
                cv.id                AS printing_id,
                c.canonical_code     AS card_code,
                cv.variant_key,
                cv.is_alt,
                cv.is_sp,
                cv.is_manga_rare,
                cv.is_golden_manga_rare,
                ia.local_path        AS miru_image_path,
                ia.is_primary
            FROM card_variants cv
            JOIN cards c ON c.id = cv.card_id
            LEFT JOIN image_assets ia ON ia.printing_id = cv.id AND ia.is_primary = 1
            WHERE cv.id IN ({placeholders})
            ORDER BY c.canonical_code, cv.variant_key
        """
        printing_rows = con.execute(sql, all_pids).fetchall()

        # For each printing, find candidate market_products by card_code + treatment signal
        rows: list[dict[str, Any]] = []
        for pr in printing_rows:
            pid = pr["printing_id"]
            code = pr["card_code"]
            conflict_status = "CONFLICT_PENDING" if pid in CONFLICT_PIDS else "REFERENCE"

            # Find candidate market_products for this card_code
            candidates_sql = """
                SELECT
                    mp.id           AS market_product_id,
                    mp.product_name,
                    mp.image_url    AS tcg_image_url
                FROM market_products mp
                WHERE mp.product_name LIKE '%' || ? || '%'
                  AND (
                    mp.product_name LIKE '%Alternate Art%'
                    OR mp.product_name LIKE '%Parallel%'
                    OR mp.product_name LIKE '%Alt Art%'
                    OR mp.product_name LIKE '%Manga%'
                  )
                ORDER BY mp.product_name
            """
            cands = con.execute(candidates_sql, (code,)).fetchall()

            # Also check installed bridge (for reference rows)
            bridge_sql = """
                SELECT pmm.market_product_id, mp.product_name, mp.image_url AS tcg_image_url
                FROM printing_market_map pmm
                JOIN market_products mp ON mp.id = pmm.market_product_id
                WHERE pmm.printing_id = ? AND pmm.is_preferred = 1
            """
            installed = con.execute(bridge_sql, (pid,)).fetchall()
            installed_mp_id = installed[0]["market_product_id"] if installed else None

            # Build candidate list
            candidate_list = []
            for c in cands:
                candidate_list.append(
                    {
                        "market_product_id": c["market_product_id"],
                        "product_name": c["product_name"],
                        "tcg_image_url": c["tcg_image_url"] or "",
                        "is_installed": c["market_product_id"] == installed_mp_id,
                    }
                )

            miru_path = pr["miru_image_path"] or ""
            row = {
                "printing_id": pid,
                "card_code": code,
                "variant_key": pr["variant_key"],
                "conflict_status": conflict_status,
                "miru_image_path": miru_path,
                "miru_image_url": ("/img/" + miru_path) if miru_path else "",
                "candidates": candidate_list,
                "has_miru_image": bool(miru_path),
                "installed_mp_id": installed_mp_id,
            }
            rows.append(row)

        con.close()
        out["rows"] = rows
        out["conflict_count"] = sum(1 for r in rows if r["conflict_status"] == "CONFLICT_PENDING")
        out["reference_count"] = sum(1 for r in rows if r["conflict_status"] == "REFERENCE")
        out["available"] = True
    except Exception as exc:
        out["error"] = f"Could not load conflict review data: {exc}"
    return out


def load_lane2_candidate_review_panel() -> dict[str, Any]:
    """Load Lane 2 alt/sp/tr/mr candidate rows for dual-image visual review.

    Reads the candidate check CSV and enriches with DB images (read-only).
    Groups by printing_id, classifies as CLEAR / MULTI_MATCH / ALREADY_OWNED.
    """
    csv_path = PROJECT_ROOT / "data" / "overlays" / "op01_lane2_candidate_check.csv"
    out: dict[str, Any] = {
        "rows": [],
        "clear_count": 0,
        "multi_count": 0,
        "owned_count": 0,
        "available": False,
        "error": "",
    }

    if not csv_path.is_file():
        out["error"] = f"CSV not found: {csv_path}"
        return out

    try:
        import csv as _csv

        with open(csv_path, encoding="utf-8") as fh:
            csv_rows = [
                r
                for r in _csv.DictReader(fh)
                if r.get("sub_classification") in ("CLEAR", "MULTI_MATCH", "ALREADY_OWNED")
            ]

        if not csv_rows:
            return out

        # Group by printing_id
        from collections import OrderedDict

        grouped: dict[int, list[dict]] = OrderedDict()
        for r in csv_rows:
            pid = int(r["printing_id"])
            grouped.setdefault(pid, []).append(r)

        db_path = FALLBACK_CATALOG_DB_PATH
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row

        rows: list[dict[str, Any]] = []
        for pid, cand_rows in grouped.items():
            first = cand_rows[0]
            card_code = first["card_code"]
            variant_key = first["variant_key"]

            # Determine pid-level classification
            subs = {r["sub_classification"] for r in cand_rows}
            if "MULTI_MATCH" in subs:
                pid_class = "MULTI_MATCH"
            elif "ALREADY_OWNED" in subs and "CLEAR" not in subs:
                pid_class = "ALREADY_OWNED"
            elif "CLEAR" in subs:
                pid_class = "CLEAR"
            else:
                pid_class = first["sub_classification"]

            # Fetch miru image
            miru_row = con.execute(
                "SELECT local_path FROM image_assets "
                "WHERE printing_id = ? AND is_primary = 1 LIMIT 1",
                (pid,),
            ).fetchone()
            miru_path = miru_row["local_path"] if miru_row else ""

            # Build candidate list with TCG image URLs from DB
            candidate_list: list[dict[str, Any]] = []
            for cr in cand_rows:
                mpid_str = cr.get("market_product_id", "")
                if not mpid_str:
                    continue
                mpid = int(mpid_str)
                tcg_row = con.execute(
                    "SELECT image_url FROM market_products WHERE id = ?",
                    (mpid,),
                ).fetchone()
                tcg_url = (tcg_row["image_url"] or "") if tcg_row else ""
                existing_owner = cr.get("existing_preferred_pid", "")

                candidate_list.append(
                    {
                        "market_product_id": mpid,
                        "product_name": cr.get("product_name", ""),
                        "tcg_image_url": tcg_url,
                        "existing_owner_pid": int(existing_owner) if existing_owner else None,
                        "sub_classification": cr["sub_classification"],
                    }
                )

            row = {
                "printing_id": pid,
                "card_code": card_code,
                "variant_key": variant_key,
                "pid_classification": pid_class,
                "miru_image_path": miru_path,
                "miru_image_url": ("/img/" + miru_path) if miru_path else "",
                "has_miru_image": bool(miru_path),
                "candidates": candidate_list,
            }
            rows.append(row)

        con.close()
        out["rows"] = rows
        out["clear_count"] = sum(1 for r in rows if r["pid_classification"] == "CLEAR")
        out["multi_count"] = sum(1 for r in rows if r["pid_classification"] == "MULTI_MATCH")
        out["owned_count"] = sum(1 for r in rows if r["pid_classification"] == "ALREADY_OWNED")
        out["available"] = True
    except Exception as exc:
        out["error"] = f"Could not load Lane 2 candidate data: {exc}"
    return out


def load_chapter19_11_install_plan_panel() -> dict[str, Any]:
    path = CHAPTER19_11_INSTALL_PLAN_CSV
    out: dict[str, Any] = {
        "rows": [],
        "total_rows": 0,
        "clear_to_install_count": 0,
        "flagged_count": 0,
        "available": False,
        "error": "",
        "source_file": path.name,
        "chapter_label": "Chapter 19.11",
        "miru_assets_root_display": str(MIRU_ASSETS_ROOT),
    }
    if not path.is_file():
        out["error"] = "Install-plan artifact not found."
        return out

    fields = (
        "printing_id",
        "canonical_code",
        "variant_family",
        "candidate_mp_id",
        "candidate_mp_name",
        "pre_install_status",
        "snapshot_price_row_count",
    )
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                out["error"] = "Install-plan artifact is empty."
                return out

            rows: list[dict[str, Any]] = []
            clear_count = 0
            for raw in reader:
                if not raw:
                    continue
                row = {field: str(raw.get(field) or "").strip() for field in fields}
                status = row["pre_install_status"].upper()
                row["is_clear_to_install"] = status == "CLEAR_TO_INSTALL"
                if row["is_clear_to_install"]:
                    clear_count += 1
                    row["install_panel_image"] = _install_plan_resolve_install_panel_image(
                        row.get("canonical_code") or "",
                        row.get("variant_family") or "",
                    )
                else:
                    row["install_panel_image"] = None
                rows.append(row)

            rows.sort(key=lambda r: ((r.get("canonical_code") or ""), (r.get("printing_id") or "")))

            # Hydrate price data from Chapter 19.12 artifact
            price_map = _load_chapter19_12_price_hydration()
            if price_map:
                _empty_price: dict[str, str] = {
                    "market_price": "",
                    "mid_price": "",
                    "low_price": "",
                    "subtype": "",
                    "prices_updated_at": "",
                }
                for row in rows:
                    pid = row.get("printing_id") or ""
                    row["price"] = price_map.get(pid, _empty_price)

            out["rows"] = rows
            out["total_rows"] = len(rows)
            out["clear_to_install_count"] = clear_count
            out["flagged_count"] = len(rows) - clear_count
            out["available"] = True
            return out
    except Exception:
        out["error"] = "Install-plan artifact could not be read."
        return out


def render_page(page_key: str, current_endpoint: str):
    issues = startup_issues()
    brand_assets = build_brand_assets()
    training_status: dict[str, Any] = {}
    dev_status = None
    ready_install_panel: dict[str, Any] = {}
    op01_conflict_panel: dict[str, Any] = {}
    lane2_candidate_panel: dict[str, Any] = {}
    runtime_restart_token = ""
    status_snapshot: list[dict[str, Any]] = []
    runtime_dependencies: list[dict[str, Any]] = []
    runtime_issues: list[str] = []

    if page_key == "training":
        training_status = build_training_status()
        training_status["voyage"] = ensure_voyage_state(
            training_status, training_status.get("voyage")
        )
    elif page_key in ("dev", "dev_monitor"):
        dev_status = build_dev_bootstrap_status()
        if page_key == "dev":
            # Unified /dev surface: heavy legacy panels are not rendered (avoid wasted work).
            ready_install_panel = {}
            op01_conflict_panel = {}
            lane2_candidate_panel = {}
        runtime_restart_token = (os.environ.get("MIRU_RUNTIME_RESTART_TOKEN") or "").strip()
    elif page_key == "status":
        status_snapshot = build_status_snapshot()
        runtime_dependencies = collect_runtime_dependencies()
        runtime_issues = runtime_issue_messages()

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
        status_snapshot=status_snapshot,
        runtime_dependencies=runtime_dependencies,
        runtime_issues=runtime_issues,
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
        ready_install_panel=ready_install_panel,
        op01_conflict_panel=op01_conflict_panel,
        lane2_candidate_panel=lane2_candidate_panel,
        dev_status_url=url_for("dev_status"),
        runtime_restart_token=runtime_restart_token,
        main_site_port=int(PROJECT_MIRU_PORT),
    )


OPTCG_IMAGES_ROOT = Path("D:/OPTCG_Images")
OFFICIAL_RULES_DB_PATH = PROJECT_ROOT / "data" / "miru_official_rules.db"


def _catalog_image_rel_to_images_cards_url(raw: Any) -> str | None:
    """Map a catalog ``card_variants.image_path`` (relative to OPTCG root) to a Miru URL."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text or any(part == ".." for part in text.split("/")):
        return None
    return f"/images/cards/{text}"


def resolve_card_image_url(card_code: str) -> str | None:
    """Return a public ``/images/cards/...`` URL if a matching file exists under OPTCG.

    Tries set folder = first segment of ``card_code`` (e.g. ST06 from ST06-015), then a
    collapsed numeric form (e.g. OP07 → OP7) when the exact folder is missing.
    """
    code = str(card_code or "").strip().upper()
    if not code or "-" not in code:
        return None
    prefix = code.split("-", 1)[0].strip()
    if not prefix:
        return None
    set_folders = [prefix]
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", prefix)
    if m:
        letters, digits = m.group(1).upper(), m.group(2)
        collapsed = letters + str(int(digits))
        if collapsed != prefix:
            set_folders.append(collapsed)
    for set_folder in set_folders:
        thumb = OPTCG_IMAGES_ROOT / "thumbs" / set_folder / f"{code}.webp"
        try:
            if thumb.is_file():
                return f"/images/cards/thumbs/{set_folder}/{code}.webp"
        except OSError:
            pass
        for ext in (".jpg", ".png"):
            main = OPTCG_IMAGES_ROOT / set_folder / f"{code}{ext}"
            try:
                if main.is_file():
                    return f"/images/cards/{set_folder}/{code}{ext}"
            except OSError:
                pass
    return None


def _ruling_effective_date_prefix(val: Any) -> str | None:
    raw = str(val or "").strip()
    if not raw:
        return None
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    raw = raw[:10]
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _lookup_ruling_citation_from_card_rulings_catalog(
    catalog_db_path: Path, card_code: str
) -> dict[str, Any] | None:
    """Fallback: ``official_card_rulings`` on the catalog DB when rules-DB legality path misses."""
    code = str(card_code or "").strip().upper()
    if not code:
        return None
    path = Path(catalog_db_path)
    if not path.is_file():
        return None

    def _opt_str(val: Any) -> str | None:
        t = str(val or "").strip()
        return t if t else None

    try:
        with closing(sqlite3.connect(str(path))) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_card_rulings'"
            ).fetchone():
                return None
            row = conn.execute(
                """
                SELECT source_title, source_type, source_url, source_anchor
                FROM official_card_rulings
                WHERE upper(trim(coalesce(card_code, ''))) = ?
                  AND trim(coalesce(source_url, '')) != ''
                ORDER BY effective_at DESC, id DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {
        "source_title": _opt_str(row["source_title"]),
        "source_type": _opt_str(row["source_type"]) or "other_official",
        "source_url": _opt_str(row["source_url"]),
        "source_reference": None,
        "source_anchor": _opt_str(row["source_anchor"]),
        "unban_effective_at": None,
    }


def lookup_legality_sensitive_ruling_citation(
    catalog_db_path: Path, card_code: str
) -> dict[str, Any] | None:
    """Banlist citation from ``miru_official_rules.db``; fallback to catalog ``official_card_rulings``."""
    code = str(card_code or "").strip().upper()
    if not code:
        return None

    def _opt_str(val: Any) -> str | None:
        t = str(val or "").strip()
        return t if t else None

    rules_path = Path(OFFICIAL_RULES_DB_PATH)
    if not rules_path.is_file():
        return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

    today = _utc_today()
    rows: list[sqlite3.Row] = []
    notice: sqlite3.Row | None = None
    unban_iso: str | None = None
    try:
        with closing(
            sqlite3.connect(f"file:{rules_path.resolve().as_posix()}?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            if not (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_legality_history'"
                ).fetchone()
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_rule_notices'"
                ).fetchone()
            ):
                return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

            rows = list(
                conn.execute(
                    """
                    SELECT lh.legality_state, lh.effective_start, lh.notice_id,
                           lh.is_current, lh.id
                    FROM official_legality_history lh
                    WHERE upper(trim(lh.card_code)) = ?
                    ORDER BY lh.effective_start DESC, lh.id DESC
                    LIMIT 5
                    """,
                    (code,),
                ).fetchall()
            )
            if not rows:
                return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

            for r in rows:
                st = str(r["legality_state"] or "").strip().lower()
                if st != "legal":
                    continue
                eff = _ruling_effective_date_prefix(r["effective_start"])
                if not eff:
                    continue
                try:
                    d = date.fromisoformat(eff)
                except ValueError:
                    continue
                if d > today:
                    unban_iso = eff
                    break

            if unban_iso is None:
                extras = conn.execute(
                    """
                    SELECT effective_start
                    FROM official_legality_history
                    WHERE upper(trim(card_code)) = ?
                      AND lower(trim(legality_state)) = 'legal'
                      AND trim(coalesce(effective_start, '')) != ''
                    ORDER BY effective_start ASC, id ASC
                    """,
                    (code,),
                ).fetchall()
                for r in extras:
                    eff = _ruling_effective_date_prefix(r["effective_start"])
                    if not eff:
                        continue
                    try:
                        d = date.fromisoformat(eff)
                    except ValueError:
                        continue
                    if d > today:
                        unban_iso = eff
                        break

            ban_row = None
            for r in rows:
                if (
                    str(r["legality_state"] or "").strip().lower() == "banned"
                    and int(r["is_current"] or 0) == 1
                ):
                    ban_row = r
                    break
            if ban_row is None:
                for r in rows:
                    if str(r["legality_state"] or "").strip().lower() == "banned":
                        ban_row = r
                        break

            if ban_row is None:
                return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

            notice_id = str(ban_row["notice_id"] or "").strip()
            if not notice_id:
                return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

            notice = conn.execute(
                """
                SELECT source_url, source_reference, title
                FROM official_rule_notices
                WHERE notice_id = ?
                LIMIT 1
                """,
                (notice_id,),
            ).fetchone()
    except sqlite3.Error:
        return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

    if not notice or not str(notice["source_url"] or "").strip():
        return _lookup_ruling_citation_from_card_rulings_catalog(catalog_db_path, code)

    return {
        "source_title": _opt_str(notice["title"]),
        "source_type": "banlist",
        "source_url": _opt_str(notice["source_url"]),
        "source_reference": _opt_str(notice["source_reference"]),
        "unban_effective_at": unban_iso,
        "source_anchor": None,
    }


def _image_review_port_ok() -> bool:
    return int(CURRENT_SERVER_PORT or 0) == 18765


def _parse_ocr_audit_data_line(line: str) -> dict[str, str] | None:
    line = line.strip()
    if not line or line.startswith("==="):
        return None
    parts = line.split(" | ")
    if len(parts) < 5:
        return None
    status = parts[-1].strip()
    if status not in ("MISMATCH", "UNREADABLE"):
        return None
    folder = parts[0].strip()
    filename = parts[1].strip()
    expected = parts[2].strip()
    vision = " | ".join(parts[3:-1]).strip()
    return {
        "folder": folder,
        "filename": filename,
        "expected_code": expected,
        "vision_returned": vision,
        "status": status,
    }


def _image_review_queue_item_id(folder: str, filename: str) -> str:
    return f"{folder}|{filename}"


def _set_prefix_from_expected_code(code: str) -> str:
    c = (code or "").strip().upper()
    if not c:
        return "UNKNOWN"
    return c.split("-", 1)[0]


def _load_image_review_decisions_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_image_review_decisions(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _image_review_img_url(folder: str, filename: str) -> str:
    root = MIRU_ASSETS_ROOT.resolve()
    try:
        rel = (Path(folder) / filename).resolve().relative_to(root)
    except ValueError:
        rel = Path(filename)
    return "/img/" + rel.as_posix()


def _image_review_target_path_from_id(item_id: str) -> Path | None:
    if "|" not in item_id:
        return None
    folder, fname = item_id.split("|", 1)
    target = (Path(folder) / fname).resolve()
    root = MIRU_ASSETS_ROOT.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def build_image_review_queue_response() -> dict[str, Any]:
    audit_path = OCR_AUDIT_PARALLEL_REPRINT_PATH
    if not audit_path.is_file():
        return {
            "total": 0,
            "reviewed": 0,
            "remaining": 0,
            "groups": {},
        }
    text = audit_path.read_text(encoding="utf-8", errors="replace")
    by_id: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        rec = _parse_ocr_audit_data_line(line)
        if not rec:
            continue
        iid = _image_review_queue_item_id(rec["folder"], rec["filename"])
        by_id[iid] = rec

    with _IMAGE_REVIEW_DECISIONS_LOCK:
        decisions = _load_image_review_decisions_unlocked(IMAGE_REVIEW_DECISIONS_PATH)

    total = len(by_id)
    reviewed = sum(1 for iid in by_id if iid in decisions)
    groups: dict[str, list[dict[str, Any]]] = {}
    remaining = 0
    for iid, rec in sorted(
        by_id.items(), key=lambda x: (x[1]["folder"].lower(), x[1]["filename"].lower())
    ):
        if iid in decisions:
            continue
        remaining += 1
        prefix = _set_prefix_from_expected_code(rec["expected_code"])
        groups.setdefault(prefix, []).append(
            {
                "id": iid,
                "folder": rec["folder"],
                "filename": rec["filename"],
                "expected_code": rec["expected_code"],
                "vision_returned": rec["vision_returned"],
                "img_url": _image_review_img_url(rec["folder"], rec["filename"]),
                "status": rec["status"],
            }
        )
    sorted_groups = {k: groups[k] for k in sorted(groups.keys())}
    return {
        "total": total,
        "reviewed": reviewed,
        "remaining": remaining,
        "groups": sorted_groups,
    }


_RECLASSIFY_TREATMENTS = frozenset(
    {"alt_art", "parallel", "reprint", "tournament_promo", "sp", "tr"}
)


def _norm_rel_asset_path(p: str) -> str:
    return p.replace("\\", "/").strip().lstrip("/").lower()


def _reclassify_next_pr_index(dest_dir: Path, code: str, family: str) -> int:
    """Next index N for ``{code}_pN.png`` (family 'p') or ``{code}_rN.png`` (family 'r')."""
    if family == "p":
        pat = re.compile("^" + re.escape(code) + r"_p(\d+)\.png$", re.I)
    else:
        pat = re.compile("^" + re.escape(code) + r"_r(\d+)\.png$", re.I)
    max_n = 0
    if dest_dir.is_dir():
        for f in dest_dir.iterdir():
            if not f.is_file():
                continue
            m = pat.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _find_variant_row_for_reclassify(
    conn: sqlite3.Connection, canonical_code: str, source_rel: str
) -> int | None:
    canon = canonical_code.strip().upper()
    rows = conn.execute(
        """
        SELECT cv.id, cv.image_path FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE upper(trim(c.canonical_code)) = ?
        """,
        (canon,),
    ).fetchall()
    if not rows:
        return None
    src_norm = _norm_rel_asset_path(source_rel)
    src_base = Path(source_rel.replace("\\", "/")).name.lower()
    exact: list[int] = []
    suffix: list[int] = []
    empty: list[int] = []
    for vid, ipath in rows:
        ip = str(ipath or "").strip()
        if not ip:
            empty.append(int(vid))
            continue
        ipn = _norm_rel_asset_path(ip)
        if ipn == src_norm:
            exact.append(int(vid))
        elif ipn.endswith("/" + src_base) or ipn == src_base:
            suffix.append(int(vid))
    if exact:
        return exact[0]
    if suffix:
        return suffix[0]
    if empty:
        return empty[0]
    return None


def _reclassify_dest_filename_and_variant_key(
    code: str, treatment: str, dest_dir: Path
) -> tuple[str, str]:
    if treatment == "alt_art":
        return f"{code}_alt.png", "alt_art"
    if treatment == "tournament_promo":
        return f"{code}_alt.png", "tournament_promo"
    if treatment == "sp":
        return f"{code}_sp.png", "sp"
    if treatment == "tr":
        return f"{code}_tr.png", "tr"
    if treatment == "parallel":
        n = _reclassify_next_pr_index(dest_dir, code, "p")
        return f"{code}_p{n}.png", f"parallel {n}"
    if treatment == "reprint":
        n = _reclassify_next_pr_index(dest_dir, code, "r")
        return f"{code}_r{n}.png", f"r{n}"
    raise ValueError("invalid treatment")


def run_image_review_reclassify(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Move asset under Miru_Assets, update ``data/card_catalog.db`` ``card_variants``, log decisions."""
    item_id = str(payload.get("id") or "").strip()
    if "|" not in item_id:
        return {"error": "id must be folder|filename."}, 400
    parsed = normalize_card_code(str(payload.get("canonical_code") or ""))
    canonical_code = (parsed.get("canonical_code") or "").strip().upper()
    if not canonical_code:
        return {"error": "invalid or missing canonical_code."}, 400
    treatment = str(payload.get("variant_treatment") or "").strip().lower()
    if treatment not in _RECLASSIFY_TREATMENTS:
        return {"error": "invalid variant_treatment."}, 400
    dkey = str(payload.get("distribution_product_key") or "").strip()
    if not dkey:
        return {"error": "distribution_product_key is required."}, 400
    release_set_name = str(payload.get("release_set_name") or "").strip()

    src_path = _image_review_target_path_from_id(item_id)
    if src_path is None:
        return {"error": "invalid id (path outside Miru_Assets)."}, 400
    if not src_path.is_file():
        return {"error": "source file does not exist."}, 400

    root = MIRU_ASSETS_ROOT.resolve()
    rel_src = src_path.resolve().relative_to(root).as_posix()

    dest_dir = root / dkey / treatment
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname, variant_key_db = _reclassify_dest_filename_and_variant_key(
        canonical_code, treatment, dest_dir
    )
    dest_path = dest_dir / fname
    if dest_path.resolve() == src_path.resolve():
        return {"error": "source and destination are the same path."}, 400
    if treatment in ("alt_art", "tournament_promo", "sp", "tr") and dest_path.is_file():
        return {"error": f"destination already exists: {dest_path}"}, 409

    is_alt = 1 if treatment in ("alt_art", "tournament_promo") else 0
    is_sp = 1 if treatment == "sp" else 0
    is_tr = 1 if treatment == "tr" else 0
    now_ts = datetime.now(UTC).isoformat()

    if not FALLBACK_CATALOG_DB_PATH.is_file():
        return {"error": "card_catalog.db not found."}, 503

    moved = False

    def rollback_move() -> None:
        nonlocal moved
        if moved and dest_path.is_file() and not src_path.is_file():
            with suppress(OSError):
                shutil.move(str(dest_path), str(src_path))
            moved = False

    try:
        shutil.move(str(src_path), str(dest_path))
        moved = True
    except OSError as e:
        return {"error": f"move failed: {e}"}, 500

    rel_dest = dest_path.resolve().relative_to(root).as_posix()

    try:
        with closing(sqlite3.connect(str(FALLBACK_CATALOG_DB_PATH))) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            crow = conn.execute(
                "SELECT id FROM cards WHERE upper(trim(canonical_code)) = ?",
                (canonical_code,),
            ).fetchone()
            if not crow:
                rollback_move()
                return {"error": f"no cards row for canonical_code {canonical_code}."}, 400
            card_id = int(crow[0])

            row_id = _find_variant_row_for_reclassify(conn, canonical_code, rel_src)
            if row_id is not None:
                conn.execute(
                    """
                    UPDATE card_variants SET
                        image_path = ?,
                        release_set_name = ?,
                        release_set_code = ?,
                        variant_key = ?,
                        distribution_product_key = ?,
                        updated_at = ?,
                        is_base = 0,
                        is_alt = ?,
                        is_sp = ?,
                        is_tr = ?,
                        source = 'image-review-reclassify'
                    WHERE id = ?
                    """,
                    (
                        rel_dest,
                        release_set_name,
                        dkey,
                        variant_key_db,
                        dkey,
                        now_ts,
                        is_alt,
                        is_sp,
                        is_tr,
                        row_id,
                    ),
                )
            else:
                dup = conn.execute(
                    """
                    SELECT id FROM card_variants
                    WHERE card_id = ? AND variant_key = ? AND trim(coalesce(print_id, '')) = ''
                    LIMIT 1
                    """,
                    (card_id, variant_key_db),
                ).fetchone()
                if dup:
                    conn.execute(
                        """
                        UPDATE card_variants SET
                            image_path = ?,
                            release_set_name = ?,
                            release_set_code = ?,
                            distribution_product_key = ?,
                            updated_at = ?,
                            is_base = 0,
                            is_alt = ?,
                            is_sp = ?,
                            is_tr = ?,
                            source = 'image-review-reclassify'
                        WHERE id = ?
                        """,
                        (
                            rel_dest,
                            release_set_name,
                            dkey,
                            dkey,
                            now_ts,
                            is_alt,
                            is_sp,
                            is_tr,
                            int(dup[0]),
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO card_variants (
                            card_id, variant_key, variant_label, print_id,
                            release_set_code, release_set_name,
                            image_path, image_url, source,
                            is_base, is_alt, is_sp, has_variant_evidence, is_tr,
                            distribution_product_key, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            card_id,
                            variant_key_db,
                            variant_key_db,
                            "",
                            dkey,
                            release_set_name,
                            rel_dest,
                            "",
                            "image-review-reclassify",
                            0,
                            is_alt,
                            is_sp,
                            1,
                            is_tr,
                            dkey,
                            now_ts,
                        ),
                    )
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        rollback_move()
        return {"error": f"database update failed: {e}"}, 500

    with _IMAGE_REVIEW_DECISIONS_LOCK:
        dec = _load_image_review_decisions_unlocked(IMAGE_REVIEW_DECISIONS_PATH)
        dec[item_id] = {
            "action": "reclassify",
            "canonical_code": canonical_code,
            "variant_treatment": treatment,
            "distribution_product_key": dkey,
            "release_set_name": release_set_name,
            "source_path": str(src_path),
            "destination_path": str(dest_path),
            "decided_at": now_ts,
        }
        _atomic_write_image_review_decisions(IMAGE_REVIEW_DECISIONS_PATH, dec)

    img_url = f"/img/{dkey}/{treatment}/{fname}"
    return (
        {
            "status": "ok",
            "destination": str(dest_path),
            "img_url": img_url,
        },
        200,
    )


STAGE_ACTIONS = frozenset({"confirm", "misroute", "reclassify", "delete"})
STAGE_TREATMENTS = frozenset({"base", "parallel", "sp", "tr", "alt_art", "ir", "mr", "gmr"})


def _set_folder_from_canonical(canonical_code: str) -> str:
    u = canonical_code.strip().upper()
    if "-" in u:
        return u.split("-", 1)[0]
    return u


def _atomic_write_staged_list(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(tmp), str(path))


def _load_staged_list_unlocked(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _safe_under_assets(p: Path) -> bool:
    try:
        p.resolve().relative_to(MIRU_ASSETS_ROOT.resolve())
        return True
    except ValueError:
        return False


def _staging_filename_and_variant_key(code: str, treatment: str, dest_dir: Path) -> tuple[str, str]:
    t = treatment.lower().strip()
    if t == "base":
        return f"{code}_base.png", "base"
    if t == "parallel":
        n = _reclassify_next_pr_index(dest_dir, code, "p")
        return f"{code}_p{n}.png", f"parallel {n}"
    if t == "sp":
        return f"{code}_sp.png", "sp"
    if t == "tr":
        return f"{code}_tr.png", "tr"
    if t == "alt_art":
        return f"{code}_alt.png", "alt_art"
    if t == "ir":
        return f"{code}_ir.png", "ir"
    if t == "mr":
        return f"{code}_mr.png", "mr"
    if t == "gmr":
        return f"{code}_gmr.png", "gmr"
    raise ValueError(f"unsupported variant_treatment: {treatment}")


def _compute_stage_destination_path(
    *,
    action: str,
    file_path: Path,
    canonical_code: str,
    variant_treatment: str | None,
    distribution_product_key: str | None,
) -> Path | None:
    root = MIRU_ASSETS_ROOT.resolve()
    parsed = normalize_card_code(canonical_code)
    code = (parsed.get("canonical_code") or "").strip().upper()
    if not code:
        raise ValueError("invalid canonical_code")
    act = action.lower().strip()
    if act == "delete":
        return None
    vt = (variant_treatment or "parallel").lower().strip()
    if act == "confirm":
        if vt != "parallel":
            vt = "parallel"
        set_folder = _set_folder_from_canonical(code)
        dest_dir = root / set_folder / "parallel"
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname, _ = _staging_filename_and_variant_key(code, "parallel", dest_dir)
        return dest_dir / fname
    if act in ("reclassify", "misroute"):
        if not distribution_product_key or not distribution_product_key.strip():
            raise ValueError("distribution_product_key required for reclassify/misroute")
        if vt not in STAGE_TREATMENTS:
            raise ValueError("invalid variant_treatment")
        dkey = distribution_product_key.strip()
        dest_dir = root / dkey / vt
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname, _ = _staging_filename_and_variant_key(code, vt, dest_dir)
        dest = dest_dir / fname
        if dest.resolve() == file_path.resolve():
            raise ValueError("source and destination are the same path")
        return dest
    raise ValueError(f"unsupported action: {action}")


def _treatment_to_flags(
    treatment: str,
) -> tuple[int, int, int, int, int, int]:
    """is_alt, is_sp, is_tr, is_illustration_rare, is_manga_rare, is_golden_manga_rare"""
    t = treatment.lower().strip()
    z = (0, 0, 0, 0, 0, 0)
    if t == "alt_art":
        return (1, 0, 0, 0, 0, 0)
    if t == "sp":
        return (0, 1, 0, 0, 0, 0)
    if t == "tr":
        return (0, 0, 1, 0, 0, 0)
    if t == "ir":
        return (0, 0, 0, 1, 0, 0)
    if t == "mr":
        return (0, 0, 0, 0, 1, 0)
    if t == "gmr":
        return (0, 0, 0, 0, 0, 1)
    return z


def _apply_commit_catalog_update(
    conn: sqlite3.Connection,
    *,
    canonical_code: str,
    rel_src: str,
    rel_dest: str,
    variant_key_db: str,
    dkey: str,
    release_set_name: str,
    treatment: str,
) -> None:
    now_ts = datetime.now(UTC).isoformat()
    ia, isp, itr, iir, imr, igmr = _treatment_to_flags(treatment)
    is_base_val = 1 if treatment.lower().strip() == "base" else 0
    crow = conn.execute(
        "SELECT id FROM cards WHERE upper(trim(canonical_code)) = ?",
        (canonical_code.strip().upper(),),
    ).fetchone()
    if not crow:
        raise ValueError(f"no cards row for {canonical_code}")
    card_id = int(crow[0])

    row_id = _find_variant_row_for_reclassify(conn, canonical_code, rel_src)
    base_set = (
        "image_path = ?, release_set_name = ?, release_set_code = ?, variant_key = ?, "
        "distribution_product_key = ?, updated_at = ?, is_base = ?, "
        "is_alt = ?, is_sp = ?, is_tr = ?, is_illustration_rare = ?, "
        "is_manga_rare = ?, is_golden_manga_rare = ?, source = 'image-review-commit' "
    )
    params_base = (
        rel_dest,
        release_set_name,
        dkey,
        variant_key_db,
        dkey,
        now_ts,
        is_base_val,
        ia,
        isp,
        itr,
        iir,
        imr,
        igmr,
    )
    if row_id is not None:
        conn.execute(
            f"UPDATE card_variants SET {base_set} WHERE id = ?",
            (*params_base, row_id),
        )
        return
    dup = conn.execute(
        """
        SELECT id FROM card_variants
        WHERE card_id = ? AND variant_key = ? AND trim(coalesce(print_id, '')) = ''
        LIMIT 1
        """,
        (card_id, variant_key_db),
    ).fetchone()
    if dup:
        conn.execute(
            f"UPDATE card_variants SET {base_set} WHERE id = ?",
            (*params_base, int(dup[0])),
        )
        return
    conn.execute(
        """
        INSERT INTO card_variants (
            card_id, variant_key, variant_label, print_id,
            release_set_code, release_set_name,
            image_path, image_url, source,
            is_base, is_alt, is_sp, has_variant_evidence, is_tr,
            is_illustration_rare, is_manga_rare, is_golden_manga_rare,
            distribution_product_key, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            variant_key_db,
            variant_key_db,
            "",
            dkey,
            release_set_name,
            rel_dest,
            "",
            "image-review-commit",
            is_base_val,
            ia,
            isp,
            1,
            itr,
            iir,
            imr,
            igmr,
            dkey,
            now_ts,
        ),
    )


def _variant_key_for_staged_record(rec: dict[str, Any]) -> str:
    vt = str(rec.get("variant_treatment") or "parallel").lower().strip()
    dest = rec.get("destination_path")
    if not dest:
        return vt
    name = Path(str(dest)).name
    m = re.match(
        r"^(.+?)_p(\d+)\.png$",
        name,
        re.I,
    )
    if m:
        return f"parallel {m.group(2)}"
    m = re.match(r"^(.+?)_(sp|tr|alt|ir|mr|gmr|base)\.png$", name, re.I)
    if m:
        suf = m.group(2).lower()
        mapping = {
            "sp": "sp",
            "tr": "tr",
            "alt": "alt_art",
            "ir": "ir",
            "mr": "mr",
            "gmr": "gmr",
            "base": "base",
        }
        return mapping.get(suf, vt)
    return vt


def image_review_add_stage(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    action = str(body.get("action") or "").lower().strip()
    if action not in STAGE_ACTIONS:
        return {"error": "invalid action"}, 400
    fp: Path | None = None
    raw_fp = body.get("file_path")
    if raw_fp:
        fp = Path(str(raw_fp).strip())
    elif body.get("id"):
        fp = _image_review_target_path_from_id(str(body.get("id")).strip())
    if fp is None or not fp.is_file() or not _safe_under_assets(fp):
        return {"error": "invalid file_path or id"}, 400
    parsed = normalize_card_code(str(body.get("canonical_code") or ""))
    canon = (parsed.get("canonical_code") or "").strip().upper()
    if not canon:
        return {"error": "canonical_code required"}, 400
    vt_in = body.get("variant_treatment")
    vt_s = str(vt_in).lower().strip() if vt_in not in (None, "") else None
    dkey_raw = body.get("distribution_product_key")
    dkey_s = str(dkey_raw).strip() if dkey_raw not in (None, "") else None
    rname_raw = body.get("release_set_name")
    rname_s = str(rname_raw).strip() if rname_raw not in (None, "") else None

    dest: Path | None = None
    if action == "delete":
        dest = None
    else:
        try:
            dest = _compute_stage_destination_path(
                action=action,
                file_path=fp,
                canonical_code=canon,
                variant_treatment=vt_s,
                distribution_product_key=dkey_s,
            )
        except ValueError as e:
            return {"error": str(e)}, 400
        if dest is not None and dest.is_file():
            return {"error": "destination already exists"}, 409

    decision_id = str(uuid.uuid4())
    ts = datetime.now(UTC).isoformat()
    if action == "delete":
        rec: dict[str, Any] = {
            "decision_id": decision_id,
            "timestamp": ts,
            "file_path": str(fp.resolve()),
            "canonical_code": canon,
            "action": "delete",
            "destination_path": None,
            "variant_treatment": None,
            "distribution_product_key": None,
            "release_set_name": None,
        }
    else:
        rec = {
            "decision_id": decision_id,
            "timestamp": ts,
            "file_path": str(fp.resolve()),
            "canonical_code": canon,
            "action": action,
            "destination_path": str(dest.resolve()) if dest else None,
            "variant_treatment": vt_s or ("parallel" if action == "confirm" else None),
            "distribution_product_key": dkey_s,
            "release_set_name": rname_s,
        }
    with _IMAGE_REVIEW_STAGED_LOCK:
        items = _load_staged_list_unlocked(IMAGE_REVIEW_STAGED_PATH)
        items.append(rec)
        _atomic_write_staged_list(IMAGE_REVIEW_STAGED_PATH, items)
    return {"decision_id": decision_id}, 200


def image_review_legend_rows() -> list[dict[str, Any]]:
    """One row per ``distribution_product_key``: best ``release_set_name`` by count, then name."""
    if not FALLBACK_CATALOG_DB_PATH.is_file():
        return []
    try:
        with closing(sqlite3.connect(str(FALLBACK_CATALOG_DB_PATH))) as conn:
            rows = conn.execute(
                """
                SELECT distribution_product_key, release_set_name, COUNT(*) AS card_count
                FROM card_variants
                WHERE distribution_product_key IS NOT NULL
                  AND distribution_product_key NOT LIKE '_unclassified%'
                GROUP BY distribution_product_key, release_set_name
                ORDER BY distribution_product_key ASC
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    grouped: dict[str, list[tuple[str, int]]] = {}
    for dkey, rname, cnt in rows:
        if not dkey:
            continue
        dk = str(dkey).strip()
        if not dk:
            continue
        grouped.setdefault(dk, []).append((str(rname or "").strip(), int(cnt or 0)))
    out: list[dict[str, Any]] = []
    assets_root = MIRU_ASSETS_ROOT.resolve()
    for dkey in sorted(grouped.keys()):
        variants = grouped[dkey]
        if not variants:
            continue
        max_c = max(v[1] for v in variants)
        tied = [v for v in variants if v[1] == max_c]
        best_name = min((v[0] for v in tied), default="")
        folder_path = str((assets_root / dkey).resolve())
        if not folder_path.endswith("\\"):
            folder_path += "\\"
        out.append(
            {
                "key": dkey,
                "product_name": best_name,
                "folder": folder_path,
                "card_count": max_c,
            }
        )
    return out


def image_review_variants_rows(canonical_code: str) -> list[dict[str, Any]]:
    if not FALLBACK_CATALOG_DB_PATH.is_file():
        return []
    code = canonical_code.strip().upper()
    if not code:
        return []
    try:
        with closing(sqlite3.connect(str(FALLBACK_CATALOG_DB_PATH))) as conn:
            rows = conn.execute(
                """
                SELECT cv.variant_key, cv.distribution_product_key, cv.release_set_name
                FROM card_variants cv
                JOIN cards c ON c.id = cv.card_id
                WHERE upper(trim(c.canonical_code)) = ?
                ORDER BY cv.variant_key
                """,
                (code,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "variant_key": r[0] or "",
            "variant_treatment": r[0] or "",
            "distribution_product_key": r[1],
            "release_set_name": r[2] or "",
        }
        for r in rows
    ]


def execute_image_review_staged_commit() -> dict[str, Any]:
    """Execute all staged decisions in order. Clears staged file on full success."""
    root = MIRU_ASSETS_ROOT.resolve()
    if not FALLBACK_CATALOG_DB_PATH.is_file():
        return {"committed": 0, "failed": 1, "failed_id": None, "reason": "card_catalog.db missing"}

    with _IMAGE_REVIEW_STAGED_LOCK:
        staged = _load_staged_list_unlocked(IMAGE_REVIEW_STAGED_PATH)

    if not staged:
        return {"committed": 0, "failed": 0}

    committed = 0
    for rec in staged:
        did = str(rec.get("decision_id") or "")
        act = str(rec.get("action") or "").lower().strip()
        fp = Path(str(rec.get("file_path") or ""))
        try:
            if not _safe_under_assets(fp) or not fp.is_file():
                raise OSError(f"source missing or not under Miru_Assets: {fp}")
            rel_src = fp.resolve().relative_to(root).as_posix()

            if act == "delete":
                fp.unlink()
                committed += 1
                continue

            dest_s = rec.get("destination_path")
            if not dest_s:
                raise ValueError("destination_path required")
            dest = Path(str(dest_s))
            if not _safe_under_assets(dest):
                raise ValueError("destination outside Miru_Assets")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file():
                raise OSError(f"destination already exists: {dest}")
            shutil.move(str(fp), str(dest))
            rel_dest = dest.resolve().relative_to(root).as_posix()
            committed += 1

            canon = str(rec.get("canonical_code") or "").strip().upper()
            vt = str(rec.get("variant_treatment") or "parallel").lower().strip()
            dkey = rec.get("distribution_product_key")
            rname = str(rec.get("release_set_name") or "")

            if act in ("reclassify", "misroute") and dkey:
                vkey = _variant_key_for_staged_record(rec)
                with closing(sqlite3.connect(str(FALLBACK_CATALOG_DB_PATH))) as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    _apply_commit_catalog_update(
                        conn,
                        canonical_code=canon,
                        rel_src=rel_src,
                        rel_dest=rel_dest,
                        variant_key_db=vkey,
                        dkey=str(dkey).strip(),
                        release_set_name=rname,
                        treatment=vt,
                    )
                    conn.commit()
        except Exception as e:
            return {
                "committed": committed,
                "failed": 1,
                "failed_id": did or None,
                "reason": str(e),
            }

    with _IMAGE_REVIEW_STAGED_LOCK:
        _atomic_write_staged_list(IMAGE_REVIEW_STAGED_PATH, [])
    if committed > 0:
        send_pushover_notification(
            title="Miru",
            message=f"Miru: Image review commit complete — {committed} actions executed",
            logger=None,
        )
    return {"committed": committed, "failed": 0}


def create_app() -> Flask:
    configure_dev_training_review(PROJECT_ROOT, MIRU_ASSETS_ROOT)
    configure_evidence_watchdog(PROJECT_ROOT)
    configure_evidence_collectors(PROJECT_ROOT, MIRU_ASSETS_ROOT)
    configure_recurrence(PROJECT_ROOT)
    init_evidence_schema()
    init_recurrence_schema()
    seed_recurrence_from_history()
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["PYTHON_NAME"] = Path(sys.executable).name
    log_pushover_startup_status(app.logger)
    compute_asset_version()
    _image_fetch_state_lock = Lock()
    _image_fetch_state: dict[str, Any] = {"running": False}
    _image_fetch_schedule: dict[str, Timer | None] = {"timer": None}

    def _safe_collect_evidence(review_id: int) -> None:
        """Background evidence collection — swallows exceptions to avoid crash."""
        try:
            result = collect_evidence_for_review(review_id)
            app.logger.info("Evidence collected for review %d: %s", review_id, result)
        except Exception:
            app.logger.exception("Evidence collection failed for review %d", review_id)

    def _card_has_completed_evidence(card_code: str) -> bool:
        """Check if a card already has non-PENDING evidence reconciliation.

        Used to skip redundant re-collection when the operator explicitly
        rejects/holds a card that already has completed evidence.
        """
        import sqlite3 as _sql

        db = PROJECT_ROOT / "data" / "miru_dev_training_reviews.db"
        if not db.is_file():
            return False
        try:
            with closing(_sql.connect(str(db), timeout=5)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute(
                    "SELECT er.reconciliation_status "
                    "FROM dev_training_reviews dtr "
                    "JOIN evidence_reconciliation er ON er.review_id = dtr.id "
                    "WHERE dtr.card_code = ? "
                    "AND er.reconciliation_status NOT IN ('PENDING') "
                    "ORDER BY er.reconciled_at DESC LIMIT 1",
                    (card_code.upper(),),
                ).fetchone()
                return row is not None
        except _sql.Error:
            return False

    def _append_image_fetch_log(line: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            MIRU_FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with MIRU_FETCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {line}\n")
        except Exception:
            pass

    def _run_fetch_missing_images_job(trigger: str) -> None:
        _append_image_fetch_log(f"FETCH_JOB_START trigger={trigger}")
        app.logger.info("Miru image fetch started (trigger=%s).", trigger)
        summary: dict[str, Any] = {"fetched": 0, "skipped": 0, "failed": []}
        try:
            summary = fetch_all_missing(
                db_path=FALLBACK_CATALOG_DB_PATH,
                assets_dir=MIRU_ASSETS_ROOT,
                log_callback=lambda msg: app.logger.info(msg),
            )
        except Exception as exc:
            app.logger.exception("Miru image fetch failed (trigger=%s): %s", trigger, exc)
            _append_image_fetch_log(f"FETCH_JOB_FAIL trigger={trigger} error={exc}")
            send_pushover_notification(
                title="Miru Image Fetch Complete",
                message="Fetched: 0 | Skipped: 0 | Failed: 1",
                logger=app.logger,
            )
            with _image_fetch_state_lock:
                _image_fetch_state["running"] = False
            return

        failed_count = len(summary.get("failed") or [])
        fetched_count = int(summary.get("fetched") or 0)
        skipped_count = int(summary.get("skipped") or 0)
        _append_image_fetch_log(
            "FETCH_JOB_DONE "
            f"trigger={trigger} fetched={fetched_count} skipped={skipped_count} failed={failed_count}"
        )
        app.logger.info(
            "Miru image fetch finished (trigger=%s, fetched=%s, skipped=%s, failed=%s).",
            trigger,
            fetched_count,
            skipped_count,
            failed_count,
        )
        send_pushover_notification(
            title="Miru Image Fetch Complete",
            message=f"Fetched: {fetched_count} | Skipped: {skipped_count} | Failed: {failed_count}",
            logger=app.logger,
        )
        with _image_fetch_state_lock:
            _image_fetch_state["running"] = False

    def _start_fetch_missing_images_job(trigger: str) -> bool:
        with _image_fetch_state_lock:
            if bool(_image_fetch_state["running"]):
                return False
            _image_fetch_state["running"] = True
        Thread(
            target=_run_fetch_missing_images_job,
            args=(trigger,),
            daemon=True,
        ).start()
        return True

    def _seconds_until_next_3am(now_local: datetime | None = None) -> int:
        now = now_local or datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        return max(int((next_run - now).total_seconds()), 1)

    def _schedule_next_nightly_image_fetch() -> None:
        delay_seconds = _seconds_until_next_3am()
        timer = Timer(delay_seconds, _nightly_image_fetch_tick)
        timer.daemon = True
        _image_fetch_schedule["timer"] = timer
        timer.start()
        app.logger.info("Scheduled nightly Miru image fetch in %s seconds.", delay_seconds)

    def _nightly_image_fetch_tick() -> None:
        _start_fetch_missing_images_job(trigger="nightly_0300")
        _schedule_next_nightly_image_fetch()

    # DISABLED: nightly Bandai CDN image fetch — superseded by OPTCG API lane (2026-04-06)
    # Re-enable by uncommenting the line below once OPTCG API cutover is fully validated.
    # _schedule_next_nightly_image_fetch()

    # ── Evidence watchdog (5-min recurring timer) ────────────────────────
    _EVIDENCE_WATCHDOG_INTERVAL = 300  # noqa: N806  # seconds

    def _evidence_watchdog_loop() -> None:
        try:
            flipped = evidence_watchdog_tick()
            if flipped:
                app.logger.info("Evidence watchdog flipped %d row(s) to ERROR.", flipped)
        except Exception:
            app.logger.exception("Evidence watchdog tick failed.")
        _schedule_evidence_watchdog()

    def _schedule_evidence_watchdog() -> None:
        t = Timer(_EVIDENCE_WATCHDOG_INTERVAL, _evidence_watchdog_loop)
        t.daemon = True
        t.start()

    _schedule_evidence_watchdog()

    @app.after_request
    def _miru_cache_control_core_static(response):
        """Force revalidation for main JS/CSS so mobile clients pick up new builds after restart."""
        try:
            path = (request.path or "").replace("\\", "/")
        except Exception:
            return response
        if path.endswith("/miru_ai.js") or path.endswith("/miru_ai.css"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/"):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"{exc.code} {exc.name}: {exc.description}",
                        }
                    ),
                    exc.code,
                )
            return exc
        print(
            f"[miru_ai_server] Unhandled error while serving {request.path}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc()
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Miru AI server error: {exc.__class__.__name__}: {exc}",
                    }
                ),
                500,
            )
        return (
            "Miru AI server error. Check the server console for the exact traceback and dependency diagnostics.",
            500,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.get("/")
    def index():
        brand_assets = build_brand_assets()
        return render_template(
            "miru_hub.html",
            app_name=APP_NAME,
            app_tagline=APP_TAGLINE,
            favicon_url=brand_assets["favicon_url"],
            asset_version=compute_asset_version(),
        )

    @app.get("/ask")
    def ask_page():
        # Retired legacy surface — redirected to Miru Hub (surface cleanup pass).
        return redirect("/", code=302)

    @app.get("/dossiers")
    def dossiers_page():
        # Retired legacy surface — redirected to Miru Hub (surface cleanup pass).
        return redirect("/", code=302)

    @app.get("/gaps")
    def gaps_page():
        return redirect("/", code=302)

    @app.get("/training")
    def training_page():
        return redirect("/", code=302)

    @app.get("/status")
    def status_page():
        return redirect("/", code=302)

    @app.get("/dev")
    def dev_page():
        return render_page("dev", "dev_page")

    @app.get("/dev/approve/<path:card_code>")
    def dev_approve_publication_link(card_code: str):
        """GET link handler: publish_requires_review → publish_ready (no JS). Local / LAN only."""
        if not is_local_request():
            abort(403)
        code = str(card_code or "").strip().upper()
        if not code:
            return redirect("/dev")
        ok = update_publication_review_status(
            code,
            "publish_ready",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            require_review_state=True,
        )
        if ok:
            return redirect(f"/dev?approved={quote(code, safe='')}")
        return redirect("/dev")

    @app.get("/dev/reject/<path:card_code>")
    def dev_reject_publication_link(card_code: str):
        """GET link handler: publish_requires_review → publish_deferred (no JS). Local / LAN only."""
        if not is_local_request():
            abort(403)
        code = str(card_code or "").strip().upper()
        if not code:
            return redirect("/dev")
        ok = update_publication_review_status(
            code,
            "publish_deferred",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            require_review_state=True,
        )
        if ok:
            return redirect(f"/dev?rejected={quote(code, safe='')}")
        if dismiss_image_variant_sp_operator_review(code, project_db_path=FALLBACK_CATALOG_DB_PATH):
            return redirect(f"/dev?rejected={quote(code, safe='')}")
        return redirect("/dev")

    @app.route("/images/cards/<path:filename>")
    def serve_card_image(filename):
        return send_from_directory("D:/OPTCG_Images", filename)

    @app.get("/leader/<leader_code>")
    def leader_hub(leader_code: str):
        return redirect("/", code=302)

    @app.get("/card/<card_code>")
    def card_page(card_code: str):
        # Retired legacy surface — redirected to Miru Hub (surface cleanup pass).
        return redirect("/", code=302)

    @app.get("/api/health")
    def health():
        runtime_dependencies = collect_runtime_dependencies()
        catalog_status = inspect_fallback_catalog_db(FALLBACK_CATALOG_DB_PATH)
        return jsonify(
            {
                "status": "ok",
                "app_name": APP_NAME,
                "server_started_at": _SERVER_STARTED_AT,
                "helper_script_ready": SCRIPT_PATH.is_file(),
                "api_key_ready": bool(os.getenv("OPENAI_API_KEY", "").strip()),
                "default_mode": MODE_CONFIGS[0]["key"],
                "pages": [
                    "/",
                    "/ask",
                    "/dossiers",
                    "/gaps",
                    "/training",
                    "/status",
                    "/dev",
                    "/dev/monitor",
                ],
                "runtime_dependencies": runtime_dependencies,
                "runtime_issues": runtime_issue_messages(),
                "fallback_catalog": catalog_status,
                "training_status": build_training_status(),
            }
        )

    @app.get("/api/worktree-status")
    def worktree_status():
        """Lightweight status for worktree runtime: 18765 (self) and 18080 (dashboard). One request from phone to verify both."""
        payload = {
            "18765": "ok",
            "18080": "unhealthy",
            str(int(PROJECT_MIRU_PORT)): "unhealthy",
            "worktree": True,
        }
        try:
            req = Request(
                "http://127.0.0.1:18080/",
                headers={"User-Agent": "Miru-Worktree-Status/1"},
            )
            with closing(urlopen(req, timeout=3)) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if (resp.getcode() or 0) == 200 and "Miru" in body:
                    payload["18080"] = "ok"
        except (HTTPError, URLError, OSError, ValueError):
            pass
        mp = str(int(PROJECT_MIRU_PORT))
        try:
            req_m = Request(
                f"http://127.0.0.1:{mp}/",
                headers={"User-Agent": "Miru-Worktree-Status/1"},
            )
            with closing(urlopen(req_m, timeout=3)) as resp_m:
                code = int(resp_m.getcode() or 0)
                if code in (200, 204, 301, 302, 304):
                    payload[mp] = "ok"
        except (HTTPError, URLError, OSError, ValueError):
            pass
        return jsonify(payload)

    _TAILSCALE_NET = ipaddress.ip_network("100.0.0.0/8")  # noqa: N806  # Tailscale IPv4
    _TAILSCALE_NET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")  # noqa: N806  # Tailscale IPv6 ULA
    _RUNTIME_RESTART_TOKEN = (  # noqa: N806
        os.environ.get("MIRU_RUNTIME_RESTART_TOKEN") or ""
    ).strip()

    def _runtime_control_client_ip() -> str:
        """Client IP for runtime allowlist: X-Forwarded-For (first), X-Real-IP, then REMOTE_ADDR."""
        forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            # First IP is the original client (client, proxy1, proxy2)
            remote = forwarded.split(",")[0].strip()
        else:
            real = (request.headers.get("X-Real-IP") or "").strip()
            remote = real or (request.environ.get("REMOTE_ADDR") or "").strip()
        return remote

    def _runtime_restart_token_matches() -> bool:
        if not _RUNTIME_RESTART_TOKEN:
            return False
        token = (request.headers.get("X-Miru-Runtime-Token") or "").strip()
        return bool(token and token == _RUNTIME_RESTART_TOKEN)

    def _runtime_trusted_network_client() -> bool:
        """True if the resolved client IP is loopback, RFC1918/CGNAT private, or Tailscale."""
        try:
            remote = _runtime_control_client_ip()
            if not remote:
                return True
            if remote in ("127.0.0.1", "::1"):
                return True
            try:
                addr = ipaddress.ip_address(remote)
            except ValueError:
                return False
            # Explicit Tailscale check FIRST before is_private()
            if addr in _TAILSCALE_NET:
                return True
            if getattr(addr, "version", 0) == 6 and addr in _TAILSCALE_NET6:
                return True
            return bool(addr.is_private)
        except Exception:
            return False

    def _is_runtime_control_allowed() -> bool:
        """True if request is from localhost, private LAN, Tailscale, or has valid MIRU_RUNTIME_RESTART_TOKEN.

        Used for Miru AI self-restart (18765), worktree stack, main-site control, and operator handoff writes.
        """
        if _runtime_restart_token_matches():
            return True
        return _runtime_trusted_network_client()

    def _is_project_miru_dashboard_restart_allowed() -> bool:
        """Allow restarting the Project Miru dashboard (18080) from /dev without depending on XFF alone.

        Uses :func:`is_local_request` (WSGI ``REMOTE_ADDR``) so a missing or incorrect
        ``X-Forwarded-For`` header does not block the operator on the same machine or LAN.
        Self-restart and other runtime controls still use :func:`_is_runtime_control_allowed`.
        """
        if _runtime_restart_token_matches():
            return True
        if is_local_request():
            return True
        return _runtime_trusted_network_client()

    @app.get("/api/runtime/status")
    def runtime_status():
        """Runtime status for Dev page control: 18765 and 18080 health. Same as worktree-status with a checked_at timestamp."""
        payload = load_runtime_status_payload(force=True)
        payload["restart_allowed"] = _is_project_miru_dashboard_restart_allowed()
        return jsonify(payload)

    @app.get("/api/hub/summary")
    def hub_summary():
        """Aggregated hub dashboard data for the Miru Hub root page."""
        payload = build_hub_summary_payload()
        payload["restart_allowed"] = _is_runtime_control_allowed()
        return jsonify(payload)

    def _run_worktree_script(
        script_name: str,
        args: list[str] | None = None,
        wait: bool = True,
        timeout: int = 120,
    ) -> tuple[int, str, str]:
        """Run a PowerShell script from windows/ using authoritative worktree scripts. Returns (returncode, stdout, stderr)."""
        windows_dir = PROJECT_ROOT / "windows"
        script_path = windows_dir / script_name
        if not script_path.is_file():
            return -1, "", f"Script not found: {script_path}"
        pwsh = shutil.which("powershell.exe") or "powershell.exe"
        cmd = [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        if args:
            cmd.extend(args)
        env = os.environ.copy()
        is_dashboard_restart = "start_project_miru_dashboard.ps1" in script_name
        if is_dashboard_restart:
            env["MIRU_DASHBOARD_NO_RELOAD"] = "1"
            # The Dev server can be started from a Werkzeug-managed process tree.
            # If those reloader vars leak into the dashboard restart path on Windows,
            # Werkzeug will attempt socket.fromfd() against a non-socket handle.
            env.pop("WERKZEUG_SERVER_FD", None)
            env.pop("WERKZEUG_RUN_MAIN", None)
        try:
            if wait:
                if is_dashboard_restart:
                    # Avoid captured pipes here: the dashboard child can keep inherited
                    # handles alive on Windows and make the parent restart call hang.
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(PROJECT_ROOT),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                    )
                    return (int(proc.wait(timeout=timeout)), "", "")
                r = subprocess.run(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                return (r.returncode, r.stdout or "", r.stderr or "")
            # Fire-and-forget (for restart self or worktree)
            subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            return (0, "initiated", "")
        except subprocess.TimeoutExpired:
            return (-2, "", "timeout")
        except Exception as e:
            return (-1, "", str(e))

    @app.post("/api/runtime/restart/18080")
    def runtime_restart_18080():
        """Restart Project Miru dashboard (18080). Local-only. Uses authoritative start_project_miru_dashboard.ps1 with -Force."""
        if not _is_project_miru_dashboard_restart_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Restart allowed only from this machine or Tailscale",
                    }
                ),
                403,
            )
        code, out, err = _run_worktree_script(
            "start_project_miru_dashboard.ps1", ["-Force"], wait=True, timeout=90
        )
        if code == 0:
            return jsonify(
                {
                    "ok": True,
                    "service": "18080",
                    "message": "Project Miru restarted",
                    "detail": (out or "").strip() or "Script completed successfully",
                }
            )
        return (
            jsonify(
                {
                    "ok": False,
                    "service": "18080",
                    "error": "Restart failed",
                    "detail": (err or out or f"exit code {code}").strip(),
                }
            ),
            502,
        )

    @app.post("/api/runtime/restart/18765")
    def runtime_restart_18765():
        """Restart Miru AI Dev (18765). Local-only. Fire-and-forget; this process will be replaced."""
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Restart allowed only from this machine or Tailscale",
                    }
                ),
                403,
            )
        _run_worktree_script("start_miru_ai_dev.ps1", ["-Force"], wait=False)
        return (
            jsonify(
                {
                    "ok": True,
                    "service": "18765",
                    "message": "Restart initiated",
                    "detail": "This tab will close. Reconnect to /dev in a few seconds.",
                }
            ),
            202,
        )

    @app.post("/api/runtime/restart/worktree")
    def runtime_restart_worktree():
        """Restart full worktree stack (18080 + 18765). Local-only. Fire-and-forget."""
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Restart allowed only from this machine or Tailscale",
                    }
                ),
                403,
            )
        _run_worktree_script("start_op_miru_worktree.ps1", ["-Native"], wait=False)
        return (
            jsonify(
                {
                    "ok": True,
                    "service": "worktree",
                    "message": "Worktree restart initiated",
                    "detail": "Reconnect to /dev in a few seconds.",
                }
            ),
            202,
        )

    @app.post("/api/runtime/restart/main-site")
    def runtime_restart_main_site():
        """
        Ensure main stable site (PROJECT_MIRU_PORT, default 8080) is running.
        Uses start_main_stable.ps1 -Start (docker compose when available); does not stack duplicate listeners.
        """
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Restart allowed only from this machine or Tailscale",
                    }
                ),
                403,
            )
        port = int(PROJECT_MIRU_PORT)
        code, out, err = _run_worktree_script(
            "start_main_stable.ps1",
            ["-Start", "-Port", str(port)],
            wait=True,
            timeout=180,
        )
        if code == 0:
            return jsonify(
                {
                    "ok": True,
                    "service": "main-site",
                    "port": port,
                    "message": "Main site start/verify completed",
                    "detail": (out or "").strip() or "Script completed successfully",
                }
            )
        return (
            jsonify(
                {
                    "ok": False,
                    "service": "main-site",
                    "port": port,
                    "error": "Main site script failed",
                    "detail": (err or out or f"exit code {code}").strip(),
                }
            ),
            502,
        )

    @app.get("/api/training-status")
    def training_status():
        abort(404)

    @app.get("/api/dev-status")
    def dev_status():
        summary_only = str(request.args.get("view") or "").strip().lower() == "summary"
        surface = str(request.args.get("surface") or "").strip().lower()
        include_heavy = str(request.args.get("include") or "").strip().lower() in {
            "heavy",
            "all",
            "full",
        }
        payload = build_dev_status(lightweight=summary_only, include_heavy_sections=include_heavy)
        if summary_only:
            payload = ensure_control_layer_payload(payload, force_runtime_probe=True)
        if summary_only and surface == "cockpit":
            payload = _strip_dev_cockpit_dev_status_payload(payload)
        return jsonify(trim_dev_status_payload(payload))

    @app.route("/api/dev/operator-handoff/resolve", methods=["POST"], strict_slashes=False)
    def dev_operator_handoff_resolve():
        """Persist operator acknowledgement for the current handoff need signature (data/miru_operator_handoff_resolution.json)."""
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Handoff resolution is only allowed from this machine, private LAN, Tailscale, or with a valid runtime token header.",
                    }
                ),
                403,
            )
        body = request.get_json(silent=True) or {}
        note = str(body.get("note") or "").strip()
        payload = build_dev_status(lightweight=True, include_heavy_sections=False)
        osr = payload.get("operator_self_report") or {}
        issues = payload.get("issues") or {}
        fp = compute_operator_handoff_need_fingerprint(osr, issues)
        if not fp:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Cannot record handoff resolution while operator self-report is in error state; fix self-report first.",
                    }
                ),
                400,
            )
        saved = save_operator_handoff_resolution(fp, note=note)
        payload2 = build_dev_status(lightweight=True, include_heavy_sections=False)
        handoff = build_operator_handoff_payload(payload2)
        return jsonify({"ok": True, "saved": saved, "operator_handoff": handoff})

    @app.post("/api/dev/operator-handoff/clear-resolution")
    def dev_operator_handoff_clear_resolution():
        """Remove stored acknowledgement so an urgent self-report surfaces an active handoff again."""
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Clearing handoff resolution is only allowed from this machine, private LAN, Tailscale, or with a valid runtime token header.",
                    }
                ),
                403,
            )
        clear_operator_handoff_resolution()
        payload = build_dev_status(lightweight=True, include_heavy_sections=False)
        return jsonify({"ok": True, "operator_handoff": build_operator_handoff_payload(payload)})

    @app.get("/api/dev/lane2-review")
    def dev_lane2_review():
        """Return Lane 2 candidate review data with reviewability gating.

        Splits rows into ready / not_ready / already_owned based on:
        - miru_image_source = 'local_asset' only (NO Bandai CDN fallback)
        - READY_FOR_REVIEW requires local asset + at least one candidate
          with a non-null tcg_image_url
        """
        panel = load_lane2_candidate_review_panel()
        ready_rows: list[dict[str, Any]] = []
        not_ready_rows: list[dict[str, Any]] = []
        already_owned_rows: list[dict[str, Any]] = []

        for r in panel.get("rows", []):
            has_local = bool(r.get("miru_image_path"))
            miru_image_source = "local_asset" if has_local else None

            candidates_out = [
                {
                    "market_product_id": c["market_product_id"],
                    "product_name": c["product_name"],
                    "tcg_image_url": c["tcg_image_url"] or None,
                    "existing_owner_pid": c.get("existing_owner_pid"),
                    "sub_classification": c.get("sub_classification", ""),
                }
                for c in r.get("candidates", [])
            ]

            has_any_tcg_image = any(c["tcg_image_url"] for c in candidates_out)

            if r["pid_classification"] == "ALREADY_OWNED":
                review_status = "ALREADY_OWNED"
                review_context = "All candidates owned by other printings"
            elif has_local and has_any_tcg_image:
                review_status = "READY_FOR_REVIEW"
                review_context = "Local asset available; TCG image(s) present"
            elif not has_local:
                review_status = "NOT_READY_FOR_REVIEW"
                review_context = (
                    "No local image asset — Bandai CDN shows base art, " "not treatment variant"
                )
            else:
                review_status = "NOT_READY_FOR_REVIEW"
                review_context = "Local asset present but no candidate has a TCG image URL"

            row_out = {
                "printing_id": r["printing_id"],
                "card_code": r["card_code"],
                "variant_key": r["variant_key"],
                "sub_classification": r["pid_classification"],
                "miru_image_path": r.get("miru_image_path") or None,
                "miru_image_source": miru_image_source,
                "review_status": review_status,
                "review_context": review_context,
                "candidates": candidates_out,
            }

            if review_status == "ALREADY_OWNED":
                already_owned_rows.append(row_out)
            elif review_status == "READY_FOR_REVIEW":
                ready_rows.append(row_out)
            else:
                not_ready_rows.append(row_out)

        return jsonify(
            {
                "ready": ready_rows,
                "not_ready": not_ready_rows,
                "already_owned": already_owned_rows,
                "summary": {
                    "ready_count": len(ready_rows),
                    "not_ready_count": len(not_ready_rows),
                    "already_owned_count": len(already_owned_rows),
                    "clear": panel.get("clear_count", 0),
                    "multi_match": panel.get("multi_count", 0),
                },
            }
        )

    @app.get("/api/dev/action-governance")
    def dev_action_governance():
        summary_only = str(request.args.get("view") or "").strip().lower() == "summary"
        target_card = str(request.args.get("card") or "").strip().upper()
        target_batch = str(request.args.get("batch") or "").strip()
        payload = build_dev_status(
            lightweight=summary_only, include_heavy_sections=not summary_only
        )
        if summary_only:
            payload = ensure_control_layer_payload(payload, force_runtime_probe=True)
        snapshot = build_action_governance_snapshot(
            dev_payload=payload,
            target_card_code=target_card,
            target_batch_id=target_batch,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            runtime_dossier_db_path=LEARNING_DOSSIER_DB_PATH,
            canonical_dossier_db_path=DOSSIER_DB_PATH,
            rules_db_path=PROJECT_ROOT / "data" / "miru_official_rules.db",
            deck_intel_db_path=DECK_INTEL_DB_PATH,
            prices_path=PRICES_PATH,
            persist=True,
        )
        return jsonify(snapshot)

    @app.post("/api/dev/action-governance/execute")
    def dev_action_governance_execute():
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Governed action execution is only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        action_id = str(payload.get("action_id") or "").strip()
        target_card = str(payload.get("card_code") or "").strip().upper()
        target_batch = str(payload.get("batch_id") or "").strip()
        member_card_codes = payload.get("member_card_codes") or []
        if not isinstance(member_card_codes, list):
            member_card_codes = []
        limit = payload.get("limit")
        note = str(payload.get("note") or "").strip()
        current = build_dev_status(lightweight=True, include_heavy_sections=False)
        result = execute_governed_action(
            action_id=action_id,
            dev_payload=current,
            target_card_code=target_card,
            batch_id=target_batch,
            member_card_codes=[
                str(item or "").strip().upper()
                for item in member_card_codes
                if str(item or "").strip()
            ],
            limit=int(limit) if str(limit or "").strip() else None,
            note=note,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            runtime_dossier_db_path=LEARNING_DOSSIER_DB_PATH,
            canonical_dossier_db_path=DOSSIER_DB_PATH,
            rules_db_path=PROJECT_ROOT / "data" / "miru_official_rules.db",
            deck_intel_db_path=DECK_INTEL_DB_PATH,
            prices_path=PRICES_PATH,
        )
        return jsonify(result), 200 if result.get("ok") else 400

    @app.get("/api/dev/mcp/status")
    def dev_mcp_status():
        probe = str(request.args.get("probe") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return jsonify(build_mcp_governance_summary(probe=probe))

    @app.post("/api/dev/mcp/catalog-sync")
    def dev_mcp_catalog_sync():
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "MCP catalog sync is only allowed from this machine, private LAN, Tailscale, or with a valid runtime token.",
                    }
                ),
                403,
            )
        report = sync_catalog_snapshot()
        ok = str(report.get("status") or "") == "synced"
        return jsonify({"ok": ok, "report": report}), 200 if ok else 409

    @app.post("/api/dev/mcp/research")
    def dev_mcp_research():
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Governed MCP research is only allowed from this machine, private LAN, Tailscale, or with a valid runtime token.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        lane_id = str(payload.get("lane_id") or "").strip().lower()
        query = str(payload.get("query") or "").strip()
        if not lane_id or not query:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "lane_id and query are required.",
                    }
                ),
                400,
            )
        raw_max_results = payload.get("max_results")
        try:
            max_results = min(max(int(raw_max_results or 3), 1), 5)
        except (TypeError, ValueError):
            max_results = 3
        try:
            result = run_governed_research(
                lane_id=lane_id,
                query=query,
                card_code=str(payload.get("card_code") or "").strip().upper(),
                set_code=str(payload.get("set_code") or "").strip().upper(),
                max_results=max_results,
                lead_type=str(payload.get("lead_type") or "review_lead").strip() or "review_lead",
            )
        except McpInvocationError as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "fail_closed": True,
                    }
                ),
                502,
            )
        return jsonify(result)

    @app.get("/api/dev/mcp/research/leads")
    def dev_mcp_research_leads():
        limit = min(max(int(request.args.get("limit") or 20), 1), 100)
        return jsonify(list_research_review_leads(limit=limit))

    @app.get("/api/dev/review-queue")
    def dev_review_queue():
        limit = min(max(int(request.args.get("limit") or 8), 1), 40)
        return jsonify(
            load_review_queue_summary(
                project_db_path=FALLBACK_CATALOG_DB_PATH,
                limit=limit,
            )
        )

    @app.get("/api/dev/publication-stage")
    def dev_publication_stage():
        limit = min(max(int(request.args.get("limit") or 8), 1), 40)
        return jsonify(
            load_publication_stage_summary(
                project_db_path=FALLBACK_CATALOG_DB_PATH,
                limit=limit,
            )
        )

    @app.get("/api/dev/publication-batches")
    def dev_publication_batches():
        limit = min(max(int(request.args.get("limit") or 8), 1), 40)
        batch_id = str(request.args.get("batch") or "").strip()
        if batch_id:
            return jsonify(
                build_publication_batch_summary(
                    batch_id=batch_id,
                    project_db_path=FALLBACK_CATALOG_DB_PATH,
                    limit=limit,
                )
            )
        return jsonify(
            load_publication_batch_summary(
                project_db_path=FALLBACK_CATALOG_DB_PATH,
                limit=limit,
            )
        )

    @app.post("/api/dev/review-queue/resolve")
    def dev_review_queue_resolve():
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Review queue updates are only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_key = str(payload.get("item_key") or "").strip()
        target_id = str(payload.get("target_id") or "").strip().upper()
        note = str(payload.get("note") or "").strip()
        result = resolve_review_queue_item(
            item_key=item_key,
            target_id=target_id,
            status="resolved",
            note=note,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
        )
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/dev/review-queue/defer")
    def dev_review_queue_defer():
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Review queue updates are only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_key = str(payload.get("item_key") or "").strip()
        target_id = str(payload.get("target_id") or "").strip().upper()
        note = str(payload.get("note") or "").strip()
        result = resolve_review_queue_item(
            item_key=item_key,
            target_id=target_id,
            status="deferred",
            note=note,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
        )
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/dev/review-queue/approve")
    def dev_review_queue_approve():
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Review queue updates are only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_key = str(payload.get("item_key") or "").strip()
        target_id = str(payload.get("target_id") or "").strip().upper()
        note = str(payload.get("note") or "").strip()
        result = update_review_queue_item(
            item_key=item_key,
            target_id=target_id,
            status="resolved",
            approval_state="approved_for_candidate",
            note=note,
            decision_source="dev_review_queue_approve",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
        )
        return jsonify(result), 200 if result.get("ok") else 400

    @app.post("/api/dev/review-queue/reject")
    def dev_review_queue_reject():
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Review queue updates are only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_key = str(payload.get("item_key") or "").strip()
        target_id = str(payload.get("target_id") or "").strip().upper()
        note = str(payload.get("note") or "").strip()
        result = update_review_queue_item(
            item_key=item_key,
            target_id=target_id,
            status="resolved",
            approval_state="rejected",
            note=note,
            decision_source="dev_review_queue_reject",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
        )
        if result.get("ok"):
            return jsonify(result), 200
        if dismiss_image_variant_sp_operator_review(
            target_id, project_db_path=FALLBACK_CATALOG_DB_PATH
        ):
            return (
                jsonify(
                    {
                        "ok": True,
                        "item_key": item_key,
                        "target_id": target_id,
                        "dismissed": "image_variant_sp_without_queue_row",
                    }
                ),
                200,
            )
        return jsonify(result), 400

    @app.get("/api/dev/status")
    def legacy_dev_status():
        summary_only = str(request.args.get("view") or "").strip().lower() == "summary"
        include_heavy = str(request.args.get("include") or "").strip().lower() in {
            "heavy",
            "all",
            "full",
        }
        payload = build_dev_status(lightweight=summary_only, include_heavy_sections=include_heavy)
        if summary_only:
            payload = ensure_control_layer_payload(payload, force_runtime_probe=True)
        return jsonify(trim_dev_status_payload(payload))

    @app.get("/dev-status")
    def legacy_dev_status_root():
        summary_only = str(request.args.get("view") or "").strip().lower() == "summary"
        include_heavy = str(request.args.get("include") or "").strip().lower() in {
            "heavy",
            "all",
            "full",
        }
        payload = build_dev_status(lightweight=summary_only, include_heavy_sections=include_heavy)
        if summary_only:
            payload = ensure_control_layer_payload(payload, force_runtime_probe=True)
        return jsonify(trim_dev_status_payload(payload))

    @app.get("/api/dev/monitor-panel")
    def dev_monitor_panel():
        return jsonify(build_monitor_panel_payload())

    @app.get("/api/dev/image-coverage")
    def dev_image_coverage():
        return jsonify(build_image_coverage_payload())

    @app.get("/api/dev/training-review/queue")
    def dev_training_review_queue():
        """Catalog-backed training queue + Miru_Assets image URLs (18765 only)."""
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "Training review is only available on port 18765.",
                    }
                ),
                403,
            )
        set_code = request.args.get("set_code", "OP01").strip().upper()
        limit = int(request.args.get("limit", "28"))
        offset = int(request.args.get("offset", "0"))
        return jsonify(
            build_training_review_queue_payload(
                set_code_filter=set_code,
                limit=limit,
                offset=offset,
            )
        )

    @app.post("/api/dev/training-review/submit")
    def dev_training_review_submit():
        """Persist operator review decisions for later Miru use (localhost write)."""
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Training review is only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Training review submit is only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        card_code = str(payload.get("cardId") or "").strip().upper()
        variant_id = str(payload.get("variantId") or "").strip()
        verdict = str(payload.get("verdict") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        if not card_code or not verdict or action not in ("approve", "fix_it", "hold"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "cardId, verdict, and action are required.",
                    }
                ),
                400,
            )
        printing_id: int | None = None
        if variant_id.isdigit():
            printing_id = int(variant_id)
        # Accept optional structured correction detail array from enriched
        # review payloads.  Falls back to empty list for coarse submissions.
        raw_correction = payload.get("correctionDetail")
        correction_detail = raw_correction if isinstance(raw_correction, list) else []
        record = {
            "card_code": card_code,
            "printing_id": printing_id,
            "variant_key": str(payload.get("variantKey") or ""),
            "miru_image_relpath": str(payload.get("miruAssetsRelPath") or ""),
            "verdict": verdict,
            "issues": payload.get("issues") if isinstance(payload.get("issues"), list) else [],
            "because": str(payload.get("because") or ""),
            "source": str(payload.get("source") or ""),
            "missing_image_source_url": str(payload.get("missingImageSourceUrl") or ""),
            "missing_image_upload_name": str(payload.get("missingImageUploadName") or ""),
            "action": action,
            "client_payload": payload,
            "correction_detail": correction_detail,
        }
        ok, msg, review_id = persist_training_review_row(record)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 500
        # Fire evidence collection in background (daemon thread, non-blocking).
        # Skip re-collection when the operator explicitly rejects/holds a card
        # that already has completed evidence — avoids the contradiction loop
        # where reject → new evidence → re-contradiction → card re-appears.
        should_collect = True
        if review_id is not None and action in ("fix_it", "hold"):
            should_collect = not _card_has_completed_evidence(card_code)
        if review_id is not None and should_collect:
            Thread(
                target=_safe_collect_evidence,
                args=(review_id,),
                daemon=True,
            ).start()
        return jsonify({"ok": True, "stored": "dev_training_reviews", "detail": msg})

    @app.get("/api/dev/candidate-queue")
    def dev_candidate_queue():
        """Candidate queue + history surface (18765 only, read-only)."""
        if not _image_review_port_ok():
            return (
                jsonify({"error": "Candidate queue is only available on port 18765."}),
                403,
            )
        return jsonify(build_candidate_queue_payload(card_code_prefix="OP01-"))

    @app.get("/api/dev/op01/throughput")
    def dev_op01_throughput():
        """OP01-scoped throughput stats for the mission control surface."""
        if not _image_review_port_ok():
            return jsonify({"error": "Only available on port 18765."}), 403
        return jsonify(op01_throughput_stats())

    @app.get("/api/dev/helper/status")
    def dev_helper_status():
        """Local helper lane status (18765 only)."""
        if not _image_review_port_ok():
            return jsonify({"error": "Helper status is only available on port 18765."}), 403
        return jsonify(helper_status())

    @app.post("/api/dev/helper/lane")
    def dev_helper_lane():
        """Session helper lane on/off without restart (18765, localhost-only)."""
        if not _image_review_port_ok():
            return jsonify({"error": "Helper lane is only available on port 18765."}), 403
        if not is_local_request():
            return jsonify({"error": "Helper lane control is localhost-only."}), 403
        payload = request.get_json(silent=True) or {}
        if payload.get("reset"):
            set_helper_runtime_override(None)
        elif "enabled" in payload:
            v = payload.get("enabled")
            if not isinstance(v, bool):
                return jsonify({"error": "enabled must be a boolean."}), 400
            set_helper_runtime_override(v)
        else:
            return (
                jsonify({"error": 'Send {"enabled": true|false} or {"reset": true}.'}),
                400,
            )
        return jsonify(helper_status())

    @app.get("/api/dev/operator/price-context")
    def dev_operator_price_context():
        """Catalog-backed market price snapshot for operator review (18765)."""
        if not _image_review_port_ok():
            return (
                jsonify({"error": "Price context is only available on port 18765."}),
                403,
            )
        try:
            pid = int(request.args.get("printing_id") or "0")
        except ValueError:
            return jsonify({"ok": False, "error": "printing_id required."}), 400
        card_code = str(request.args.get("card_code") or "").strip()
        variant_key = str(request.args.get("variant_key") or "").strip()
        if not card_code:
            return jsonify({"ok": False, "error": "card_code required."}), 400
        out = build_operator_price_snapshot(
            printing_id=pid, card_code=card_code, variant_key=variant_key
        )
        status = 200 if out.get("ok") else 400
        return jsonify(out), status

    @app.post("/api/dev/operator/price-refresh")
    def dev_operator_price_refresh():
        """Refresh one printing's price from local TCGCSV snapshot (localhost-only)."""
        if not _image_review_port_ok():
            return (
                jsonify({"error": "Price refresh is only available on port 18765."}),
                403,
            )
        if not is_local_request():
            return jsonify({"error": "Price refresh is localhost-only."}), 403
        payload = request.get_json(silent=True) or {}
        try:
            pid = int(payload.get("printing_id") or 0)
        except ValueError:
            return jsonify({"ok": False, "error": "printing_id required."}), 400
        card_code = str(payload.get("card_code") or "").strip()
        variant_key = str(payload.get("variant_key") or "").strip()
        if not card_code:
            return jsonify({"ok": False, "error": "card_code required."}), 400
        out = refresh_operator_price_from_tcgcsv(
            printing_id=pid, card_code=card_code, variant_key=variant_key
        )
        if out.get("ok"):
            return jsonify(out)
        return jsonify(out), 400

    @app.post("/api/dev/helper/invoke")
    def dev_helper_invoke():
        """Invoke a local helper function (18765 only, advisory output).

        Payload: {"task": "<task_name>", "params": {...}}
        Supported tasks: summarize_candidate, explain_elevation,
                         draft_note, suggest_correction
        """
        if not _image_review_port_ok():
            return jsonify({"error": "Helper is only available on port 18765."}), 403
        if not is_local_request():
            return jsonify({"error": "Helper invoke is localhost-only."}), 403

        payload = request.get_json(silent=True) or {}
        task = str(payload.get("task") or "").strip()
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if task == "summarize_candidate":
            return jsonify(summarize_candidate_rationale(params))
        if task == "explain_elevation":
            return jsonify(explain_elevation(params))
        if task == "draft_note":
            return jsonify(
                draft_review_note(
                    issue_type=str(params.get("issue_type") or ""),
                    because=str(params.get("because") or ""),
                    card_code=str(params.get("card_code") or ""),
                    variant_key=str(params.get("variant_key") or ""),
                )
            )
        if task == "suggest_correction":
            return jsonify(
                suggest_correction_detail(
                    issue_type=str(params.get("issue_type") or ""),
                    card_code=str(params.get("card_code") or ""),
                    variant_key=str(params.get("variant_key") or ""),
                    because=str(params.get("because") or ""),
                )
            )
        return jsonify({"ok": False, "error": f"Unknown task: {task}"}), 400

    @app.get("/img/<path:relpath>")
    def dev_miru_assets_image(relpath: str):
        """Serve Miru_Assets images for image-review queue (18765 only)."""
        if not _image_review_port_ok():
            abort(404)
        root = MIRU_ASSETS_ROOT.resolve()
        candidate = (root / relpath.replace("\\", "/")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            abort(404)
        if not candidate.is_file():
            abort(404)
        return send_from_directory(str(candidate.parent), candidate.name)

    @app.get("/api/dev/image-review/queue")
    def dev_image_review_queue():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        return jsonify(build_image_review_queue_response())

    @app.post("/api/dev/image-review/decide")
    def dev_image_review_decide():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get("id") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        if not item_id:
            return jsonify({"error": "id is required."}), 400
        if action not in ("confirm", "misroute", "delete"):
            return jsonify({"error": "action must be confirm, misroute, or delete."}), 400
        now = datetime.now(UTC).isoformat()
        with _IMAGE_REVIEW_DECISIONS_LOCK:
            data = _load_image_review_decisions_unlocked(IMAGE_REVIEW_DECISIONS_PATH)
            if action == "delete":
                data[item_id] = {"action": "pending_delete", "decided_at": now}
            else:
                data[item_id] = {"action": action, "decided_at": now}
            _atomic_write_image_review_decisions(IMAGE_REVIEW_DECISIONS_PATH, data)
        if action == "delete":
            return jsonify({"status": "pending_delete", "id": item_id})
        return jsonify({"status": "ok", "id": item_id, "action": action})

    @app.post("/api/dev/image-review/confirm-delete")
    def dev_image_review_confirm_delete():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get("id") or "").strip()
        if not item_id:
            return jsonify({"error": "id is required."}), 400
        now = datetime.now(UTC).isoformat()
        with _IMAGE_REVIEW_DECISIONS_LOCK:
            data = _load_image_review_decisions_unlocked(IMAGE_REVIEW_DECISIONS_PATH)
            ent = data.get(item_id)
            if not ent or str(ent.get("action") or "") != "pending_delete":
                return (
                    jsonify(
                        {
                            "error": "Entry must exist with action pending_delete.",
                            "id": item_id,
                        }
                    ),
                    400,
                )
            target = _image_review_target_path_from_id(item_id)
            if target is None:
                return jsonify({"error": "Invalid id path (outside Miru_Assets)."}), 400
            if target.is_file():
                try:
                    target.unlink()
                except OSError as e:
                    return jsonify({"error": str(e)}), 500
            data[item_id] = {"action": "deleted", "decided_at": now}
            _atomic_write_image_review_decisions(IMAGE_REVIEW_DECISIONS_PATH, data)
        return jsonify({"status": "deleted", "id": item_id})

    @app.post("/api/dev/image-review/batch-decide")
    def dev_image_review_batch_decide():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list):
            return jsonify({"error": "ids must be a list."}), 400
        action = str(payload.get("action") or "").strip().lower()
        if action not in ("confirm", "misroute", "delete"):
            return jsonify({"error": "action must be confirm, misroute, or delete."}), 400
        ids_ordered = [str(x).strip() for x in raw_ids if str(x).strip()]
        seen: set[str] = set()
        ids_unique: list[str] = []
        for i in ids_ordered:
            if i in seen:
                continue
            seen.add(i)
            ids_unique.append(i)
        now = datetime.now(UTC).isoformat()
        count = 0
        with _IMAGE_REVIEW_DECISIONS_LOCK:
            data = _load_image_review_decisions_unlocked(IMAGE_REVIEW_DECISIONS_PATH)
            for item_id in ids_unique:
                if action == "delete":
                    data[item_id] = {"action": "pending_delete", "decided_at": now}
                else:
                    data[item_id] = {"action": action, "decided_at": now}
                count += 1
            _atomic_write_image_review_decisions(IMAGE_REVIEW_DECISIONS_PATH, data)
        return jsonify({"status": "ok", "count": count})

    @app.post("/api/dev/image-review/reclassify")
    def dev_image_review_reclassify():
        """Move asset to provenance folder, update card_catalog card_variants, log decision (18765 only)."""
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        body, status = run_image_review_reclassify(payload)
        return jsonify(body), status

    @app.post("/api/dev/image-review/stage")
    def dev_image_review_stage():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        body, status = image_review_add_stage(request.get_json(silent=True) or {})
        return jsonify(body), status

    @app.get("/api/dev/image-review/staged")
    def dev_image_review_staged_get():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        with _IMAGE_REVIEW_STAGED_LOCK:
            items = _load_staged_list_unlocked(IMAGE_REVIEW_STAGED_PATH)
        return jsonify({"items": items})

    @app.delete("/api/dev/image-review/staged/<decision_id>")
    def dev_image_review_staged_delete(decision_id: str):
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        did = str(decision_id or "").strip()
        if not did:
            return jsonify({"error": "decision_id required"}), 400
        with _IMAGE_REVIEW_STAGED_LOCK:
            items = _load_staged_list_unlocked(IMAGE_REVIEW_STAGED_PATH)
            new_items = [x for x in items if str(x.get("decision_id")) != did]
            if len(new_items) == len(items):
                return jsonify({"error": "not found"}), 404
            _atomic_write_staged_list(IMAGE_REVIEW_STAGED_PATH, new_items)
        return jsonify({"ok": True})

    @app.post("/api/dev/image-review/commit")
    def dev_image_review_commit():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {"error": "This action is only allowed from localhost-equivalent clients."}
                ),
                403,
            )
        result = execute_image_review_staged_commit()
        status = 200 if int(result.get("failed") or 0) == 0 else 409
        return jsonify(result), status

    @app.get("/api/dev/image-review/legend")
    def dev_image_review_legend():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        return jsonify(image_review_legend_rows())

    @app.get("/api/dev/image-review/variants")
    def dev_image_review_variants():
        if not _image_review_port_ok():
            return (
                jsonify(
                    {
                        "error": "image-review endpoints are only available on port 18765.",
                    }
                ),
                403,
            )
        code = str(request.args.get("code") or "").strip()
        return jsonify({"variants": image_review_variants_rows(code)})

    @app.get("/api/dev/catalog-publish-coverage")
    def dev_catalog_publish_coverage():
        pct = compute_catalog_publish_pulse_coverage_percent(
            project_db_path=FALLBACK_CATALOG_DB_PATH
        )
        if pct is None:
            return (
                jsonify({"ok": False, "error": "catalog_unavailable"}),
                503,
            )
        return jsonify({"ok": True, "coverage_percent": pct})

    @app.get("/api/dev/validation-audit")
    def dev_validation_audit():
        return jsonify(build_validation_audit_payload())

    @app.get("/api/dev/resource-metrics")
    def dev_resource_metrics():
        return jsonify(build_resource_metrics_payload())

    @app.get("/api/dev/official-rulings")
    def dev_official_rulings():
        """Dev-only: lookup official rulings by card_code and/or topic_key and optional query. Returns best match + more list with citations."""
        card_code = str(request.args.get("card_code") or "").strip() or None
        topic_key = str(request.args.get("topic_key") or "").strip() or None
        query = str(request.args.get("query") or "").strip() or None
        try:
            from tools.miru_official_rules import (
                DEFAULT_RULES_DB_PATH,
                format_source_citation,
                get_best_official_ruling_match,
                search_official_rulings,
            )
        except ImportError:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Official rules module not available",
                        "best_match": None,
                        "more": [],
                    }
                ),
                500,
            )
        if not DEFAULT_RULES_DB_PATH.is_file():
            return jsonify({"ok": True, "best_match": None, "more": [], "empty": True})
        best = get_best_official_ruling_match(
            DEFAULT_RULES_DB_PATH,
            card_code=card_code,
            topic_key=topic_key,
            query=query,
            prefer_card_specific=True,
        )
        candidates = search_official_rulings(
            DEFAULT_RULES_DB_PATH,
            card_code=card_code,
            topic_key=topic_key,
            query=query,
            status="current",
            limit=15,
        )

        def with_citation(r: dict[str, Any]) -> dict[str, Any]:
            out = {
                k: v for k, v in r.items() if isinstance(v, type(None) | str | int | float | bool)
            }
            out["citation"] = format_source_citation(r)
            return out

        best_match = with_citation(best) if best else None
        more = [
            with_citation(r)
            for r in candidates
            if not best_match or r.get("ruling_id") != best_match.get("ruling_id")
        ][:10]
        return jsonify(
            {
                "ok": True,
                "best_match": best_match,
                "more": more,
                "empty": best_match is None and not more,
            }
        )

    @app.get("/dev/operator-console")
    def dev_operator_console_page():
        """Operator console — mobile-first single-column review surface."""
        if int(CURRENT_SERVER_PORT or 0) != 18765:
            abort(404)
        brand_assets = build_brand_assets()
        return render_template(
            "operator_console.html",
            app_name=APP_NAME,
            favicon_url=brand_assets["favicon_url"],
            asset_version=compute_asset_version(),
        )

    @app.post("/api/dev/training-review/verify-action")
    def dev_training_review_verify_action():
        """Pre-flight governance check before committing an operator action."""
        if not _image_review_port_ok():
            return jsonify({"ok": False, "error": "Only available on port 18765."}), 403
        if not is_local_request():
            return jsonify({"ok": False, "error": "Localhost only."}), 403
        payload = request.get_json(silent=True) or {}
        card_id = str(payload.get("cardId") or "").strip()
        variant_id = str(payload.get("variantId") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        return jsonify(verify_action_preflight(card_id, variant_id, action))

    @app.get("/api/dev/operator-console/legend")
    def dev_operator_console_legend():
        """Badge/state/evidence metadata for operator legend modal."""
        if not _image_review_port_ok():
            return jsonify({"error": "Only available on port 18765."}), 403
        return jsonify(
            {
                "badges": [
                    {
                        "key": "false_parallel",
                        "label": "False Parallel",
                        "description": "Variant code issue — possible duplicate or mismatched parallel print.",
                        "color": "#f87171",
                    },
                    {
                        "key": "name_mismatch",
                        "label": "Name Mismatch",
                        "description": "Card name differs across sources or prior reviews.",
                        "color": "#fb923c",
                    },
                    {
                        "key": "stat_mismatch",
                        "label": "Stat Mismatch",
                        "description": "Stats or attributes conflict between evidence sources.",
                        "color": "#fbbf24",
                    },
                    {
                        "key": "missing_art",
                        "label": "Missing Art",
                        "description": "No verified local artwork found for this card.",
                        "color": "#a78bfa",
                    },
                    {
                        "key": "unverified",
                        "label": "Unverified",
                        "description": "Card exists in catalog but has no supporting evidence.",
                        "color": "#94a3b8",
                    },
                    {
                        "key": "new_card",
                        "label": "New Card",
                        "description": "First appearance in review queue — no prior operator decisions.",
                        "color": "#38bdf8",
                    },
                ],
                "states": [
                    {
                        "key": "live",
                        "label": "Live",
                        "description": "Card is active in catalog with no local image staged.",
                    },
                    {
                        "key": "staged",
                        "label": "Staged",
                        "description": "Card has a verified local image ready for use.",
                    },
                ],
                "evidenceSources": [
                    {
                        "key": "BANDAI_CDN_CHECK",
                        "label": "Bandai CDN",
                        "weight": 0.25,
                        "canContradict": True,
                    },
                    {
                        "key": "INTERNAL_ASSET_CHECK",
                        "label": "Internal Asset",
                        "weight": 0.25,
                        "canContradict": False,
                    },
                    {
                        "key": "PM_PARITY_CHECK",
                        "label": "PM Parity",
                        "weight": 0.20,
                        "canContradict": False,
                    },
                    {
                        "key": "JUSTTCG_CONSTRAINED",
                        "label": "JustTCG",
                        "weight": 0.15,
                        "canContradict": True,
                    },
                    {
                        "key": "OPTCGAPI_CROSS_CHECK",
                        "label": "OPTCG API",
                        "weight": 0.08,
                        "canContradict": False,
                    },
                    {
                        "key": "OPERATOR_URL",
                        "label": "Operator URL",
                        "weight": 0.15,
                        "canContradict": False,
                    },
                    {
                        "key": "PERPLEXITY",
                        "label": "Perplexity",
                        "weight": 0.05,
                        "canContradict": False,
                    },
                    {"key": "YOUTUBE", "label": "YouTube", "weight": 0.03, "canContradict": False},
                ],
                "verdicts": [
                    {
                        "key": "looks_correct",
                        "label": "Looks Correct",
                        "description": "Card data appears accurate across all checked fields.",
                    },
                    {
                        "key": "needs_review",
                        "label": "Needs Review",
                        "description": "One or more issues identified — correction detail required.",
                    },
                    {
                        "key": "not_sure",
                        "label": "Not Sure",
                        "description": "Insufficient evidence to make a confident determination.",
                    },
                ],
            }
        )

    def _proxy_dev_action_to_main(path: str) -> tuple[bool, dict[str, Any]]:
        """POST to main runtime and return (ok, body). Used when this server is worktree (e.g. 18765)."""
        base = (
            (resolve_runtime_monitor_status_url() or "").replace("/api/dev-status", "").rstrip("/")
        )
        if not base:
            return False, {"ok": False, "error": "Main runtime URL not configured."}
        url = f"{base}{path}"
        try:
            req = Request(
                url,
                data=json.dumps({}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with closing(urlopen(req, timeout=10)) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return True, (
                    body if isinstance(body, dict) else {"ok": True, "message": str(body)}
                )
        except HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8")) if e.fp else {}
            except Exception:
                body = {}
            return False, (body if isinstance(body, dict) else {"ok": False, "error": str(e)})
        except (URLError, OSError, ValueError) as e:
            return False, {"ok": False, "error": str(e)}

    @app.post("/api/dev/wake")
    def dev_wake():
        """Wake Miru (main runtime). On worktree this proxies to main; on main returns success."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Wake is only allowed from localhost."}),
                403,
            )
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            ok, body = _proxy_dev_action_to_main("/api/dev/wake")
            return jsonify(body), 200 if ok else 502
        return jsonify({"ok": True, "message": "Miru is awake."})

    @app.post("/api/dev/start-learner")
    def dev_start_learner():
        """Start the learner. On worktree with real control: launch process. Proxies to main if MIRU_RUNTIME_STATUS_URL set. Prevents duplicate start."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Start learner is only allowed from localhost.",
                    }
                ),
                403,
            )
        invalidate_ttl_cache("learning_engine_status")
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            ok, body = _proxy_dev_action_to_main("/api/dev/start-learner")
            if ok:
                return jsonify(body), 200
            error_text = str((body or {}).get("error") or "")
            if "404 Not Found" not in error_text:
                return jsonify(body), 502
        # Real worktree learner process control when this server is the worktree runtime
        if _is_worktree_runtime():
            running_pids = _list_worktree_learner_process_ids()
            if running_pids:
                primary_pid = int(running_pids[0])
                _write_worktree_learner_pid(primary_pid)
                clear_runtime_truth_cache()
                return (
                    jsonify(
                        {
                            "ok": False,
                            "already_running": True,
                            "learner_state": "Running",
                            "learner_pid": primary_pid,
                            "learner_process_count": len(running_pids),
                            "message": "Learner is already running. Use Refresh to see current state.",
                        }
                    ),
                    200,
                )
            record = _read_worktree_learner_pid()
            if record and _is_worktree_learner_process_alive(int(record["pid"])):
                clear_runtime_truth_cache()
                return (
                    jsonify(
                        {
                            "ok": False,
                            "already_running": True,
                            "learner_state": "Running",
                            "learner_pid": record["pid"],
                            "message": "Learner is already running. Use Refresh to see current state.",
                        }
                    ),
                    200,
                )
            if record and not _is_worktree_learner_process_alive(int(record["pid"])):
                _clear_worktree_learner_pid()
            fresh_status = load_learning_engine_status(
                queue_db_path=LEARNING_QUEUE_DB_PATH,
                status_db_path=LEARNING_STATUS_DB_PATH,
                dossier_db_path=LEARNING_DOSSIER_DB_PATH,
                total_cards=0,
            )
            state_fresh = compute_learner_state_and_freshness(fresh_status)
            learner_state = state_fresh.get("learner_state", "")
            heartbeat_fresh = state_fresh.get("heartbeat_freshness", "none")
            if learner_state in ("Running", "Starting") and heartbeat_fresh == "fresh":
                clear_runtime_truth_cache()
                return (
                    jsonify(
                        {
                            "ok": False,
                            "already_running": True,
                            "learner_state": learner_state,
                            "message": "Learner is already running (heartbeat fresh). Use Refresh to see current state.",
                        }
                    ),
                    200,
                )
            ok, message, pid = _start_worktree_learner_process()
            clear_runtime_truth_cache()
            invalidate_ttl_cache("learning_engine_status")
            if ok:
                return jsonify(
                    {
                        "ok": True,
                        "status": "started",
                        "learner_pid": pid,
                        "message": message,
                    }
                )
            return jsonify({"ok": False, "status": "error", "error": message}), 200
        # Non-worktree local: acknowledge only
        clear_runtime_truth_cache()
        return jsonify(
            {
                "ok": True,
                "message": "Start Learner acknowledged. This runtime does not run the worktree learner process. Use the worktree Dev page (port 18765) to start the learner.",
            }
        )

    @app.post("/api/dev/stop-learner")
    def dev_stop_learner():
        """Stop the learner. On worktree with real control: stop the managed process. Proxies to main if MIRU_RUNTIME_STATUS_URL set."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Stop learner is only allowed from localhost.",
                    }
                ),
                403,
            )
        invalidate_ttl_cache("learning_engine_status")
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            ok, body = _proxy_dev_action_to_main("/api/dev/stop-learner")
            return jsonify(body), 200 if ok else 502
        if _is_worktree_runtime():
            ok, message = _stop_worktree_learner_process()
            clear_runtime_truth_cache()
            invalidate_ttl_cache("learning_engine_status")
            insight_sync_report: dict[str, Any] = {}
            if ok:
                try:
                    report = run_worktree_card_insight_sync()
                    with _LAST_INSIGHT_SYNC_LOCK:
                        _LAST_INSIGHT_SYNC_REPORT.clear()
                        _LAST_INSIGHT_SYNC_REPORT.update(
                            {
                                "at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                                "synced_cards": int(
                                    (report.get("sync_result") or {}).get("synced_cards") or 0
                                ),
                                "inserted_insights": int(
                                    (report.get("sync_result") or {}).get("inserted_insights") or 0
                                ),
                                "replaced_insights": int(
                                    (report.get("sync_result") or {}).get("replaced_insights") or 0
                                ),
                                "insight_count_after": int(report.get("insight_count_after") or 0),
                                "dossier_status": report.get("dossier_status") or {},
                                "trigger": "after_stop",
                            }
                        )
                    res = report.get("sync_result") or {}
                    insight_sync_report = {
                        "ran": True,
                        "synced_cards": res.get("synced_cards", 0),
                        "inserted_insights": res.get("inserted_insights", 0),
                        "replaced_insights": res.get("replaced_insights", 0),
                        "insight_count_after": report.get("insight_count_after", 0),
                    }
                except Exception as e:
                    insight_sync_report = {"ran": True, "error": str(e)}
            return jsonify(
                {
                    "ok": ok,
                    "status": "stopped" if ok else "error",
                    "message": message,
                    "insight_sync": insight_sync_report,
                }
            )
        clear_runtime_truth_cache()
        return jsonify(
            {
                "ok": True,
                "message": "This runtime does not manage the worktree learner. Use the worktree Dev page (port 18765) to stop the learner.",
            }
        )

    @app.post("/api/dev/sync-insights")
    def dev_sync_insights():
        """Run worktree card insight sync on demand. Worktree-local paths only. Localhost only. Preserves post-stop automatic sync."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Sync insights is only allowed from localhost.",
                    }
                ),
                403,
            )
        invalidate_ttl_cache("learning_engine_status")
        try:
            report = run_worktree_card_insight_sync()
            with _LAST_INSIGHT_SYNC_LOCK:
                _LAST_INSIGHT_SYNC_REPORT.clear()
                _LAST_INSIGHT_SYNC_REPORT.update(
                    {
                        "at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                        "synced_cards": int(
                            (report.get("sync_result") or {}).get("synced_cards") or 0
                        ),
                        "inserted_insights": int(
                            (report.get("sync_result") or {}).get("inserted_insights") or 0
                        ),
                        "replaced_insights": int(
                            (report.get("sync_result") or {}).get("replaced_insights") or 0
                        ),
                        "insight_count_after": int(report.get("insight_count_after") or 0),
                        "dossier_status": report.get("dossier_status") or {},
                        "trigger": "manual",
                    }
                )
            res = report.get("sync_result") or {}
            insight_sync = {
                "ran": True,
                "synced_cards": res.get("synced_cards", 0),
                "inserted_insights": res.get("inserted_insights", 0),
                "replaced_insights": res.get("replaced_insights", 0),
                "insight_count_after": report.get("insight_count_after", 0),
                "dossier_status": report.get("dossier_status") or {},
            }
            return jsonify(
                {
                    "ok": True,
                    "message": f"Insight sync completed. {insight_sync['synced_cards']} cards, {insight_sync['inserted_insights']} inserted, {insight_sync['replaced_insights']} replaced.",
                    "insight_sync": insight_sync,
                }
            )
        except Exception as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(e),
                        "insight_sync": {"ran": True, "error": str(e)},
                    }
                ),
                200,
            )

    @app.post("/api/dev/refresh-status")
    def dev_refresh_status():
        """Clear cached status so the next fetch gets fresh data from the main runtime."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Refresh status is only allowed from localhost.",
                    }
                ),
                403,
            )
        clear_runtime_truth_cache()
        invalidate_ttl_cache("learning_engine_status")
        return jsonify(
            {
                "ok": True,
                "message": "Status cache cleared. Refresh the page or click Refresh to see current state.",
            }
        )

    @app.post("/api/dev/set-learner-mode")
    def dev_set_learner_mode():
        """Set learner mode (DRY_RUN, SANDBOX, REVIEW_REQUIRED, ACTIVE). Localhost only."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Set learner mode is only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else request.form
        mode = str(payload.get("mode") or "").strip().upper()
        if mode not in LEARNER_MODES:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Invalid mode. Use one of: {', '.join(LEARNER_MODES)}",
                    }
                ),
                400,
            )
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            base = remote_url.replace("/api/dev-status", "").rstrip("/")
            url = f"{base}/api/dev/set-learner-mode"
            try:
                req = Request(
                    url,
                    data=json.dumps({"mode": mode}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with closing(urlopen(req, timeout=10)) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    clear_runtime_truth_cache()
                    return jsonify(body), 200
            except (HTTPError, URLError, OSError, ValueError) as e:
                clear_runtime_truth_cache()
                return jsonify({"ok": False, "error": str(e)}), 502
        ok = set_learner_mode(mode)
        clear_runtime_truth_cache()
        return jsonify(
            {
                "ok": ok,
                "mode": get_learner_mode(),
                "message": f"Mode set to {get_learner_mode()}. Refresh status to see it.",
            }
        )

    @app.get("/api/dev/activity-feed")
    def dev_activity_feed():
        """Lightweight activity feed for near-live polling."""
        limit = min(int(request.args.get("limit") or 30), 80)
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            base = remote_url.replace("/api/dev-status", "").rstrip("/")
            url = f"{base}/api/dev/activity-feed?limit={limit}"
            try:
                with urlopen(url, timeout=8) as resp:
                    return jsonify(json.load(resp))
            except (HTTPError, URLError, OSError, ValueError):
                return (
                    jsonify({"activity": [], "error": "Could not fetch from main runtime"}),
                    502,
                )
        events = load_monitor_engine_events(status_db_path=LEARNING_STATUS_DB_PATH, limit=limit)
        return jsonify({"activity": events["recent_activity"]})

    @app.get("/api/dev/pending-approvals")
    def dev_pending_approvals():
        """Actionable miru_review_queue pending rows plus legacy learner queue."""
        publication: list[dict[str, Any]] = []
        catalog_path = Path(FALLBACK_CATALOG_DB_PATH)
        if catalog_path.is_file():
            _pub_sql = """
                SELECT
                    rq.item_key,
                    rq.target_id,
                    rq.status,
                    rq.approval_state,
                    rq.readiness_state,
                    rq.review_reason,
                    rq.guardrail_label,
                    rq.confidence_score,
                    rq.risk_level,
                    rq.recommended_next_step,
                    rq.summary_text,
                    rq.updated_at,
                    rq.created_at,
                    c.card_name,
                    c.set_code,
                    c.set_name
                FROM miru_review_queue rq
                LEFT JOIN cards c
                    ON upper(trim(c.canonical_code)) = upper(trim(rq.target_id))
                LEFT JOIN image_variant_analysis iv
                    ON upper(trim(iv.canonical_code)) = upper(trim(rq.target_id))
                WHERE rq.status = 'pending'
                  AND (
                        trim(coalesce(rq.approval_state, '')) = ''
                     OR rq.approval_state = 'pending_review'
                      )
                  AND NOT (
                        trim(coalesce(rq.queue_type, '')) = 'image_variant_sp'
                        AND lower(trim(coalesce(iv.review_status, ''))) = 'reviewed_not_sp'
                      )
                ORDER BY
                    CASE WHEN rq.approval_state = 'pending_review' THEN 0
                         WHEN trim(coalesce(rq.approval_state, '')) = '' THEN 1
                         ELSE 2
                    END,
                    rq.confidence_score DESC,
                    rq.updated_at DESC
                LIMIT ?
            """
            try:
                with closing(sqlite3.connect(str(catalog_path))) as _pub_conn:
                    _pub_conn.row_factory = sqlite3.Row
                    _pub_rows = _pub_conn.execute(_pub_sql, (500,)).fetchall()
            except sqlite3.Error:
                _pub_rows = []
            for row in _pub_rows:
                code = str(row["target_id"] or "").strip().upper()
                preview_src = (
                    str(row["summary_text"] or "").strip()
                    or str(row["review_reason"] or "").strip()
                )
                preview = preview_src.replace("\n", " ").strip()
                if len(preview) > 220:
                    preview = preview[:217] + "…"
                try:
                    conf = float(row["confidence_score"] or 0.0)
                except (TypeError, ValueError):
                    conf = 0.0
                if conf < 0.0:
                    conf = 0.0
                if conf > 1.0:
                    conf = 1.0
                publication.append(
                    {
                        "queue_kind": "publication",
                        "queue_type": "publication",
                        "item_key": str(row["item_key"] or "").strip(),
                        "target_id": str(row["target_id"] or "").strip(),
                        "card_code": code,
                        "status": str(row["status"] or "").strip(),
                        "approval_state": str(row["approval_state"] or "").strip(),
                        "readiness_state": str(row["readiness_state"] or "").strip(),
                        "review_reason": str(row["review_reason"] or "").strip(),
                        "guardrail_label": str(row["guardrail_label"] or "").strip(),
                        "confidence_score": conf,
                        "risk_level": str(row["risk_level"] or "").strip(),
                        "recommended_next_step": str(row["recommended_next_step"] or "").strip(),
                        "summary_text": str(row["summary_text"] or "").strip(),
                        "updated_at": str(row["updated_at"] or "").strip(),
                        "created_at": str(row["created_at"] or "").strip(),
                        "card_name": str(row["card_name"] or "").strip(),
                        "set_code": str(row["set_code"] or "").strip(),
                        "set_name": str(row["set_name"] or "").strip(),
                        "id": code,
                        "insight_type": str(row["guardrail_label"] or "").strip() or "—",
                        "confidence": conf,
                        "insight_preview": preview or "—",
                    }
                )

        learner = load_pending_approvals(status_db_path=LEARNING_STATUS_DB_PATH)

        def _load_card_insights(card_code: str) -> list[dict[str, Any]]:
            code = str(card_code or "").strip().upper()
            if not code:
                return []
            path = Path(FALLBACK_CATALOG_DB_PATH)
            if not path.is_file():
                return []
            try:
                with closing(sqlite3.connect(str(path))) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        """
                        SELECT insight_type, insight_text, confidence, quality_tier
                        FROM miru_card_insights
                        WHERE card_id = ?
                        ORDER BY confidence DESC
                        """,
                        (code,),
                    ).fetchall()
            except sqlite3.Error:
                return []
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "insight_type": str(row["insight_type"] or "").strip(),
                        "insight_text": str(row["insight_text"] or "").strip(),
                        "confidence": float(row["confidence"] or 0.0),
                        "quality_tier": str(row["quality_tier"] or "").strip(),
                    }
                )
            return out

        for row in publication:
            row["image_url"] = resolve_card_image_url(row.get("card_code", ""))
            insights = _load_card_insights(row.get("card_code", ""))
            row["insights"] = insights
            row["insights_count"] = len(insights)
            rr = str(row.get("review_reason") or "").strip().lower()
            if rr == "legality_sensitive":
                row["ruling_citation"] = lookup_legality_sensitive_ruling_citation(
                    FALLBACK_CATALOG_DB_PATH, row.get("card_code", "")
                )
            else:
                row["ruling_citation"] = None
        for row in learner:
            row["queue_kind"] = "learner"
            row["image_url"] = resolve_card_image_url(row.get("card_code", ""))
            insights = _load_card_insights(row.get("card_code", ""))
            row["insights"] = insights
            row["insights_count"] = len(insights)
            rr = str(row.get("review_reason") or "").strip().lower()
            if rr == "legality_sensitive":
                row["ruling_citation"] = lookup_legality_sensitive_ruling_citation(
                    FALLBACK_CATALOG_DB_PATH, row.get("card_code", "")
                )
            else:
                row["ruling_citation"] = None
        resp = jsonify(
            {
                "items": publication + learner,
                "catalog_db_path": str(FALLBACK_CATALOG_DB_PATH),
                "publication_count": len(publication),
                "learner_count": len(learner),
            }
        )
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    @app.get("/api/dev/recently-published")
    def dev_recently_published():
        """Read-only: card_intelligence rows in publish-ready states, newest publish update first."""
        catalog_path = Path(FALLBACK_CATALOG_DB_PATH)
        if not catalog_path.is_file():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Catalog database not found: {catalog_path}",
                    }
                ),
                500,
            )
        sql = """
            SELECT
                c.canonical_code AS card_code,
                c.card_name AS card_name,
                ci.publish_status,
                ci.role_label,
                ci.role_summary,
                ci.deck_usage_summary,
                ci.publication_rationale,
                ci.coverage_gap_summary,
                ci.publication_candidate_profile,
                ci.confidence_score,
                ci.publish_updated_at,
                (
                    SELECT v.image_path
                    FROM card_variants v
                    WHERE v.card_id = c.id
                      AND COALESCE(v.is_base, 0) = 1
                      AND trim(COALESCE(v.image_path, '')) != ''
                      AND lower(replace(trim(v.image_path), char(92), '/')) LIKE 'thumbs/%'
                    ORDER BY v.id ASC
                    LIMIT 1
                ) AS cv_thumb_path,
                (
                    SELECT v.image_path
                    FROM card_variants v
                    WHERE v.card_id = c.id
                      AND trim(COALESCE(v.image_path, '')) != ''
                      AND lower(replace(trim(v.image_path), char(92), '/')) NOT LIKE 'thumbs/%'
                    ORDER BY COALESCE(v.is_base, 0) DESC, v.id ASC
                    LIMIT 1
                ) AS cv_set_image_path
            FROM card_intelligence ci
            INNER JOIN cards c ON c.id = ci.card_id
            WHERE lower(trim(coalesce(ci.publish_status, '')))
                IN ('publish_ready', 'approved_for_candidate')
            ORDER BY ci.publish_updated_at DESC NULLS LAST
        """
        try:
            with closing(
                sqlite3.connect(f"file:{catalog_path.resolve().as_posix()}?mode=ro", uri=True)
            ) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql).fetchall()
        except sqlite3.Error as exc:
            return (
                jsonify({"ok": False, "error": str(exc)}),
                500,
            )
        items: list[dict[str, Any]] = []
        for row in rows:
            role_label = str(row["role_label"] or "").strip()
            profile = str(row["publication_candidate_profile"] or "").strip()
            insight_type = role_label or profile or ""
            insight_text = ""
            for key in (
                "role_summary",
                "deck_usage_summary",
                "publication_rationale",
                "coverage_gap_summary",
            ):
                cand = str(row[key] or "").strip()
                if cand:
                    insight_text = cand
                    break
            try:
                conf = float(row["confidence_score"] or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            items.append(
                {
                    "card_code": str(row["card_code"] or "").strip().upper(),
                    "card_name": str(row["card_name"] or "").strip(),
                    "publish_status": str(row["publish_status"] or "").strip(),
                    "insight_type": insight_type,
                    "confidence": conf,
                    "insight_text": insight_text,
                    "published_at": str(row["publish_updated_at"] or "").strip(),
                    "thumb_path": _catalog_image_rel_to_images_cards_url(row["cv_thumb_path"]),
                    "image_path": _catalog_image_rel_to_images_cards_url(row["cv_set_image_path"]),
                }
            )
        return jsonify({"ok": True, "count": len(items), "items": items})

    @app.get("/api/dev/tasks")
    def dev_get_tasks():
        with _TASK_QUEUE_LOCK:
            items = _read_task_queue_items()
        items_sorted = sorted(
            items,
            key=lambda row: str(row.get("created_at") or row.get("timestamp") or ""),
            reverse=True,
        )
        return jsonify({"ok": True, "items": items_sorted})

    @app.post("/api/dev/tasks")
    def dev_post_task():
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else request.form
        prompt = str(payload.get("prompt") or "").rstrip()
        scope = str(payload.get("scope") or "").strip()
        label = str(payload.get("label") or "").strip()
        if not label:
            return jsonify({"ok": False, "error": "Label is required."}), 400
        if not prompt:
            return jsonify({"ok": False, "error": "Prompt is required."}), 400
        if not scope:
            scope = "Port 18765"

        now = datetime.now(UTC).isoformat()
        task_id = uuid.uuid4().hex
        item: dict[str, Any] = {
            "task_id": task_id,
            "label": label,
            "prompt": prompt,
            "scope": scope,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        with _TASK_QUEUE_LOCK:
            items = _read_task_queue_items()
            items.append(item)
            _write_task_queue_items(items)
        return jsonify({"ok": True, "item": item}), 200

    @app.patch("/api/dev/tasks/<task_id>")
    def dev_patch_task(task_id: str):
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else request.form
        status = str(payload.get("status") or "").strip().lower()
        allowed = {"queued", "in_progress", "done", "failed"}
        if status and status not in allowed:
            return (
                jsonify({"ok": False, "error": "Invalid status."}),
                400,
            )
        with _TASK_QUEUE_LOCK:
            items = _read_task_queue_items()
            updated = None
            for row in items:
                row_id = str(row.get("task_id") or row.get("id") or "")
                if row_id == str(task_id):
                    if status:
                        row["status"] = status
                    row["updated_at"] = datetime.now(UTC).isoformat()
                    updated = row
                    break
            if updated is None:
                return jsonify({"ok": False, "error": "Task not found."}), 404
            _write_task_queue_items(items)
        return jsonify({"ok": True, "item": updated}), 200

    @app.post("/api/dev/publish-ready-insight-sync")
    def dev_publish_ready_insight_sync():
        """Bounded sync: miru_card_insights for card_intelligence.publish_ready (18765 only)."""
        if int(CURRENT_SERVER_PORT or 0) != 18765:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "publish-ready-insight-sync is only available on port 18765.",
                    }
                ),
                403,
            )
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "This action is only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        raw_limit = payload.get("limit", 70)
        try:
            lim = int(raw_limit)
        except (TypeError, ValueError):
            lim = 70
        lim = max(1, min(lim, 80))

        result = run_publish_ready_insight_sync(
            limit=lim,
            project_db_path=FALLBACK_CATALOG_DB_PATH,
        )

        ensure_catalog_sync_schema(FALLBACK_CATALOG_DB_PATH)
        with closing(connect_catalog_db(FALLBACK_CATALOG_DB_PATH)) as conn:
            _log_action_history(
                conn,
                action_id="publish.storefront_mutation",
                action_title="Apply storefront mutation",
                category="publish",
                target_type="project_miru_storefront",
                target_id="publish_ready_insight_sync",
                execution_status="executed",
                eligibility="allowed_with_review",
                guardrail_label="Review required",
                risk_level="medium",
                confidence_score=0.92,
                rationale=(
                    "Testing environment execution: publish_ready insights will be populated into "
                    "card_catalog.db for 18080 test site consumption. Operator must explicitly trigger. "
                    "This path is blocked when 8080 promotion is re-enabled."
                ),
                sync_reason="operator_test_env_trigger",
                payload={
                    "decision_source": "operator_test_env_trigger",
                    "limit": lim,
                    "publish_ready_codes": result.get("publish_ready_codes") or [],
                    "sync_report_keys": list((result.get("sync_report") or {}).keys()),
                },
            )

        return jsonify(result)

    @app.post("/api/dev/publish-review/approve")
    def dev_publish_review_approve():
        """Set publish_status to publish_ready for one card (catalog only). Localhost only."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Approve is only allowed from localhost."}),
                403,
            )
        payload = request.get_json(silent=True) or {}
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not card_code:
            return jsonify({"ok": False, "error": "card_code is required."}), 400
        ok = update_publication_review_status(
            card_code,
            "publish_ready",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            require_review_state=True,
        )
        if not ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"No publish_requires_review row for {card_code}.",
                    }
                ),
                404,
            )
        return jsonify({"ok": True, "card_code": card_code, "publish_status": "publish_ready"})

    @app.post("/api/dev/publish-review/reject")
    def dev_publish_review_reject():
        """Set publish_status to publish_deferred for one card (catalog only). Localhost only."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Reject is only allowed from localhost."}),
                403,
            )
        payload = request.get_json(silent=True) or {}
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not card_code:
            return jsonify({"ok": False, "error": "card_code is required."}), 400
        ok = update_publication_review_status(
            card_code,
            "publish_deferred",
            project_db_path=FALLBACK_CATALOG_DB_PATH,
            require_review_state=True,
        )
        if not ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"No publish_requires_review row for {card_code}.",
                    }
                ),
                404,
            )
        return jsonify({"ok": True, "card_code": card_code, "publish_status": "publish_deferred"})

    @app.post("/api/dev/publish-review/approve-all")
    def dev_publish_review_approve_all():
        """Move all publish_requires_review rows to publish_ready. Localhost only."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Approve all is only allowed from localhost.",
                    }
                ),
                403,
            )
        n = approve_all_publication_review(project_db_path=FALLBACK_CATALOG_DB_PATH)
        return jsonify({"ok": True, "updated": n})

    @app.post("/api/dev/approve-validation")
    def dev_approve_validation():
        """Approve one item: sync to project and remove from review queue. Localhost only."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Approve is only allowed from localhost."}),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_id = payload.get("id")
        card_code = str(payload.get("card_code") or "").strip().upper()
        source_id = str(payload.get("source_id") or "").strip().lower()
        if not card_code or not source_id:
            return (
                jsonify({"ok": False, "error": "card_code and source_id are required."}),
                400,
            )
        dossier_path = Path(LEARNING_DOSSIER_DB_PATH)
        if not dossier_path.is_file():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Dossier DB not found; cannot load source record.",
                    }
                ),
                502,
            )
        try:
            with closing(sqlite3.connect(dossier_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT field_payload_json
                    FROM learning_dossier_sources
                    WHERE card_code = ? AND source_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (card_code, source_id),
                ).fetchone()
        except sqlite3.Error as e:
            return jsonify({"ok": False, "error": f"Dossier read failed: {e}"}), 502
        if not row:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"No source record for {card_code} from {source_id}.",
                    }
                ),
                404,
            )
        try:
            data = json.loads(str(row["field_payload_json"] or "{}"))
        except json.JSONDecodeError as e:
            return jsonify({"ok": False, "error": f"Invalid payload: {e}"}), 502
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Invalid payload shape."}), 502
        traits_raw = data.get("traits")
        if isinstance(traits_raw, list):
            traits = [str(x).strip() for x in traits_raw if str(x).strip()]
        else:
            text = str(traits_raw or "")
            traits = (
                [p.strip() for p in text.replace("/", " ").split() if p.strip()] if text else []
            )
        try:
            record = NormalizedSourceRecord(
                card_code=str(data.get("card_code") or card_code).strip().upper(),
                card_name=str(data.get("card_name") or "").strip(),
                set_code=str(data.get("set_code") or "").strip().upper(),
                set_name=str(data.get("set_name") or "").strip(),
                rarity=str(data.get("rarity") or "").strip(),
                color=str(data.get("color") or "").strip(),
                card_type=str(data.get("card_type") or "").strip(),
                cost=str(data.get("cost") or "").strip(),
                power=str(data.get("power") or "").strip(),
                counter=str(data.get("counter") or "").strip(),
                attribute=str(data.get("attribute") or "").strip(),
                traits=traits,
                life=str(data.get("life") or "").strip(),
                effect_text=str(data.get("effect_text") or "").strip(),
                trigger_text=str(data.get("trigger_text") or "").strip(),
                source_id=str(data.get("source_id") or source_id).strip().lower(),
                source_url=str(data.get("source_url") or "").strip(),
                source_reference=str(data.get("source_reference") or "").strip(),
                fetched_at=str(data.get("fetched_at") or "").strip(),
            )
        except (TypeError, ValueError) as e:
            return jsonify({"ok": False, "error": f"Could not build record: {e}"}), 502
        sync = MiruProjectDbSync(project_db_path=FALLBACK_CATALOG_DB_PATH, sync_immediate=True)
        try:
            sync.queue_validated_record(record, task_type="verify_official_fields")
        except Exception as e:
            return jsonify({"ok": False, "error": f"Sync failed: {e}"}), 502
        status_path = Path(LEARNING_STATUS_DB_PATH)
        if status_path.is_file() and item_id is not None:
            try:
                with closing(sqlite3.connect(status_path)) as conn:
                    conn.execute("DELETE FROM learner_review_queue WHERE id = ?", (int(item_id),))
                    conn.commit()
            except sqlite3.Error:
                pass
        return jsonify(
            {
                "ok": True,
                "card_code": card_code,
                "source_id": source_id,
                "message": "Approved and synced.",
            }
        )

    @app.post("/api/dev/reject-validation")
    def dev_reject_validation():
        """Remove one item from the review queue (reject). Localhost only."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Reject is only allowed from localhost."}),
                403,
            )
        payload = request.get_json(silent=True) or {}
        item_id = payload.get("id")
        if item_id is None:
            return jsonify({"ok": False, "error": "id is required."}), 400
        status_path = Path(LEARNING_STATUS_DB_PATH)
        if not status_path.is_file():
            return jsonify({"ok": False, "error": "Status DB not found."}), 502
        try:
            with closing(sqlite3.connect(status_path)) as conn:
                conn.execute("DELETE FROM learner_review_queue WHERE id = ?", (int(item_id),))
                conn.commit()
        except sqlite3.Error as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify(
            {
                "ok": True,
                "id": int(item_id),
                "message": "Rejected and removed from queue.",
            }
        )

    WORKTREE_REVIEW_SNAPSHOT_PATH = (  # noqa: N806
        PROJECT_ROOT / "data" / "snapshots" / "community_cardlist.json"
    )

    @app.post("/api/dev/seed-review-task")
    def dev_seed_review_task():
        """Seed one verify_official_fields task (same as run_worktree_review_cycle.py). Localhost only."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Seed review task is only allowed from localhost.",
                    }
                ),
                403,
            )
        payload = request.get_json(silent=True) or {}
        card_code = str(payload.get("card_code") or "OP01-001").strip().upper()
        if not card_code:
            return jsonify({"ok": False, "error": "card_code is required."}), 400
        if not WORKTREE_REVIEW_SNAPSHOT_PATH.is_file():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Snapshot not found: {WORKTREE_REVIEW_SNAPSHOT_PATH}",
                    }
                ),
                502,
            )
        try:
            parser = build_learner_parser()
            args = parser.parse_args([])
            args.queue_db = LEARNING_QUEUE_DB_PATH
            args.status_db = LEARNING_STATUS_DB_PATH
            args.dossier_db = LEARNING_DOSSIER_DB_PATH
            engine = build_engine_from_args(args)
            result = engine.run_once(
                card_code=card_code,
                task_type="verify_official_fields",
                source_id="community-cardlist",
                task_payload={"snapshot_path": str(WORKTREE_REVIEW_SNAPSHOT_PATH)},
            )
        except Exception as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(e),
                        "card_code": card_code,
                        "source_id": "community-cardlist",
                    }
                ),
                200,
            )
        sync = result.get("project_sync") or {}
        added = sync.get("added_to_review_queue", False)
        invalidate_ttl_cache("learning_engine_status")
        return jsonify(
            {
                "ok": result.get("ok") is True,
                "card_code": result.get("card_code") or card_code,
                "source_id": result.get("source_id") or "community-cardlist",
                "message": result.get("message")
                or result.get("error")
                or ("Added to review queue." if added else "Task completed."),
                "added_to_review_queue": added,
            }
        )

    @app.post("/api/dev/restart")
    def dev_restart():
        """Restart the learner: stop then start. On worktree with real control: stop managed process then start it. Proxies to main if configured."""
        if not is_local_request():
            return (
                jsonify({"ok": False, "error": "Restart is only allowed from localhost."}),
                403,
            )
        remote_url = resolve_runtime_monitor_status_url()
        if remote_url:
            ok, body = _proxy_dev_action_to_main("/api/dev/restart")
            return jsonify(body), 200 if ok else 502
        if _is_worktree_runtime():
            _stop_worktree_learner_process()
            time.sleep(1)
            ok, message, pid = _start_worktree_learner_process()
            clear_runtime_truth_cache()
            invalidate_ttl_cache("learning_engine_status")
            return jsonify(
                {
                    "ok": ok,
                    "status": "restarting" if ok else "error",
                    "learner_pid": pid,
                    "message": message if ok else f"Restart failed: {message}",
                }
            )
        return jsonify(
            {
                "ok": True,
                "message": "This runtime does not manage the worktree learner. Use the worktree Dev page (port 18765) to restart.",
            }
        )

    def _kill_processes_bound_to_port(port: int) -> list[int]:
        """Windows netstat/taskkill cleanup for ghost listeners on a target port."""
        if os.name != "nt":
            return []
        pids: set[int] = set()
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return []
        needle = f":{int(port)}"
        for raw_line in (out or "").splitlines():
            line = str(raw_line or "").strip()
            if not line or needle not in line:
                continue
            parts = [p for p in line.split() if p]
            if len(parts) < 5:
                continue
            pid_raw = parts[-1]
            if pid_raw.isdigit():
                pid = int(pid_raw)
                if pid > 0:
                    pids.add(pid)
        killed: list[int] = []
        for pid in sorted(pids):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                killed.append(pid)
            except Exception:
                continue
        if killed:
            time.sleep(1)
        return killed

    @app.post("/api/dev/restart/<int:port>")
    def dev_restart_port(port: int):
        """Restart only approved service ports from /dev runtime controls."""
        if not _is_runtime_control_allowed():
            return (
                jsonify(
                    {
                        "error": "Restart allowed only from this machine or Tailscale",
                    }
                ),
                403,
            )
        if port not in {18765, 18080}:
            return (
                jsonify(
                    {
                        "error": "Unsupported port",
                    }
                ),
                400,
            )
        if port == 18765:
            _kill_processes_bound_to_port(port)
            code, out, err = _run_worktree_script("start_miru_ai_dev.ps1", ["-Force"], wait=False)
        else:
            result = subprocess.run(
                ["nssm", "restart", "MiruDashboard"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            code = result.returncode
            out = result.stdout
            err = result.stderr
        if code != 0:
            return (
                jsonify(
                    {
                        "error": (err or out or "Restart launch failed").strip(),
                    }
                ),
                502,
            )
        return jsonify({"status": "restarting", "port": int(port)})

    @app.get("/api/dev/usage")
    def legacy_dev_usage():
        return jsonify(build_legacy_dev_usage())

    @app.get("/api/dev/worker-last-run")
    def dev_worker_last_run():
        """Return latest scheduled worker run (data/miru_worker_last_run.json). Read-only; no DB. Missing file → { \"action\": \"no_run_recorded\" }."""
        return jsonify(load_worker_last_run())

    @app.get("/api/dev/self-report")
    def dev_self_report():
        """Localhost-only: Miru operator self-report (data/miru_self_report.json). Read-only file; not on the public storefront."""
        if not is_local_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Self-report is only available from localhost.",
                    }
                ),
                403,
            )
        path = PROJECT_ROOT / "data" / "miru_self_report.json"
        if not path.is_file():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Self-report not generated yet.",
                        "hint": "Run: python -m tools.miru_self_report or complete the sandbox cycle (Stage 9).",
                    }
                ),
                404,
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return (
                jsonify({"ok": False, "error": f"Failed to read self-report: {exc}"}),
                500,
            )
        return jsonify({"ok": True, "path": "data/miru_self_report.json", "report": payload})

    @app.get("/api/dev/validation_insights")
    def legacy_validation_insights():
        return jsonify(build_legacy_validation_insights())

    @app.post("/api/dev/fetch-missing-images")
    def dev_fetch_missing_images():
        if not _runtime_trusted_network_client():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Image fetch is only allowed from this machine, private LAN, or Tailscale.",
                    }
                ),
                403,
            )
        started = _start_fetch_missing_images_job(trigger="manual_api")
        if not started:
            return jsonify(
                {
                    "status": "started",
                    "message": "Image fetch already running in background",
                }
            )
        return jsonify(
            {
                "status": "started",
                "message": "Image fetch running in background",
            }
        )

    @app.post("/api/dev/test-pushover")
    def dev_test_pushover():
        if not (app.config.get("TESTING") or is_local_request()):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Pushover test endpoint is limited to localhost.",
                    }
                ),
                403,
            )

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form
        use_learning_summary = (
            parse_bool_flag(payload.get("use_learning_summary"))
            or not str(payload.get("message", "")).strip()
        )
        dry_run = parse_bool_flag(payload.get("dry_run"))
        learning_preview: dict[str, Any] | None = None
        if use_learning_summary:
            training_status = build_training_status()
            learning_status = load_learning_engine_status(
                queue_db_path=LEARNING_QUEUE_DB_PATH,
                status_db_path=LEARNING_STATUS_DB_PATH,
                dossier_db_path=LEARNING_DOSSIER_DB_PATH,
                total_cards=int(training_status.get("total_cards") or 0),
            )
            learning_preview = build_learning_notification_payload(
                training_status,
                learning_status,
                previous_snapshot=load_pushover_learning_snapshot(),
            )
        title = str(payload.get("title", "")).strip() or (
            learning_preview["title"] if learning_preview else "Miru AI Test"
        )
        message = str(payload.get("message", "")).strip() or (
            learning_preview["message"]
            if learning_preview
            else f"Miru AI test notification from {PROJECT_ROOT} at {current_timestamp()}."
        )
        priority_raw = str(payload.get("priority", "")).strip()
        priority: int | None = None
        if priority_raw:
            try:
                priority = int(priority_raw)
            except ValueError:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"Invalid priority value: {priority_raw}",
                        }
                    ),
                    400,
                )

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
            if bool(result["ok"]) and learning_preview is not None:
                save_pushover_learning_snapshot(
                    dict(learning_preview["snapshot"]),
                    title=str(learning_preview["title"] or ""),
                    message=str(learning_preview["message"] or ""),
                )
        response_body = {
            "ok": bool(result["ok"]),
            "pushover": build_pushover_runtime_state(
                training_status=training_status if use_learning_summary else None,
                learning_status=learning_status if use_learning_summary else None,
            ),
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
            return (
                jsonify(
                    {
                        "ok": False,
                        "card_code": canonical_code,
                        "error": "Validation audit not found.",
                    }
                ),
                404,
            )
        return jsonify({"ok": True, "card_code": canonical_code, "audit": audit})

    @app.get("/api/run")
    @app.get("/api/run/")
    @app.post("/api/run")
    @app.post("/api/run/")
    def run_request():
        abort(404)

    @app.get("/api/ops/report")
    def ops_report():
        """Ops summary: budget state, DLQ count, last 10 completions, last 5 heartbeats."""

        def _read_jsonl_tail(path: Path, n: int) -> list[dict]:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                tail = [ln for ln in lines if ln.strip()][-n:]
                result = []
                for ln in tail:
                    with suppress(Exception):
                        result.append(json.loads(ln))
                return result
            except Exception:
                return []

        def _count_jsonl_lines(path: Path) -> int:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                return sum(1 for ln in lines if ln.strip())
            except Exception:
                return 0

        budget = None
        with suppress(Exception):
            budget = json.loads(LIMITS_STATUS_PATH.read_text(encoding="utf-8"))

        dlq_path = PROJECT_ROOT / "data" / "dispatch_dlq.jsonl"
        dlq_count = _count_jsonl_lines(dlq_path)

        completions_path = PROJECT_ROOT / "data" / "cc_completion_log.jsonl"
        last_completions = _read_jsonl_tail(completions_path, 10)

        heartbeat_path = PROJECT_ROOT / "data" / "cc_heartbeat_log.jsonl"
        all_heartbeats = _read_jsonl_tail(heartbeat_path, 500)
        latest_by_worker: dict[str, dict] = {}
        for hb in all_heartbeats:
            wid = hb.get("worker_id", "unknown")
            if wid not in latest_by_worker or hb.get("ts", "") > latest_by_worker[wid].get(
                "ts", ""
            ):
                latest_by_worker[wid] = hb
        last_heartbeats = sorted(
            latest_by_worker.values(),
            key=lambda x: x.get("ts", ""),
            reverse=True,
        )[:5]

        return jsonify(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "budget": budget,
                "dlq_count": dlq_count,
                "last_completions": last_completions,
                "last_heartbeats": last_heartbeats,
            }
        )

    # ─── Shadow-review API (PRO-909 PR-A) ─────────────────────────────────
    from miru_ai import shadow_review as _shadow_review

    @app.get("/api/shadow-review/queue")
    def shadow_review_queue():
        try:
            limit = int(request.args.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        if limit <= 0:
            limit = 50
        if limit > 500:
            limit = 500
        return jsonify(_shadow_review.fetch_queue(limit=limit))

    @app.get("/api/shadow-review/item/<canonical_code>/<print_id>")
    def shadow_review_item(canonical_code: str, print_id: str):
        contributing_model = request.args.get("contributing_model", "").strip()
        if not contributing_model:
            return jsonify({"error": "contributing_model query param required"}), 400
        item = _shadow_review.fetch_item(
            canonical_code=canonical_code,
            print_id=print_id,
            contributing_model=contributing_model,
        )
        if item is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(item)

    @app.post("/api/shadow-review/verdict")
    def shadow_review_verdict():
        body = request.get_json(silent=True) or {}
        required = ("canonical_code", "print_id", "contributing_model", "verdict")
        missing = [k for k in required if not body.get(k)]
        if missing:
            return jsonify({"error": f"missing fields: {','.join(missing)}"}), 400
        try:
            result = _shadow_review.submit_verdict(
                canonical_code=body["canonical_code"],
                print_id=body["print_id"],
                contributing_model=body["contributing_model"],
                verdict=body["verdict"],
                sources_checked=body.get("sources_checked") or [],
                operator=body.get("operator", "operator"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.get("/dev/shadow-review")
    def shadow_review_page():
        # PRO-909 PR-B — operator-facing review queue. The Vite bundle is
        # shared across DevReviewHubPage / OperatorConsolePage / MiruHubPage /
        # ShadowReviewPage; App.tsx dispatches by root-element id.
        return render_template("shadow_review.html")

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightweight Flask sidecar for running miru_ai/core/ai.py from a phone or browser."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the Flask app to.")
    parser.add_argument("--port", type=int, default=18765, help="Port to bind the Flask app to.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the Flask development server in debug mode.",
    )
    return parser.parse_args()


def main() -> None:
    global CURRENT_SERVER_PORT, _SERVER_STARTED_AT
    args = parse_args()
    CURRENT_SERVER_PORT = int(args.port)
    _SERVER_STARTED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _register_miru_ai_dev_pid_lifecycle(CURRENT_SERVER_PORT)
    app = create_app()
    if os.name == "nt":
        os.environ.pop("WERKZEUG_SERVER_FD", None)
        os.environ.pop("WERKZEUG_RUN_MAIN", None)
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
