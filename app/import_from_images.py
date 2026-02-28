import os
import re
import time
import requests

IMAGES_ROOT = os.getenv("IMAGES_ROOT", "/images")
IMPORT_WEBHOOK_URL = os.getenv("IMPORT_WEBHOOK_URL", "").strip()
IMPORT_WEBHOOK_TOKEN = os.getenv("IMPORT_WEBHOOK_TOKEN", "").strip()

CARD_ID_RE = re.compile(r"((?:[A-Z]{1,4}\d{2}-\d{3})|(?:P-\d{3}))", re.I)

def scan_images_for_card_ids(images_root: str) -> list[str]:
    found = set()
    if not os.path.isdir(images_root):
        print(f"[import] IMAGES_ROOT not found: {images_root}")
        return []

    for root, _, files in os.walk(images_root):
        for fn in files:
            base, ext = os.path.splitext(fn)
            if ext.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue

            m = CARD_ID_RE.search(base)
            if not m:
                continue

            found.add(m.group(1).upper())

    return sorted(found)

def main():
    if not IMPORT_WEBHOOK_URL:
        raise SystemExit("[import] IMPORT_WEBHOOK_URL not set")
    if not IMPORT_WEBHOOK_TOKEN:
        raise SystemExit("[import] IMPORT_WEBHOOK_TOKEN not set")

    card_ids = scan_images_for_card_ids(IMAGES_ROOT)
    print(f"[import] Found {len(card_ids)} card IDs in images.")

    if not card_ids:
        print("[import] Nothing to send. Done.")
        return

    payload = {
        "token": IMPORT_WEBHOOK_TOKEN,
        "card_ids": card_ids,
        "ts": int(time.time()),
    }

    r = requests.post(IMPORT_WEBHOOK_URL, json=payload, timeout=30)
    print("[import] HTTP", r.status_code)
    try:
        print("[import] Response:", r.json())
    except Exception:
        print("[import] Response text:", (r.text or "")[:500])

if __name__ == "__main__":
    main()