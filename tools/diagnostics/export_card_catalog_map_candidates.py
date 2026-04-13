#!/usr/bin/env python
"""
Read-only candidate mapping export for card_variants missing printing_market_map.

Uses Python sqlite3 with URI mode=ro. Does not import Miru workers or modify the DB.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "card_catalog.db"
DIAG = Path(__file__).resolve().parent
REBUILD_MARKET_TABLES = ROOT / "tools" / "rebuild_market_tables.py"


def _norm(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip().upper()


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _pick(cols: list[str], *candidates: str) -> str | None:
    low = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def _extract_code_tokens(print_id: str, canonical: str) -> tuple[str, str]:
    """Return (primary_number_key, loose_hint) for indexing."""
    p = (print_id or "").strip()
    c = (canonical or "").strip()
    if c:
        return _norm(c), _norm(c)
    # print_id like EB01-001_p1 or EB01-003::alt
    m = re.match(r"^([A-Z0-9]{2,10}-\d{3,4}[A-Z]?)[:_]", p, re.I)
    if m:
        return _norm(m.group(1)), _norm(m.group(1))
    m2 = re.match(r"^([A-Z0-9]{2,10}-\d{3,4}[A-Z]?)", p, re.I)
    if m2:
        return _norm(m2.group(1)), _norm(m2.group(1))
    return "", ""


def _variant_family(variant_key: str, is_base: Any) -> str:
    vk = (variant_key or "").lower()
    if vk.startswith("parallel"):
        return "parallel"
    if vk in ("sp",):
        return "sp"
    if vk in ("alt", "alternate"):
        return "alt"
    if vk in ("tr",):
        return "tr"
    if vk in ("promo",):
        return "promo"
    if vk == "base" or int(is_base or 0) == 1:
        return "base"
    return "other"


def _parallel_index(variant_key: str, variant_label: str) -> int | None:
    for s in (variant_key or "", variant_label or ""):
        m = re.search(r"(\d+)", s)
        if m and "parallel" in s.lower():
            try:
                return int(m.group(1))
            except ValueError:
                pass
        m2 = re.search(r"parallel[_\s]*(\d+)", s.lower())
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                pass
    return None


def _image_basename_hint(path: str | None) -> str:
    if not path:
        return ""
    base = Path(path.replace("\\", "/")).name
    m = re.search(r"([A-Z0-9]{2,10}-\d{3,4}[A-Z]?)", base, re.I)
    return _norm(m.group(1)) if m else ""


@dataclass
class ScoreResult:
    score: int = 0
    reasons: list[str] = field(default_factory=list)


def _score_candidate(
    card: dict[str, Any],
    cv: dict[str, Any],
    mp: dict[str, Any],
    *,
    number_key: str,
    set_key: str,
    img_hint: str,
) -> ScoreResult:
    r = ScoreResult()
    mnum = _norm(mp.get("market_number"))
    mset = _norm(mp.get("market_set_code"))
    mvl = (mp.get("market_variant_label") or "").lower()
    pname = (mp.get("product_name") or "").lower()
    cname = (card.get("card_name") or "").lower()
    ccode = _norm(card.get("canonical_code"))
    cr = _norm(card.get("rarity"))
    mr = _norm(mp.get("rarity_market"))

    if number_key and mnum and number_key == mnum:
        r.score += 48
        r.reasons.append("market_number_eq_canonical_or_extracted")
    elif number_key and mnum and (number_key in mnum or mnum in number_key):
        r.score += 32
        r.reasons.append("market_number_partial_overlap")

    if img_hint and mnum and img_hint == mnum:
        r.score += 12
        r.reasons.append("image_basename_number_agrees")

    pset = set_key
    if pset and mset:
        if pset == mset:
            r.score += 28
            r.reasons.append("set_code_match")
        else:
            r.score -= 18
            r.reasons.append("set_code_mismatch_penalty")

    if cr and mr:
        if cr == mr:
            r.score += 12
            r.reasons.append("rarity_match")
        elif cr[:2] == mr[:2]:
            r.score += 4
            r.reasons.append("rarity_weak_match")

    fam = _variant_family(cv.get("variant_key") or "", cv.get("is_base"))
    if fam == "base":
        if not mvl or mvl in ("normal", "standard", "regular", ""):
            r.score += 14
            r.reasons.append("base_vs_normal_market_label")
        elif "parallel" in mvl or "parallel" in pname:
            r.score -= 25
            r.reasons.append("base_variant_but_parallel_product_penalty")
    elif fam == "parallel":
        if "parallel" in mvl or "parallel" in pname:
            r.score += 18
            r.reasons.append("parallel_token_in_market_fields")
            pi = _parallel_index(cv.get("variant_key") or "", cv.get("variant_label") or "")
            if pi is not None:
                if re.search(rf"parallel\s*{pi}\b", mvl + " " + pname, re.I):
                    r.score += 22
                    r.reasons.append("parallel_index_match")
                elif str(pi) in pname.replace(" ", ""):
                    r.score += 8
                    r.reasons.append("parallel_index_loose_in_name")
        else:
            r.score -= 12
            r.reasons.append("parallel_variant_no_parallel_market_penalty")
    elif fam == "sp":
        if "special" in mr.lower() or "special" in pname or " alt " in pname or "_sp" in pname:
            r.score += 12
            r.reasons.append("sp_signal_in_market")
        if mvl and "parallel" in mvl and "special" not in mvl:
            r.score += 4
    elif fam == "alt":
        if "alt" in mvl or "alternate" in pname or " art" in pname:
            r.score += 14
            r.reasons.append("alt_art_signal")

    if cname and len(cname) > 3:
        clean = (mp.get("clean_product_name") or "").lower()
        if cname in pname or cname in clean:
            r.score += 10
            r.reasons.append("card_name_substring_product")
        else:
            # token overlap
            ct = set(re.findall(r"[a-z0-9]+", cname))
            pt = set(re.findall(r"[a-z0-9]+", pname))
            inter = ct & pt - {"the", "and", "card", "op", "eb", "st"}
            if len(inter) >= 2:
                r.score += 6
                r.reasons.append("name_token_overlap")

    if (mp.get("source_name") or "").lower() == "tcgcsv":
        r.score += 3
        r.reasons.append("source_tcgcsv")

    return r


def _confidence_and_review(
    best: int,
    second: int,
    n_cand: int,
    reasons: list[str],
) -> tuple[str, int, str]:
    gap = best - second
    # Fail closed
    if best < 28:
        return "none", 0, "below_minimum_score"
    ambiguous = n_cand > 1 and gap < 12
    if best >= 78 and gap >= 18 and not ambiguous:
        return "high", 0, "strong_unique"
    if best >= 55 and gap >= 18:
        return "medium", 1 if gap < 22 or n_cand > 3 else 0, "good_gap_or_few_alts"
    if best >= 40 and gap >= 10:
        return "medium", 1, "moderate_evidence"
    if best >= 28:
        return "low", 1, "weak_or_tight_scoring"
    return "none", 0, "rejected"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FATAL: DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = DIAG / f"card_catalog_map_candidates_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    stat_before = DB_PATH.stat()
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    for tbl in ("cards", "card_variants", "market_products", "printing_market_map"):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (tbl,),
        ).fetchone():
            print(f"FATAL: missing table {tbl}", file=sys.stderr)
            conn.close()
            return 1

    cc = _cols(conn, "cards")
    cv_cols = _cols(conn, "card_variants")

    # --- Load market_products ---
    mp_rows = conn.execute(
        """
        SELECT id, market_product_id, market_set_code, market_number,
               product_name, clean_product_name, market_variant_label,
               rarity_market, source_name
        FROM market_products
        """
    ).fetchall()
    mp_dicts = [dict(r) for r in mp_rows]

    by_set_num: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_num: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mp in mp_dicts:
        sn = _norm(mp.get("market_set_code"))
        nn = _norm(mp.get("market_number"))
        if sn and nn:
            by_set_num[(sn, nn)].append(mp)
        if nn:
            by_num[nn].append(mp)

    # --- Variants missing printing_market_map (printing_id = cv.id) ---
    card_extra = [
        "c.canonical_code AS _canonical_code",
        "c.set_code AS _set_code",
        "c.card_name AS _card_name",
        "c.rarity AS _rarity",
        "c.distribution_source AS _distribution_source",
    ]
    if _pick(cc, "card_code"):
        card_extra.append("c.card_code AS _card_code")

    vsql = f"""
    SELECT cv.*, {", ".join(card_extra)}
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    WHERE NOT EXISTS (
      SELECT 1 FROM printing_market_map p WHERE p.printing_id = cv.id
    )
    ORDER BY cv.id
    LIMIT 5000
    """
    variants = [dict(r) for r in conn.execute(vsql).fetchall()]
    examined = len(variants)

    # Dynamic optional columns for export
    want_cv = [
        "variant_key",
        "variant_label",
        "source",
        "print_id",
        "image_path",
        "image_url",
        "release_set_code",
        "is_base",
        "official_provenance",
    ]
    cv_fields = [c for c in want_cv if c in cv_cols]

    mismatch_causes: Counter[str] = Counter()
    useful: Counter[str] = Counter()
    cluster_dims: dict[str, Counter[str]] = defaultdict(Counter)

    out_rows: list[dict[str, Any]] = []

    for cv in variants:
        card = {
            "canonical_code": cv.pop("_canonical_code", None),
            "set_code": cv.pop("_set_code", None),
            "card_name": cv.pop("_card_name", None),
            "rarity": cv.pop("_rarity", None),
            "distribution_source": cv.pop("_distribution_source", None),
            "card_code": cv.pop("_card_code", None),
        }
        vid = cv.get("id")
        number_key, _ = _extract_code_tokens(cv.get("print_id") or "", card.get("canonical_code") or "")
        if not number_key:
            number_key = _image_basename_hint(cv.get("image_path"))
        set_key = _norm(cv.get("release_set_code") or card.get("set_code"))

        img_hint = _image_basename_hint(cv.get("image_path"))

        # Candidate pool: prefer (set, number), else number-only (ambiguous risk)
        seen_ids: set[int] = set()
        pool: list[dict[str, Any]] = []
        if set_key and number_key and (set_key, number_key) in by_set_num:
            for mp in by_set_num[(set_key, number_key)]:
                if mp["id"] not in seen_ids:
                    seen_ids.add(mp["id"])
                    pool.append(mp)
        if number_key:
            for mp in by_num.get(number_key, []):
                if mp["id"] not in seen_ids:
                    seen_ids.add(mp["id"])
                    pool.append(mp)
        if len(pool) > 80:
            pool = pool[:80]

        scored: list[tuple[int, list[str], dict[str, Any]]] = []
        for mp in pool:
            sr = _score_candidate(card, cv, mp, number_key=number_key, set_key=set_key, img_hint=img_hint)
            if sr.score > 0:
                scored.append((sr.score, sr.reasons, mp))

        scored.sort(key=lambda x: -x[0])
        best_s = scored[0][0] if scored else 0
        second_s = scored[1][0] if len(scored) > 1 else 0
        best_mp = scored[0][2] if scored else None
        best_reasons = scored[0][1] if scored else []

        conf, review, _tag = _confidence_and_review(
            best_s, second_s, len(scored), best_reasons
        )

        if conf == "none" or best_mp is None:
            mismatch_causes["no_or_weak_candidate_pool"] += 1
            cand_pk = ""
            cand_ext = ""
            match_score = 0
            match_reasons = ""
            if not pool:
                mismatch_causes["empty_pool_after_index"] += 1
        else:
            cand_pk = best_mp["id"]
            cand_ext = best_mp.get("market_product_id") or ""
            match_score = best_s
            match_reasons = ";".join(best_reasons)
            for reason in best_reasons:
                useful[reason.split("_penalty")[0]] += 1

        if conf == "none":
            mismatch_causes["confidence_gate"] += 1

        if len(scored) > 1 and (best_s - second_s) < 12:
            mismatch_causes["tight_top_two_scores"] += 1

        # cluster keys
        src = str(cv.get("source") or "")
        cluster_dims["variant_source"][src] += 1
        cluster_dims["variant_family"][_variant_family(cv.get("variant_key"), cv.get("is_base"))] += 1
        cluster_dims["card_rarity"][_norm(card.get("rarity")) or "(empty)"] += 1
        cluster_dims["set_code"][_norm(card.get("set_code")) or "(empty)"] += 1
        cluster_dims["release_set_code"][_norm(cv.get("release_set_code")) or "(empty)"] += 1
        cluster_dims["card_distribution_source"][_norm(card.get("distribution_source")) or "(empty)"] += 1
        cluster_dims["match_confidence"][conf] += 1

        row_out: dict[str, Any] = {
            "card_variant_id": vid,
            "card_variants_card_id": cv.get("card_id"),
            "card_canonical_code": card.get("canonical_code"),
            "card_card_code": card.get("card_code"),
            "card_name": card.get("card_name"),
            "card_rarity": card.get("rarity"),
            "card_source": card.get("distribution_source"),
            "candidate_market_product_pk": cand_pk,
            "candidate_market_product_external_id": cand_ext,
            "candidate_product_name": best_mp.get("product_name") if best_mp else "",
            "candidate_clean_name": best_mp.get("clean_product_name") if best_mp else "",
            "candidate_ext_number": best_mp.get("market_number") if best_mp else "",
            "candidate_rarity": best_mp.get("rarity_market") if best_mp else "",
            "candidate_subtype": best_mp.get("market_variant_label") if best_mp else "",
            "candidate_source_name": best_mp.get("source_name") if best_mp else "",
            "match_confidence": conf,
            "match_score": match_score,
            "match_reasons": match_reasons,
            "needs_manual_review": review,
            "derived_number_key": number_key,
            "derived_set_key": set_key,
            "pool_size": len(pool),
            "scored_candidates": len(scored),
        }
        for f in cv_fields:
            row_out[f"cv_{f}"] = cv.get(f)
        out_rows.append(row_out)

    conn.close()

    # Counts
    high_c = [r for r in out_rows if r["match_confidence"] == "high" and r["needs_manual_review"] == 0]
    # manual_review file: plausible candidate but review
    manual_only = [
        r
        for r in out_rows
        if r["candidate_market_product_pk"] != ""
        and (r["needs_manual_review"] == 1 or r["match_confidence"] in ("medium", "low"))
    ]
    none_c = [r for r in out_rows if r["match_confidence"] == "none" or r["candidate_market_product_pk"] == ""]
    med_c = [r for r in out_rows if r["match_confidence"] == "medium"]
    low_c = [r for r in out_rows if r["match_confidence"] == "low"]

    fields = list(out_rows[0].keys()) if out_rows else []

    def wcsv(name: str, rows: list[dict[str, Any]]) -> None:
        with (out_dir / name).open("w", newline="", encoding="utf-8") as fh:
            if not fields:
                fh.write("empty\n")
                return
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    wcsv("missing_printing_market_map_candidates.csv", out_rows)
    wcsv("high_confidence_candidates.csv", high_c)
    wcsv("manual_review_candidates.csv", manual_only)
    wcsv("unresolved_no_candidate.csv", none_c)

    # cluster_breakdown.csv — long format
    with (out_dir / "cluster_breakdown.csv").open("w", newline="", encoding="utf-8") as fh:
        cw = csv.writer(fh)
        cw.writerow(["cluster_dimension", "cluster_value", "row_count"])
        for dim, ctr in sorted(cluster_dims.items()):
            for val, cnt in ctr.most_common():
                cw.writerow([dim, val, cnt])

    top_mismatch = mismatch_causes.most_common(12)
    top_useful = useful.most_common(15)

    rebuild_notes = ""
    if REBUILD_MARKET_TABLES.is_file():
        txt = REBUILD_MARKET_TABLES.read_text(encoding="utf-8", errors="replace")
        rebuild_notes = (
            "`tools/rebuild_market_tables.py` calls `match_card_variant` then "
            "`insert_printing_market_map(printing_id=int(match['printing_id']), market_product_id=market_product_fk)` "
            "where `market_product_fk` is the return value of `upsert_market_product` (i.e. **`market_products.id`**). "
            "`match_card_variant` resolves **`card_variants.id`** as `printing_id` (see `miru_ai/workers/tcgcsv_fetcher.py`). "
            "The diagnostic JOIN `cv.id = pmm.printing_id` matches this script.\n\n"
            "**Filtering / exclusion:** The script only processes TCGCSV groups listed in `group_set_mapping.json` "
            "with `confidence == \"high\"` and with both `products.json` and `prices.json` present under `data/tcgcsv/{group_id}/`. "
            "Any set/group not in that manifest path never gets products/maps from this tool, which can leave large "
            "`missing_printing_market_map` populations even when `market_products` rows exist from other ingest paths. "
            "`match_card_variant` also fails closed on variant families it does not recognize, ambiguous multi-row "
            "matches, or when `print_id` / `variant_key` / `release_set_code` patterns do not satisfy its SQL filters.\n\n"
            f"(Read-only scan: file present, {len(txt)} chars.)\n"
        )
    else:
        rebuild_notes = "`tools/rebuild_market_tables.py` not found at expected path.\n"

    summary = f"""# Candidate mapping summary (missing_printing_market_map)

**Generated (UTC):** {stamp}
**Database:** `{DB_PATH}` (read-only)

## Scope

- All `card_variants` with **no** `printing_market_map` row where `printing_id = card_variants.id`
  (dominant reconciliation failure bucket).

Rows examined: **{examined}**

## Candidate counts

| Tier | Count |
|------|------:|
| High confidence (unique, strong score, `needs_manual_review=0`) | {len(high_c)} |
| Medium confidence (`match_confidence=medium`) | {len(med_c)} |
| Low confidence (`match_confidence=low`) | {len(low_c)} |
| Manual review CSV (medium/low and/or `needs_manual_review=1` when a candidate pk exists) | {len(manual_only)} |
| No safe candidate (`none` or empty `candidate_market_product_pk`) | {len(none_c)} |

## Top mismatch / gate causes (heuristic counters)

| Cause | Count |
|-------|------:|
{chr(10).join(f"| `{k}` | {v} |" for k, v in top_mismatch)}

## Fields most often present in winning `match_reasons` (signal frequency)

| Signal prefix | Times in best-row reasons |
|---------------|---------------------------:|
{chr(10).join(f"| `{k}` | {v} |" for k, v in top_useful)}

## Clustering (see `cluster_breakdown.csv`)

Dimensions included: variant source, derived variant family from `variant_key`/`is_base`, card rarity, `cards.set_code`,
`card_variants.release_set_code`, `cards.distribution_source`, and `match_confidence`. Strong skew in any bucket
usually indicates a systematic ingest or matcher gap for that slice.

## `tools/rebuild_market_tables.py` (read-only review)

{rebuild_notes}

## Method (conservative)

- Index `market_products` by `(market_set_code, market_number)` and by `market_number` alone.
- For each unresolved variant, derive `derived_number_key` from `canonical_code` or `print_id` (or image basename).
- Score candidates with weighted signals (exact number, set match, rarity, variant-family hints vs `market_variant_label` / product name).
- Penalize set mismatches and obvious family mismatches (e.g. base vs parallel product).
- **Ambiguity:** small gap between top two scores forces manual review or `low` confidence.
- **No OCR**; image path used only for filename code hints.

## Outputs

- `missing_printing_market_map_candidates.csv` — full capped export ({examined} rows)
- `high_confidence_candidates.csv`
- `manual_review_candidates.csv`
- `unresolved_no_candidate.csv`
- `cluster_breakdown.csv`
- `safe_join_keys.md`
"""

    (out_dir / "candidate_mapping_summary.md").write_text(summary, encoding="utf-8")

    safe_keys = """# Safe join keys (future repair pass)

## Safest (schema-aligned)

- **`printing_market_map.printing_id` = `card_variants.id`** — internal printing/variant row.
- **`printing_market_map.market_product_id` = `market_products.id`** — internal PK, not the external TEXT id.

## Strong structured matching (composite)

- **`market_products.market_set_code` + `market_products.market_number`** together — aligns with TCGCSV extended
  \"Number\" + mapped set code from `group_set_mapping.json` in rebuild tooling.
- Add **`market_variant_label` / product name tokens** when multiple `market_products` share the same set+number
  (e.g. parallels).

## Moderately safe

- **`cards.canonical_code`** vs **`market_products.market_number`** when formats align (e.g. `EB01-001`).
- **`card_variants.release_set_code`** or **`cards.set_code`** vs **`market_products.market_set_code`** when both populated.

## Risky alone

- **`card_variants.print_id`** — human-readable; do not equate to `printing_market_map.printing_id`.
- **`market_products.market_product_id` (TEXT)** — external TCG id; fine for display, not the FK column for the map table.
- **`card_variants.tcgplayer_product_id`** — largely empty in this DB; do not rely on as primary key path.
- **Image filename only** — helpful hint, not sufficient alone.
- **Name-only fuzzy match** without set+number — high collision risk.

## Example safe composite patterns

1. `canonical_code == market_number` AND `set_code == market_set_code` AND variant family consistent with `market_variant_label`.
2. Parallel: same as (1) plus parallel index / \"Parallel N\" consistency in market name or label.
3. Base: same as (1) with `is_base=1` and normal/empty market variant label.
"""

    (out_dir / "safe_join_keys.md").write_text(safe_keys, encoding="utf-8")

    stat_after = DB_PATH.stat()
    ok = stat_before.st_size == stat_after.st_size and stat_before.st_mtime_ns == stat_after.st_mtime_ns
    (out_dir / "verification.txt").write_text(
        f"db_path={DB_PATH}\n"
        f"size_before={stat_before.st_size}\n"
        f"size_after={stat_after.st_size}\n"
        f"mtime_ns_before={stat_before.st_mtime_ns}\n"
        f"mtime_ns_after={stat_after.st_mtime_ns}\n"
        f"unchanged={ok}\n",
        encoding="utf-8",
    )

    print(f"Output: {out_dir}")
    print(f"examined={examined} high={len(high_c)} manual={len(manual_only)} none={len(none_c)}")
    print(f"DB unchanged: {ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
