import sqlite3
import os

db_path = os.path.join("data", "card_catalog.db")
print(f"Looking for DB at: {os.path.abspath(db_path)}")

conn = sqlite3.connect(db_path)
rows = conn.execute(
    'SELECT canonical_code, card_name FROM cards WHERE variant_subtype = "sp" LIMIT 20'
).fetchall()
for r in rows:
    print(r)
