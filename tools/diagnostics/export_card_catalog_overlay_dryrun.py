#!/usr/bin/env python
"""
Dry-run overlay bundle for high-confidence printing_market_map candidates.

Reads the latest diagnostics/card_catalog_map_candidates_* export and card_catalog.db
(read-only). Does not modify the database.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "card_catalog.db"
DIAG = Path(__file__).resolve().parent


def _latest_candidates_bundle() -> Path | None:
    dirs = sorted(DIAG.glob("card_catalog_map_candidates_*"), key=lambda p: p.name)
    return dirs[-1] if dirs else None


def _price_cols(conn: sqlite3.Connection) -> list[str]:
    names = [r[1] for r in conn.execute('PRAGMA table_info("market_prices")').fetchall()]
    want = [
        "market_price",
        "mid_price",
        "low_price",
        "high_price",
        "direct_low_price",
        "listed_median_price",
    ]
    return [c for c in want if c in names]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        fields = list(r.fieldnames or [])
        rows = [dict(row) for row in r]
    return fields, rows


def _as_int(s: Any) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(str(s).strip())
    except ValueError:
        return None


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FATAL: DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    bundle = _latest_candidates_bundle()
    if bundle is None:
        print("FATAL: no diagnostics/card_catalog_map_candidates_* folder", file=sys.stderr)
        return 1

    hi_path = bundle / "high_confidence_candidates.csv"
    full_path = bundle / "missing_printing_market_map_candidates.csv"
    if not hi_path.is_file():
        print(f"FATAL: missing {hi_path}", file=sys.stderr)
        return 1
    if not full_path.is_file():
        print(f"FATAL: missing {full_path}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = DIAG / f"card_catalog_overlay_dryrun_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_bundle = bundle.name

    stat_before = DB_PATH.stat()
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    price_fields = _price_cols(conn)
    extra_mpr = [c for c in ("captured_at", "subtype_name", "source_name", "id") if c in [r[1] for r in conn.execute('PRAGMA table_info("market_prices")').fetchall()]]

    _, high_rows = _read_csv(hi_path)
    _, full_rows = _read_csv(full_path)

    # --- Batch price lookup for candidate PKs ---
    pks: set[int] = set()
    for row in high_rows:
        pk = _as_int(row.get("candidate_market_product_pk"))
        if pk is not None:
            pks.add(pk)

    price_by_mp: dict[int, sqlite3.Row] = {}
    if pks:
        placeholders = ",".join("?" * len(pks))
        sql = f"""
        SELECT mpr.* FROM market_prices mpr
        INNER JOIN (
          SELECT market_product_fk AS mpfk, MAX(captured_at) AS mx
          FROM market_prices
          WHERE market_product_fk IN ({placeholders})
          GROUP BY market_product_fk
        ) t ON t.mpfk = mpr.market_product_fk AND t.mx = mpr.captured_at
        """
        for r in conn.execute(sql, tuple(pks)).fetchall():
            fk = int(r["market_product_fk"])
            if fk not in price_by_mp:
                price_by_mp[fk] = r

    conn.close()

    def price_usable(row: sqlite3.Row | None, pk: int | None) -> tuple[bool, str]:
        if pk is None:
            return False, "missing_candidate_market_product_pk"
        if row is None:
            return False, "no_market_prices_row_for_product_pk"
        if not price_fields:
            return False, "no_price_columns_in_schema"
        vals = [row[c] for c in price_fields if c in row.keys()]
        if any(v is not None for v in vals):
            return True, "at_least_one_price_column_non_null"
        return False, "market_prices_row_exists_but_all_tracked_price_columns_null"

    # --- 2) overlay CSV ---
    overlay_fields = [
        "card_variant_id",
        "candidate_market_product_pk",
        "candidate_market_product_external_id",
        "match_score",
        "match_reasons",
        "source_bundle",
        "confidence",
        "needs_manual_review",
    ]
    overlay_rows: list[dict[str, str]] = []
    sim_rows: list[dict[str, Any]] = []

    gain = 0
    nogain = 0
    no_row = 0
    null_prices = 0
    notes_ctr: Counter[str] = Counter()
    products_without_usable_price: set[int] = set()

    for row in high_rows:
        vid = row.get("card_variant_id", "")
        pk = _as_int(row.get("candidate_market_product_pk"))
        ext = row.get("candidate_market_product_external_id", "")
        mscore = row.get("match_score", "")
        mreasons = row.get("match_reasons", "")
        nrev = row.get("needs_manual_review", "")
        conf = row.get("match_confidence", "high") or "high"

        overlay_rows.append(
            {
                "card_variant_id": vid,
                "candidate_market_product_pk": str(pk) if pk is not None else "",
                "candidate_market_product_external_id": ext,
                "match_score": mscore,
                "match_reasons": mreasons,
                "source_bundle": source_bundle,
                "confidence": conf.upper(),
                "needs_manual_review": nrev,
            }
        )

        mpr = price_by_mp.get(pk) if pk is not None else None
        ok, note = price_usable(mpr, pk)
        notes_ctr[note] += 1
        if ok:
            gain += 1
        else:
            nogain += 1
            if pk is not None and mpr is None:
                no_row += 1
                products_without_usable_price.add(pk)
            elif mpr is not None:
                null_prices += 1
                if pk is not None:
                    products_without_usable_price.add(pk)

        sim = {
            "card_variant_id": vid,
            "candidate_market_product_pk": str(pk) if pk is not None else "",
            "has_price_via_overlay": 1 if ok else 0,
            "notes": note,
        }
        if mpr is not None:
            for c in price_fields:
                sim[f"mpr_{c}"] = mpr[c]
            for c in extra_mpr:
                if c in mpr.keys():
                    key = "mpr_row_id" if c == "id" else c
                    sim[key] = mpr[c]
        else:
            for c in price_fields:
                sim[f"mpr_{c}"] = ""
            for c in extra_mpr:
                key = "mpr_row_id" if c == "id" else c
                sim[key] = ""
        sim_rows.append(sim)

    with (out_dir / "printing_market_map_overlay_high_confidence.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=overlay_fields, extrasaction="ignore")
        w.writeheader()
        for r in overlay_rows:
            w.writerow(r)

    sim_fields = ["card_variant_id", "candidate_market_product_pk", "has_price_via_overlay", "notes"]
    sim_fields += [f"mpr_{c}" for c in price_fields]
    for c in extra_mpr:
        key = "mpr_row_id" if c == "id" else c
        if key not in sim_fields:
            sim_fields.append(key)

    with (out_dir / "overlay_effect_simulation.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sim_fields, extrasaction="ignore")
        w.writeheader()
        for r in sim_rows:
            w.writerow({k: r.get(k, "") for k in sim_fields})

    # --- medium / low queues ---
    med_rows = [r for r in full_rows if (r.get("match_confidence") or "").lower() == "medium"]
    low_rows = [r for r in full_rows if (r.get("match_confidence") or "").lower() == "low"]

    med_fields = [
        "card_variant_id",
        "card_canonical_code",
        "card_card_code",
        "card_name",
        "candidate_market_product_pk",
        "candidate_market_product_external_id",
        "match_score",
        "match_reasons",
        "ambiguity_notes",
        "pool_size",
        "scored_candidates",
        "match_confidence",
        "needs_manual_review",
    ]

    def ambiguity_note(r: dict[str, str]) -> str:
        parts = []
        try:
            ps = int(r.get("pool_size") or 0)
            sc = int(r.get("scored_candidates") or 0)
            if ps > 5:
                parts.append(f"large_candidate_pool n={ps}")
            if sc > 1:
                parts.append(f"multiple_scored_candidates n={sc}")
        except ValueError:
            pass
        if (r.get("needs_manual_review") or "") == "1":
            parts.append("flagged_needs_manual_review")
        return "; ".join(parts) if parts else "medium_confidence_tier"

    with (out_dir / "medium_review_queue.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=med_fields, extrasaction="ignore")
        w.writeheader()
        for r in med_rows:
            out = {k: r.get(k, "") for k in med_fields if k != "ambiguity_notes"}
            out["ambiguity_notes"] = ambiguity_note(r)
            w.writerow(out)

    low_want = [
        "card_variant_id",
        "card_canonical_code",
        "card_name",
        "candidate_market_product_pk",
        "candidate_market_product_external_id",
        "match_score",
        "match_reasons",
        "match_confidence",
        "needs_manual_review",
        "cv_variant_key",
        "cv_variant_label",
        "cv_print_id",
        "cv_image_path",
    ]
    low_fields = [c for c in low_want if full_rows and c in full_rows[0]]

    with (out_dir / "low_confidence_hold.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=low_fields, extrasaction="ignore")
        w.writeheader()
        for r in low_rows:
            w.writerow({k: r.get(k, "") for k in low_fields})

    overlay_design = f"""# Overlay design (dry-run)

## Strategy

**Recommended: versioned sidecar CSV** (e.g. `printing_market_map_overlay_high_confidence.csv` checked in or shipped beside the catalog).

### Why CSV is safest for this repo

1. **No schema migration** — `card_catalog.db` stays unchanged until operators explicitly promote rows into `printing_market_map`.
2. **Full audit trail** — Git-friendly diff of proposed `(printing_id, market_product_id)` pairs, scores, and reasons.
3. **Explicit opt-in** — PM or a future importer loads the overlay only when configured; default behavior unchanged.
4. **Easy rollback** — Remove or replace the file; no table rebuilds.

### Alternatives

| Approach | Pros | Cons |
|----------|------|------|
| **Sidecar SQLite** (`overlay.db`) | Indexed lookups, can hold multiple tiers | Second file to deploy/sync; less visible in review |
| **New overlay table in main DB** | Single connection, SQL joins | Requires migration and write access; higher risk if applied prematurely |

For a **first cleanup pass** after human review, importing CSV rows into real `printing_market_map` (append-only, idempotent `INSERT OR IGNORE`) is usually the production end state; the CSV remains the source of truth for *what* was approved.

## Input bundle

Latest candidate export used: **`{source_bundle}`**

## Semantics

- `printing_id` in a real map row = **`card_variants.id`**.
- `market_product_id` in a real map row = **`market_products.id`** (internal PK).
"""

    effect_summary = f"""# Overlay effect summary (simulated)

**Input:** `{source_bundle}` → `high_confidence_candidates.csv`  
**Simulation:** Read-only `card_catalog.db`; for each overlay target `market_products.id`, check latest `market_prices` row by `captured_at` (ties: first returned).  
**Usable price:** any of schema columns {", ".join(price_fields) or "(none)"} is non-null on that row.

## Counts

| Metric | Count |
|--------|------:|
| High-confidence overlay rows examined | {len(high_rows)} |
| Would gain price coverage via overlay (`has_price_via_overlay=1`) | {gain} |
| Would **not** resolve to a usable price with overlay | {nogain} |
| — of those: no `market_prices` row for candidate product PK | {no_row} |
| — of those: price row exists but all tracked price columns null | {null_prices} |

## Note resolution breakdown (simulation)

| Note | Count |
|------|------:|
{chr(10).join(f"| `{k}` | {v} |" for k, v in notes_ctr.most_common())}

## Products pointed to by overlay but lacking usable price (distinct PKs)

**{len(products_without_usable_price)}** distinct `market_products.id` values (see simulation `notes` and `has_price_via_overlay=0`).

## Caveats

- PM today may pick a different `market_prices` row (e.g. subtype ordering in `get_card_price_chain`); this simulation uses **latest `captured_at` only**.
- Overlay does not create prices; it only exposes existing `market_prices` through a synthetic map path.
"""

    impl = """# Implementation notes (future, conservative)

## Preferred lookup order for PM price resolution

1. **Real `printing_market_map`** — if a row exists for `card_variants.id`, use existing join path (authoritative once committed).
2. **Approved overlay** — if no real map row, consult an explicitly configured overlay source (CSV path or small SQLite sidecar) keyed by `printing_id` → `market_products.id`, then join `market_prices` the same way.
3. **Fail closed** — if neither applies, return empty price chain (current safe behavior).

This preserves production data while allowing staged rollout.

## Likely touchpoints (if approved later)

- `pm/app.py` — `get_card_price_chain(db_path, printing_id)` currently joins only `printing_market_map` → `market_products` → `market_prices`. A minimal change is a **fallback** after the main query returns empty: resolve `printing_id` through an in-memory dict loaded from the overlay CSV at startup or on first use.
- Optional: shared helper module for “resolve printing → market_product_pk” used by PM and any dashboard duplicate logic.
- **Not recommended:** running `tools/rebuild_market_tables.py` for this pass (destructive full wipe of market tables).

## Promotion workflow (operators)

1. Review `medium_review_queue.csv` and spot-check `high_confidence` rows.
2. Approve subset → append to `printing_market_map` via controlled script (separate task) or maintain overlay CSV until bulk insert is scheduled.
3. Re-run reconciliation export to confirm reduced `missing_printing_market_map` bucket.
"""

    (out_dir / "overlay_design.md").write_text(overlay_design, encoding="utf-8")
    (out_dir / "overlay_effect_summary.md").write_text(effect_summary, encoding="utf-8")
    (out_dir / "implementation_notes.md").write_text(impl, encoding="utf-8")

    stat_after = DB_PATH.stat()
    ok = stat_before.st_size == stat_after.st_size and stat_before.st_mtime_ns == stat_after.st_mtime_ns
    (out_dir / "verification.txt").write_text(
        f"db_path={DB_PATH}\n"
        f"candidate_bundle={bundle}\n"
        f"size_before={stat_before.st_size}\n"
        f"size_after={stat_after.st_size}\n"
        f"mtime_ns_before={stat_before.st_mtime_ns}\n"
        f"mtime_ns_after={stat_after.st_mtime_ns}\n"
        f"unchanged={ok}\n",
        encoding="utf-8",
    )

    print(f"Output: {out_dir}")
    print(f"high_rows={len(high_rows)} gain_price={gain} still_unresolved={nogain} DB_ok={ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
