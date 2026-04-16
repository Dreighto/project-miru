"""
Read-only recon: manifest + products.json samples -> proposed Bandai-style set_code mapping.
Writes data/tcgcsv/group_set_mapping.json (JSON array only; no DB).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "tcgcsv" / "manifest.json"
PRODUCTS_DIR = ROOT / "data" / "tcgcsv"
OUT_PATH = ROOT / "data" / "tcgcsv" / "group_set_mapping.json"

# Main booster / flagship set names -> official-style OP## (release order reference).
MAIN_SET_TO_OP: dict[str, str] = {
    "Romance Dawn": "OP01",
    "Paramount War": "OP02",
    "Pillars of Strength": "OP03",
    "Kingdoms of Intrigue": "OP04",
    "Awakening of the New Era": "OP05",
    "Wings of the Captain": "OP06",
    "500 Years in the Future": "OP07",
    "Two Legends": "OP08",
    "Emperors in the New World": "OP09",
    "Royal Blood": "OP10",
    "A Fist of Divine Speed": "OP11",
    "Legacy of the Master": "OP12",
    "Carrying On His Will": "OP13",
    "The Azure Sea's Seven": "OP14",
    "Adventure on Kami's Island": "OP15",
}

EXTRA_BOOSTER_TO_EB: dict[str, str] = {
    "Extra Booster: Memorial Collection": "EB01",
    "Extra Booster: Anime 25th Collection": "EB02",
    "Extra Booster: One Piece Heroines Edition": "EB03",
}

SPECIAL_EXACT: dict[str, tuple[str, str]] = {
    # proposed_set_code, confidence
    "Premium Booster -The Best-": ("PRB01", "high"),
    "Premium Booster -The Best- Vol. 2": ("PRB02", "high"),
    "One Piece Promotion Cards": ("P", "high"),
}


def _strip_promo_suffix(group_name: str) -> str | None:
    """If name is a variant of a main set, return base title for lookup."""
    gn = group_name.strip()
    if gn.endswith(" Pre-Release Cards"):
        return gn[: -len(" Pre-Release Cards")].strip()
    if gn.endswith(" Release Event Cards"):
        return gn[: -len(" Release Event Cards")].strip()
    m = re.match(r"^(.+?):\s*\d+(?:st|nd|rd|th)\s+Anniversary Tournament Cards\s*$", gn, re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?):\s*Pre-Release Cards\s*$", gn, re.I)
    if m:
        return m.group(1).strip()
    return None


def propose_set_code(group_name: str) -> tuple[str, str]:
    """
    Return (proposed_set_code, confidence high|low|unknown).
    """
    gn = group_name.strip()

    if gn in SPECIAL_EXACT:
        code, conf = SPECIAL_EXACT[gn]
        return code, conf

    if gn in EXTRA_BOOSTER_TO_EB:
        return EXTRA_BOOSTER_TO_EB[gn], "high"

    if gn in MAIN_SET_TO_OP:
        return MAIN_SET_TO_OP[gn], "high"

    m = re.match(r"^Starter Deck (\d+):\s*", gn)
    if m:
        return f"ST{int(m.group(1)):02d}", "high"

    m = re.match(r"^Super Pre-Release Starter Deck (\d+):\s*", gn)
    if m:
        return f"ST{int(m.group(1)):02d}", "low"

    base = _strip_promo_suffix(gn)
    if base and base in MAIN_SET_TO_OP:
        return MAIN_SET_TO_OP[base], "low"

    if gn == "Starter Deck EX: Gear 5":
        return "STEX", "low"

    if gn.startswith("Ultra Deck:"):
        return "ULTRA", "unknown"

    if gn in (
        "Learn Together Deck Set",
        "One Piece Demo Deck Cards",
        "Revision Pack Cards",
        "One Piece Collection Sets",
    ):
        return "MISC", "unknown"

    return "UNKNOWN", "unknown"


def _extended_kv(product: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in product.get("extendedData") or []:
        if not isinstance(row, dict):
            continue
        k = str(row.get("name") or "").strip()
        v = str(row.get("value") or "").strip()
        if k:
            out[k] = v
    return out


def _code_like_from_product(product: dict) -> list[str]:
    hints: list[str] = []
    name = str(product.get("name") or "")
    clean = str(product.get("cleanName") or "")
    for blob in (name, clean):
        hints += re.findall(r"\bOP\d{2}\b", blob, flags=re.I)
        hints += re.findall(r"\bST\d{2}\b", blob, flags=re.I)
        hints += re.findall(r"\bEB\d{2}\b", blob, flags=re.I)
        hints += re.findall(r"\bPRB\d{2}\b", blob, flags=re.I)
        hints += re.findall(r"\(\d{3}\)", blob)
    ex = _extended_kv(product)
    for key in ("Number", "Card Number", "Rarity"):
        if key in ex and ex[key]:
            hints.append(f"{key}={ex[key][:40]}")
    # de-dup preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for h in hints:
        u = h.upper() if h.startswith("OP") or h.startswith("ST") else h
        if u not in seen:
            seen.add(u)
            uniq.append(h)
    return uniq[:8]


def sample_products(group_id: int) -> tuple[list[dict], str | None]:
    path = PRODUCTS_DIR / str(group_id) / "products.json"
    if not path.is_file():
        return [], f"missing file: {path.relative_to(ROOT)}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], f"read/parse error: {e}"
    results = data.get("results")
    if not isinstance(results, list) or len(results) == 0:
        return [], "empty or invalid results[]"
    return results[:3], None


def print_product_sample(group_id: int, group_name: str, products: list[dict]) -> None:
    print(f"  --- samples group {group_id} ({group_name}) ---")
    for p in products:
        pid = p.get("productId")
        name = p.get("name")
        clean = p.get("cleanName")
        gid = p.get("groupId")
        hints = _code_like_from_product(p)
        print(f"    productId={pid} groupId={gid}")
        print(f"    name={name!r}")
        if clean:
            print(f"    cleanName={clean!r}")
        if hints:
            print(f"    code_hints={hints}")


def main() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"FAILED: manifest not found: {MANIFEST_PATH}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    groups = manifest.get("groups") or []
    if not isinstance(groups, list):
        print("FAILED: manifest.groups is not a list", file=sys.stderr)
        return 1

    total_manifest = len(groups)
    mapping: list[dict] = []
    missing_products: list[str] = []

    print("=" * 100)
    print("FULL MAPPING TABLE (group_id | proposed_set_code | conf | group_name)")
    print("=" * 100)

    for g in groups:
        gid = int(g.get("group_id") or 0)
        gname = str(g.get("group_name") or "").strip()
        proposed, conf = propose_set_code(gname)

        prods, err = sample_products(gid)
        names = [str(p.get("name") or "") for p in prods]
        if err:
            missing_products.append(f"{gid} {gname!r}: {err}")
            # Missing/empty products.json needs operator review
            if conf == "high":
                conf = "low"

        mapping.append(
            {
                "group_id": gid,
                "group_name": gname,
                "proposed_set_code": proposed,
                "confidence": conf,
                "sample_product_names": names,
            }
        )

        flag = f" [{err}]" if err else ""
        print(f"{gid:6d} | {proposed:12s} | {conf:8s} | {gname}{flag}")

        if prods:
            print_product_sample(gid, gname, prods)

    high_n = sum(1 for m in mapping if m["confidence"] == "high")
    low_n = sum(1 for m in mapping if m["confidence"] == "low")
    unk_n = sum(1 for m in mapping if m["confidence"] == "unknown")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Total groups in manifest: {total_manifest}")
    print(f"High confidence mapping: {high_n}")
    print(f"Low confidence (operator review): {low_n}")
    print(f"Unknown: {unk_n}")
    print(f"Rows written to {OUT_PATH.relative_to(ROOT)}: {len(mapping)}")
    if missing_products:
        print("Groups with missing/empty products.json:")
        for line in missing_products:
            print(f"  - {line}")
    print(f"Verified on disk: {OUT_PATH.is_file()}  size={OUT_PATH.stat().st_size} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
