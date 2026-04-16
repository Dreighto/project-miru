import sqlite3
import sys

DB_PATH = r'D:\dev\tcg-watcher-worktree\data\card_catalog.db'

KNOWN_SET_CODES = {
    'OP01','OP02','OP03','OP04','OP05','OP06','OP07','OP08',
    'OP09','OP10','OP11','OP12','OP13','OP14','OP15',
    'EB01','EB02','EB03','EB04',
    'ST01','ST02','ST03','ST04','ST05','ST06','ST07','ST08',
    'ST09','ST10','ST11','ST12','ST13','ST14','ST15','ST16',
    'ST17','ST18','ST19','ST20','ST21','ST22','ST23','ST24',
    'ST25','ST26','ST27','ST28','ST29',
    'P','PRB01','PRB02'
}

def get_code_prefix(canonical_code):
    if '-' not in canonical_code:
        return canonical_code
    return canonical_code.split('-')[0]

def get_appearance_type(is_promo, is_base, variant_key,
                        release_set_code, code_prefix):
    if is_promo:
        return 'promo'
    if is_base and release_set_code == code_prefix:
        return 'original'
    if is_base and release_set_code != code_prefix:
        return 'reprint'
    if variant_key and variant_key.startswith('r'):
        return 'reprint'
    return 'reprint'

def run():
    sys.stdout.reconfigure(encoding='utf-8')

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM card_product_appearances")
    existing = cur.fetchone()[0]
    if existing > 0:
        print(f"ERROR: card_product_appearances already has {existing} rows. Stopping.")
        con.close()
        sys.exit(1)

    cur.execute("""
        SELECT
            cv.id,
            cv.card_id,
            c.canonical_code,
            cv.variant_key,
            cv.release_set_code,
            cv.is_base,
            cv.is_promo,
            cv.is_sp,
            cv.is_tr,
            cv.is_alt,
            cv.is_manga_rare
        FROM card_variants cv
        JOIN cards c ON cv.card_id = c.id
        ORDER BY cv.id
    """)
    variants = cur.fetchall()

    inserted = 0
    skipped_no_set = 0
    type_counts = {}
    anomalous_set_codes = set()

    for row in variants:
        (printing_id, card_id, canonical_code, variant_key,
         release_set_code, is_base, is_promo, is_sp, is_tr,
         is_alt, is_manga_rare) = row

        if not release_set_code or not release_set_code.strip():
            skipped_no_set += 1
            continue

        code_prefix = get_code_prefix(canonical_code)
        appearance_type = get_appearance_type(
            is_promo, is_base, variant_key,
            release_set_code, code_prefix
        )

        if release_set_code not in KNOWN_SET_CODES:
            anomalous_set_codes.add(release_set_code)

        try:
            cur.execute("""
                INSERT OR IGNORE INTO card_product_appearances
                    (card_id, set_code, printing_id,
                     appearance_type, source_key, notes)
                VALUES (?, ?, ?, ?, 'bandai_official',
                        'Phase A - derived from card_variants.release_set_code')
            """, (card_id, release_set_code, printing_id, appearance_type))
            if cur.rowcount == 1:
                inserted += 1
                type_counts[appearance_type] = type_counts.get(appearance_type, 0) + 1
        except Exception as e:
            print(f"ERROR on printing_id={printing_id} card={canonical_code}: {e}")

    con.commit()
    con.close()

    print(f"\n=== card_product_appearances Phase A complete ===")
    print(f"card_variants processed: {len(variants)}")
    print(f"Skipped (no release_set_code): {skipped_no_set}")
    print(f"Rows inserted: {inserted}")
    print(f"\nBreakdown by appearance_type:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {count}")
    if anomalous_set_codes:
        print(f"\nAnomalous set codes (not in known list):")
        for code in sorted(anomalous_set_codes):
            print(f"  {code}")

if __name__ == '__main__':
    run()
