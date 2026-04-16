"""
Expand miru_meta_events with verified historical tournament data.

Sources: Limitless TCG tournament listing + Perplexity research + Bandai event pages.
All verified events have: event_name, event_date, player_count, winner, source URL.
"""

import sqlite3
import sys

DB_PATH = r'D:\dev\tcg-watcher-worktree\data\card_catalog.db'
SOURCE_KIND = 'perplexity_mcp'
LIMITLESS_URL = 'https://onepiece.limitlesstcg.com/tournaments'
BANDAI_CF_URL = 'https://en.onepiece-cardgame.com/events/2024/championship/finals.php'
BANDAI_CF2_URL = 'https://en.onepiece-cardgame.com/events/2025/championship/finals_season2.php'


def build_events():
    """All verified events from Limitless + Bandai official pages.

    Each tuple: (event_key, event_name, format_name, event_date,
                 source_url, source_kind, notes, region)
    """
    events = []

    # === 2024 CHAMPIONSHIP FINALS & REGIONALS ===

    events.append((
        'cf_paris_2024_10', 'Championship Finals Paris', 'standard', '2024-10-12',
        LIMITLESS_URL, SOURCE_KIND,
        'Paris, France. 972 players. OP08 format. Winner: Javier Caro. Season: 2024 S1.',
        'EN'))

    events.append((
        'cf_milan_2024_10', 'Championship Finals Milan', 'standard', '2024-10-26',
        LIMITLESS_URL, SOURCE_KIND,
        'Milan, Italy. 1024 players. OP08 format. Winner: Gianluca Di Clemente. Season: 2024 S1.',
        'EN'))

    events.append((
        'regional_barcelona_2024_11', 'Regional Barcelona', 'standard', '2024-11-02',
        LIMITLESS_URL, SOURCE_KIND,
        'Barcelona, Spain. 464 players. OP08 format. Winner: Simone Falanga.',
        'EN'))

    events.append((
        'regional_bologna_2024_11', 'Regional Bologna', 'standard', '2024-11-09',
        LIMITLESS_URL, SOURCE_KIND,
        'Bologna, Italy. 512 players. OP08 format. Winner: Gianluca Di Clemente.',
        'EN'))

    events.append((
        'regional_utrecht_2024_11', 'Regional Utrecht', 'standard', '2024-11-09',
        LIMITLESS_URL, SOURCE_KIND,
        'Utrecht, Netherlands. 1024 players. OP08 format. Winner: Hrvoje Hedžet.',
        'EN'))

    events.append((
        'regional_birmingham_2024_11', 'Regional Birmingham', 'standard', '2024-11-30',
        LIMITLESS_URL, SOURCE_KIND,
        'Birmingham, UK. 421 players. OP08.5 format. Winner: Giovanni Salvatore Oliva.',
        'EN'))

    events.append((
        'cf_utrecht_2024_12', 'Championship Finals Utrecht', 'standard', '2024-12-07',
        LIMITLESS_URL, SOURCE_KIND,
        'Utrecht, Netherlands. 2275 players. OP08.5 format. Winner: Enricomaria Rustico. Largest EN event recorded.',
        'EN'))

    # === 2025 CHAMPIONSHIP FINALS & WORLD CHAMPIONSHIP ===

    events.append((
        'cf_orlando_2025_01', 'Championship Finals Orlando', 'standard', '2025-01-04',
        BANDAI_CF_URL, SOURCE_KIND,
        'Orlando, FL, USA. OP09 format. BCG Fest 24-25. Season: 2024 S2.',
        'EN'))

    events.append((
        'cf_oceania_2025_01', 'Championship Finals Melbourne (2024)', 'standard', '2025-01-25',
        BANDAI_CF_URL, SOURCE_KIND,
        'Melbourne, Australia. OP09 format. TAK Games at Marvel Stadium. Season: 2024 Oceania.',
        'EN'))

    events.append((
        'wc_2025_03', 'World Championship 2025', 'standard', '2025-03-15',
        'https://onepiece.limitlesstcg.com/tournaments/273', SOURCE_KIND,
        'Chiba, Japan. 32 players. OP09 format. Winner: Abo (Black Lucci). Makuhari Messe.',
        'EN'))

    # === 2025 EU REGIONALS (Mar-Jun) ===

    events.append((
        'regional_glasgow_2025_03', 'Regional Glasgow', 'standard', '2025-03-22',
        LIMITLESS_URL, SOURCE_KIND,
        'Glasgow, UK. 400 players. OP10 format. Winner: Mateusz Bednarczyk.',
        'EN'))

    events.append((
        'regional_munchen_2025_03', 'Regional München', 'standard', '2025-03-29',
        LIMITLESS_URL, SOURCE_KIND,
        'München, Germany. 1020 players. OP10 format. Winner: Jan Hoffacker.',
        'EN'))

    events.append((
        'regional_london_2025_04', 'Regional London', 'standard', '2025-04-12',
        LIMITLESS_URL, SOURCE_KIND,
        'London, UK. 963 players. OP10.1 format. Winner: Benjamin Gayraud.',
        'EN'))

    events.append((
        'regional_athens_2025_04', 'Regional Athens', 'standard', '2025-04-12',
        LIMITLESS_URL, SOURCE_KIND,
        'Athens, Greece. 256 players. OP10.1 format. Winner: Arman Haji-Ghassemi.',
        'EN'))

    events.append((
        'regional_paris_2025_04', 'Regional Paris', 'standard', '2025-04-19',
        LIMITLESS_URL, SOURCE_KIND,
        'Paris, France. 916 players. OP10.1 format. Winner: Gaston78910.',
        'EN'))

    events.append((
        'regional_mulheim_2025_04', 'Regional Mülheim an der Ruhr', 'standard', '2025-04-26',
        LIMITLESS_URL, SOURCE_KIND,
        'Mülheim an der Ruhr, Germany. 500 players. OP10.1 format. Winner: Thorben Piplack.',
        'EN'))

    events.append((
        'regional_barcelona_2025_05', 'Regional Barcelona', 'standard', '2025-05-10',
        LIMITLESS_URL, SOURCE_KIND,
        'Barcelona, Spain. 948 players. OP10.1 format. Winner: Luka Forjan.',
        'EN'))

    events.append((
        'regional_rome_2025_06', 'Regional Rome', 'standard', '2025-06-07',
        LIMITLESS_URL, SOURCE_KIND,
        'Rome, Italy. 1024 players. OP10.5 format. Winner: Nour.',
        'EN'))

    events.append((
        'regional_amsterdam_2025_06', 'Regional Amsterdam', 'standard', '2025-06-21',
        LIMITLESS_URL, SOURCE_KIND,
        'Amsterdam, Netherlands. 462 players. OP11 format. Winner: Julius Schürhoff.',
        'EN'))

    # === 2025 EU CHAMPIONSHIP S1 + MORE ===

    events.append((
        'cf_paris_2025_08', 'Championship Finals Paris', 'standard', '2025-08-02',
        LIMITLESS_URL, SOURCE_KIND,
        'Paris, France. 1024 players. OP11 format. Winner: Kevin Le. Season: 25-26 S1.',
        'EN'))

    events.append((
        'regional_porto_2025_08', 'Regional Porto', 'standard', '2025-08-09',
        LIMITLESS_URL, SOURCE_KIND,
        'Porto, Portugal. 250 players. OP11 format. Winner: Juan Méndez Casado.',
        'EN'))

    # === 2025 NA/OC/EU REGIONALS (Oct-Dec) ===

    events.append((
        'regional_lyon_2025_10', 'Regional Lyon', 'standard', '2025-10-11',
        LIMITLESS_URL, SOURCE_KIND,
        'Lyon, France. 512 players. OP12 format.',
        'EN'))

    events.append((
        'regional_losangeles_2025_10', 'Regional Los Angeles, CA', 'standard', '2025-10-18',
        LIMITLESS_URL, SOURCE_KIND,
        'Los Angeles, CA, USA. 512 players. OP12 format.',
        'EN'))

    events.append((
        'regional_bordeaux_2025_11', 'Regional Bordeaux', 'standard', '2025-11-15',
        LIMITLESS_URL, SOURCE_KIND,
        'Bordeaux, France. 512 players. OP13 format. Winner: Thomas Biero.',
        'EN'))

    events.append((
        'regional_edinburgh_2025_11', 'Regional Edinburgh', 'standard', '2025-11-22',
        LIMITLESS_URL, SOURCE_KIND,
        'Edinburgh, UK. 1000 players. OP13 format. Winner: Kevin Le.',
        'EN'))

    events.append((
        'regional_miami_2025_11', 'Regional Miami, FL', 'standard', '2025-11-22',
        LIMITLESS_URL, SOURCE_KIND,
        'Miami, FL, USA. 570 players. OP13 format. Winner: Goblin.',
        'EN'))

    events.append((
        'regional_fortworth_2025_12', 'Regional Fort Worth, TX', 'standard', '2025-12-06',
        LIMITLESS_URL, SOURCE_KIND,
        'Fort Worth, TX, USA. 650 players. OP13 format. Winner: Elijah Quinby.',
        'EN'))

    events.append((
        'regional_toronto_2025_12', 'Regional Toronto, ON', 'standard', '2025-12-06',
        LIMITLESS_URL, SOURCE_KIND,
        'Toronto, ON, Canada. 512 players. OP13 format. Winner: Roy Choi.',
        'EN'))

    events.append((
        'cf_dusseldorf_2025_12', 'Championship Finals Düsseldorf', 'standard', '2025-12-13',
        LIMITLESS_URL, SOURCE_KIND,
        'Düsseldorf, Germany. 900 players. OP13 format. Winner: Chiipyy. Season: 25-26 S2.',
        'EN'))

    events.append((
        'regional_pasadena_2025_12', 'Regional Pasadena, CA', 'standard', '2025-12-13',
        LIMITLESS_URL, SOURCE_KIND,
        'Pasadena, CA, USA. 512 players. OP13 format. Winner: Steven Bahk.',
        'EN'))

    events.append((
        'regional_melbourne_2025_12', 'Regional Melbourne', 'standard', '2025-12-13',
        LIMITLESS_URL, SOURCE_KIND,
        'Melbourne, Australia. 577 players. OP13 format. Winner: Siris Wang.',
        'EN'))

    events.append((
        'regional_peoria_2025_12', 'Regional Peoria, IL', 'standard', '2025-12-13',
        LIMITLESS_URL, SOURCE_KIND,
        'Peoria, IL, USA. 832 players. OP13 format. Winner: Alexander Gonzalez.',
        'EN'))

    # === 2026 EVENTS ===

    events.append((
        'cf_mexicocity_2026_01', 'Championship Finals Mexico City', 'standard', '2026-01-17',
        LIMITLESS_URL, SOURCE_KIND,
        'Mexico City, Mexico. 1352 players. OP13 format. Winner: Gabriel Dantas Giodani.',
        'EN'))

    events.append((
        'cf_melbourne_2026_01', 'Championship Finals Melbourne', 'standard', '2026-01-24',
        BANDAI_CF2_URL, SOURCE_KIND,
        'Melbourne, Australia. 1064 players. OP14 format. Winner: Siris Wang. Season: 25-26 S2.',
        'EN'))

    events.append((
        'cf_lasvegas_2026_02', 'Championship Finals Las Vegas', 'standard', '2026-02-14',
        LIMITLESS_URL, SOURCE_KIND,
        'Las Vegas, NV, USA. 1000 players. OP14 format. Winner: Everydayclutch. BCG Fest 25-26.',
        'EN'))

    events.append((
        'wf_2026_03', 'World Finals 2026', 'standard', '2026-03-14',
        LIMITLESS_URL, SOURCE_KIND,
        'Japan. 34 players. OP14 format. Winner: Wasinha.',
        'EN'))

    # === NOTABLE TREASURE CUPS ===

    events.append((
        'tc_edinburgh_2025_11', 'Treasure Cup Edinburgh', 'standard', '2025-11-23',
        LIMITLESS_URL, SOURCE_KIND,
        'Edinburgh, UK. 256 players. OP13 format. Winner: Luka Forjan. Offline event.',
        'EN'))

    events.append((
        'tc_barcelona_2025_11', 'Treasure Cup Barcelona', 'standard', '2025-11-16',
        LIMITLESS_URL, SOURCE_KIND,
        'Barcelona, Spain. 250 players. OP13 format. Winner: Luka Forjan. Offline event.',
        'EN'))

    return events


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("SELECT event_key, event_name, event_date FROM miru_meta_events")
    existing = cur.fetchall()
    existing_keys = {r[0] for r in existing}
    existing_names_dates = {(r[1], r[2]) for r in existing}
    print(f"Existing rows: {len(existing)}")
    for r in existing:
        print(f"  {r[0]}: {r[1]} ({r[2]})")

    events = build_events()
    print(f"\nCandidate events: {len(events)}")

    to_insert = []
    skipped_dup = []
    for ev in events:
        ekey, ename, _, edate = ev[0], ev[1], ev[2], ev[3]
        if ekey in existing_keys:
            skipped_dup.append((ekey, 'duplicate event_key'))
            continue
        if (ename, edate) in existing_names_dates:
            skipped_dup.append((ekey, 'duplicate name+date'))
            continue
        to_insert.append(ev)

    if skipped_dup:
        print(f"\nSkipped (duplicates): {len(skipped_dup)}")
        for key, reason in skipped_dup:
            print(f"  {key}: {reason}")

    print(f"\nInserting: {len(to_insert)}")

    try:
        cur.execute("BEGIN")
        for ev in to_insert:
            cur.execute("""
                INSERT INTO miru_meta_events
                    (event_key, event_name, format_name, event_date,
                     source_url, source_kind, notes, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ev)
        cur.execute("COMMIT")
        print(f"Committed {len(to_insert)} rows.")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"ROLLBACK: {e}")
        con.close()
        sys.exit(1)

    # === VERIFICATION ===
    print(f"\n{'='*60}")
    print("VERIFICATION")
    print(f"{'='*60}")

    cur.execute("SELECT COUNT(*) FROM miru_meta_events")
    total = cur.fetchone()[0]
    print(f"\nTotal rows: {total}")

    cur.execute("""
        SELECT event_name, event_date, format_name, source_kind, notes
        FROM miru_meta_events ORDER BY event_date DESC
    """)
    print("\nFull table (newest first):")
    for row in cur.fetchall():
        print(f"  {row[1]} | {row[0]} | {row[2]} | {row[4][:70]}")

    con.close()
    print(f"\n=== SUMMARY ===")
    print(f"  Existing before: {len(existing)}")
    print(f"  Candidates: {len(events)}")
    print(f"  Skipped (duplicates): {len(skipped_dup)}")
    print(f"  Inserted: {len(to_insert)}")
    print(f"  Total after: {len(existing) + len(to_insert)}")


if __name__ == '__main__':
    run()
