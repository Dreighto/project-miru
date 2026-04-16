import json
import time
import urllib.request

BASE = "https://tcgcsv.com/tcgplayer"
UA = "ProjectMiru/1.0 (fan project)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    time.sleep(1)
    return data


OP_CATEGORY_ID = 68
OP01_GROUP_ID = 3188

products = fetch(f"{BASE}/{OP_CATEGORY_ID}/{OP01_GROUP_ID}/products")
print(f"Total products: {products['totalItems']}")
print()

for p in products["results"]:
    ext = {e["name"]: e["value"] for e in p.get("extendedData", [])}
    number = ext.get("Number", "")
    rarity = ext.get("Rarity", "")
    variant = ext.get("Variant", "")
    # Only print cards with a number — skip sealed products
    if number:
        print(f"{number} | {rarity} | {p['productId']} | {p['name']} | variant={variant!r}")
