from __future__ import annotations

from contextlib import closing
import json
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "miru_ai_onepiece_knowledge.json"
DEFAULT_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"

SOURCE_PRIORITY = (
    "official-cardlist",
    "local-catalog",
    "community-cache",
    "meta-context",
)

CARD_CODE_RE = re.compile(
    r"\b(?P<prefix>OP|EB|ST|PRB|P)\s*[- ]?\s*(?P<series>\d{1,3})(?:\s*[- ]\s*(?P<number>\d{1,3}[A-Z]?))?(?:[_ -]?(?P<print>p\d+))?\b",
    re.I,
)
SET_CODE_RE = re.compile(r"\b(?P<prefix>OP|EB|ST|PRB)\s*[- ]?\s*(?P<series>\d{1,3})\b", re.I)
SERIES_LABEL_CODE_RE = re.compile(r"\[(?P<code>[A-Z0-9-]+)\]")

CARD_QUERY_STOP_WORDS = {
    "a",
    "an",
    "and",
    "art",
    "card",
    "codex",
    "deck",
    "for",
    "fix",
    "help",
    "in",
    "make",
    "matching",
    "miru",
    "mode",
    "of",
    "on",
    "piece",
    "plan",
    "prompt",
    "review",
    "set",
    "tcg",
    "the",
    "to",
    "understand",
    "write",
}

EFFECT_VOCABULARY = {
    "on play": "On Play effect timing",
    "when attacking": "When Attacking effect timing",
    "activate main": "Activate: Main effect timing",
    "activate battle": "Activate: Battle effect timing",
    "don!!": "DON!! resource symbol",
    "rush": "Rush keyword",
    "blocker": "Blocker keyword",
    "banish": "Banish keyword",
    "double attack": "Double Attack keyword",
    "counter": "Counter value or Counter event language",
    "trigger": "Trigger effect language",
    "rested": "Rested status language",
    "ko": "KO / removal effect language",
    "leader": "Leader card type",
    "character": "Character card type",
    "event": "Event card type",
    "stage": "Stage card type",
}

GAMEPLAY_GUIDE = {
    "don!!": "DON!! is the resource system in One Piece TCG. It pays costs and can often be attached to Leaders or Characters for extra power or effects.",
    "counter": "Counter is the defensive value you can use from hand during battle. A card with 1000 or 2000 counter can help protect your Leader or Characters.",
    "trigger": "Trigger text is an effect you can activate when the card is checked from your Life area.",
    "blocker": "Blocker lets you redirect an attack from your Leader or another target onto the Blocker.",
    "rush": "Rush lets a Character attack on the turn it is played.",
    "banish": "Banish sends Life cards to the trash instead of adding them to hand when damage is dealt.",
    "double attack": "Double Attack deals 2 Life damage when the attack goes through.",
    "leader": "Leaders define your deck colors and life total and often have turn-based effects.",
    "character": "Characters are your main board units. They attack, block, and provide most combat effects.",
    "event": "Events are one-shot cards that usually provide battle tricks, removal, or utility effects.",
    "stage": "Stages stay on board and provide persistent effects or activated abilities.",
}

VARIANT_GUIDE = {
    "alt": "Alt art usually means the card is the same game piece but printed with different artwork.",
    "parallel": "Parallel is a print variant marker often used for alternate finishes or alternate artwork.",
    "sp": "SP usually means a special print variant rather than a different card effect.",
    "promo": "Promo cards come from promotional products or events rather than the main booster numbering flow.",
    "manga": "Manga cards are highly collectible alternate prints with manga-style art treatment.",
    "reprint": "Reprints keep the same card identity but appear in a later release or product.",
    "signed": "Signed variants are special collectible prints and should not be treated as different gameplay cards.",
    "illustration": "Illustration-box style variants are print variants tied to a product or promotional packaging.",
}

GENERAL_GUIDE = {
    "how cards work": [
        "One Piece cards are mainly Leaders, Characters, Events, and Stages.",
        "Cards usually show cost, color, type, trait, attribute, power, counter, and effect text.",
        "Leaders also define deck colors and your starting Life.",
        "DON!! is the resource system that pays costs and can also be attached for extra power or effects.",
        "You usually win by attacking the opposing Leader until all of that Leader's Life is gone.",
    ],
    "printed information": [
        "A printed card can include name, card code, set code, rarity, color, cost, power, counter, attribute, type/trait, block icon, effect text, trigger text, and artist or illustration details when available.",
        "Not every card uses every field. Leaders use Life instead of Counter in many cases.",
    ],
    "variants": [
        "Variants usually change print treatment or art rather than gameplay identity.",
        "Useful variant markers include alt art, SP, promo, manga, parallel, reprint, signed, and illustration-box prints.",
        "Matching logic should separate gameplay identity from print identity.",
    ],
}

GENERAL_GUIDE_TRIGGERS = {
    "how cards work": (
        "how one piece cards work",
        "how cards work",
        "understand the card game better",
        "understand the card game",
    ),
    "printed information": (
        "printed on a one piece card",
        "what information is printed",
        "printed information",
    ),
    "variants": (
        "how do variants work",
        "how variants work",
        "what is a manga card",
        "what is an sp card",
        "difference between a promo card and a set card",
    ),
}

VARIANT_RULES = (
    ("alternate art", "alt art"),
    ("alt art", "alt art"),
    ("alt-art", "alt art"),
    ("a lt", "alt"),
    ("parallel art", "parallel"),
    ("parallel", "parallel"),
    ("special art", "sp"),
    ("sp card", "sp"),
    ("manga rare", "manga"),
    ("comic", "manga"),
    ("comic style", "manga"),
    ("illustrationbox", "illustration box"),
    ("illustration box", "illustration box"),
    ("pirate foil", "pirate foil"),
    ("promo foil", "promo"),
    ("promo", "promo"),
    ("reprint", "reprint"),
    ("signed", "signed"),
)


def normalize_lookup_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.replace("&", " and ")
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_display_text(value: str) -> str:
    text = unescape(value or "").replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", text)


def strip_html(fragment: str) -> str:
    text = unescape(fragment or "").replace("<br>", "\n").replace("<br/>", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\s+\n", "\n", text)
    return clean_display_text(text)


def normalize_set_code(value: str) -> str:
    text = (value or "").strip().upper()
    if not text:
        return ""
    match = SET_CODE_RE.search(text)
    if match:
        return f"{match.group('prefix').upper()}{match.group('series').zfill(2)}"
    if text.startswith("P"):
        return "P"
    return text


def normalize_card_code(value: str) -> dict[str, str]:
    text = (value or "").strip().upper()
    if not text:
        return {"canonical_code": "", "set_code": "", "card_number": "", "print_code": ""}

    match = CARD_CODE_RE.search(text)
    if not match:
        return {"canonical_code": "", "set_code": "", "card_number": "", "print_code": ""}

    prefix = match.group("prefix").upper()
    series = match.group("series").zfill(2 if prefix != "P" else 3)
    number = (match.group("number") or "").upper()
    print_suffix = (match.group("print") or "").lower()

    if prefix == "P":
        canonical_code = f"P-{series.zfill(3)}"
        set_code = "P"
        card_number = series.zfill(3)
    elif number:
        canonical_code = f"{prefix}{series}-{number.zfill(3) if number[:-1].isdigit() else number}"
        set_code = f"{prefix}{series}"
        card_number = number.zfill(3) if number[:-1].isdigit() else number
    else:
        canonical_code = f"{prefix}{series}"
        set_code = canonical_code
        card_number = ""

    print_code = canonical_code
    if print_suffix:
        print_code = f"{canonical_code}_{print_suffix}"

    return {
        "canonical_code": canonical_code,
        "set_code": set_code,
        "card_number": card_number,
        "print_code": print_code,
    }


def normalize_variant_text(value: str) -> dict[str, Any]:
    text = normalize_lookup_text(value)
    if not text:
        return {
            "raw": "",
            "display": "Base",
            "normalized": "base",
            "tokens": [],
            "signals": [],
            "is_base": True,
        }

    for needle, replacement in VARIANT_RULES:
        text = text.replace(needle, replacement)

    text = re.sub(r"\ba\s*l\s*t\b", "alt", text)
    text = re.sub(r"\billustration\s*box\b", "illustration box", text)
    text = re.sub(r"\balt(?:ernate)?\s*0*([1-9])\b", r"alt art \1", text)
    text = re.sub(r"\bp\s*0*([1-9])\b", r"parallel \1", text)
    text = re.sub(r"\b_?p([1-9])\b", r"parallel \1", text)
    text = re.sub(r"\s+", " ", text).strip()

    tokens = []
    signals: set[str] = set()
    for token in text.split():
        if token in {"alt", "altart"}:
            token = "alt"
        tokens.append(token)
        if token in {"alt", "art"} or token.startswith("alt"):
            signals.add("alt")
        elif token == "parallel" or token.startswith("parallel"):
            signals.add("parallel")
        elif token == "sp":
            signals.add("sp")
        elif token == "promo" or token.startswith("promo"):
            signals.add("promo")
        elif token == "manga" or token.startswith("manga"):
            signals.add("manga")
        elif token == "signed":
            signals.add("signed")
        elif token == "reprint":
            signals.add("reprint")
        elif token == "foil":
            signals.add("foil")
        elif token == "illustration":
            signals.add("illustration")

    normalized = text
    display = text.title().replace("Sp", "SP")
    if normalized == "base":
        display = "Base"

    return {
        "raw": value or "",
        "display": display,
        "normalized": normalized or "base",
        "tokens": sorted(set(tokens)),
        "signals": sorted(signals),
        "is_base": normalized in {"", "base"},
    }


def detect_effect_terms(text: str) -> list[str]:
    normalized = normalize_lookup_text(text)
    matches = []
    for needle, description in EFFECT_VOCABULARY.items():
        normalized_needle = normalize_lookup_text(needle)
        if normalized_needle and normalized_needle in normalized:
            matches.append(description)
    return matches


def merge_field(
    current_value: Any,
    current_source: str,
    candidate_value: Any,
    candidate_source: str,
    discrepancies: list[str],
    field_name: str,
) -> tuple[Any, str]:
    if candidate_value in (None, "", [], {}):
        return current_value, current_source
    if current_value in (None, "", [], {}):
        return candidate_value, candidate_source

    current_priority = SOURCE_PRIORITY.index(current_source) if current_source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
    candidate_priority = SOURCE_PRIORITY.index(candidate_source) if candidate_source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)

    if current_value == candidate_value:
        return current_value, current_source
    if candidate_priority < current_priority:
        discrepancies.append(
            f"{field_name}: replaced {current_value!r} from {current_source} with {candidate_value!r} from {candidate_source}"
        )
        return candidate_value, candidate_source

    discrepancies.append(
        f"{field_name}: kept {current_value!r} from {current_source} instead of {candidate_value!r} from {candidate_source}"
    )
    return current_value, current_source


def _coerce_catalog_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def inspect_fallback_catalog_db(db_path: Path | None = None) -> dict[str, Any]:
    path = Path(db_path or DEFAULT_CATALOG_DB_PATH)
    status = {
        "path": str(path),
        "exists": path.is_file(),
        "openable": False,
        "usable": False,
        "cards": 0,
        "variants": 0,
        "sets": 0,
        "error": "",
    }
    if not status["exists"]:
        status["error"] = "Fallback catalog database does not exist yet."
        return status

    try:
        with closing(sqlite3.connect(path)) as conn:
            required_tables = {"cards", "card_variants", "sets"}
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing_tables = sorted(required_tables - tables)
            if missing_tables:
                status["error"] = (
                    "Fallback catalog database is missing tables: "
                    + ", ".join(missing_tables)
                )
                return status

            status["openable"] = True
            status["cards"] = int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
            status["variants"] = int(
                conn.execute("SELECT COUNT(*) FROM card_variants").fetchone()[0]
            )
            status["sets"] = int(conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0])
            status["usable"] = status["cards"] > 0
            if not status["usable"]:
                status["error"] = "Fallback catalog database opened but contains no cards."
    except sqlite3.Error as exc:
        status["error"] = f"{exc.__class__.__name__}: {exc}"

    return status


def initialize_fallback_catalog_db(
    db_path: Path | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    database_path = Path(db_path or DEFAULT_CATALOG_DB_PATH)
    knowledge_path = Path(cache_path or DEFAULT_CACHE_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    if not knowledge_path.is_file():
        status = inspect_fallback_catalog_db(database_path)
        if not status["exists"]:
            status["error"] = (
                f"Cannot initialize fallback catalog database because the local "
                f"knowledge cache is missing: {knowledge_path}"
            )
        return status

    payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    cards = payload.get("cards", {}) or {}
    sets = payload.get("sets", {}) or {}

    schema = """
        PRAGMA foreign_keys = ON;
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
    """

    with closing(sqlite3.connect(database_path)) as conn:
        conn.executescript(schema)
        conn.execute("DELETE FROM card_variants")
        conn.execute("DELETE FROM cards")
        conn.execute("DELETE FROM sets")

        for set_code, entry in sorted(sets.items()):
            conn.execute(
                """
                INSERT INTO sets (
                    set_code,
                    set_name,
                    series_code_display,
                    series_id,
                    sources_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalize_set_code(set_code),
                    clean_display_text(entry.get("set_name") or ""),
                    clean_display_text(entry.get("series_code_display") or ""),
                    clean_display_text(entry.get("series_id") or ""),
                    json.dumps(entry.get("sources") or [], ensure_ascii=False),
                ),
            )

        for canonical_code, entry in sorted(cards.items()):
            normalized = normalize_card_code(canonical_code)
            set_code = normalize_set_code(entry.get("set_code") or normalized["set_code"])
            set_name = clean_display_text(entry.get("set_name") or "")
            if set_code and not conn.execute(
                "SELECT 1 FROM sets WHERE set_code = ?",
                (set_code,),
            ).fetchone():
                conn.execute(
                    """
                    INSERT INTO sets (set_code, set_name, series_code_display, series_id, sources_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        set_code,
                        set_name,
                        set_code,
                        "",
                        json.dumps([], ensure_ascii=False),
                    ),
                )

            cursor = conn.execute(
                """
                INSERT INTO cards (
                    canonical_code,
                    set_code,
                    card_number,
                    set_name,
                    card_name,
                    rarity,
                    color,
                    card_type,
                    cost,
                    power,
                    counter,
                    attribute,
                    traits,
                    life,
                    block_icon,
                    effect_text,
                    trigger_text,
                    aliases_json,
                    sources_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["canonical_code"] or clean_display_text(canonical_code),
                    set_code,
                    clean_display_text(normalized["card_number"]),
                    set_name,
                    clean_display_text(entry.get("card_name") or canonical_code),
                    clean_display_text(entry.get("rarity") or ""),
                    clean_display_text(entry.get("color") or ""),
                    clean_display_text(entry.get("card_type") or ""),
                    _coerce_catalog_int(entry.get("cost")),
                    clean_display_text(entry.get("power") or ""),
                    clean_display_text(entry.get("counter") or ""),
                    clean_display_text(entry.get("attribute") or ""),
                    clean_display_text(entry.get("traits") or ""),
                    clean_display_text(entry.get("life") or ""),
                    clean_display_text(entry.get("block_icon") or ""),
                    clean_display_text(entry.get("effect_text") or ""),
                    clean_display_text(entry.get("trigger_text") or ""),
                    json.dumps(sorted(set(entry.get("aliases") or [])), ensure_ascii=False),
                    json.dumps(entry.get("sources") or [], ensure_ascii=False),
                ),
            )
            card_id = int(cursor.lastrowid)

            for print_entry in entry.get("prints") or []:
                variant_label = clean_display_text(
                    print_entry.get("variant_label")
                    or print_entry.get("variant_key")
                    or "Base"
                )
                variant_info = normalize_variant_text(variant_label)
                signals = {signal.lower() for signal in (print_entry.get("signals") or [])}
                conn.execute(
                    """
                    INSERT OR IGNORE INTO card_variants (
                        card_id,
                        variant_key,
                        variant_label,
                        print_id,
                        release_set_code,
                        release_set_name,
                        image_path,
                        image_url,
                        source,
                        is_base,
                        is_alt,
                        is_sp,
                        has_variant_evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        variant_info["normalized"],
                        variant_info["display"],
                        clean_display_text(
                            print_entry.get("print_id")
                            or normalized["print_code"]
                            or normalized["canonical_code"]
                        ),
                        normalize_set_code(
                            print_entry.get("release_set_code")
                            or set_code
                        ),
                        clean_display_text(
                            print_entry.get("release_set_name") or set_name
                        ),
                        clean_display_text(print_entry.get("image_path") or ""),
                        clean_display_text(print_entry.get("image_url") or ""),
                        clean_display_text(print_entry.get("source") or "local-catalog"),
                        int(bool(variant_info["is_base"])),
                        int(bool("alt" in signals or "alt" in variant_info["signals"])),
                        int(bool("sp" in signals or "sp" in variant_info["signals"])),
                        int(
                            bool(
                                not variant_info["is_base"]
                                or signals
                                or clean_display_text(print_entry.get("image_path") or "")
                                or clean_display_text(print_entry.get("image_url") or "")
                            )
                        ),
                    ),
                )
        conn.commit()

    return inspect_fallback_catalog_db(database_path)


def load_local_catalog_snapshot(db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = Path(db_path or DEFAULT_CATALOG_DB_PATH)
    if not path.is_file():
        initialize_fallback_catalog_db(db_path=path)
    status = inspect_fallback_catalog_db(path)
    if not status["usable"]:
        return {}

    query = """
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
            v.variant_key,
            v.variant_label,
            v.is_base,
            v.is_alt,
            v.is_sp,
            v.has_variant_evidence,
            v.image_path
        FROM cards c
        LEFT JOIN card_variants v ON v.card_id = c.id
        ORDER BY c.canonical_code, v.variant_key
    """
    by_code: dict[str, dict[str, Any]] = {}
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query):
            code = (row["canonical_code"] or "").strip().upper()
            if not code:
                continue
            entry = by_code.setdefault(
                code,
                {
                    "canonical_code": code,
                    "set_code": (row["set_code"] or "").strip().upper(),
                    "card_number": (row["card_number"] or "").strip().upper(),
                    "set_name": clean_display_text(row["set_name"] or ""),
                    "card_name": clean_display_text(row["card_name"] or ""),
                    "rarity": clean_display_text(row["rarity"] or ""),
                    "color": clean_display_text(row["color"] or ""),
                    "card_type": clean_display_text(row["card_type"] or ""),
                    "cost": row["cost"],
                    "power": clean_display_text(row["power"] or ""),
                    "counter": clean_display_text(row["counter"] or ""),
                    "attribute": clean_display_text(row["attribute"] or ""),
                    "traits": clean_display_text(row["traits"] or ""),
                    "life": clean_display_text(row["life"] or ""),
                    "effect_text": clean_display_text(row["effect_text"] or ""),
                    "trigger_text": clean_display_text(row["trigger_text"] or ""),
                    "variants": [],
                },
            )
            variant_key = clean_display_text(row["variant_key"] or "")
            if not variant_key:
                continue
            variant_info = normalize_variant_text(row["variant_label"] or row["variant_key"] or "")
            entry["variants"].append(
                {
                    "variant_key": variant_info["normalized"],
                    "variant_label": variant_info["display"],
                    "signals": variant_info["signals"]
                    or (["alt"] if row["is_alt"] else [])
                    or (["sp"] if row["is_sp"] else []),
                    "is_base": bool(row["is_base"]),
                    "image_path": clean_display_text(row["image_path"] or ""),
                    "source": "local-catalog",
                }
            )
    return by_code


@dataclass
class CardMatch:
    canonical_code: str
    card_name: str
    set_code: str
    set_name: str
    confidence: str
    reason: str
    matched_on: str
    variant_match: str
    assumption: str = ""


class OnePieceKnowledgeBase:
    def __init__(self, payload: dict[str, Any], source_path: Path | None = None):
        self.payload = payload or {}
        self.source_path = Path(source_path) if source_path else None
        self.cards: dict[str, dict[str, Any]] = payload.get("cards", {}) or {}
        self.sets: dict[str, dict[str, Any]] = payload.get("sets", {}) or {}
        self.meta: dict[str, Any] = payload.get("_meta", {}) or {}
        self.name_index: dict[str, list[str]] = {}
        self.alias_index: dict[str, list[str]] = {}
        self.set_index: dict[str, list[str]] = {}
        self._enrich_loaded_cards()
        self._build_indexes()

    @classmethod
    def load(cls, path: Path | None = None) -> "OnePieceKnowledgeBase":
        knowledge_path = Path(path or DEFAULT_CACHE_PATH)
        if knowledge_path.is_file():
            initialize_fallback_catalog_db(cache_path=knowledge_path)
            payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
            return cls(payload, source_path=knowledge_path)

        local_snapshot = load_local_catalog_snapshot()
        payload = {
            "_meta": {
                "generated_at": "",
                "knowledge_version": 0,
                "source_priority": list(SOURCE_PRIORITY),
                "notes": [
                    "Knowledge cache not found. Falling back to local catalog snapshot only."
                ],
            },
            "cards": {},
            "sets": {},
        }
        for code, entry in local_snapshot.items():
            payload["cards"][code] = {
                "canonical_code": code,
                "card_name": entry.get("card_name") or code,
                "set_code": entry.get("set_code") or "",
                "set_name": entry.get("set_name") or "",
                "rarity": entry.get("rarity") or "",
                "color": entry.get("color") or "",
                "card_type": entry.get("card_type") or "",
                "cost": entry.get("cost"),
                "power": entry.get("power") or "",
                "counter": entry.get("counter") or "",
                "attribute": entry.get("attribute") or "",
                "traits": entry.get("traits") or "",
                "block_icon": "",
                "effect_text": entry.get("effect_text") or "",
                "trigger_text": entry.get("trigger_text") or "",
                "life": entry.get("life") or "",
                "artist_credit": "",
                "illustration_type": "",
                "aliases": sorted({entry.get("card_name") or "", code}),
                "prints": entry.get("variants") or [],
                "sources": ["local-catalog"],
                "field_sources": {},
                "discrepancies": [],
            }
        return cls(payload, source_path=knowledge_path)

    def _build_indexes(self) -> None:
        for code, card in self.cards.items():
            aliases = set(card.get("aliases") or [])
            aliases.add(card.get("card_name") or "")
            aliases.add(code)
            for alias in aliases:
                normalized = normalize_lookup_text(alias)
                if not normalized:
                    continue
                self.alias_index.setdefault(normalized, []).append(code)
                if alias == card.get("card_name"):
                    self.name_index.setdefault(normalized, []).append(code)

            release_codes = self.card_release_set_codes(card)
            for set_code in release_codes:
                if set_code:
                    self.set_index.setdefault(set_code, []).append(code)

    def stats(self) -> dict[str, Any]:
        return {
            "cards": len(self.cards),
            "sets": len(self.sets),
            "generated_at": self.meta.get("generated_at", ""),
            "sources": self.meta.get("sources_used", []),
        }

    def _enrich_loaded_cards(self) -> None:
        for card in self.cards.values():
            card.setdefault("artist_credit", "")
            card.setdefault("illustration_type", "")
            card.setdefault("field_sources", {})
            prints = card.get("prints") or []
            for print_entry in prints:
                variant_signals = set(print_entry.get("signals") or [])
                illustration_type = clean_display_text(print_entry.get("illustration_type") or "")
                if not illustration_type:
                    illustration_type = self.derive_print_illustration_type(print_entry)
                    if illustration_type:
                        print_entry["illustration_type"] = illustration_type
                artist_credit = clean_display_text(print_entry.get("artist_credit") or "")
                if artist_credit:
                    print_entry["artist_credit"] = artist_credit
                if variant_signals and not print_entry.get("print_treatment"):
                    print_entry["print_treatment"] = clean_display_text(print_entry.get("variant_label") or illustration_type or "Variant")

            if not clean_display_text(card.get("illustration_type") or ""):
                derived_type = self.derive_card_illustration_type(card)
                if derived_type:
                    card["illustration_type"] = derived_type
                    card["field_sources"].setdefault("illustration_type", "derived-print-metadata")

    def derive_print_illustration_type(self, print_entry: dict[str, Any]) -> str:
        signals = {signal.lower() for signal in (print_entry.get("signals") or [])}
        label = normalize_lookup_text(print_entry.get("variant_label") or "")
        if "manga" in signals or "manga" in label:
            return "Manga art"
        if "sp" in signals or label == "sp":
            return "SP art"
        if "signed" in signals or "signed" in label:
            return "Signed art"
        if "illustration" in signals or "illustration box" in label:
            return "Illustration box print"
        if "alt" in signals or "alt art" in label or label == "alt":
            return "Alt art"
        if "parallel" in signals or "foil" in signals or "parallel" in label or "pirate foil" in label:
            return "Parallel / foil print"
        if "reprint" in signals or "reprint" in label:
            return "Reprint print"
        if "promo" in signals:
            return "Promo print"
        return "Base artwork"

    def derive_card_illustration_type(self, card: dict[str, Any]) -> str:
        styles = self.list_known_illustration_types(card)
        if not styles:
            return ""
        return styles[0]

    def list_known_illustration_types(self, card: dict[str, Any]) -> list[str]:
        styles = []
        for print_entry in card.get("prints") or []:
            style = clean_display_text(
                print_entry.get("illustration_type") or self.derive_print_illustration_type(print_entry)
            )
            if style and style not in styles:
                styles.append(style)
        return styles

    def card_summary_line(self, card: dict[str, Any]) -> str:
        parts = [
            card.get("canonical_code") or "Unknown code",
            card.get("card_name") or "Unknown card",
        ]
        if card.get("color"):
            parts.append(card["color"])
        if card.get("card_type"):
            parts.append(card["card_type"])
        if card.get("set_name") or card.get("set_code"):
            parts.append(card.get("set_name") or card.get("set_code"))
        return " | ".join(parts)

    def split_query_into_subquestions(self, text: str) -> list[str]:
        raw = clean_display_text(text or "")
        if not raw:
            return []
        normalized = raw.replace("?", ". ").replace(";", ". ")
        fragments = [fragment.strip(" ,.") for fragment in re.split(r"\.\s+|\n+", normalized) if fragment.strip(" ,.")] 
        clauses: list[str] = []
        for fragment in fragments:
            parts = re.split(
                r",\s+(?=(?:who|what|which|how|tell|explain|compare)\b)|\s+and\s+(?=(?:who|what|which|how|tell|explain|compare)\b)",
                fragment,
                flags=re.I,
            )
            for part in parts:
                cleaned = clean_display_text(part).strip(" ,.")
                if cleaned and cleaned not in clauses:
                    clauses.append(cleaned)
        return clauses

    def detect_subquestion_intents(
        self,
        request_text: str,
        references: dict[str, Any],
        matches: list[CardMatch],
        gameplay_topics: list[tuple[str, str]],
    ) -> list[dict[str, str]]:
        normalized = normalize_lookup_text(request_text)
        intents: list[dict[str, str]] = []

        def add_intent(key: str, label: str, target: str = "") -> None:
            entry = {"key": key, "label": label, "target": target}
            if entry not in intents:
                intents.append(entry)

        if matches and any(phrase in normalized for phrase in ("what is", "explain", "tell me about")):
            add_intent("card_identity", f"Card identity for {matches[0].canonical_code}", matches[0].canonical_code)
        if references["set_codes"] and any(phrase in normalized for phrase in ("what is", "what cards are in", "what cards exist in")):
            add_intent("set_identity", f"Set overview for {references['set_codes'][0]}", references["set_codes"][0])
        if "what cards are in" in normalized or "what cards exist in" in normalized:
            if references["set_codes"]:
                add_intent("set_contents", f"Set contents sample for {references['set_codes'][0]}", references["set_codes"][0])
        if any(term in normalized for term in ("who drew", "artist", "illustrator", "illustrated")):
            target = matches[0].canonical_code if matches else ""
            add_intent("artist_credit", f"Artist credit{f' for {target}' if target else ''}", target)
        if "illustration type" in normalized or "what illustration type" in normalized:
            target = matches[0].canonical_code if matches else ""
            add_intent("illustration_type", f"Illustration type{f' for {target}' if target else ''}", target)
        if any(term in normalized for term in ("variant", "variants", "alt art", "promo card", "manga card", "reprint", "sp card")):
            target = matches[0].canonical_code if matches else (references["set_codes"][0] if references["set_codes"] else "")
            add_intent("variants", f"Variant coverage{f' for {target}' if target else ''}", target)
        if "attribute" in normalized:
            add_intent("attribute", f"Attribute{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "card type" in normalized or "type of card" in normalized:
            add_intent("card_type", f"Card type{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "rarity" in normalized:
            add_intent("rarity", f"Rarity{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "color" in normalized:
            add_intent("color", f"Color{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "cost" in normalized:
            add_intent("cost", f"Cost{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "power" in normalized:
            add_intent("power", f"Power{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "counter" in normalized:
            add_intent("counter", f"Counter meaning or value{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "life" in normalized:
            add_intent("life", f"Life value{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "effect" in normalized:
            add_intent("effect_text", f"Effect analysis{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "trigger" in normalized:
            add_intent("trigger_text", f"Trigger explanation{f' for {matches[0].canonical_code}' if matches else ''}", matches[0].canonical_code if matches else "")
        if "compare" in normalized or "difference between" in normalized or "different from" in normalized:
            add_intent("comparison", "Comparison request", matches[0].canonical_code if matches else "")
        if "how should miru detect" in normalized or "how would miru detect" in normalized or ("miru" in normalized and "detect" in normalized):
            add_intent("miru_detection", "Miru detection guidance", "")
        if any(
            term in normalized
            for term in (
                "represented in the catalog",
                "what card information should miru store",
                "what catalog fields should exist",
                "catalog fields should exist",
            )
        ):
            add_intent("catalog_representation", "Catalog representation guidance", "")
        if any(
            term in normalized
            for term in (
                "what does miru still need to learn",
                "what is still missing",
                "which fields are known",
                "which fields are missing",
                "missing data",
                "missing field",
                "cover op",
                "cover eb",
                "cover st",
            )
        ):
            add_intent("knowledge_gap", "Knowledge gap summary", "")
        if "what should i ask next" in normalized or "next question" in normalized or "ask next" in normalized:
            add_intent("next_questions", "Follow-up suggestions", "")

        if gameplay_topics:
            for topic, _ in gameplay_topics:
                add_intent("gameplay_topic", f"Gameplay explanation: {topic.upper() if topic == 'don!!' else topic.title()}", topic)
        elif references["effect_terms"]:
            for term in references["effect_terms"]:
                add_intent("gameplay_topic", f"Gameplay explanation: {term}", term)

        if not intents:
            if matches:
                add_intent("card_identity", f"Card identity for {matches[0].canonical_code}", matches[0].canonical_code)
            elif references["set_codes"]:
                add_intent("set_identity", f"Set overview for {references['set_codes'][0]}", references["set_codes"][0])
            else:
                add_intent("general_rules", "General OPTCG explanation", "")
        return intents

    def extract_references(self, text: str) -> dict[str, Any]:
        text = text or ""
        card_codes: list[str] = []
        for match in CARD_CODE_RE.finditer(text):
            if not match.group("number") and match.group("prefix").upper() != "P":
                continue
            normalized = normalize_card_code(match.group(0)).get("canonical_code")
            if normalized and normalized not in card_codes:
                card_codes.append(normalized)

        set_codes: list[str] = []
        for match in SET_CODE_RE.finditer(text):
            normalized = normalize_set_code(match.group(0))
            if normalized and normalized not in set_codes:
                set_codes.append(normalized)

        variant_info = normalize_variant_text(text)
        has_real_variant_request = not variant_info["is_base"]
        effect_terms = detect_effect_terms(text)

        return {
            "card_codes": card_codes,
            "set_codes": set_codes,
            "variant": variant_info,
            "effect_terms": effect_terms,
        }

    def _score_card_match(
        self,
        card: dict[str, Any],
        query_text: str,
        set_refs: set[str],
        variant_signals: set[str],
    ) -> tuple[float, str]:
        normalized_query = normalize_lookup_text(query_text)
        if not normalized_query:
            return 0.0, ""

        code = card.get("canonical_code", "")
        if normalized_query == normalize_lookup_text(code):
            return 1000.0, "exact card code"

        aliases = set(card.get("aliases") or []) | {card.get("card_name") or "", code}
        release_set_codes = self.card_release_set_codes(card)
        best_score = 0.0
        best_reason = ""

        for alias in aliases:
            normalized_alias = normalize_lookup_text(alias)
            if not normalized_alias:
                continue
            if normalized_query == normalized_alias:
                score = 900.0
                reason = f"exact name match: {alias}"
            elif normalized_alias in normalized_query and len(normalized_alias) >= 4:
                score = 760.0 + min(len(normalized_alias), 40)
                reason = f"name mention: {alias}"
            else:
                ratio = SequenceMatcher(None, normalized_query, normalized_alias).ratio()
                if ratio < 0.72:
                    continue
                score = ratio * 400.0
                reason = f"fuzzy name match: {alias}"

            if set_refs and ((card.get("set_code") in set_refs) or (release_set_codes & set_refs)):
                score += 90.0
                reason += " + set"

            if variant_signals and self.card_supports_variant(card, variant_signals):
                score += 35.0
                reason += " + variant"

            if score > best_score:
                best_score = score
                best_reason = reason

        return best_score, best_reason

    def card_supports_variant(self, card: dict[str, Any], variant_signals: set[str]) -> bool:
        if not variant_signals:
            return True
        for print_entry in card.get("prints") or []:
            signals = set(print_entry.get("signals") or [])
            if signals & variant_signals:
                return True
        return False

    def card_release_set_codes(self, card: dict[str, Any]) -> set[str]:
        codes = set()
        if card.get("set_code"):
            codes.add(card["set_code"])
        for print_entry in card.get("prints") or []:
            release_code = print_entry.get("release_set_code") or ""
            if release_code:
                codes.add(release_code)
            for alias_code in print_entry.get("release_set_codes") or []:
                if alias_code:
                    codes.add(alias_code)
        return codes

    def lookup_cards(self, query_text: str, limit: int = 5) -> list[CardMatch]:
        references = self.extract_references(query_text)
        set_refs = set(references["set_codes"])
        variant_signals = set(references["variant"]["signals"])
        matches: list[CardMatch] = []

        for code in references["card_codes"]:
            card = self.cards.get(code)
            if not card:
                continue
            matches.append(
                CardMatch(
                    canonical_code=code,
                    card_name=card.get("card_name") or code,
                    set_code=card.get("set_code") or "",
                    set_name=card.get("set_name") or "",
                    confidence="exact",
                    reason="explicit card code",
                    matched_on=code,
                    variant_match=", ".join(variant_signals) if variant_signals else "",
                )
            )

        if matches:
            return matches[:limit]

        query_terms = [
            term
            for term in normalize_lookup_text(query_text).split()
            if term not in CARD_QUERY_STOP_WORDS and len(term) >= 3
        ]
        candidate_query = " ".join(query_terms) or query_text

        scored: list[tuple[float, str, dict[str, Any]]] = []
        for card in self.cards.values():
            score, reason = self._score_card_match(card, candidate_query, set_refs, variant_signals)
            if score <= 0:
                continue
            scored.append((score, reason, card))

        if set_refs:
            scored_in_scope = [
                item for item in scored if self.card_release_set_codes(item[2]) & set_refs
            ]
            if scored_in_scope:
                scored = scored_in_scope
            else:
                return []

        scored.sort(key=lambda item: (-item[0], item[2].get("canonical_code", "")))
        top = scored[:limit]
        if not top:
            return []

        for index, (score, reason, card) in enumerate(top):
            assumption = ""
            if index == 0 and len(top) > 1 and abs(top[0][0] - top[1][0]) < 45:
                assumption = "Multiple cards look plausible. Treat this as a best guess, not a proven identity."
            matches.append(
                CardMatch(
                    canonical_code=card.get("canonical_code") or "",
                    card_name=card.get("card_name") or "",
                    set_code=card.get("set_code") or "",
                    set_name=card.get("set_name") or "",
                    confidence="high" if score >= 760 else "medium",
                    reason=reason,
                    matched_on=candidate_query,
                    variant_match=", ".join(variant_signals) if variant_signals else "",
                    assumption=assumption,
                )
            )
        return matches

    def summarize_matches(self, matches: list[CardMatch]) -> list[str]:
        lines = []
        for match in matches:
            line = (
                f"- {match.canonical_code} | {match.card_name} | Set: {match.set_name or match.set_code}"
                f" | Confidence: {match.confidence} | Match: {match.reason}"
            )
            if match.variant_match:
                line += f" | Variant hint: {match.variant_match}"
            if match.assumption:
                line += f" | Assumption: {match.assumption}"
            lines.append(line)
        return lines

    def build_prompt_context(self, request_text: str, mode: str) -> dict[str, Any]:
        references = self.extract_references(request_text)
        matches = self.lookup_cards(request_text, limit=5)
        assumptions = []
        if not matches and references["card_codes"]:
            assumptions.append(
                "A card code was mentioned, but it was not found in the local knowledge cache."
            )
        if references["set_codes"] and not matches:
            assumptions.append(
                "No specific card identity was resolved inside the detected set scope. Treat the set reference as confirmed, but keep card-level details explicit or unknown."
            )
        assumptions.extend(match.assumption for match in matches if match.assumption)

        detected_sets = []
        for set_code in references["set_codes"]:
            set_entry = self.sets.get(set_code) or {}
            set_name = set_entry.get("set_name") or ""
            if set_name:
                detected_sets.append(f"{set_code} = {set_name}")
            else:
                detected_sets.append(set_code)

        lines = [
            "One Piece TCG context",
            "- Use this context as trusted card data. If a requested detail is not listed here, treat it as unknown instead of inventing it.",
        ]
        if detected_sets:
            lines.append(f"- Detected set references: {', '.join(detected_sets)}")
        if references["variant"]["signals"]:
            lines.append(
                "- Detected variant language: "
                + ", ".join(references["variant"]["signals"])
            )
        if references["effect_terms"]:
            lines.append(
                "- Detected gameplay terms: " + ", ".join(references["effect_terms"])
            )
        if matches:
            lines.append("- Matched cards:")
            lines.extend(self.summarize_matches(matches))
        if assumptions:
            lines.append("- Assumptions / uncertainty:")
            for assumption in assumptions:
                lines.append(f"  - {assumption}")
        if not matches and not detected_sets and not references["effect_terms"]:
            lines.append("- No specific card or set reference was resolved from the request.")

        if mode == "codex prompt":
            lines.append(
                "- In Codex Prompt mode, prefer a paste-ready implementation prompt over a generic explanation."
            )

        return {
            "matches": matches,
            "references": references,
            "assumptions": assumptions,
            "text": "\n".join(lines),
        }

    def build_review_guidance(self) -> str:
        return "\n".join(
            [
                "One Piece TCG-specific correctness checks",
                "- Card codes should normalize cleanly: OP09-001, EB03-012, ST21-017, P-093, PRB01-001.",
                "- Variant language should normalize explicitly instead of guessing: alt art, sp, promo, parallel, manga, reprint, signed, pirate foil, illustration box.",
                "- Official card facts should win over heuristic or unofficial guesses when sources disagree.",
                "- If a card reference is ambiguous, the code should preserve uncertainty instead of silently picking a wrong card.",
                "- Set names, set codes, and card numbers should stay aligned.",
            ]
        )

    def find_gameplay_topics(self, text: str) -> list[tuple[str, str]]:
        normalized = normalize_lookup_text(text)
        topics = []
        for key, description in GAMEPLAY_GUIDE.items():
            normalized_key = normalize_lookup_text(key)
            if normalized_key and normalized_key in normalized:
                topics.append((key, description))
        if "how do variants work" in normalized or "variants work" in normalized:
            topics.append(("variants", "Variants usually change print treatment or artwork rather than gameplay identity."))
        return topics

    def set_summary(self, set_code: str, limit: int = 8) -> dict[str, Any]:
        normalized = normalize_set_code(set_code)
        set_entry = self.sets.get(normalized) or {}
        card_codes = sorted(
            set(self.set_index.get(normalized) or []),
            key=lambda code: (self.cards.get(code, {}).get("card_name", ""), code),
        )
        sample_cards = []
        for code in card_codes[:limit]:
            card = self.cards.get(code) or {}
            sample_cards.append(f"{code} {card.get('card_name') or code}")
        return {
            "set_code": normalized,
            "set_name": set_entry.get("set_name") or "",
            "card_count": len(card_codes),
            "sample_cards": sample_cards,
        }

    def summarize_variant_support(self, card: dict[str, Any]) -> dict[str, Any]:
        labels = []
        signals: set[str] = set()
        release_sets: set[str] = set()
        for print_entry in card.get("prints") or []:
            label = clean_display_text(print_entry.get("variant_label") or "")
            if label and label not in labels:
                labels.append(label)
            for signal in print_entry.get("signals") or []:
                if signal:
                    signals.add(signal)
            release_set_name = clean_display_text(print_entry.get("release_set_name") or "")
            release_set_code = clean_display_text(print_entry.get("release_set_code") or "")
            if release_set_name:
                release_sets.add(release_set_name)
            elif release_set_code:
                release_sets.add(release_set_code)
        return {
            "labels": labels,
            "signals": sorted(signals),
            "release_sets": sorted(release_sets),
        }

    def build_effect_analysis(self, card: dict[str, Any]) -> list[str]:
        lines = []
        effect_text = clean_display_text(card.get("effect_text") or "")
        trigger_text = clean_display_text(card.get("trigger_text") or "")
        detected = detect_effect_terms(" ".join(part for part in (effect_text, trigger_text) if part))
        if detected:
            lines.append("Detected mechanics: " + ", ".join(detected))
        if effect_text:
            lines.append("Effect text: " + effect_text)
        if trigger_text:
            lines.append("Trigger text: " + trigger_text)
        if not effect_text and not trigger_text:
            lines.append("No effect or trigger text was available in the current cache.")
        return lines

    def build_card_metadata(self, card: dict[str, Any]) -> list[str]:
        variant_summary = self.summarize_variant_support(card)
        illustration_styles = self.list_known_illustration_types(card)
        artist_credit = clean_display_text(card.get("artist_credit") or "")
        illustration_type = clean_display_text(card.get("illustration_type") or "")
        lines = [
            f"{card.get('canonical_code') or ''} | {card.get('card_name') or ''}",
            f"Set: {card.get('set_name') or card.get('set_code') or 'Unknown'}",
            f"Rarity: {card.get('rarity') or 'Unknown'}",
            f"Color: {card.get('color') or 'Unknown'}",
            f"Card type: {card.get('card_type') or 'Unknown'}",
            f"Cost: {card.get('cost') if card.get('cost') not in (None, '') else 'Unknown'}",
            f"Power: {card.get('power') or 'Unknown'}",
            f"Counter: {card.get('counter') or 'Unknown'}",
            f"Attribute: {card.get('attribute') or 'Unknown'}",
            f"Type / trait: {card.get('traits') or 'Unknown'}",
            f"Life: {card.get('life') or 'Unknown'}",
            f"Block icon: {card.get('block_icon') or 'Unknown'}",
            f"Known variant types: {', '.join(variant_summary['signals']) if variant_summary['signals'] else 'Base-only in current cache'}",
            f"Known print labels: {', '.join(variant_summary['labels']) if variant_summary['labels'] else 'Base'}",
            f"Known illustration / print styles: {', '.join(illustration_styles) if illustration_styles else 'Base artwork in current cache'}",
            f"Release sets / prints: {', '.join(variant_summary['release_sets']) if variant_summary['release_sets'] else (card.get('set_name') or card.get('set_code') or 'Unknown')}",
            f"Artist credit: {artist_credit or 'not available in the current local cache.'}",
            f"Illustration type: {illustration_type or 'not available in the current local cache.'}",
        ]
        return lines

    def build_missing_field_notes(
        self,
        card: dict[str, Any],
        set_summaries: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        set_summaries = set_summaries or []
        if card:
            missing = []
            field_labels = (
                ("rarity", "Rarity"),
                ("color", "Color"),
                ("card_type", "Card type"),
                ("cost", "Cost"),
                ("power", "Power"),
                ("counter", "Counter"),
                ("attribute", "Attribute"),
                ("traits", "Type / trait"),
                ("life", "Life"),
                ("block_icon", "Block icon"),
                ("effect_text", "Effect text"),
                ("trigger_text", "Trigger text"),
                ("artist_credit", "Artist credit"),
                ("illustration_type", "Illustration type"),
            )
            for key, label in field_labels:
                value = card.get(key)
                if value in (None, "", []):
                    missing.append(f"{label}: missing or not yet confirmed in the current local cache.")

            variant_summary = self.summarize_variant_support(card)
            if not variant_summary["signals"]:
                missing.append("Variant coverage: only the base print is confirmed in the current cache.")

            return missing or ["No major field gap was obvious for the resolved card."]

        if set_summaries:
            summary = set_summaries[0]
            return [
                f"No single card was resolved, so field completeness is only confirmed at the {summary['set_code']} set level right now.",
                "Artist, illustration, and print-level gaps still need card-by-card confirmation inside this set.",
            ]

        return [
            "No specific card or set was resolved strongly enough to measure field completeness directly.",
            "Ask about a card code or set code to surface concrete missing-field notes.",
        ]

    def answer_subquestion(
        self,
        intent: dict[str, str],
        request_text: str,
        references: dict[str, Any],
        matches: list[CardMatch],
        sections: dict[str, list[str]],
    ) -> str:
        key = intent["key"]
        top_card = self.cards.get(matches[0].canonical_code) if matches else {}
        top_set_code = references["set_codes"][0] if references["set_codes"] else ""
        top_set_summary = self.set_summary(top_set_code) if top_set_code else {}
        gameplay_lookup = {topic: description for topic, description in self.find_gameplay_topics(request_text)}

        if key == "card_identity" and top_card:
            return f"Card identity: {self.card_summary_line(top_card)}."
        if key == "set_identity" and top_set_summary:
            return (
                f"Set identity: {top_set_summary['set_code']} is {top_set_summary['set_name'] or 'an unresolved set label'}"
                f" with {top_set_summary['card_count']} cached cards."
            )
        if key == "set_contents" and top_set_summary:
            sample_cards = ", ".join(top_set_summary["sample_cards"][:6]) or "No sample cards were available."
            return f"Set contents sample: {sample_cards}."
        if key == "artist_credit":
            if top_card and clean_display_text(top_card.get("artist_credit") or ""):
                return f"Artist credit: {clean_display_text(top_card.get('artist_credit') or '')}."
            return "Artist credit: not available in the current local cache or current trusted print metadata."
        if key == "illustration_type":
            if top_card:
                illustration_type = clean_display_text(top_card.get("illustration_type") or "")
                styles = ", ".join(self.list_known_illustration_types(top_card))
                if illustration_type:
                    return f"Illustration type: {illustration_type}. Known print styles: {styles or illustration_type}."
            return "Illustration type: not available as a direct official field in the current local cache."
        if key == "variants":
            if top_card:
                variant_summary = self.summarize_variant_support(top_card)
                labels = ", ".join(variant_summary["labels"]) or "Base"
                styles = ", ".join(self.list_known_illustration_types(top_card)) or "Base artwork"
                return f"Variants: known prints include {labels}. Known illustration / print styles: {styles}."
            if top_set_summary:
                return f"Variants: use the set context for {top_set_summary['set_code']} and keep alt, parallel, SP, manga, promo, and reprint markers explicit."
            return "Variants: keep alt art, SP, promo, manga, reprint, and other print markers explicit instead of collapsing them into the base card."
        if key == "attribute" and top_card:
            return f"Attribute: {top_card.get('attribute') or 'Unknown'}."
        if key == "card_type" and top_card:
            return f"Card type: {top_card.get('card_type') or 'Unknown'}."
        if key == "rarity" and top_card:
            return f"Rarity: {top_card.get('rarity') or 'Unknown'}."
        if key == "color" and top_card:
            return f"Color: {top_card.get('color') or 'Unknown'}."
        if key == "cost" and top_card:
            return f"Cost: {top_card.get('cost') if top_card.get('cost') not in (None, '') else 'Unknown'}."
        if key == "power" and top_card:
            return f"Power: {top_card.get('power') or 'Unknown'}."
        if key == "counter" and top_card:
            return f"Counter: {top_card.get('counter') or 'Unknown'}."
        if key == "life" and top_card:
            return f"Life: {top_card.get('life') or 'Unknown'}."
        if key == "effect_text" and top_card:
            return f"Effect analysis: {' '.join(self.build_effect_analysis(top_card))}"
        if key == "trigger_text" and top_card:
            trigger_text = clean_display_text(top_card.get("trigger_text") or "")
            return f"Trigger: {trigger_text or 'No trigger text was available in the current cache.'}"
        if key == "comparison":
            if top_card and top_card.get("set_code") == "P":
                return "Comparison: this resolved as a promo card family, so Miru should treat its P-numbering and product context separately from normal booster-set numbering."
            if top_card:
                return "Comparison: use explicit card codes, set codes, and variant hints before comparing this card to promos or other same-name prints."
            return "Comparison: promo cards usually use promotional numbering and product context, while set cards belong to numbered booster or deck releases."
        if key == "catalog_representation":
            return (
                "Catalog representation: keep one canonical card record with card code, card name, set code, set name, rarity, color, "
                "card type, cost, power, counter, attribute, trait, life, block icon, effect text, trigger text, artist credit, "
                "illustration type, known variant labels, source metadata, and a separate field that tracks whether a local image exists."
            )
        if key == "knowledge_gap":
            missing_notes = self.build_missing_field_notes(top_card, [top_set_summary] if top_set_summary else [])
            return "Knowledge gap: " + " ".join(missing_notes[:3])
        if key == "miru_detection":
            return "Miru detection: prefer explicit card codes first, then set scope, then normalized variant tokens and filename hints before falling back to name-only matching."
        if key == "next_questions":
            return "Follow-up request: use the suggestions below to keep drilling into card identity, variants, effect analysis, or Miru detection logic."
        if key == "gameplay_topic":
            topic_key = intent.get("target") or ""
            explanation = gameplay_lookup.get(topic_key) or next(
                (note.removeprefix("- ").strip() for note in sections.get("Knowledge notes", []) if topic_key.lower() in note.lower()),
                "",
            )
            return f"{intent['label']}: {explanation or 'No mechanic explanation was resolved beyond the current glossary notes.'}"
        if key == "general_rules":
            note_items = [item.removeprefix("- ").strip() for item in sections.get("Knowledge notes", [])[:5]]
            notes = " ".join(note_items)
            return (
                "General OPTCG explanation: "
                + (
                    notes
                    or (
                        "Cards are mainly Leaders, Characters, Events, and Stages. "
                        "They use DON!! as the main resource system, and most cards show core printed facts like cost, power, counter, "
                        "effect text, trigger text, and card type."
                    )
                )
            )
        return ""

    def build_follow_up_suggestions(
        self,
        request_text: str,
        references: dict[str, Any],
        matches: list[CardMatch],
        gameplay_topics: list[tuple[str, str]],
    ) -> list[str]:
        suggestions: list[str] = []
        if matches:
            code = matches[0].canonical_code
            name = matches[0].card_name
            suggestions.append(f"Ask what variants exist for {code} {name}.")
            suggestions.append(f"Ask for effect analysis or trigger text for {code}.")
            suggestions.append(f"Ask how Miru should identify {code} from filenames or loose names.")
        if references["set_codes"]:
            set_code = references["set_codes"][0]
            suggestions.append(f"Ask which cards in {set_code} are easiest to confuse by name or variant.")
            suggestions.append(f"Ask how Miru should detect {set_code} alt arts without breaking base cards.")
        if gameplay_topics:
            topic = gameplay_topics[0][0]
            display = topic.upper() if topic == "don!!" else topic.title()
            suggestions.append(f"Ask for a concrete card example that uses {display}.")
        if not suggestions:
            suggestions.append("Ask about a specific card code such as OP09-001 or EB04-031.")
            suggestions.append("Ask what fields are known or missing for a specific card or set.")
            suggestions.append("Ask how Miru should identify promo, SP, alt art, or manga prints.")
        suggestions.append("Ask which missing fields or ambiguous matches Miru should resolve next.")

        deduped = []
        for suggestion in suggestions:
            if suggestion not in deduped:
                deduped.append(suggestion)
        return deduped[:4]

    def detect_query_focuses(self, text: str) -> list[str]:
        normalized = normalize_lookup_text(text)
        focuses = []
        if any(token in normalized for token in ("fix", "implement", "build", "dashboard", "app py", "codex", "repository", "repo")):
            focuses.append("miru development")
        if any(token in normalized for token in ("missing", "catalog", "learn", "coverage", "represented in the catalog")):
            focuses.append("knowledge gap")
        if self.extract_references(text)["card_codes"]:
            focuses.append("specific card")
        if self.extract_references(text)["set_codes"]:
            focuses.append("set")
        if self.extract_references(text)["variant"]["signals"]:
            focuses.append("variant")
        if self.find_gameplay_topics(text) or self.extract_references(text)["effect_terms"]:
            focuses.append("gameplay mechanic")
        return focuses or ["general optcg knowledge"]

    def build_structured_understanding(self, request_text: str, mode: str = "card knowledge") -> dict[str, Any]:
        references = self.extract_references(request_text)
        matches = self.lookup_cards(request_text, limit=5)
        focuses = self.detect_query_focuses(request_text)
        assumptions = []
        if references["card_codes"] and not matches:
            assumptions.append("A specific card code was mentioned, but it was not found in the current local cache.")
        assumptions.extend(match.assumption for match in matches if match.assumption)

        detected_sets = []
        for set_code in references["set_codes"]:
            summary = self.set_summary(set_code)
            if summary["set_name"]:
                detected_sets.append(f"{summary['set_code']} = {summary['set_name']}")
            else:
                detected_sets.append(summary["set_code"])

        gameplay_topics = self.find_gameplay_topics(request_text)
        subquestion_clauses = self.split_query_into_subquestions(request_text)
        subquestion_intents = self.detect_subquestion_intents(
            request_text,
            references,
            matches,
            gameplay_topics,
        )
        card_metadata = []
        effect_analysis = []
        if matches:
            top_card = self.cards.get(matches[0].canonical_code) or {}
            if top_card:
                card_metadata = self.build_card_metadata(top_card)
                effect_analysis = self.build_effect_analysis(top_card)

        set_summaries = []
        for set_code in references["set_codes"][:2]:
            summary = self.set_summary(set_code)
            if summary["set_code"]:
                set_summaries.append(summary)
        missing_field_notes = self.build_missing_field_notes(top_card if matches else {}, set_summaries)

        glossary_lines = []
        normalized = normalize_lookup_text(request_text)
        for key, lines in GENERAL_GUIDE.items():
            triggers = GENERAL_GUIDE_TRIGGERS.get(key, (key,))
            if any(trigger in normalized for trigger in triggers):
                glossary_lines.extend(lines)
        variant_signals = references["variant"]["signals"]
        for signal in variant_signals:
            explanation = VARIANT_GUIDE.get(signal)
            if explanation:
                glossary_lines.append(f"{signal.upper() if signal == 'sp' else signal}: {explanation}")
        for _, description in gameplay_topics:
            glossary_lines.append(description)

        answer_breakdown = []
        temp_sections = {
            "Knowledge notes": glossary_lines or ["No extra glossary note was needed for this query."],
        }
        for intent in subquestion_intents:
            answer = self.answer_subquestion(intent, request_text, references, matches, temp_sections)
            if answer:
                answer_breakdown.append(answer)

        detected_subquestions = []
        if len(subquestion_clauses) > 1:
            detected_subquestions.extend(subquestion_clauses[:5])
        if len(detected_subquestions) < 5:
            for intent in subquestion_intents:
                label = intent["label"]
                if label not in detected_subquestions:
                    detected_subquestions.append(label)
                if len(detected_subquestions) >= 5:
                    break

        follow_up_suggestions = self.build_follow_up_suggestions(
            request_text,
            references,
            matches,
            gameplay_topics,
        )

        sections = {
            "Detected card references": [
                *(references["card_codes"] or []),
                *(f"{match.canonical_code} ({match.card_name})" for match in matches[:3] if match.canonical_code not in references["card_codes"]),
            ] or ["None resolved from the query."],
            "Detected set references": detected_sets or ["None resolved from the query."],
            "Detected variant language": variant_signals or ["No explicit variant hints detected."],
            "Detected gameplay mechanics": [
                *(references["effect_terms"] or []),
                *(topic for topic, _ in gameplay_topics if topic not in references["effect_terms"]),
            ] or ["No specific gameplay mechanic terms detected."],
            "Detected sub-questions": detected_subquestions or ["No explicit sub-question split was needed."],
            "Card metadata": card_metadata or ["No single card was resolved strongly enough to show full metadata."],
            "Missing or unknown fields": missing_field_notes,
            "Effect analysis": effect_analysis or ["No card effect analysis was available for this query yet."],
            "Set context": [
                f"{summary['set_code']} | {summary['set_name'] or 'Unknown set name'} | Cached cards: {summary['card_count']}"
                + (f" | Sample cards: {', '.join(summary['sample_cards'])}" if summary["sample_cards"] else "")
                for summary in set_summaries
            ] or ["No set-level summary was needed for this query."],
            "Possible card matches": self.summarize_matches(matches) or ["No card matches were strong enough to list."],
            "Answer breakdown": answer_breakdown or ["Use the detected card, set, and glossary sections as the main answer."],
            "Ambiguity notes": assumptions or ["No major ambiguity detected."],
            "Knowledge notes": glossary_lines or ["No extra glossary note was needed for this query."],
            "Possible follow-ups": follow_up_suggestions or ["No follow-up suggestion was needed for this query."],
        }

        lines = ["OPTCG understanding", f"- Query focus: {', '.join(focuses)}"]
        for heading, values in sections.items():
            lines.append(heading)
            for value in values:
                prefix = "-" if not str(value).startswith("-") else ""
                lines.append(f"{prefix} {value}".rstrip())

        return {
            "references": references,
            "matches": matches,
            "focuses": focuses,
            "subquestions": subquestion_intents,
            "multi_question": len(subquestion_intents) > 1 or len(subquestion_clauses) > 1,
            "sections": sections,
            "text": "\n".join(lines),
        }


def load_onepiece_knowledge(path: Path | None = None) -> OnePieceKnowledgeBase:
    return OnePieceKnowledgeBase.load(path=path)
