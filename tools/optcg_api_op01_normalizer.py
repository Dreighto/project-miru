#!/usr/bin/env python3
"""
optcg_api_op01_normalizer.py -- Read-only discovery pass

Pulls OP01 card data from the OPTCG API, normalizes each printing against
Miru's locked variant-treatment taxonomy, cross-references local
card_variants + image_assets (READ-ONLY), and writes a discovery CSV
showing where the API agrees, conflicts, or extends our local coverage.

NO DB WRITES.  NO IMAGE FETCHES.  NO PM CHANGES.  NO SERVICE RESTARTS.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Windows console encoding fix ──────────────────────────────────────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# ── Paths (all derived from repo root) ────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
RAW_DIR = ROOT / "data" / "api_exploration" / "optcg_api_op01"
ERROR_CSV_PATH = ROOT / "data" / "api_exploration" / "optcg_api_op01_errors.csv"
OUTPUT_CSV_PATH = ROOT / "data" / "overlays" / "optcg_api_op01_normalized.csv"

# ── API constants ─────────────────────────────────────────────────────────
API_BASE = "https://optcgapi.com/api"
USER_AGENT = "ProjectMiru/1.0 (OP01-normalizer; read-only discovery; 1s pacing)"
HTTP_TIMEOUT = 30
PACING_SEC = 1.0
MAX_RETRIES = 2

# ── Locked suffix taxonomy ────────────────────────────────��───────────────
PARALLEL_RE = re.compile(r"^_p(\d+)$")
REPRINT_RE = re.compile(r"^_r(\d+)$")

# ── Output CSV column order ──────────────────────────────────��────────────
CSV_COLUMNS = [
    "card_id",
    "card_image_id",
    "set_name",
    "set_id",
    "card_name",
    "rarity",
    "inferred_treatment",
    "inferred_treatment_confidence",
    "card_image_url",
    "local_canonical_code_match",
    "local_variant_match_id",
    "local_image_asset_exists",
    "gap_status",
    "raw_json_path",
]


# ── Utility functions ─────────────────────────────────────────────────────
def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str) -> tuple[int, str]:
    """GET request. Returns (status_code, body_text).
    On network failure returns (0, error_message)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        return e.code, body
    except (URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def http_get_retry(url: str) -> tuple[int, str]:
    """GET with up to MAX_RETRIES retries on failure."""
    for attempt in range(MAX_RETRIES + 1):
        status, body = http_get(url)
        if status == 200:
            return status, body
        if attempt < MAX_RETRIES:
            time.sleep(PACING_SEC)
    return status, body


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ── Taxonomy decoder ─────────────────────────────────────────────────────
def infer_treatment(card_set_id: str, card_image_id: str) -> tuple[str, str]:
    """Apply the locked suffix taxonomy to card_image_id.
    Returns (treatment, confidence)."""
    if not card_image_id or not card_set_id:
        return "SUFFIX_UNKNOWN", "UNKNOWN"
    if not card_image_id.startswith(card_set_id):
        return "SUFFIX_UNKNOWN", "UNKNOWN"

    suffix = card_image_id[len(card_set_id) :]

    if suffix == "":
        return "base", "HIGH"
    if PARALLEL_RE.match(suffix):
        return "parallel", "HIGH"
    if REPRINT_RE.match(suffix):
        return "reprint", "HIGH"
    return "SUFFIX_UNKNOWN", "UNKNOWN"


def extract_suffix(card_set_id: str, card_image_id: str) -> str:
    """Return the raw suffix string (including leading underscore) or ''."""
    if card_image_id and card_image_id.startswith(card_set_id):
        return card_image_id[len(card_set_id) :]
    return card_image_id or ""


def clean_api_name(name: str) -> str:
    """Strip OPTCG API parenthetical suffixes for name comparison.
    'Roronoa Zoro (001)' -> 'Roronoa Zoro'
    'Izo (OP01-033) (Full Art)' -> 'Izo'
    """
    out = str(name or "").strip()
    while True:
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", out).strip()
        if stripped == out:
            break
        out = stripped
    return out


# ── Local DB loader (READ-ONLY) ──────────────────────────��───────────────
def load_local_op01(
    db_path: Path,
) -> tuple[
    dict[str, dict],
    dict[str, list[dict]],
    set[int],
]:
    """Load OP01 cards, card_variants, and image_asset printing_ids.

    Returns:
        cards_by_code    {canonical_code: {id, card_name, set_name, ...}}
        variants_by_code {canonical_code: [list of variant dicts]}
        ia_printing_ids  set of card_variant ids that have image_assets rows
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # -- cards --
    cur.execute(
        "SELECT id, canonical_code, card_name, set_name, set_code, rarity "
        "FROM cards WHERE canonical_code LIKE 'OP01-%' ORDER BY canonical_code"
    )
    cards_by_code: dict[str, dict] = {}
    card_id_to_code: dict[int, str] = {}
    all_card_ids: list[int] = []
    for row in cur.fetchall():
        d = dict(row)
        code = d["canonical_code"]
        cards_by_code[code] = d
        card_id_to_code[d["id"]] = code
        all_card_ids.append(d["id"])

    # -- card_variants --
    variants_by_code: dict[str, list[dict]] = defaultdict(list)
    all_cv_ids: list[int] = []
    if all_card_ids:
        ph = ",".join("?" * len(all_card_ids))
        cur.execute(
            f"SELECT id, card_id, variant_key, variant_label, print_id, "
            f"release_set_code, release_set_name, image_url, "
            f"is_base, is_alt, is_sp, is_tr, is_manga_rare, "
            f"is_golden_manga_rare, is_promo, is_serialized, "
            f"is_illustration_rare "
            f"FROM card_variants WHERE card_id IN ({ph}) "
            f"ORDER BY card_id, variant_key",
            all_card_ids,
        )
        for row in cur.fetchall():
            d = dict(row)
            code = card_id_to_code.get(d["card_id"])
            if code:
                variants_by_code[code].append(d)
                all_cv_ids.append(d["id"])

    # -- image_assets --
    ia_printing_ids: set[int] = set()
    for i in range(0, len(all_cv_ids), 500):
        batch = all_cv_ids[i : i + 500]
        ph = ",".join("?" * len(batch))
        cur.execute(
            f"SELECT DISTINCT printing_id FROM image_assets "
            f"WHERE printing_id IN ({ph})",
            batch,
        )
        for row in cur.fetchall():
            ia_printing_ids.add(row[0])

    conn.close()
    return cards_by_code, dict(variants_by_code), ia_printing_ids


# ── Variant matcher ───────────────────────────────────────────────────���───
def find_local_match(
    local_variants: list[dict],
    treatment: str,
    card_image_id: str,
) -> int | None:
    """Return the card_variants.id of the best local match, or None.

    Strategy:
      1. Exact print_id match (strongest signal).
      2. Category-level fallback per the locked taxonomy spec.
    """
    # 1. Exact print_id match
    for cv in local_variants:
        if cv["print_id"] == card_image_id:
            return cv["id"]

    # 2. Category fallback
    if treatment == "base":
        for cv in local_variants:
            if cv["is_base"] == 1:
                return cv["id"]
    elif treatment == "parallel":
        for cv in local_variants:
            vk = str(cv["variant_key"] or "")
            if vk.startswith("p") or vk == "parallel":
                return cv["id"]
    elif treatment == "reprint":
        for cv in local_variants:
            vk = str(cv["variant_key"] or "")
            if re.match(r"^r\d", vk):
                return cv["id"]

    return None


# ── Gap-status classifier ───────────────────────────────���────────────────
def classify_gap(
    treatment: str,
    confidence: str,
    local_code_exists: bool,
    local_cv_id: int | None,
    has_image_asset: bool,
    name_mismatch: bool,
) -> str:
    if confidence == "UNKNOWN":
        return "SUFFIX_UNKNOWN"
    if name_mismatch:
        return "OPTCG_BANDAI_MISMATCH"
    if local_cv_id is None:
        return "NEW_PRINTING_NOT_IN_LOCAL"
    if not has_image_asset:
        return "LOCAL_HAS_NO_IMAGE_ASSET"
    return "MATCHED"


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("OPTCG API OP01 NORMALIZER -- Read-Only Discovery Pass")
    print("=" * 70)
    print(f"  Timestamp : {ts()}")
    print(f"  DB        : {DB_PATH}")
    print(f"  Raw dir   : {RAW_DIR}")
    print(f"  Output CSV: {OUTPUT_CSV_PATH}")
    print()

    # ================================================================
    # STEP 1 -- Endpoint discovery
    # ================================================================
    print("STEP 1 -- Endpoint Discovery")
    print("-" * 50)

    set_wide_mode: str | None = None
    set_wide_count = 0
    for fmt in ("OP-01", "OP01"):
        url = f"{API_BASE}/sets/{fmt}/"
        time.sleep(PACING_SEC)
        status, body = http_get(url)
        valid = False
        count = 0
        try:
            data = json.loads(body)
            if isinstance(data, list):
                valid = len(data) > 0
                count = len(data)
        except (json.JSONDecodeError, TypeError):
            pass
        tag = f"HTTP {status}, valid={'YES' if valid else 'NO'}"
        if valid:
            tag += f", {count} rows"
        print(f"  GET /api/sets/{fmt}/  =>  {tag}")
        if valid and set_wide_mode is None:
            set_wide_mode = fmt
            set_wide_count = count

    if set_wide_mode:
        print(f"  >> Set-wide works with '{set_wide_mode}' ({set_wide_count} rows)")
    else:
        print("  >> No set-wide endpoint returned valid data")
    print(f"  >> Using per-card mode for full cross-set printing discovery")
    print()

    # ================================================================
    # Load local DB (READ-ONLY)
    # ================================================================
    print("Loading card_catalog.db (READ-ONLY, file:...?mode=ro) ...")
    cards_by_code, variants_by_code, ia_printing_ids = load_local_op01(DB_PATH)
    op01_codes = sorted(cards_by_code.keys())
    total_local_variants = sum(len(v) for v in variants_by_code.values())
    print(
        f"  {len(op01_codes)} OP01 cards | "
        f"{total_local_variants} variant rows | "
        f"{len(ia_printing_ids)} image_asset printing_ids"
    )
    print()

    # ================================================================
    # STEP 2 -- Fetch + save raw responses (per-card mode)
    # ================================================================
    print("STEP 2 -- Fetch per-card data from OPTCG API")
    print("-" * 50)
    print(f"  {len(op01_codes)} cards @ {PACING_SEC}s pacing")
    eta = len(op01_codes) * PACING_SEC
    print(f"  Estimated wall-clock: ~{int(eta)}s ({eta / 60:.1f} min)")
    print()

    all_printings: dict[str, list[dict]] = {}
    http_errors: list[dict] = []

    for idx, code in enumerate(op01_codes, 1):
        if idx > 1:
            time.sleep(PACING_SEC)

        url = f"{API_BASE}/sets/card/{code}/"
        status, body = http_get_retry(url)

        if status == 200:
            try:
                data = json.loads(body)
                if isinstance(data, list):
                    all_printings[code] = data
                    save_json(RAW_DIR / f"{code}.json", data)
                else:
                    all_printings[code] = []
                    http_errors.append(
                        {
                            "card_code": code,
                            "http_status": status,
                            "error_message": "Response is not a JSON array",
                            "fetched_at": ts(),
                        }
                    )
            except json.JSONDecodeError as e:
                all_printings[code] = []
                http_errors.append(
                    {
                        "card_code": code,
                        "http_status": status,
                        "error_message": f"JSON decode: {e}",
                        "fetched_at": ts(),
                    }
                )
        else:
            all_printings[code] = []
            http_errors.append(
                {
                    "card_code": code,
                    "http_status": status,
                    "error_message": body[:300] if body else "(empty)",
                    "fetched_at": ts(),
                }
            )

        # progress ticker
        if idx % 20 == 0 or idx == len(op01_codes):
            n_print = len(all_printings.get(code, []))
            print(f"  [{idx:>3}/{len(op01_codes)}]  {code}  printings={n_print}")

    # write errors CSV
    if http_errors:
        with open(ERROR_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["card_code", "http_status", "error_message", "fetched_at"],
            )
            w.writeheader()
            w.writerows(http_errors)
        print(f"\n  Errors: {len(http_errors)} -> {ERROR_CSV_PATH.relative_to(ROOT)}")
    else:
        print("\n  HTTP errors: 0")

    total_printings = sum(len(v) for v in all_printings.values())
    codes_with_data = sum(1 for v in all_printings.values() if v)
    print(f"  Cards with data: {codes_with_data}/{len(op01_codes)}")
    print(f"  Total printings fetched: {total_printings}")
    print()

    # ================================================================
    # STEPS 3-5 -- Normalize, cross-reference, classify gap_status
    # ================================================================
    print("STEPS 3-5 -- Normalize + Cross-reference + Classify")
    print("-" * 50)

    csv_rows: list[dict[str, str]] = []
    suffix_counter: Counter[str] = Counter()
    unknown_suffixes: set[str] = set()

    for code in op01_codes:
        printings = all_printings.get(code, [])
        local_card = cards_by_code.get(code)
        local_variants = variants_by_code.get(code, [])

        for raw in printings:
            card_set_id = str(raw.get("card_set_id") or "").strip()
            card_image_id = str(raw.get("card_image_id") or "").strip()
            card_image = str(raw.get("card_image") or "").strip()
            set_name_api = str(raw.get("set_name") or "").strip()
            set_id_api = str(raw.get("set_id") or "").strip()
            card_name_api = str(raw.get("card_name") or "").strip()
            rarity_api = str(raw.get("rarity") or "").strip()

            # -- Step 3: infer treatment --
            treatment, confidence = infer_treatment(card_set_id, card_image_id)

            # track suffixes
            sfx = extract_suffix(card_set_id, card_image_id)
            sfx_label = sfx if sfx else "(none/base)"
            suffix_counter[sfx_label] += 1
            if confidence == "UNKNOWN" and sfx:
                unknown_suffixes.add(sfx)

            # -- Step 4: cross-reference local --
            local_code_match = code in cards_by_code
            local_cv_id: int | None = None
            local_image_exists = False

            if local_variants:
                local_cv_id = find_local_match(
                    local_variants, treatment, card_image_id
                )
                if local_cv_id is not None:
                    local_image_exists = local_cv_id in ia_printing_ids

            # -- name mismatch (base printings only) --
            name_mismatch = False
            if treatment == "base" and local_card:
                local_name = str(local_card.get("card_name") or "").strip()
                api_clean = clean_api_name(card_name_api)
                if local_name and api_clean:
                    if local_name.lower() != api_clean.lower():
                        name_mismatch = True

            # -- Step 5: classify --
            gap = classify_gap(
                treatment,
                confidence,
                local_code_match,
                local_cv_id,
                local_image_exists,
                name_mismatch,
            )

            csv_rows.append(
                {
                    "card_id": card_set_id,
                    "card_image_id": card_image_id,
                    "set_name": set_name_api,
                    "set_id": set_id_api,
                    "card_name": card_name_api,
                    "rarity": rarity_api,
                    "inferred_treatment": treatment,
                    "inferred_treatment_confidence": confidence,
                    "card_image_url": card_image,
                    "local_canonical_code_match": "yes" if local_code_match else "no",
                    "local_variant_match_id": str(local_cv_id) if local_cv_id else "",
                    "local_image_asset_exists": "yes" if local_image_exists else "no",
                    "gap_status": gap,
                    "raw_json_path": f"data/api_exploration/optcg_api_op01/{code}.json",
                }
            )

    print(f"  Normalized rows: {len(csv_rows)}")
    print()

    # ================================================================
    # STEP 6 -- Write output CSV
    # ================================================================
    print("STEP 6 -- Write output CSV")
    print("-" * 50)

    with open(OUTPUT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(csv_rows)

    print(f"  Path: {OUTPUT_CSV_PATH.relative_to(ROOT)}")
    print(f"  Rows: {len(csv_rows)}")
    print()

    # ================================================================
    # STEP 7 -- Report summary
    # ================================================================
    print("=" * 70)
    print("STEP 7 -- REPORT SUMMARY")
    print("=" * 70)

    # -- totals --
    print(f"\n  Total OP01 cards queried:            {len(op01_codes)}")
    print(f"  Cards returning API data:             {codes_with_data}")
    print(f"  Total printings across all cards:     {total_printings}")
    print(f"  Normalized CSV rows:                  {len(csv_rows)}")

    # -- gap_status breakdown --
    gap_counts = Counter(r["gap_status"] for r in csv_rows)
    print(f"\n  Printings per gap_status:")
    for bucket in (
        "MATCHED",
        "LOCAL_HAS_NO_IMAGE_ASSET",
        "NEW_PRINTING_NOT_IN_LOCAL",
        "SUFFIX_UNKNOWN",
        "OPTCG_BANDAI_MISMATCH",
    ):
        ct = gap_counts.get(bucket, 0)
        flag = "  *** CRITICAL ***" if bucket == "OPTCG_BANDAI_MISMATCH" and ct > 0 else ""
        print(f"    {bucket:40s} {ct:>4d}{flag}")

    # -- suffix analysis --
    print(f"\n  Distinct card_image_id suffixes: {len(suffix_counter)}")
    for sfx, ct in suffix_counter.most_common():
        print(f"    {sfx:25s} {ct:>4d}")

    if unknown_suffixes:
        print(f"\n  UNKNOWN suffixes (need operator taxonomy review):")
        for sfx in sorted(unknown_suffixes):
            print(f"    '{sfx}'")
    else:
        print(f"\n  No UNKNOWN suffixes found (all fit locked taxonomy)")

    # -- OPTCG_BANDAI_MISMATCH detail --
    mismatches = [r for r in csv_rows if r["gap_status"] == "OPTCG_BANDAI_MISMATCH"]
    print(f"\n  OPTCG_BANDAI_MISMATCH rows: {len(mismatches)}")
    if mismatches:
        for r in mismatches:
            local_name = cards_by_code.get(r["card_id"], {}).get("card_name", "?")
            api_clean = clean_api_name(r["card_name"])
            print(
                f"    {r['card_id']}: "
                f"API='{api_clean}' vs Local='{local_name}'"
            )

    # -- Specific card reports --
    print(f"\n  {'=' * 50}")
    print(f"  SPECIFIC CARD REPORTS")
    print(f"  {'=' * 50}")

    for target, question in [
        (
            "OP01-016",
            "Multiple printings with distinct card_image_id suffixes?",
        ),
        (
            "OP01-025",
            "Printing matching standard parallel missing from JustTCG?",
        ),
        (
            "OP01-004",
            "Base only or are there alt/promo printings?",
        ),
    ]:
        rows = [r for r in csv_rows if r["card_id"] == target]
        print(f"\n  {target} ({len(rows)} printings) -- {question}")
        for r in rows:
            print(
                f"    image_id={r['card_image_id']:22s}  "
                f"set={r['set_id']:8s}  "
                f"treatment={r['inferred_treatment']:16s}  "
                f"conf={r['inferred_treatment_confidence']:7s}  "
                f"gap={r['gap_status']}"
            )
        # Distinct suffixes for this card
        suffixes_here = sorted(
            set(extract_suffix(target, r["card_image_id"]) or "(base)" for r in rows)
        )
        print(f"    Distinct suffixes: {suffixes_here}")

    # -- HTTP errors summary --
    print(f"\n  HTTP errors encountered: {len(http_errors)}")
    if http_errors:
        for e in http_errors[:10]:
            print(f"    {e['card_code']} HTTP {e['http_status']}: {e['error_message'][:80]}")
        if len(http_errors) > 10:
            print(f"    ... and {len(http_errors) - 10} more")

    # -- Paths --
    print(f"\n  Output CSV:         {OUTPUT_CSV_PATH.relative_to(ROOT)}")
    print(f"  Raw JSON directory: {RAW_DIR.relative_to(ROOT)}")

    # -- Endpoint mode used --
    print(f"\n  Endpoint mode: per-card (/api/sets/card/<code>/)")
    if set_wide_mode:
        print(f"  Set-wide also works: /api/sets/{set_wide_mode}/ ({set_wide_count} rows)")

    # ================================================================
    # Verification footer
    # ================================================================
    print(f"\n  {'=' * 50}")
    print(f"  VERIFICATION FOOTER")
    print(f"  {'=' * 50}")
    print(f"  DB_PATH_CONFIRMED:        {DB_PATH}")
    print(f"  DB_OPENED_READ_ONLY:      yes (file:...?mode=ro, uri=True)")
    print(f"  DB_WRITES_PERFORMED:      no")
    print(f"  PM_18080_TOUCHED:         no")
    print(f"  PORT_8765_TOUCHED:        no")
    print(f"  IMAGE_FETCHES_PERFORMED:  no")
    print(f"  RESTART_PERFORMED:        no")

    # ================================================================
    # Verdict
    # ================================================================
    if codes_with_data >= 118 and total_printings > 0:
        verdict = "CONFIRMED WORKING"
    elif codes_with_data >= 60:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAILED"

    print(f"\n  {'=' * 50}")
    print(f"  VERDICT: {verdict}")
    print(f"  {'=' * 50}")

    return 0 if verdict == "CONFIRMED WORKING" else 1


if __name__ == "__main__":
    raise SystemExit(main())
