import json
import urllib.request

BASE = "https://tcgcsv.com/tcgplayer"
UA = "Mozilla/5.0"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# STEP 1 — Find One Piece category ID
print("=== STEP 1: Categories ===")
cats = fetch(f"{BASE}/categories")
for c in cats["results"]:
    if "piece" in c["name"].lower() or "piece" in c["displayName"].lower():
        print(f"categoryId: {c['categoryId']} | name: {c['name']} | display: {c['displayName']}")

# STEP 2 — Get One Piece groups (sets)
# Replace 68 below with actual categoryId from Step 1 if different
OP_CATEGORY_ID = None
for c in cats["results"]:
    if "piece" in c["name"].lower() or "piece" in c["displayName"].lower():
        OP_CATEGORY_ID = c["categoryId"]
        break

if not OP_CATEGORY_ID:
    print("ERROR: Could not find One Piece category")
    raise SystemExit(1)

print(f"\n=== STEP 2: Groups for categoryId {OP_CATEGORY_ID} ===")
groups = fetch(f"{BASE}/{OP_CATEGORY_ID}/groups")
print(f"Total groups: {groups['totalItems']}")
for g in groups["results"]:
    print(f"  groupId: {g['groupId']} | name: {g['name']} | abbrev: {g.get('abbreviation', '')}")

# STEP 3 — Find OP01 group ID
op01_group_id = None
for g in groups["results"]:
    if "OP-01" in g["name"] or "Romance Dawn" in g["name"] or g.get("abbreviation", "") == "OP01":
        op01_group_id = g["groupId"]
        print(f"\nOP01 group found: groupId={op01_group_id} name={g['name']}")
        break

if not op01_group_id:
    print("ERROR: Could not find OP01 group")
    raise SystemExit(1)

# STEP 4 — Get OP01 products (first 20 only for review)
print(f"\n=== STEP 4: First 20 products in OP01 (groupId {op01_group_id}) ===")
products = fetch(f"{BASE}/{OP_CATEGORY_ID}/{op01_group_id}/products")
print(f"Total products: {products['totalItems']}")
for p in products["results"][:20]:
    ext = {e["name"]: e["value"] for e in p.get("extendedData", [])}
    print(
        f"  productId: {p['productId']} | name: {p['name']} | number: {ext.get('Number', '')} | "
        f"rarity: {ext.get('Rarity', '')} | variant: {ext.get('Variant', '')}"
    )
