import sqlite3

conn = sqlite3.connect("data/card_catalog.db")
conn.execute("PRAGMA foreign_keys = ON")

cur = conn.execute(
    "DELETE FROM cards WHERE canonical_code IN ('P-038', 'P-040')"
)
deleted = cur.rowcount
conn.commit()

cur = conn.execute(
    "SELECT COUNT(*) FROM cards WHERE canonical_code IN ('P-038', 'P-040')"
)
remaining = cur.fetchone()[0]

cur = conn.execute("SELECT COUNT(*) FROM cards")
total = cur.fetchone()[0]

print("Rows deleted:", deleted)
print("Remaining (should be 0):", remaining)
print("Total cards now:", total)
conn.close()
