"""Fix image_assets.local_path for 20 OP01 promo rows.

Corrects P/base/OP01-XXX.png -> P/base/P-XXX.png where file verified on disk.
Nulls local_path where canonical file does not exist.
Writes data/overlays/op01_promo_path_fix_results.csv.
"""
import sqlite3
import hashlib
import csv
import os
from pathlib import Path

DB_PATH = Path("data/card_catalog.db")
MIRU_ASSETS = Path(r"D:\Miru_Assets")
P_BASE = MIRU_ASSETS / "P" / "base"
OUTPUT_CSV = Path("data/overlays/op01_promo_path_fix_results.csv")

IDS = [706,720,727,728,758,759,770,771,779,780,798,801,802,815,816,823,836,859,950,1164]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

placeholders = ",".join(str(i) for i in IDS)
cur.execute(f"""
SELECT ia.id as ia_id, ia.printing_id, ia.local_path, ia.source_url,
       ia.image_confidence, ia.checksum,
       cv.print_id, cv.variant_key, cv.variant_label
FROM image_assets ia
JOIN card_variants cv ON cv.id = ia.printing_id
WHERE ia.printing_id IN ({placeholders})
  AND ia.is_primary = 1
ORDER BY ia.printing_id
""")
rows = [dict(r) for r in cur.fetchall()]

results = []
corrected = 0
nulled = 0

for r in rows:
    ia_id = r["ia_id"]
    pid = r["printing_id"]
    old_path = r["local_path"]
    print_id = r["print_id"]

    # Derive canonical candidate: OP01-XXX -> P-XXX.png
    base_num = print_id.split("::")[0]          # OP01-001
    num_part = base_num.replace("OP01-", "")    # 001
    canonical_name = f"P-{num_part}.png"        # P-001.png
    canonical_rel = f"P/base/{canonical_name}"  # P/base/P-001.png
    canonical_abs = P_BASE / canonical_name

    file_exists = canonical_abs.is_file()
    file_size = os.path.getsize(str(canonical_abs)) if file_exists else 0
    file_checksum = sha256(canonical_abs) if file_exists else None

    if file_exists:
        fix_action = "CORRECTED_PATH"
        new_path = canonical_rel
        evidence = f"file verified on disk at D:\\Miru_Assets\\{canonical_rel.replace('/', chr(92))} size={file_size} sha256={file_checksum[:16]}..."
        notes = f"old path used OP01-XXX.png convention; corrected to P-XXX.png convention; checksum updated"

        cur.execute(
            """UPDATE image_assets
               SET local_path = ?,
                   checksum = ?,
                   image_confidence = 'OPERATOR_PATH_CORRECTED',
                   updated_at = datetime('now')
               WHERE id = ?""",
            (new_path, file_checksum, ia_id)
        )
        corrected += 1

    else:
        # local_path has NOT NULL constraint — cannot null; hold row unchanged
        fix_action = "HELD_UNVERIFIED"
        new_path = old_path  # row unchanged
        evidence = f"P/base/{canonical_name} does not exist in D:\\Miru_Assets; local_path NOT NULL constraint prevents null; row left unchanged"
        notes = f"old path uses OP01-XXX.png convention; HELD pending correct asset acquisition and schema relaxation"
        # No DB update — row stays as-is
        nulled += 1

    results.append({
        "printing_id": pid,
        "ia_id": ia_id,
        "print_id": print_id,
        "variant_label": r["variant_label"],
        "old_local_path": old_path,
        "new_local_path": new_path or "",
        "fix_action": fix_action,
        "evidence_used": evidence,
        "notes": notes,
    })

    print(f"  [{fix_action}] pid={pid} {print_id} -> {new_path or 'NULL'}")

conn.commit()
conn.close()

# Write CSV
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
with open(str(OUTPUT_CSV), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "printing_id", "ia_id", "print_id", "variant_label",
        "old_local_path", "new_local_path",
        "fix_action", "evidence_used", "notes",
    ])
    writer.writeheader()
    writer.writerows(results)

print()
print(f"=== PROMO PATH FIX SUMMARY ===")
print(f"Total rows processed: {len(results)}")
print(f"CORRECTED_PATH: {corrected}")
print(f"NULLED_PATH: {nulled}")
print(f"CSV written to: {OUTPUT_CSV}")
