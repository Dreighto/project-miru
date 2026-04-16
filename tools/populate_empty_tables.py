"""
Populate six empty tables in card_catalog.db with verified data.
Phase A: card_errata, restriction_pairs (Bandai official)
Phase B: miru_deck_archetypes, miru_meta_events, miru_card_usage (perplexity_mcp)
Phase C: card_rulings (deferred - flagged)

Source: Official Bandai errata/restriction pages + Limitless/Perplexity meta research
"""

import sqlite3
import sys

DB_PATH = r'D:\dev\tcg-watcher-worktree\data\card_catalog.db'

ERRATA_URL = 'https://en.onepiece-cardgame.com/rules/errata_card/'
RESTRICTION_URL = 'https://en.onepiece-cardgame.com/rules/restriction/'


def build_errata_data():
    """All errata from the official Bandai errata page, verified 2026-04-15."""
    rows = []

    st_sep_2022 = [
        'ST01-001', 'ST01-005', 'ST01-007', 'ST01-014', 'ST01-015',
        'ST01-016', 'ST01-017', 'ST02-005', 'ST02-008', 'ST02-009',
        'ST02-015', 'ST02-016', 'ST02-017', 'ST03-001', 'ST03-003',
        'ST03-004', 'ST03-007', 'ST03-009', 'ST03-014', 'ST03-015',
        'ST03-016', 'ST03-017', 'ST04-001', 'ST04-002', 'ST04-003',
        'ST04-004', 'ST04-008', 'ST04-010', 'ST04-014', 'ST04-015',
        'ST04-016', 'ST04-017',
    ]
    for code in st_sep_2022:
        rows.append((
            code, '2022-09-26', 'effect_text',
            'Original text without "up to" qualifier',
            'Corrected text with "up to" qualifier added',
            'ST01-04 "up to" errata batch for EN translation accuracy',
        ))

    op01_feb_2023 = [
        'OP01-002', 'OP01-003', 'OP01-005', 'OP01-006', 'OP01-007',
        'OP01-014', 'OP01-015', 'OP01-016', 'OP01-017', 'OP01-020',
        'OP01-026', 'OP01-027', 'OP01-028', 'OP01-029', 'OP01-030',
        'OP01-033', 'OP01-034', 'OP01-035', 'OP01-038', 'OP01-040',
        'OP01-041', 'OP01-042', 'OP01-044', 'OP01-047', 'OP01-048',
        'OP01-049', 'OP01-050', 'OP01-051', 'OP01-054', 'OP01-056',
        'OP01-057', 'OP01-058', 'OP01-059', 'OP01-061', 'OP01-063',
        'OP01-064', 'OP01-069', 'OP01-070', 'OP01-071', 'OP01-074',
        'OP01-079', 'OP01-084', 'OP01-085', 'OP01-086', 'OP01-087',
        'OP01-088', 'OP01-089', 'OP01-090', 'OP01-093', 'OP01-096',
        'OP01-097', 'OP01-098', 'OP01-101', 'OP01-106', 'OP01-108',
        'OP01-112', 'OP01-113', 'OP01-115', 'OP01-116', 'OP01-117',
        'OP01-118', 'OP01-119', 'ST02-007',
    ]
    for code in op01_feb_2023:
        note = 'OP01 "up to" errata batch for EN translation accuracy'
        if code == 'OP01-112':
            note = 'OP01-112 Page One: [On Play] changed to [Activate: Main] timing correction'
        if code == 'ST02-007':
            note = 'ST02-007 Jewelry Bonney: "up to" and "this card" to "this Character" correction'
        rows.append((
            code, '2023-02-17', 'effect_text',
            'Original text without "up to" qualifier',
            'Corrected text with "up to" qualifier added',
            note,
        ))

    individual = [
        ('OP02-002', '2023-03-03', 'effect_text',
         '"this Leader or 1 of your Characters is given a DON!! card"',
         '"this Leader or any of your Characters is given a DON!! card"',
         'OP02-002 Monkey.D.Garp: "1 of" changed to "any of" for DON!! assignment trigger'),

        ('ST02-013', '2023-03-31', 'effect_text',
         '"Set this card as active"',
         '"Set this Character as active"',
         'ST02-013 Eustass Kid: "this card" corrected to "this Character"'),

        ('OP02-071', '2023-04-21', 'effect_text',
         '"a DON!! card on your field is returned"',
         '"a DON!! card on the field is returned"',
         'OP02-071 Magellan Leader: "your field" changed to "the field" for DON!! return trigger'),

        ('OP03-047', '2023-07-14', 'effect_text',
         '"you may trash 7 cards ... You may return up to 1 ... and trash 2 cards"',
         '"you may trash 7 cards ... Return up to 1 ... and you may trash 2 cards"',
         'OP03-047 Zeff: "may" repositioned for optional trash clause'),

        ('OP03-054', '2023-07-14', 'effect_text',
         '"[Trigger] You may draw 1 card and trash 1 card"',
         '"[Trigger] Draw 1 card and you may trash 1 card"',
         'OP03-054 Usopp Rubber Band: draw made mandatory, trash made optional in trigger'),

        ('OP05-032', '2023-12-08', 'effect_text',
         '"rest up to 1 of your Characters"',
         '"rest 1 of your Characters"',
         'OP05-032 Pica: "up to" removed from K.O. replacement rest clause'),

        ('ST14-014', '2024-08-16', 'effect_text',
         '"If you have a Character with a cost of 8 or more"',
         '"If there is a Character with a cost of 8 or more"',
         'ST14-014 Gum-Gum Giant Rifle: "you have" changed to "there is" for cost-8 check'),

        ('OP06-034', '2024-12-06', 'traits',
         'Fish-Man',
         'Merfolk',
         'OP06-034 Hyouzou: Trait corrected from Fish-Man to Merfolk'),

        ('OP09-058', '2024-12-13', 'effect_text',
         '"Return up to 1 of your opponent\'s Characters with a cost of 6 or less"',
         '"Your opponent chooses 1 of their Character with a cost of 6 or less and return"',
         'OP09-058 Special Muggy Ball: Changed from player-choice bounce to opponent-choice bounce'),

        ('OP07-097', '2025-05-16', 'effect_text',
         '"Select up to 1 {Egghead} typSelectup to 1 {Egghead} type card"',
         '"Select up to 1 {Egghead} type card"',
         'OP07-097 Vegapunk Leader: Misprint corrected (duplicated text fragment removed, colon added)'),

        ('OP13-077', '2025-10-24', 'effect_text',
         '"gains +3000 power during this turn"',
         '"gains +3000 power during this battle"',
         'OP13-077 Go All the Way to the Top: "this turn" corrected to "this battle"'),

        ('OP13-119', '2025-10-30', 'effect_text',
         '"[On Play] If your Leader is multicolored, set up to 4 of your DON!! cards as active..."',
         '"[On Play] Give up to 1 rested DON!! card to your Leader. Then, you may return up to 1..."',
         'OP13-119 Portgas.D.Ace Wanted Poster: Complete effect rewrite (misprint correction)'),

        ('OP14-009', '2025-12-19', 'traits',
         'The Seven Warlords of the Sea/Supernovas/Heart Pirates',
         'Supernovas/Heart Pirates',
         'OP14-009 Trafalgar Law: Removed incorrect trait "The Seven Warlords of the Sea"'),

        ('OP15-023', '2026-03-13', 'effect_text',
         '"Give up to 1 rested DON!! card from its owner\'s cost area"',
         '"Give up to 1 DON!! card from its owner\'s cost area"',
         'OP15-023 Arlong: Removed incorrect "rested" qualifier from DON!! card giving'),
    ]
    rows.extend(individual)
    return rows


def build_restriction_pairs():
    """Restriction pairs from the official Bandai restriction page, verified 2026-04-15."""
    return [
        ('OP11-040', 'OP11-067', 'co-restriction', 'EN', 'standard',
         '2025-08-30', 'bandai_official',
         'https://en.onepiece-cardgame.com/topics/019.php',
         '', 'Banned pair: Monkey.D.Luffy + Charlotte Katakuri cannot be in the same deck'),

        ('OP11-040', 'OP08-069', 'co-restriction', 'EN', 'standard',
         '2025-08-30', 'bandai_official',
         'https://en.onepiece-cardgame.com/topics/019.php',
         '', 'Banned pair: Monkey.D.Luffy + Charlotte Linlin cannot be in the same deck'),

        ('OP07-115', 'EB04-058', 'co-restriction', 'EN', 'standard',
         '2026-04-10', 'bandai_official',
         'https://en.onepiece-cardgame.com/news/restriction-260501.html',
         '', 'Banned pair: I Re-Quasar Helllp!! + Borsalino - excessive game time'),
    ]


def build_archetypes():
    """Deck archetypes from Limitless/Perplexity research, verified 2026-04-15.
    archetype_key, display_name, format_name, representative_leader_code,
    confidence_score, notes
    """
    return [
        ('black_imu', 'Black Imu', 'standard', 'OP13-079',
         0.95, 'Tier 1 since OP13. Dominant control deck. 17.13% meta share OP14.5 (Limitless). Source: perplexity_mcp research.'),

        ('red_blue_ace', 'Red/Blue Ace', 'standard', 'OP13-002',
         0.95, 'Tier 1 since OP13. Aggressive multicolor deck. 13.40% meta share OP14.5 (Limitless). Source: perplexity_mcp research.'),

        ('green_mihawk', 'Green Mihawk', 'standard', 'OP14-020',
         0.90, 'Tier 1 OP14 leader. Strong control with Perona support. 13.03% meta share OP14.5. Source: perplexity_mcp research.'),

        ('purple_yellow_rosinante', 'Purple/Yellow Rosinante', 'standard', 'OP12-061',
         0.90, 'Tier 1. Boosted by EB04 support. 18.37% meta share OP14.5 - highest representation. Source: perplexity_mcp research.'),

        ('blue_purple_boa', 'Blue/Purple Boa Hancock', 'standard', 'OP14-041',
         0.90, 'Tier 1. Explosive attack pressure with hand recovery. Strong trigger package. Source: perplexity_mcp research.'),

        ('purple_doflamingo', 'Purple Doflamingo', 'standard', 'OP14-060',
         0.85, 'Tier 1-2. Skill-intensive control deck. Competitive vs top meta leaders. Source: perplexity_mcp research.'),

        ('blue_purple_sanji', 'Blue/Purple Sanji', 'standard', 'OP12-041',
         0.80, 'Tier 1-2. 12.15% meta share OP14.5 (Limitless). Strong staple deck. Source: perplexity_mcp research.'),

        ('green_jinbe', 'Green Jinbe', 'standard', 'OP14-040',
         0.80, 'Tier 2. Most aggressive OP14 leader. Strong draw power with offensive pressure. Source: perplexity_mcp research.'),

        ('red_green_sabo', 'Red/Green Sabo', 'standard', 'OP13-004',
         0.75, 'Tier 2. Balanced matchup spread. Can compete with Tier 1 decks. Source: perplexity_mcp research.'),

        ('yellow_enel', 'Yellow Enel', 'standard', 'OP15-058',
         0.70, 'Tier 2. New OP15 leader with strong matchups vs OP14 leaders. Emerging archetype. Source: perplexity_mcp research.'),
    ]


def build_meta_events():
    """Tournament events from Limitless/Perplexity research, verified 2026-04-15.
    event_key, event_name, format_name, event_date, source_url,
    source_kind, notes, region
    """
    return [
        ('regional_bonn_2026_03', 'Regional Bonn', 'standard', '2026-03-21',
         'https://onepiece.limitlesstcg.com/tournaments',
         'perplexity_mcp', '1024 players. OP14.5 format. Top decks: Rosinante, Imu, Ace, Mihawk.', 'EN'),

        ('regional_mesquite_2026_03', 'Regional Mesquite, TX', 'standard', '2026-03-22',
         'https://onepiece.limitlesstcg.com/tournaments',
         'perplexity_mcp', 'OP14.5 format. Top finishes: Mihawk 1st, Rosinante 3rd.', 'EN'),

        ('regional_melbourne_2026_03', 'Regional Melbourne', 'standard', '2026-03-22',
         'https://onepiece.limitlesstcg.com/tournaments',
         'perplexity_mcp', 'OP14.5 format. Part of same weekend as Mesquite regional.', 'EN'),
    ]


def build_card_usage():
    """Card usage from Limitless OP14.5 meta representation data, verified 2026-04-15.
    card_code, archetype_key, usage_count, format_name, source_kind,
    period_label, observed_at, notes, region
    """
    return [
        ('OP13-079', 'black_imu', 1713, 'standard', 'perplexity_mcp',
         'OP14.5', '2026-03-21',
         'Meta share basis points (17.13% = 1713 bps) from Limitless OP14.5 tournament data', 'EN'),

        ('OP13-002', 'red_blue_ace', 1340, 'standard', 'perplexity_mcp',
         'OP14.5', '2026-03-21',
         'Meta share basis points (13.40%) from Limitless OP14.5 tournament data', 'EN'),

        ('OP14-020', 'green_mihawk', 1303, 'standard', 'perplexity_mcp',
         'OP14.5', '2026-03-21',
         'Meta share basis points (13.03%) from Limitless OP14.5 tournament data', 'EN'),

        ('OP12-061', 'purple_yellow_rosinante', 1837, 'standard', 'perplexity_mcp',
         'OP14.5', '2026-03-21',
         'Meta share basis points (18.37% - highest) from Limitless OP14.5 tournament data', 'EN'),

        ('OP12-041', 'blue_purple_sanji', 1215, 'standard', 'perplexity_mcp',
         'OP14.5', '2026-03-21',
         'Meta share basis points (12.15%) from Limitless OP14.5 tournament data', 'EN'),
    ]


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    for tbl in ['card_errata', 'card_rulings', 'restriction_pairs',
                 'miru_deck_archetypes', 'miru_meta_events', 'miru_card_usage']:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur.fetchone()[0]
        if cnt > 0:
            print(f"STOP: {tbl} already has {cnt} rows. Aborting.")
            con.close()
            sys.exit(1)

    cur.execute("SELECT canonical_code FROM cards")
    valid_codes = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT source_key FROM data_sources WHERE ethics_status='approved' AND is_active=1")
    approved_sources = {r[0] for r in cur.fetchall()}
    print(f"Approved sources: {sorted(approved_sources)}")

    if 'bandai_official' not in approved_sources:
        print("STOP: bandai_official not approved. Cannot proceed with Phase 2.")
        con.close()
        sys.exit(1)

    errata_data = build_errata_data()
    restriction_data = build_restriction_pairs()
    archetype_data = build_archetypes()
    event_data = build_meta_events()
    usage_data = build_card_usage()

    skipped = []
    inserted_counts = {}

    # === CARD_ERRATA ===
    print("\n=== card_errata ===")
    try:
        cur.execute("BEGIN")
        ins = 0
        for (code, edate, field, old_v, new_v, note) in errata_data:
            if code not in valid_codes:
                skipped.append(('card_errata', code, 'card_code not in cards table'))
                continue
            cur.execute("""
                INSERT INTO card_errata
                    (card_code, errata_date, field_name, old_value, new_value,
                     region, source_key, source_url, is_active, notes)
                VALUES (?, ?, ?, ?, ?, 'EN', 'bandai_official', ?, 1, ?)
            """, (code, edate, field, old_v, new_v, ERRATA_URL, note))
            ins += 1
        cur.execute("COMMIT")
        inserted_counts['card_errata'] = ins
        print(f"  Inserted: {ins}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  ROLLBACK: {e}")
        inserted_counts['card_errata'] = 0

    # === RESTRICTION_PAIRS ===
    print("\n=== restriction_pairs ===")
    try:
        cur.execute("BEGIN")
        ins = 0
        for (ca, cb, ptype, region, fmt, edate, skey, surl, nid, note) in restriction_data:
            if ca not in valid_codes:
                skipped.append(('restriction_pairs', ca, 'card_code_a not in cards table'))
                continue
            if cb not in valid_codes:
                skipped.append(('restriction_pairs', cb, 'card_code_b not in cards table'))
                continue
            if skey not in approved_sources:
                skipped.append(('restriction_pairs', f'{ca}+{cb}', f'source_key {skey} not approved'))
                continue
            cur.execute("""
                INSERT INTO restriction_pairs
                    (card_code_a, card_code_b, pairing_type, region, format_name,
                     effective_date, source_key, source_url, notice_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ca, cb, ptype, region, fmt, edate, skey, surl, nid, note))
            ins += 1
        cur.execute("COMMIT")
        inserted_counts['restriction_pairs'] = ins
        print(f"  Inserted: {ins}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  ROLLBACK: {e}")
        inserted_counts['restriction_pairs'] = 0

    # === CARD_RULINGS (deferred) ===
    print("\n=== card_rulings ===")
    print("  DEFERRED: Q&A rulings require PDF parsing of official Q&A documents.")
    print("  Official Q&A PDFs at en.onepiece-cardgame.com/rules/ are not machine-readable.")
    print("  0 rows inserted. Flagged for future population with verified Q&A data.")
    inserted_counts['card_rulings'] = 0

    # === MIRU_DECK_ARCHETYPES ===
    print("\n=== miru_deck_archetypes ===")
    try:
        cur.execute("BEGIN")
        ins = 0
        for (akey, dname, fmt, leader, conf, note) in archetype_data:
            if leader not in valid_codes:
                skipped.append(('miru_deck_archetypes', leader, 'leader code not in cards table'))
                continue
            cur.execute("""
                INSERT INTO miru_deck_archetypes
                    (archetype_key, display_name, format_name,
                     representative_leader_code, confidence_score, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (akey, dname, fmt, leader, conf, note))
            ins += 1
        cur.execute("COMMIT")
        inserted_counts['miru_deck_archetypes'] = ins
        print(f"  Inserted: {ins}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  ROLLBACK: {e}")
        inserted_counts['miru_deck_archetypes'] = 0

    # === MIRU_META_EVENTS ===
    print("\n=== miru_meta_events ===")
    try:
        cur.execute("BEGIN")
        ins = 0
        for (ekey, ename, fmt, edate, surl, skind, note, region) in event_data:
            cur.execute("""
                INSERT INTO miru_meta_events
                    (event_key, event_name, format_name, event_date,
                     source_url, source_kind, notes, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ekey, ename, fmt, edate, surl, skind, note, region))
            ins += 1
        cur.execute("COMMIT")
        inserted_counts['miru_meta_events'] = ins
        print(f"  Inserted: {ins}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  ROLLBACK: {e}")
        inserted_counts['miru_meta_events'] = 0

    # === MIRU_CARD_USAGE ===
    print("\n=== miru_card_usage ===")
    try:
        cur.execute("BEGIN")
        ins = 0
        for (code, akey, ucount, fmt, skind, period, obs, note, region) in usage_data:
            if code not in valid_codes:
                skipped.append(('miru_card_usage', code, 'card_code not in cards table'))
                continue
            cur.execute("""
                INSERT INTO miru_card_usage
                    (card_code, archetype_key, usage_count, format_name,
                     source_kind, period_label, observed_at, notes, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (code, akey, ucount, fmt, skind, period, obs, note, region))
            ins += 1
        cur.execute("COMMIT")
        inserted_counts['miru_card_usage'] = ins
        print(f"  Inserted: {ins}")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"  ROLLBACK: {e}")
        inserted_counts['miru_card_usage'] = 0

    # === VERIFICATION ===
    print("\n=== VERIFICATION ===")

    cur.execute("""
        SELECT 'card_errata' AS tbl, COUNT(*) FROM card_errata
        UNION ALL SELECT 'card_rulings', COUNT(*) FROM card_rulings
        UNION ALL SELECT 'restriction_pairs', COUNT(*) FROM restriction_pairs
        UNION ALL SELECT 'miru_deck_archetypes', COUNT(*) FROM miru_deck_archetypes
        UNION ALL SELECT 'miru_meta_events', COUNT(*) FROM miru_meta_events
        UNION ALL SELECT 'miru_card_usage', COUNT(*) FROM miru_card_usage
    """)
    print("\nRow counts:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}")

    print("\nReferential integrity:")
    for check_name, query in [
        ('card_errata orphans',
         "SELECT COUNT(*) FROM card_errata ce LEFT JOIN cards c ON ce.card_code = c.canonical_code WHERE c.canonical_code IS NULL"),
        ('restriction_pairs card_a orphans',
         "SELECT COUNT(*) FROM restriction_pairs rp LEFT JOIN cards c ON rp.card_code_a = c.canonical_code WHERE c.canonical_code IS NULL"),
        ('restriction_pairs card_b orphans',
         "SELECT COUNT(*) FROM restriction_pairs rp LEFT JOIN cards c ON rp.card_code_b = c.canonical_code WHERE c.canonical_code IS NULL"),
        ('miru_card_usage orphans',
         "SELECT COUNT(*) FROM miru_card_usage cu LEFT JOIN cards c ON cu.card_code = c.canonical_code WHERE c.canonical_code IS NULL"),
    ]:
        cur.execute(query)
        cnt = cur.fetchone()[0]
        status = 'PASS' if cnt == 0 else f'FAIL ({cnt} orphans)'
        print(f"  {check_name}: {status}")

    print("\nEthics gate (all source_keys used):")
    cur.execute("""
        SELECT DISTINCT source_key FROM card_errata
        UNION SELECT DISTINCT source_key FROM restriction_pairs
    """)
    for row in cur.fetchall():
        is_approved = row[0] in approved_sources
        print(f"  {row[0]}: {'APPROVED' if is_approved else 'NOT APPROVED - FLAG'}")

    print("\nSample rows:")
    for tbl in ['card_errata', 'restriction_pairs', 'miru_deck_archetypes',
                 'miru_meta_events', 'miru_card_usage']:
        cur.execute(f"SELECT * FROM {tbl} LIMIT 3")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if rows:
            print(f"\n  {tbl} ({len(cols)} cols):")
            for r in rows:
                print(f"    {dict(zip(cols, r))}")

    if skipped:
        print(f"\n=== SKIPPED ENTRIES ({len(skipped)}) ===")
        for tbl, code, reason in skipped:
            print(f"  [{tbl}] {code}: {reason}")

    con.close()

    print("\n=== SUMMARY ===")
    for tbl, cnt in inserted_counts.items():
        print(f"  {tbl}: {cnt} inserted")
    print(f"  Skipped: {len(skipped)}")
    print(f"  card_rulings: DEFERRED (0 inserted, flagged for future Q&A PDF population)")


if __name__ == '__main__':
    run()
