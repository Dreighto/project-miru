"""
Phase A Step 2 (revised): Build image_fetch_manifest.json from card_variants + classification log.

Read-only DB. Writes only data/image_fetch_manifest.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
CLASS_LOG_PATH = ROOT / "data" / "phase_a_classification_log.json"
OUT_PATH = ROOT / "data" / "image_fetch_manifest.json"
MIN_DB_BYTES = 10 * 1024 * 1024

CDN_PREFIX = "https://en.onepiece-cardgame.com/images/cardlist/card/"


def load_classification_by_id(path: Path) -> dict[int, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, dict[str, Any]] = {}
    for row in raw:
        rid = int(row["id"])
        cr = row.get("classification_reason")
        if cr is not None and not isinstance(cr, str):
            cr = str(cr)
        out[rid] = {
            "conflict_flag": bool(row.get("conflict_flag")),
            "classification_reason": cr,
        }
    return out


def family_bucket_key(fam_val: Any) -> str:
    if fam_val is None:
        return "(null)"
    t = trim(fam_val)
    return t if t else "(empty)"


def step1_family_totals(class_log_path: Path) -> dict[str, int]:
    raw = json.loads(class_log_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = defaultdict(int)
    for row in raw:
        counts[family_bucket_key(row.get("normalized_variant_family"))] += 1
    return dict(counts)


def trim(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def jstr_or_null(raw: Any) -> str | None:
    if raw is None:
        return None
    t = trim(raw)
    return t if t != "" else None


def build_target_local_path(
    family_trimmed: str,
    npk_trimmed: str,
    canonical_trimmed: str,
    nvk_raw: Any,
) -> str:
    c = canonical_trimmed
    p = npk_trimmed
    if family_trimmed == "base":
        return f"{p}/base/{c}.png"
    vk = "" if nvk_raw is None else str(nvk_raw)
    return f"{p}/{family_trimmed}/{c}{vk}.png"


def assign_readiness_precedence(
    canonical_trimmed: str,
    fam_raw: Any,
    npk_raw: Any,
    conflict_flag: bool,
    class_reason: str | None,
) -> tuple[str, str | None]:
    """
    Exact order: BLOCKED_UNKNOWN cases, then REVIEW_REQUIRED, else READY.
    """
    fam_t = trim(fam_raw) if fam_raw is not None else ""
    if not fam_t or fam_t == "unknown":
        return "BLOCKED_UNKNOWN", "unknown_family"
    if not canonical_trimmed:
        return "BLOCKED_UNKNOWN", "null_canonical_code"
    pk_t = trim(npk_raw) if npk_raw is not None else ""
    if not pk_t:
        return "BLOCKED_UNKNOWN", "null_product_key"

    if pk_t.startswith("_"):
        return "REVIEW_REQUIRED", "unclassified_product_key"
    if conflict_flag:
        cr = class_reason if class_reason else ""
        return "REVIEW_REQUIRED", f"classification_conflict:{cr}"

    return "READY", None


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: missing database {DB_PATH}", file=sys.stderr)
        return 1
    if DB_PATH.stat().st_size < MIN_DB_BYTES:
        print(f"FAILED: database smaller than {MIN_DB_BYTES} bytes", file=sys.stderr)
        return 1
    if not CLASS_LOG_PATH.is_file():
        print(f"FAILED: missing {CLASS_LOG_PATH}", file=sys.stderr)
        return 1

    class_by_id = load_classification_by_id(CLASS_LOG_PATH)
    step1_by_family = step1_family_totals(CLASS_LOG_PATH)

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    q = """
    SELECT cv.id, cv.card_id, cv.print_id,
           cv.normalized_product_key, cv.normalized_variant_family, cv.normalized_variant_key,
           cv.release_set_code, cv.distribution_product_key,
           c.canonical_code
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    ORDER BY cv.id
    """
    rows = cur.execute(q).fetchall()
    conn.close()

    entries: list[dict[str, Any]] = []
    disagree_count = 0

    for row in rows:
        rid = int(row["id"])
        cc_db = row["canonical_code"]
        canonical_trimmed = trim(cc_db)
        print_id = row["print_id"] if row["print_id"] is not None else ""
        cc_raw = cc_db if cc_db is not None else ""
        agreed = print_id == cc_raw
        if not agreed:
            disagree_count += 1

        log = class_by_id.get(rid)
        conflict_flag = log["conflict_flag"] if log else False
        class_reason = log["classification_reason"] if log else None

        npk_raw = row["normalized_product_key"]
        nvk_raw = row["normalized_variant_key"]
        fam_raw = row["normalized_variant_family"]

        readiness, reason = assign_readiness_precedence(
            canonical_trimmed,
            fam_raw,
            npk_raw,
            conflict_flag,
            class_reason,
        )

        fam_for_path = trim(fam_raw) if fam_raw is not None else "unknown"
        npk_for_path = trim(npk_raw) if npk_raw is not None else ""

        target_local_path: str | None = None
        if readiness in ("READY", "REVIEW_REQUIRED"):
            target_local_path = build_target_local_path(
                fam_for_path,
                npk_for_path,
                canonical_trimmed,
                nvk_raw,
            )

        if readiness == "BLOCKED_UNKNOWN" and not canonical_trimmed:
            bandai_url = None
        elif canonical_trimmed:
            bandai_url = f"{CDN_PREFIX}{canonical_trimmed}.png"
        else:
            bandai_url = None

        entries.append(
            {
                "card_variants_id": rid,
                "canonical_code": jstr_or_null(cc_db),
                "print_id": print_id,
                "canonical_code_agreed": agreed,
                "normalized_product_key": jstr_or_null(npk_raw),
                "normalized_variant_family": jstr_or_null(fam_raw),
                "normalized_variant_key": None
                if nvk_raw is None
                else trim(nvk_raw),
                "bandai_cdn_candidate_url": bandai_url,
                "target_local_path": target_local_path,
                "fetch_readiness": readiness,
                "readiness_reason": reason,
            }
        )

    # Path uniqueness: READY + REVIEW_REQUIRED only, non-null path
    active_by_path: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        if e["fetch_readiness"] in ("READY", "REVIEW_REQUIRED"):
            pth = e["target_local_path"]
            if pth:
                active_by_path[pth].append(i)

    collision_paths: list[str] = []
    for path_key, idxs in active_by_path.items():
        if len(idxs) > 1:
            ids_set = {entries[j]["card_variants_id"] for j in idxs}
            if len(ids_set) > 1:
                collision_paths.append(path_key)
                for j in idxs:
                    ent = entries[j]
                    ent["fetch_readiness"] = "REVIEW_REQUIRED"
                    ent["readiness_reason"] = "path_collision"

    ready_count = sum(1 for e in entries if e["fetch_readiness"] == "READY")
    review_count = sum(1 for e in entries if e["fetch_readiness"] == "REVIEW_REQUIRED")
    blocked_count = sum(1 for e in entries if e["fetch_readiness"] == "BLOCKED_UNKNOWN")

    all_families: set[str] = set()
    for e in entries:
        all_families.add(family_bucket_key(e["normalized_variant_family"]))
    all_families.update(step1_by_family.keys())

    family_breakdown: dict[str, dict[str, int]] = {}
    for fam in sorted(all_families):
        family_breakdown[fam] = {
            "ready": 0,
            "review_required": 0,
            "blocked_unknown": 0,
            "total": 0,
        }

    for e in entries:
        fk = family_bucket_key(e["normalized_variant_family"])
        if fk not in family_breakdown:
            family_breakdown[fk] = {
                "ready": 0,
                "review_required": 0,
                "blocked_unknown": 0,
                "total": 0,
            }
        st = e["fetch_readiness"]
        if st == "READY":
            family_breakdown[fk]["ready"] += 1
        elif st == "REVIEW_REQUIRED":
            family_breakdown[fk]["review_required"] += 1
        else:
            family_breakdown[fk]["blocked_unknown"] += 1
        family_breakdown[fk]["total"] += 1

    unclassified_ready = sum(
        1
        for e in entries
        if e["fetch_readiness"] == "READY"
        and (e["normalized_product_key"] or "").upper() == "_UNCLASSIFIED"
    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_entries": len(entries),
        "ready_count": ready_count,
        "review_required_count": review_count,
        "blocked_unknown_count": blocked_count,
        "family_breakdown": family_breakdown,
        "entries": entries,
    }

    OUT_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Reconciliation manifest totals vs Step 1 ---
    manifest_fam_totals = {k: v["total"] for k, v in family_breakdown.items()}
    reconcile_ok = sum(step1_by_family.values()) == len(entries) == sum(
        manifest_fam_totals.values()
    )
    step1_keys = set(step1_by_family.keys())
    man_keys = set(manifest_fam_totals.keys())
    fam_key_mismatch = step1_keys.symmetric_difference(man_keys)

    # Group examples by reason
    def group_by_reason(
        status: str,
    ) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
        counts: dict[str, int] = defaultdict(int)
        examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            if e["fetch_readiness"] != status:
                continue
            r = e["readiness_reason"] or "(null)"
            counts[r] += 1
            if len(examples[r]) < 20:
                examples[r].append(e)
        return dict(counts), dict(examples)

    rev_counts, rev_ex = group_by_reason("REVIEW_REQUIRED")
    blk_counts, blk_ex = group_by_reason("BLOCKED_UNKNOWN")

    ready_by_pk = defaultdict(int)
    for e in entries:
        if e["fetch_readiness"] == "READY":
            pk = e["normalized_product_key"] or "(null)"
            ready_by_pk[pk] += 1
    top_pk = sorted(ready_by_pk.items(), key=lambda x: -x[1])[:20]

    # --- Print summary ---
    print("=== Phase A Step 2 (revised) - image fetch manifest ===")
    print(f"total_entries: {len(entries)}")
    print(f"READY: {ready_count}")
    print(f"REVIEW_REQUIRED: {review_count}")
    print(f"BLOCKED_UNKNOWN: {blocked_count}")
    print(f"path_collision_count: {len(collision_paths)}")
    if collision_paths:
        print("collision paths:")
        for cp in collision_paths:
            print(f"  {cp}")
    print(
        f"canonical_code vs print_id disagreements (informational): {disagree_count}"
    )
    print(f"READY with normalized_product_key _UNCLASSIFIED: {unclassified_ready}")
    print()

    print("family_breakdown (READY / REVIEW_REQUIRED / BLOCKED_UNKNOWN / TOTAL):")
    for fam in sorted(family_breakdown.keys()):
        b = family_breakdown[fam]
        print(
            f"  {fam!r}: ready={b['ready']} review={b['review_required']} "
            f"blocked={b['blocked_unknown']} total={b['total']}"
        )
    print()
    print("Step 1 classification log totals by family (for reconciliation):")
    for fam in sorted(step1_by_family.keys()):
        print(f"  {fam!r}: {step1_by_family[fam]}")
    print(f"step1_total_rows: {sum(step1_by_family.values())}")
    print(f"manifest_total_rows: {len(entries)}")
    print(f"sum_manifest_family_total: {sum(manifest_fam_totals.values())}")
    print(f"reconcile_row_counts_ok: {reconcile_ok}")
    if fam_key_mismatch:
        print(f"family_key_symmetric_diff_vs_step1: {sorted(fam_key_mismatch)}")
    print()

    print("Top 20 READY by normalized_product_key:")
    for k, v in top_pk:
        print(f"  {k!r}: {v}")
    print()

    print("REVIEW_REQUIRED by readiness_reason (count + up to 20 examples each):")
    for reason in sorted(rev_counts.keys(), key=lambda x: (-rev_counts[x], x)):
        print(f"  [{reason}] count={rev_counts[reason]}")
        for ex in rev_ex.get(reason, []):
            print(
                f"    id={ex['card_variants_id']} path={ex['target_local_path']!r} "
                f"pk={ex['normalized_product_key']!r} fam={ex['normalized_variant_family']!r}"
            )
    print()

    print("BLOCKED_UNKNOWN by readiness_reason (count + up to 20 examples each):")
    for reason in sorted(blk_counts.keys(), key=lambda x: (-blk_counts[x], x)):
        print(f"  [{reason}] count={blk_counts[reason]}")
        for ex in blk_ex.get(reason, []):
            print(
                f"    id={ex['card_variants_id']} canon={ex['canonical_code']!r} "
                f"pk={ex['normalized_product_key']!r} fam={ex['normalized_variant_family']!r}"
            )

    print()
    print(f"manifest written: {OUT_PATH}")

    collisions_ok = len(collision_paths) == 0
    unclass_ok = unclassified_ready == 0
    status = (
        "CONFIRMED WORKING"
        if collisions_ok and unclass_ok and reconcile_ok
        else "INCONCLUSIVE"
    )
    if not DB_PATH.is_file() or not OUT_PATH.is_file():
        status = "FAILED"
    print(f"status: {status}")

    print()
    print("=== COMPLETION CONTRACT ===")
    print(f"total_entries: {len(entries)}")
    print(f"ready_count: {ready_count}")
    print(f"review_required_count: {review_count}")
    print(f"blocked_unknown_count: {blocked_count}")
    print(f"family_breakdown: (printed above)")
    print(f"path_collision_count: {len(collision_paths)}")
    print(f"ready_UNCLASSIFIED_product_key_count: {unclassified_ready}")
    print("REVIEW_REQUIRED reason counts:", rev_counts)
    print("BLOCKED_UNKNOWN reason counts:", blk_counts)
    print(f"manifest_path: {OUT_PATH}")
    print(f"status: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
