import sqlite3

conn = sqlite3.connect(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
rows = conn.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
for name, sql in rows:
    print(f"\n--- {name} ---")
    print(sql)
conn.close()
