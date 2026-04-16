"""
card_product_appearances Phase D — add distribution source context to promo rows.

Updates the notes field for P-### promo rows where the distribution
source has been verified from official Bandai card list, Limitless,
and retailer descriptions.
"""

import sqlite3
import sys

DB_PATH = r'D:\dev\tcg-watcher-worktree\data\card_catalog.db'

DISTRIBUTION_MAP = {
    'P-001': 'Promotion Pack 2022 (launch event promo)',
    'P-002': 'Promotion Pack 2022 (launch event promo)',
    'P-003': 'Promotion Pack 2022 (launch event promo)',
    'P-004': 'Promotion Pack 2022 (launch event promo)',
    'P-005': 'Promotion Pack 2022 (launch event promo)',
    'P-006': 'Demo Deck / Tournament Pack Vol.1',
    'P-007': 'V-Jump / Saikyo Jump magazine / Tournament Pack Vol.1',
    'P-008': 'V-Jump / Saikyo Jump magazine / Tournament Pack Vol.1',
    'P-009': 'V-Jump / Saikyo Jump magazine / Tournament Pack Vol.1',
    'P-010': 'V-Jump / Saikyo Jump magazine / Tournament Pack Vol.1',
    'P-011': 'FILM RED Promotion Card Set / Premium Card Collection -Uta-',
    'P-012': 'FILM RED Promotion Card Set',
    'P-013': 'FILM RED Promotion Card Set',
    'P-014': 'FILM RED Promotion Card Set',
    'P-015': 'FILM RED Promotion Card Set',
    'P-016': 'FILM RED Promotion Card Set',
    'P-017': 'FILM RED Promotion Card Set / Tournament Pack Vol.7',
    'P-018': 'FILM RED Promotion Card Set',
    'P-019': 'FILM RED Promotion Card Set / Tournament Pack Vol.7',
    'P-020': 'FILM RED Promotion Card Set',
    'P-021': 'FILM RED Promotion Card Set',
    'P-022': 'FILM RED Promotion Card Set',
    'P-023': 'FILM RED Promotion Card Set',
    'P-024': 'Event Pack Vol.1',
    'P-025': 'V-Jump magazine (Oct 2022 issue)',
    'P-026': 'Tournament Pack Vol.2 era',
    'P-027': 'Tournament Pack Vol.2 era',
    'P-028': 'Tournament Pack Vol.2 era',
    'P-029': 'Event Pack Vol.1',
    'P-030': 'Demo Deck 2023 / Promotion Pack 2023',
    'P-031': 'Premium Card Collection -Uta-',
    'P-032': 'Event Pack Vol.1',
    'P-033': 'OP-02 Pre-Release Pack / Promotion Pack 2023',
    'P-034': 'Promotion Pack 2023',
    'P-035': 'Promotion Pack 2023',
    'P-036': 'Tournament Pack Vol.3 era',
    'P-037': 'Tournament Pack Vol.3 era',
    'P-039': 'Promotion Pack 2023',
    'P-041': 'Promotion Pack Vol.3 / Starter Deck 18 inclusion',
    'P-042': 'Tournament Pack Vol.4 era',
    'P-043': 'English 1st Anniversary stamped promo',
    'P-045': 'OP-06 Pre-Release Tournament participation prize',
    'P-046': 'Promotion Pack Vol.4 era',
    'P-047': 'Promotion Pack Vol.4',
    'P-048': 'Promotion Pack Vol.4',
    'P-049': 'Promotion Pack Vol.4',
    'P-050': 'Promotion Pack Vol.4 / Premium Card Collection -Live Action Edition-',
    'P-051': 'Promotion Pack Vol.4 / Premium Card Collection -Live Action Edition-',
    'P-052': 'Promotion Pack Vol.4 / Sealed Battle Kit Vol.1',
    'P-053': 'Promotion Pack Vol.4 / Premium Card Collection -Live Action Edition-',
    'P-054': 'Promotion Pack Vol.4',
    'P-055': 'Promotion Pack Vol.4 / Premium Card Collection -Live Action Edition-',
    'P-056': 'Promotion Pack Vol.4 / Premium Card Collection -Live Action Edition- / Sealed Battle Kit Vol.1',
    'P-057': 'Starter Deck 11: Uta Deck Battle participation',
    'P-058': 'Starter Deck 11: Uta Deck Battle participation',
    'P-059': 'Starter Deck 11: Uta Deck Battle participation',
    'P-060': 'Starter Deck 11: Uta Deck Battle participation',
    'P-061': 'Gift Collection 2023',
    'P-062': 'Event promo (Hody & Hyouzou)',
    'P-063': 'Card exchange event promo',
    'P-065': 'Tournament promo reprint (Tony Tony.Chopper)',
    'P-068': 'Premium Card Collection -BANDAI CARD GAMES Fest. 23-24 Edition-',
    'P-069': 'Premium Card Collection -BANDAI CARD GAMES Fest. 23-24 Edition- / multiple editions',
    'P-070': 'V-Jump magazine / Event Pack Vol.4',
    'P-071': 'Premium Card Collection -BANDAI CARD GAMES Fest. 23-24 Edition-',
    'P-072': 'English Version 1st Anniversary Set era',
    'P-073': 'Regional Participation Pack 2024 Vol.2 era',
    'P-074': 'Regional Participation Pack 2024 Vol.2 era',
    'P-075': 'Tournament Pack Vol.7 era',
    'P-076': 'Sakazuki replacement promo (issued when OP05-041 was banned, June 2024)',
    'P-077': 'Event Pack Vol.5 / Championship 2024 era',
    'P-078': 'Offline Regional Participation Pack 2024 Vol.3',
    'P-079': 'Offline Regional Participation Pack 2024 Vol.3',
    'P-081': 'V-Jump magazine (Dracule Mihawk)',
    'P-082': 'Store 2-on-2 Battle 2025 participation',
    'P-083': 'Store 2-on-2 Battle 2025 era',
    'P-085': 'Tournament Pack 2025 Vol.1 era',
    'P-088': 'V-Jump magazine (Trafalgar Law)',
    'P-089': 'Offline Regional Participation Pack 2025 Vol.2',
    'P-090': 'Event Pack Vol.7 era',
    'P-091': 'Event Pack Vol.7',
    'P-092': 'Treasure Campaign Pack era',
    'P-093': 'Treasure Campaign Pack era',
    'P-096': 'Promotion Card Set 2025',
    'P-097': 'Promotion Card Set 2025',
    'P-098': 'Promotion Card Set 2025 era',
    'P-099': 'Premium Card Collection -Best Selection Vol.5- era',
    'P-100': 'Premium Card Collection -Best Selection Vol.5- era',
    'P-101': 'English Version 1st Anniversary Set',
    'P-102': 'English Version 1st Anniversary Set',
    'P-103': 'English Version 1st Anniversary Set',
    'P-104': 'English Version 1st Anniversary Set',
    'P-105': 'English Version 1st Anniversary Set',
    'P-106': 'English Version 1st Anniversary Set',
    'P-107': 'Premium Card Collection -Best Selection Vol.5- era',
    'P-111': 'Regional Participation Pack 2026 Vol.1 era',
    'P-112': 'Regional Participation Pack 2026 Vol.1 era',
    'P-113': 'Regional Participation Pack 2026 Vol.1 era',
    'P-117': 'Block 4 promo (Nami OP03-040 errata replacement, distributed at Regionals 25-26)',
}


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        SELECT cpa.id, c.canonical_code, cpa.set_code, cpa.appearance_type, cpa.notes
        FROM card_product_appearances cpa
        JOIN cards c ON cpa.card_id = c.id
        WHERE cpa.appearance_type = 'promo' AND cpa.set_code = 'P'
          AND c.canonical_code LIKE 'P-%'
        ORDER BY c.canonical_code
    """)
    promo_rows = cur.fetchall()
    print(f"Total promo rows with set_code='P': {len(promo_rows)}")

    unique_codes = sorted(set(r[1] for r in promo_rows))
    print(f"Unique P-### codes: {len(unique_codes)}")

    updates = []
    unresolved_codes = []
    for code in unique_codes:
        if code in DISTRIBUTION_MAP:
            source = DISTRIBUTION_MAP[code]
            row_ids = [r[0] for r in promo_rows if r[1] == code]
            for rid in row_ids:
                updates.append((f'Phase D — distribution: {source}', rid))
        else:
            unresolved_codes.append(code)

    print(f"\nCodes with confirmed distribution: {len(unique_codes) - len(unresolved_codes)}")
    print(f"Codes unresolved: {len(unresolved_codes)}")
    print(f"Rows to update: {len(updates)}")

    if updates:
        try:
            cur.execute("BEGIN")
            for notes_val, rid in updates:
                cur.execute(
                    "UPDATE card_product_appearances SET notes = ? WHERE id = ?",
                    (notes_val, rid),
                )
            cur.execute("COMMIT")
            print(f"Committed {len(updates)} updates.")
        except Exception as e:
            cur.execute("ROLLBACK")
            print(f"ROLLBACK: {e}")
            con.close()
            sys.exit(1)

    # === VERIFICATION ===
    print(f"\n{'='*60}")
    print("VERIFICATION")
    print(f"{'='*60}")

    cur.execute("""
        SELECT COUNT(*) FROM card_product_appearances
        WHERE appearance_type = 'promo' AND notes LIKE 'Phase D%'
    """)
    updated_count = cur.fetchone()[0]
    print(f"\nRows with Phase D notes: {updated_count}")

    cur.execute("""
        SELECT cpa.id, c.canonical_code, cpa.notes
        FROM card_product_appearances cpa
        JOIN cards c ON cpa.card_id = c.id
        WHERE cpa.appearance_type = 'promo' AND cpa.notes LIKE 'Phase D%'
        ORDER BY c.canonical_code
        LIMIT 10
    """)
    print("\nSample updated rows:")
    for row in cur.fetchall():
        print(f"  id={row[0]} {row[1]}: {row[2]}")

    cur.execute("""
        SELECT COUNT(*) FROM card_product_appearances
        WHERE appearance_type = 'promo' AND set_code = 'P'
          AND (notes IS NULL OR notes NOT LIKE 'Phase D%')
    """)
    remaining = cur.fetchone()[0]
    print(f"\nUnresolved promo rows (set_code=P, no Phase D note): {remaining}")

    if unresolved_codes:
        print(f"\nUnresolved card codes ({len(unresolved_codes)}):")
        for c in unresolved_codes:
            print(f"  {c}")

    con.close()

    print(f"\n=== SUMMARY ===")
    print(f"  Unique P-### codes in DB: {len(unique_codes)}")
    print(f"  Codes with confirmed source: {len(unique_codes) - len(unresolved_codes)}")
    print(f"  Rows updated: {len(updates)}")
    print(f"  Codes unresolved: {len(unresolved_codes)}")


if __name__ == '__main__':
    run()
