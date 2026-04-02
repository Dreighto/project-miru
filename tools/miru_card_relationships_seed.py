#!/usr/bin/env python3
"""
Phase 2: card_relationships schema + operator seed (session 2026-03-24).

Run: python tools/miru_card_relationships_seed.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

UNIQUE_MARK = "UNIQUE(card_id, related_entity, relationship_type)"

DDL = """
CREATE TABLE IF NOT EXISTS card_relationships (
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL,
    related_entity TEXT NOT NULL,
    related_entity_type TEXT NOT NULL CHECK(
        related_entity_type IN ('card', 'leader', 'archetype', 'package')
    ),
    relationship_type TEXT NOT NULL CHECK(
        relationship_type IN (
            'supports_leader',
            'frequently_appears_with',
            'searches_target',
            'protects_combo_piece',
            'enables_tempo_swing',
            'payoff_for_setup',
            'overlaps_archetype_package',
            'budget_alternative',
            'enables_cost_reduction',
            'provides_recursion',
            'provides_draw',
            'provides_removal',
            'provides_don_acceleration',
            'provides_life_recovery',
            'enables_finisher'
        )
    ),
    evidence_source TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK(
        confidence IN ('low', 'medium', 'high', 'verified')
    ),
    status TEXT NOT NULL DEFAULT 'inferred' CHECK(
        status IN ('inferred', 'corroborated', 'verified', 'rejected')
    ),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(card_id, related_entity, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_card_relationships_card_id
    ON card_relationships(card_id);
CREATE INDEX IF NOT EXISTS idx_card_relationships_related_entity
    ON card_relationships(related_entity);
CREATE INDEX IF NOT EXISTS idx_card_relationships_type
    ON card_relationships(relationship_type);
"""

# Recreate table with UNIQUE when upgrading from pre-unique schema.
MIGRATION_SQL = """
BEGIN TRANSACTION;

CREATE TABLE card_relationships_new (
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL,
    related_entity TEXT NOT NULL,
    related_entity_type TEXT NOT NULL CHECK(
        related_entity_type IN ('card', 'leader', 'archetype', 'package')
    ),
    relationship_type TEXT NOT NULL CHECK(
        relationship_type IN (
            'supports_leader',
            'frequently_appears_with',
            'searches_target',
            'protects_combo_piece',
            'enables_tempo_swing',
            'payoff_for_setup',
            'overlaps_archetype_package',
            'budget_alternative',
            'enables_cost_reduction',
            'provides_recursion',
            'provides_draw',
            'provides_removal',
            'provides_don_acceleration',
            'provides_life_recovery',
            'enables_finisher'
        )
    ),
    evidence_source TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK(
        confidence IN ('low', 'medium', 'high', 'verified')
    ),
    status TEXT NOT NULL DEFAULT 'inferred' CHECK(
        status IN ('inferred', 'corroborated', 'verified', 'rejected')
    ),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(card_id, related_entity, relationship_type)
);

INSERT INTO card_relationships_new
    SELECT * FROM card_relationships;

DROP TABLE card_relationships;
ALTER TABLE card_relationships_new RENAME TO card_relationships;

CREATE INDEX IF NOT EXISTS idx_card_relationships_card_id
    ON card_relationships(card_id);
CREATE INDEX IF NOT EXISTS idx_card_relationships_related_entity
    ON card_relationships(related_entity);
CREATE INDEX IF NOT EXISTS idx_card_relationships_type
    ON card_relationships(relationship_type);

COMMIT;
"""


def ensure_card_relationships_unique_schema(conn: sqlite3.Connection) -> None:
    """Migrate legacy card_relationships (no UNIQUE) to schema with UNIQUE constraint."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='card_relationships'"
    ).fetchone()
    if row is None:
        return
    sql = row[0] or ""
    if UNIQUE_MARK in sql:
        return
    conn.executescript(MIGRATION_SQL)
    conn.commit()

EVIDENCE = "operator_knowledge_2026_03_24"

# (card_id, related_entity, related_entity_type, relationship_type, status, confidence, notes, shell_leader_for_report)
SEED: list[tuple[str, str, str, str, str, str, str, str]] = [
    # --- ROSINANTE (OP12-061) ---
    (
        "P-093",
        "OP12-061",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "P-093 Trafalgar Law is a core Blocker in Rosinante lists. Its On Play DON!! ramp triggers naturally because Rosinante Leader effect returns DON!! to deck, keeping P-093 condition met consistently.",
        "OP12-061",
    ),
    (
        "P-093",
        "OP12-061",
        "leader",
        "enables_tempo_swing",
        "verified",
        "high",
        "P-093 enters as a Blocker and immediately refunds a rested DON!!, letting Rosinante defend without losing momentum on the following turn.",
        "OP12-061",
    ),
    (
        "EB04-038",
        "OP12-061",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "EB04-038 Rosinante & Law counts as both Trafalgar Law and Donquixote Rosinante, making it eligible for Leader cost reduction and Leader life-save protection simultaneously.",
        "OP12-061",
    ),
    (
        "EB04-038",
        "OP12-061",
        "leader",
        "provides_draw",
        "verified",
        "high",
        "EB04-038 draws 1 card and sets 1 DON!! active on entry when DON!! condition is met, turning defensive turns into value turns inside the Rosinante shell.",
        "OP12-061",
    ),
    (
        "EB03-062",
        "OP12-061",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "EB03-062 Trafalgar Law has Rush, attacks immediately, then converts itself into Life recovery plus another 7-or-less cost Law from hand. One card becomes pressure, life recovery, and a fresh threat.",
        "OP12-061",
    ),
    (
        "EB03-062",
        "OP12-061",
        "leader",
        "provides_life_recovery",
        "verified",
        "high",
        "EB03-062 trash effect adds a card from top of deck to Life, partially offsetting the Life the Rosinante Leader spends to protect Law characters.",
        "OP12-061",
    ),
    (
        "OP12-073",
        "OP12-061",
        "leader",
        "payoff_for_setup",
        "verified",
        "high",
        "OP12-073 8000-power Law recovers DON!! on entry and buffs all Rosinante and Heart Pirates characters +1000 until opponent next turn. Rewards the low-DON board state Rosinante naturally creates.",
        "OP12-061",
    ),
    (
        "OP12-115",
        "OP12-061",
        "leader",
        "provides_recursion",
        "verified",
        "high",
        "OP12-115 I Love You!! at 2 or less Life returns a Trafalgar Law from trash to hand. Enables recycling of key Law pieces that have been used or discarded earlier in the game.",
        "OP12-061",
    ),
    (
        "OP12-115",
        "P-093",
        "card",
        "frequently_appears_with",
        "verified",
        "high",
        "I Love You!! recovers a Law from trash. P-093 is a Law that can be countered first then recovered via I Love You!! for reuse, effectively doubling its counter value across a game.",
        "OP12-061",
    ),
    (
        "ST10-010",
        "OP12-061",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "ST10-010 Trafalgar Law is a hand disruption piece in Rosinante lists. DON!! minus 1 to trash 2 from opponent hand if they hold 7 or more cards. Strips counters from a loaded hand.",
        "OP12-061",
    ),
    (
        "OP12-108",
        "OP12-061",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP12-108 Donquixote Rosinante is an early setup card that helps dig for Trafalgar Law pieces. Rosinante shell requires Law-heavy hand for Leader cost reduction to function consistently.",
        "OP12-061",
    ),
    (
        "OP09-069",
        "OP12-061",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP09-069 Trafalgar Law is a consistency piece that assembles the Law-heavy hand the Leader needs. Without enough Law targets in hand, the cost reduction effect has nothing to work with.",
        "OP12-061",
    ),
    # --- ACE (OP13-002) ---
    (
        "PRB02-008",
        "OP13-002",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "PRB02-008 Marco is a Blocker that draws 2 on KO. Combined with Ace Leader drawing when a 6000+ power character is KOd, Marco dying generates 3 cards drawn at once. Core defensive and draw piece.",
        "OP13-002",
    ),
    (
        "PRB02-008",
        "OP13-002",
        "leader",
        "provides_draw",
        "verified",
        "high",
        "Marco KO triggers both his own On KO draw 2 and Ace Leader draw 1 simultaneously, making him the single highest-value draw trigger in the deck.",
        "OP13-002",
    ),
    (
        "OP13-054",
        "OP13-002",
        "leader",
        "provides_don_acceleration",
        "verified",
        "high",
        "OP13-054 Yamato draws cards, interacts with board, and attaches DON!! back to Leader. Keeps the Leader effect online while developing pressure. One of the smoothest cards in the Ace shell.",
        "OP13-002",
    ),
    (
        "OP13-119",
        "OP13-002",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP13-119 character Ace combines draw, removal, and DON!! reattachment in a single On Play effect. Advances hand quality, board control, and Leader engine support simultaneously.",
        "OP13-002",
    ),
    (
        "OP13-043",
        "OP13-002",
        "leader",
        "provides_draw",
        "verified",
        "high",
        "OP13-043 Otama draw 2 trash 1 is live from turn 1 because Ace starts at 3 Life, immediately satisfying her 3 or less Life condition. Core early hand-fixing piece.",
        "OP13-002",
    ),
    (
        "OP13-042",
        "OP13-002",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP13-042 12000-power Edward Newgate Blocker reloads Leader DON!!, strengthens another character, and improves hand on entry. Anchors late game while setting up the next pressure turn.",
        "OP13-002",
    ),
    (
        "OP13-057",
        "OP13-002",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP13-057 at 1 or less Life prevents opponent from activating Blockers when Ace Leader attacks. Turns Ace into a sudden unblockable closer when opponent believes they have stabilized.",
        "OP13-002",
    ),
    (
        "OP13-016",
        "OP13-002",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP13-016 Monkey D Garp is the turn 1 priority in Ace lists. Starts hand-building immediately so the deck reaches its 6000-power body package consistently by midgame.",
        "OP13-002",
    ),
    (
        "ST22-015",
        "OP13-002",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "ST22-015 I Am Whitebeard is a defensive Event glue card that makes Ace survival into late game reliable. Pairs with the draw engine to give the deck both offense and defense.",
        "OP13-002",
    ),
    (
        "OP08-047",
        "OP13-002",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP08-047 Jozu keeps the 6000+ power body count high, ensuring the Leader draw effect stays active throughout the game even after board trades.",
        "OP13-002",
    ),
    # --- IMU (OP13-079) ---
    (
        "OP13-099",
        "OP13-079",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP13-099 The Empty Throne lets you play a Five Elders character for 3 cost up to your DON!! count. Without this Stage, Imu has no payoff. With it, one turn floods the board with multiple massive bodies.",
        "OP13-079",
    ),
    (
        "OP13-082",
        "OP13-099",
        "card",
        "payoff_for_setup",
        "verified",
        "high",
        "OP13-082 Five Elders is the primary payoff card played through The Empty Throne. The entire Imu setup phase exists to make this card land as early and as cleanly as possible.",
        "OP13-079",
    ),
    (
        "OP13-084",
        "OP13-082",
        "card",
        "enables_finisher",
        "verified",
        "high",
        "OP13-084 Ju Peter sets base power of Five Elders to 7000. Turns a large board into a lethal board. The difference between 5000 and 7000 across multiple bodies is what closes games.",
        "OP13-079",
    ),
    (
        "OP13-086",
        "OP13-079",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP13-086 Saint Shalria builds both hand and trash simultaneously in early turns. Both zones are required for the Imu engine. She can be cashed in with Leader effect once her job is done.",
        "OP13-079",
    ),
    (
        "OP13-096",
        "OP13-079",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP13-096 The Five Elders Are at Your Service builds hand and fills trash simultaneously. Speeds up the exact dual-zone setup Imu needs before The Empty Throne can fire effectively.",
        "OP13-079",
    ),
    (
        "OP13-083",
        "OP13-079",
        "leader",
        "payoff_for_setup",
        "verified",
        "high",
        "OP13-083 Saturn On Play extends value and sets up future Five Elders turns. Part of what makes Imu feel like it can reload pressure across multiple waves instead of firing once.",
        "OP13-079",
    ),
    (
        "OP13-089",
        "OP13-079",
        "leader",
        "payoff_for_setup",
        "verified",
        "high",
        "OP13-089 Warcury On KO finds more Five Elders pieces. Opponent removing him actively helps Imu set up the next wave. No clean answer exists for this card.",
        "OP13-079",
    ),
    (
        "OP13-080",
        "OP13-079",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP13-080 Nusjuro handles opposing characters during setup window. Imu cannot goldfish setup forever. Nusjuro buys the time the engine needs to reach payoff safely.",
        "OP13-079",
    ),
    (
        "OP13-091",
        "OP13-079",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP13-091 Mars keeps opposing threats in check during setup window. Prevents opponent from running away with the game while Imu assembles its Five Elders engine.",
        "OP13-079",
    ),
    # --- MIHAWK (OP14-020) ---
    (
        "OP14-027",
        "OP14-020",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP14-027 Shanks when rested by Leader effect immediately rests an opposing character with 7000 power or less. While rested, all opposing characters lose 1000 power. Leader activation becomes board control.",
        "OP14-020",
    ),
    (
        "OP14-027",
        "OP14-020",
        "leader",
        "enables_tempo_swing",
        "verified",
        "high",
        "Mihawk resting Shanks with Leader effect converts the DON!! reactivation into simultaneous board control plus power debuff. One activation does two things at once.",
        "OP14-020",
    ),
    (
        "OP14-119",
        "OP14-020",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP14-119 Mihawk character when rested locks an opposing character with cost 9 or less from becoming active next turn. Leader rests him on entry, so lock effect is immediate with no wait.",
        "OP14-020",
    ),
    (
        "OP12-034",
        "OP14-020",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP12-034 Perona looks at top 5 and grabs a Slash character or green Event on play. Primary consistency engine for Mihawk shell. Without her the deck lacks the pieces it needs at the right time.",
        "OP14-020",
    ),
    (
        "OP14-039",
        "OP14-020",
        "leader",
        "provides_draw",
        "verified",
        "high",
        "OP14-039 Coffin Boat draws 1 on entry and sets a DON!! active at end of turn if Leader is Mihawk. Free consistent value that works alongside the engine instead of against it.",
        "OP14-020",
    ),
    (
        "OP14-029",
        "OP14-020",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP14-029 Tashigi is a 5-cost character that immediately turns the Leader effect on. Her self-rest power gain and removal protection reward the same self-rest plan the Leader already wants.",
        "OP14-020",
    ),
    (
        "ST24-004",
        "OP14-020",
        "leader",
        "payoff_for_setup",
        "verified",
        "high",
        "ST24-004 Law and Bepo rests an opposing character on entry and locks it through next refresh. If opponent has 2 or more rested characters, Leader gains 2000 power. Punishes what Mihawk creates.",
        "OP14-020",
    ),
    (
        "OP06-038",
        "OP14-020",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP06-038 Trichiliocosm counter Event scales to plus 4000 when you have 8 or more rested cards. Mihawk naturally ends turns with many rested cards, making this bonus consistently achievable.",
        "OP14-020",
    ),
    # --- BOA (OP14-041) ---
    (
        "OP06-106",
        "OP14-041",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP06-106 Hiyori lets you swap a card from Life with one from hand. Boa wants specific Trigger cards loaded in Life. Hiyori sculpts the Life pile so the right cards are waiting when attacked.",
        "OP14-041",
    ),
    (
        "OP14-113",
        "OP14-041",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP14-113 Marguerite searches top 5 for Amazon Lily or Kuja Pirates card on play and is also a Trigger body herself. Never doing just one job. Core consistency piece.",
        "OP14-041",
    ),
    (
        "OP14-105",
        "OP14-041",
        "leader",
        "enables_tempo_swing",
        "verified",
        "high",
        "OP14-105 Gorgon Sisters reveals 3 Amazon Lily or Kuja Pirates to give all characters a rested DON!! including Leader. Also a Trigger. Turns a pile of small pieces into real board pressure.",
        "OP14-041",
    ),
    (
        "OP12-119",
        "OP14-041",
        "leader",
        "provides_life_recovery",
        "verified",
        "high",
        "OP12-119 Kuma adds a card to Life on entry and another if KOd on opponent turn. Keeps Trigger engine loaded from both directions. Major reason current Hancock lists feel more resilient.",
        "OP14-041",
    ),
    (
        "OP14-112",
        "OP14-041",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP14-112 Boa Hancock On Play adds card to your Life and sends top opponent Life card to their hand. Trigger plays a 6000-or-less Trigger character from hand. Massive Life swing in both directions.",
        "OP14-041",
    ),
    (
        "OP14-108",
        "OP14-041",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP14-108 Rayleigh On Play or Trigger KOs an opposing character with 7000 base power or less if opponent is at 3 or less Life. Late game Trigger hit becomes free removal on key body.",
        "OP14-041",
    ),
    (
        "OP14-103",
        "OP14-041",
        "leader",
        "searches_target",
        "verified",
        "high",
        "OP14-103 Gloriosa moves cards between hand and Life keeping Trigger pile useful. Also a Trigger herself. Double duty card that improves Life quality while being live off a Life hit.",
        "OP14-041",
    ),
    (
        "OP14-114",
        "OP14-041",
        "leader",
        "provides_don_acceleration",
        "verified",
        "high",
        "OP14-114 Ran is a 5000-power Trigger body that gives a rested DON!! to Kuja Pirates Leader or character on entry. Keeps Leader DON!! x1 condition live which is critical for the KO punishment.",
        "OP14-041",
    ),
    # --- ENEL (OP15-058) ---
    (
        "OP15-061",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP15-061 Ohm is a core early Vassal. Baseline 2000 power becomes 6000 when Leader attaches 4 DON!!. On Play DON!! minus 1 draws 1 card. Self-replacing setup piece that fuels turn 2 acceleration.",
        "OP15-058",
    ),
    (
        "OP15-063",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP15-063 Gedatsu is a core Vassal. Same profile as Ohm. The deck wants multiple Vassals consistently in opening hand so Leader always has a good target for DON!! attachment.",
        "OP15-058",
    ),
    (
        "OP15-066",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP15-066 Satori is a core Vassal. The early plan is not one special setup card. It is seeing enough cheap bodies that Leader effect always has a target. Satori keeps that reliable.",
        "OP15-058",
    ),
    (
        "OP15-067",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP15-067 Shura is the fourth core Vassal. Mulligan for at least two of these four Vassals to get the opening the deck is designed around.",
        "OP15-058",
    ),
    (
        "OP15-071",
        "OP15-058",
        "leader",
        "enables_tempo_swing",
        "verified",
        "high",
        "OP15-071 Holly gives herself and all Ohm cards Double Attack and 6000 base power on opponent turn. Small Vassal board becomes a board that hits twice and defends at 6000 simultaneously.",
        "OP15-058",
    ),
    (
        "OP15-118",
        "OP15-058",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP15-118 Enel at 6 or less DON!! becomes 10000 power and cannot be removed by effects. On Play looks at top 5 for 1 DON!!. Pressure and consistency in one card. Chains into the next Enel.",
        "OP15-058",
    ),
    (
        "OP15-060",
        "OP15-058",
        "leader",
        "enables_finisher",
        "verified",
        "high",
        "OP15-060 Enel same 10000 power and effect immunity at 6 or less DON!!. Activate Main gives Blocker for 1 DON!!. Can swing hard on your turn then defend on opponent turn without losing momentum.",
        "OP15-058",
    ),
    (
        "OP15-078",
        "OP15-058",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP15-078 Mamaragan Main draws 1 and rests opposing character with 5000 power or less for 2 DON!!. Counter gives plus 1000 and draws 1 at 6 or less DON!!. Best example of Enel converting DON!! spend into tempo.",
        "OP15-058",
    ),
    (
        "OP15-077",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP15-077 Lightning Dragon 0 cost spends 1 DON!! to draw 1 and lock a rested opposing character from becoming active next refresh. Pairs with Mamaragan to rest then lock bodies.",
        "OP15-058",
    ),
    (
        "OP15-077",
        "OP15-078",
        "card",
        "frequently_appears_with",
        "verified",
        "high",
        "Lightning Dragon and Mamaragan form a two-card tempo sequence: Mamaragan rests a body, Lightning Dragon locks it down next turn. Consistently paired in Enel lists.",
        "OP15-058",
    ),
    (
        "OP15-076",
        "OP15-058",
        "leader",
        "provides_removal",
        "verified",
        "high",
        "OP15-076 Lightning Beast Kiten 0 cost spends 1 DON!! to draw 1 and give opposing character minus 1000 power. Lowers power threshold so Mamaragan can reach bodies it otherwise cannot rest.",
        "OP15-058",
    ),
    (
        "OP15-076",
        "OP15-078",
        "card",
        "frequently_appears_with",
        "verified",
        "high",
        "Kiten and Mamaragan are paired tempo tools: Kiten lowers power by 1000, Mamaragan rests at 5000 or less. Together they handle bodies up to 6000 base power.",
        "OP15-058",
    ),
    (
        "OP07-064",
        "OP15-058",
        "leader",
        "supports_leader",
        "verified",
        "high",
        "OP07-064 Sanji costs 3 less and gains Blocker when your DON!! is at least 2 lower than opponent. In Enel that condition is almost always met. Strong defensive body that fits the lower-DON profile naturally.",
        "OP15-058",
    ),
]


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: {DB_PATH} not found", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_card_relationships_unique_schema(conn)
    conn.executescript(DDL)
    conn.commit()

    insert_sql = """
    INSERT OR IGNORE INTO card_relationships (
        card_id, related_entity, related_entity_type, relationship_type,
        evidence_source, confidence, status, notes, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """

    inserted = 0
    skipped_duplicate = 0
    failures: list[str] = []
    by_shell: defaultdict[str, int] = defaultdict(int)

    for row in SEED:
        (
            card_id,
            related_entity,
            related_entity_type,
            relationship_type,
            status,
            confidence,
            notes,
            shell,
        ) = row
        by_shell[shell] += 1
        try:
            cur = conn.execute(
                insert_sql,
                (
                    card_id,
                    related_entity,
                    related_entity_type,
                    relationship_type,
                    EVIDENCE,
                    confidence,
                    status,
                    notes,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped_duplicate += 1
        except sqlite3.Error as e:
            failures.append(f"{card_id}->{related_entity} ({relationship_type}): {e}")

    conn.commit()

    total_rows = conn.execute("SELECT COUNT(*) AS n FROM card_relationships").fetchone()["n"]

    print("=== card_relationships seed ===")
    print(f"Rows inserted this run: {inserted}")
    print(f"Rows skipped (duplicate key, OR IGNORE): {skipped_duplicate}")
    print(f"Total rows in card_relationships: {total_rows}")
    print(f"Failed: {len(failures)}")
    for f in failures[:25]:
        print(f"  {f}")
    if len(failures) > 25:
        print(f"  ... and {len(failures) - 25} more")
    print()
    print("Breakdown by leader shell (row count):")
    for code in (
        "OP12-061",
        "OP13-002",
        "OP13-079",
        "OP14-020",
        "OP14-041",
        "OP15-058",
    ):
        print(f"  {code}: {by_shell.get(code, 0)}")
    print()

    q4 = """
    SELECT related_entity, relationship_type, COUNT(*) as card_count
    FROM card_relationships
    GROUP BY related_entity, relationship_type
    ORDER BY related_entity, card_count DESC
    LIMIT 20
    """
    print("Step 4 query (LIMIT 20):")
    for r in conn.execute(q4).fetchall():
        print(f"  {dict(r)}")

    conn.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
