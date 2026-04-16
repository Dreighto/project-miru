"""Create card_legality_overrides, seed overrides, and populate format_set_legality."""

from __future__ import annotations

import sqlite3
import sys

DB_PATH = r"D:\dev\tcg-watcher-worktree\data\card_catalog.db"

CREATE_OVERRIDES_SQL = """
CREATE TABLE IF NOT EXISTS card_legality_overrides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_code       TEXT NOT NULL,
    exemption_type  TEXT NOT NULL,
    format_name     TEXT NOT NULL DEFAULT 'standard',
    region          TEXT NOT NULL DEFAULT 'EN',
    legal_from      TEXT NOT NULL,
    legal_until     TEXT,
    source_note     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE (card_code, format_name, region)
);
"""

permanent_spr_cards = [
    "EB01-006",
    "EB02-061",
    "OP01-016",
    "OP01-120",
    "OP02-013",
    "OP03-122",
    "OP04-083",
    "OP05-069",
    "OP05-074",
    "OP05-119",
    "OP06-118",
    "OP06-119",
    "OP07-051",
    "OP08-118",
    "OP09-004",
    "OP09-051",
    "OP09-093",
    "OP09-118",
    "OP09-119",
    "OP10-119",
    "OP11-118",
    "OP12-118",
    "OP13-118",
    "OP13-119",
    "OP13-120",
    "OP14-119",
    "OP15-118",
]

grandfathered_block4_cards = [
    "OP01-039",
    "OP01-055",
    "OP02-005",
    "OP02-068",
    "OP03-008",
    "OP03-044",
    "OP03-048",
    "OP03-072",
    "OP03-097",
    "OP04-016",
    "OP04-077",
    "OP04-096",
    "ST01-011",
    "ST02-007",
    "ST06-008",
]

standard_rows = [
    ("standard", "OP01", "EN", "2022-12-02", "2026-03-31", "Block 1"),
    ("standard", "OP02", "EN", "2023-03-10", "2026-03-31", "Block 1"),
    ("standard", "OP03", "EN", "2023-06-16", "2026-03-31", "Block 1"),
    ("standard", "OP04", "EN", "2023-09-22", "2026-03-31", "Block 1"),
    ("standard", "EB01", "EN", "2024-05-03", "2026-03-31", "Block 1"),
    ("standard", "EB02", "EN", "2025-05-16", "2026-03-31", "Block 1"),
    ("standard", "ST01", "EN", "2022-12-02", "2026-03-31", "Block 1"),
    ("standard", "ST02", "EN", "2022-12-02", "2026-03-31", "Block 1"),
    ("standard", "ST03", "EN", "2022-12-02", "2026-03-31", "Block 1"),
    ("standard", "ST04", "EN", "2022-12-02", "2026-03-31", "Block 1"),
    ("standard", "ST05", "EN", "2023-03-10", "2026-03-31", "Block 1"),
    ("standard", "ST06", "EN", "2023-03-10", "2026-03-31", "Block 1"),
    ("standard", "ST07", "EN", "2023-06-16", "2026-03-31", "Block 1"),
    ("standard", "ST08", "EN", "2023-09-22", "2026-03-31", "Block 1"),
    ("standard", "ST09", "EN", "2023-09-22", "2026-03-31", "Block 1"),
    ("standard", "OP05", "EN", "2023-12-01", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "OP06", "EN", "2024-03-08", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "OP07", "EN", "2024-06-28", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "OP08", "EN", "2024-09-27", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "ST10", "EN", "2023-12-01", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "ST11", "EN", "2023-12-01", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "ST12", "EN", "2024-03-08", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "ST13", "EN", "2024-06-28", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "ST14", "EN", "2024-06-28", "2027-03-31", "Block 2, projected rotation"),
    ("standard", "OP09", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "OP10", "EN", "2025-01-24", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "OP11", "EN", "2025-05-02", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "OP12", "EN", "2025-08-08", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "EB03", "EN", "2026-02-20", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST15", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST16", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST17", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST18", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST19", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST20", "EN", "2024-10-25", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST21", "EN", "2025-03-14", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "ST22", "EN", "2025-09-05", "2028-03-31", "Block 3, projected rotation"),
    ("standard", "OP13", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "OP14", "EN", "2026-01-16", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "OP15", "EN", "2026-04-03", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "PRB02", "EN", "2025-10-24", "2029-03-31", "Block 4, all cards updated to Block 4"),
    ("standard", "ST23", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST24", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST25", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST26", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST27", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST28", "EN", "2025-11-07", "2029-03-31", "Block 4, confirmed legal_until"),
    ("standard", "ST29", "EN", "2026-01-16", "2029-03-31", "Block 4, confirmed legal_until"),
]


def run() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    cur.executescript(CREATE_OVERRIDES_SQL)

    ins_spr = ins_gf = ins_mr = 0
    ign_spr = ign_gf = ign_mr = 0

    for code in permanent_spr_cards:
        cur.execute(
            """
            INSERT OR IGNORE INTO card_legality_overrides
            (card_code, exemption_type, format_name, region,
             legal_from, legal_until, source_note)
            VALUES (?, 'permanent_spr', 'standard', 'EN',
                    '2026-04-01', NULL,
                    'Bandai blockicon-card page, confirmed 2026-03-13')
            """,
            (code,),
        )
        if cur.rowcount == 1:
            ins_spr += 1
        else:
            ign_spr += 1

    for code in grandfathered_block4_cards:
        cur.execute(
            """
            INSERT OR IGNORE INTO card_legality_overrides
            (card_code, exemption_type, format_name, region,
             legal_from, legal_until, source_note)
            VALUES (?, 'grandfathered_block4', 'standard', 'EN',
                    '2026-04-01', '2029-03-31',
                    'Bandai blockicon-card page, Block 4 update confirmed 2026-03-13')
            """,
            (code,),
        )
        if cur.rowcount == 1:
            ins_gf += 1
        else:
            ign_gf += 1

    cur.execute(
        """
        SELECT DISTINCT c.canonical_code
        FROM card_variants cv
        JOIN cards c ON cv.card_id = c.id
        WHERE cv.is_manga_rare = 1
        ORDER BY c.canonical_code
        """
    )
    manga_rare_codes = [row[0] for row in cur.fetchall()]
    print(f"Found {len(manga_rare_codes)} Manga Rare card codes")

    for code in manga_rare_codes:
        cur.execute(
            """
            INSERT OR IGNORE INTO card_legality_overrides
            (card_code, exemption_type, format_name, region,
             legal_from, legal_until, source_note)
            VALUES (?, 'manga_rare', 'standard', 'EN',
                    '2026-04-01', NULL,
                    'Bandai Block X announcement July 2025 — all Manga Rares permanent')
            """,
            (code,),
        )
        if cur.rowcount == 1:
            ins_mr += 1
        else:
            ign_mr += 1

    con.commit()
    print(f"permanent_spr inserted: {ins_spr} (ignored duplicates: {ign_spr})")
    print(f"grandfathered_block4 inserted: {ins_gf} (ignored duplicates: {ign_gf})")
    print(f"manga_rare inserted: {ins_mr} (ignored duplicates: {ign_mr})")

    ins_std = ign_std = 0
    ins_ext = ign_ext = 0

    for row in standard_rows:
        cur.execute(
            """
            INSERT OR IGNORE INTO format_set_legality
            (format_name, set_code, region, legal_from, legal_until,
             source_key, notes)
            VALUES (?, ?, ?, ?, ?, 'bandai_official', ?)
            """,
            row,
        )
        if cur.rowcount == 1:
            ins_std += 1
        else:
            ign_std += 1

    extra_set_codes = [r[1] for r in standard_rows]
    for set_code in extra_set_codes:
        # Schema has legal_until NOT NULL; use '' for open-ended (no rotation end).
        cur.execute(
            """
            INSERT OR IGNORE INTO format_set_legality
            (format_name, set_code, region, legal_from, legal_until,
             source_key, notes)
            VALUES ('extra', ?, 'EN', '2026-04-01', '',
                    'bandai_official',
                    'Extra Regulation — all blocks legal, official Bandai format')
            """,
            (set_code,),
        )
        if cur.rowcount == 1:
            ins_ext += 1
        else:
            ign_ext += 1

    con.commit()
    print(
        f"format_set_legality rows inserted: standard={ins_std} (ignored {ign_std}), "
        f"extra={ins_ext} (ignored {ign_ext})"
    )
    print(f"standard_rows count: {len(standard_rows)}, extra_set_codes count: {len(extra_set_codes)}")

    con.close()


if __name__ == "__main__":
    run()
