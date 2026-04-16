import sqlite3

TARGET_CODES = [
    "EB04-009",
    "OP04-016",
    "OP05-037",
    "OP12-016",
    "OP12-017",
    "OP12-018",
    "OP12-019",
    "OP13-076",
]

conn = sqlite3.connect("data/card_catalog.db")
placeholders = ",".join("?" * len(TARGET_CODES))
cur = conn.execute(
    f"UPDATE cards SET cost = 0 WHERE canonical_code IN ({placeholders}) AND card_type = 'Event'",
    TARGET_CODES,
)
updated = cur.rowcount
conn.commit()
print("Rows updated:", updated)

cur = conn.execute(
    f"SELECT canonical_code, card_name, cost FROM cards WHERE canonical_code IN ({placeholders})",
    TARGET_CODES,
)
for row in cur.fetchall():
    print(row)
conn.close()
