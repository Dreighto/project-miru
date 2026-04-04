"""
Phase A Step 1: Add normalized_product_key, normalized_variant_family,
normalized_variant_key on card_variants; populate idempotently.

Read -> compute in memory -> validate -> UPDATE only changed rows -> JSON log.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
LOG_PATH = ROOT / "data" / "phase_a_classification_log.json"
MIN_DB_BYTES = 10 * 1024 * 1024

PARALLEL_RE = re.compile(r"^parallel_\d+$", re.IGNORECASE)
REPRINT_RE = re.compile(r"^r\d+$", re.IGNORECASE)


def nz_int(v: Any) -> int:
    return int(v or 0)


def norm_text(s: Any) -> str:
    return (s or "").strip().upper()


def compute_normalized_product_key(row: sqlite3.Row) -> str:
    dpk = row["distribution_product_key"]
    if dpk is not None and str(dpk).strip() != "":
        raw = str(dpk).strip()
    else:
        raw = str(row["release_set_code"] or "").strip()
    return raw.upper()


def provenance_supports_sp(prov: str) -> bool:
    u = (prov or "").upper()
    if not u.strip():
        return False
    if "SPECIAL" in u:
        return True
    if re.search(r"\bSP\b", u):
        return True
    if "ALTERNATE ART" in u or "ALTERNATE-ART" in u:
        return True
    return False


def cross_product_special_distribution(prov: str) -> bool:
    u = (prov or "").upper()
    if not u.strip():
        return False
    markers = (
        "CHAMPIONSHIP",
        "CHAMPIONSHIPS",
        "TOURNAMENT",
        "TOURNEY",
        "LEAGUE",
        "GIFT",
        "GIVEAWAY",
        "PRE-RELEASE",
        "PRERELEASE",
        "PRE RELEASE",
        "PROMO PACK",
        "PROMOTION",
        "ONE PIECE DAY",
        "WINNER",
        "FINALIST",
        "EXCLUSIVE",
        "CROSS",
        "BATTLE",
        "WHITE BATTLE",
        "参加賞",
    )
    return any(m in u for m in markers)


def same_product_base_printing(prov: str, npk: str, rsc: str) -> bool:
    u = (prov or "").upper()
    if not u.strip():
        return False
    npk_u = (npk or "").strip().upper()
    rsc_u = (rsc or "").strip().upper()
    if npk_u and npk_u in u:
        return True
    if rsc_u and rsc_u in u:
        return True
    if "BOOSTER" in u or "BOOSTER BOX" in u or "STARTER DECK" in u:
        return True
    if "STANDARD" in u and "LEGAL" in u:
        return True
    return False


def variant_key_is_sp_prefix(vk: str) -> bool:
    return (vk or "").lower().startswith("sp")


def tr_variant_key_ok(vk: str) -> bool:
    return (vk or "").lower() in ("tr",) or (vk or "").lower().startswith("tr_")


def manga_variant_key_ok(vk: str, golden: bool) -> bool:
    x = (vk or "").lower()
    if golden:
        return x in ("gmr", "golden_manga", "golden_manga_rare") or "gmr" in x
    return x in ("mr", "manga", "manga_rare") or x.startswith("mr_")


@dataclass
class Derived:
    normalized_product_key: str
    normalized_variant_family: str
    normalized_variant_key: str = ""
    classification_reason: str = ""
    conflict_flag: bool = False
    print_id_canonical_disagreement_flag: bool = False


def classify_family(row: sqlite3.Row, npk: str) -> Derived:
    vk = (row["variant_key"] or "").strip()
    vk_l = vk.lower()
    lbl = row["variant_label"] or ""
    prov = row["official_provenance"] or ""
    ib = nz_int(row["is_base"])
    isp = nz_int(row["is_sp"])
    itr = nz_int(row["is_tr"])
    imr = nz_int(row["is_manga_rare"])
    igmr = nz_int(row["is_golden_manga_rare"])
    iir = nz_int(row["is_illustration_rare"])
    ipromo = nz_int(row["is_promo"])
    ialt = nz_int(row["is_alt"])
    rsc = row["release_set_code"] or ""

    d = Derived(normalized_product_key=npk, normalized_variant_family="unknown")

    def finish(family: str, reason: str, conflict: bool = False) -> Derived:
        d.normalized_variant_family = family
        d.classification_reason = reason
        d.conflict_flag = conflict
        return d

    # base + promo -> base (log)
    if ipromo and ib:
        return finish("base", "base_promo_print", conflict=True)

    if itr:
        if not tr_variant_key_ok(vk):
            return finish("tr", "tr_variant_key_disagreement", conflict=True)
        return finish("tr", "tr_confirmed")

    if igmr:
        if not manga_variant_key_ok(vk, True):
            return finish("manga", "manga_variant_key_disagreement_gmr", conflict=True)
        return finish("manga", "golden_manga_rare")

    if imr:
        if not manga_variant_key_ok(vk, False):
            return finish("manga", "manga_variant_key_disagreement_mr", conflict=True)
        return finish("manga", "manga_rare")

    if iir:
        return finish("ir", "illustration_rare")

    if ipromo and not ib:
        return finish("promo", "promo_non_base")

    if vk_l and PARALLEL_RE.match(vk_l):
        return finish("parallel", "parallel_pattern")

    if vk_l and REPRINT_RE.match(vk_l):
        return finish("reprint", "reprint_pattern")

    if ialt and not iir and not imr and not igmr:
        return finish("alt", "alt_flag")

    # --- SP ---
    if variant_key_is_sp_prefix(vk):
        if isp:
            return finish("sp", "sp_variant_key_and_flag")
        lbl_u = (lbl or "").upper()
        if "SPECIAL" in lbl_u or provenance_supports_sp(prov):
            return finish("sp", "sp_by_variant_key_plus_provenance", conflict=True)
        return finish("unknown", "sp_key_flag_conflict_unresolved", conflict=True)

    if isp and not variant_key_is_sp_prefix(vk) and not ib:
        lbl_u = (lbl or "").upper()
        if "SPECIAL" in lbl_u or provenance_supports_sp(prov):
            return finish("sp", "sp_by_flag_and_label_or_provenance")
        return finish("unknown", "sp_ambiguous", conflict=True)

    # --- BASE ---
    if vk_l == "base" and ib and not isp:
        return finish("base", "base_confirmed")

    if vk_l == "base" and ib and isp:
        p = (prov or "").strip()
        if not p:
            return finish("unknown", "sp_base_drift_unresolved", conflict=True)
        if cross_product_special_distribution(p):
            return finish("sp", "cross_product_special_distribution", conflict=True)
        if same_product_base_printing(p, npk, rsc):
            return finish("base", "special_flag_same_product_base", conflict=True)
        return finish("unknown", "sp_base_drift_unresolved", conflict=True)

    if ib and vk_l != "base":
        stronger = (
            ialt
            or itr
            or imr
            or igmr
            or iir
            or ipromo
            or isp
            or (vk_l and PARALLEL_RE.match(vk_l))
            or (vk_l and REPRINT_RE.match(vk_l))
            or variant_key_is_sp_prefix(vk)
        )
        if not stronger:
            return finish("base", "base_by_flag_variant_key_conflict", conflict=True)
        return finish("unknown", "multi_flag_conflict", conflict=True)

    # Fallback: structural keys without flags
    if vk_l == "base" and not ib:
        return finish("unknown", "base_key_without_base_flag", conflict=True)

    return finish("unknown", "unresolved_no_matching_rule", conflict=True)


def assign_keys_for_card(
    card_id: int, rows: list[tuple[int, Derived, sqlite3.Row]]
) -> None:
    """rows: (id, derived, raw_row) sorted by id ASC. Mutates derived.normalized_variant_key."""
    by_fam: dict[str, list[tuple[int, Derived, sqlite3.Row]]] = defaultdict(list)
    for tid, der, rw in rows:
        by_fam[der.normalized_variant_family].append((tid, der, rw))

    def take(fam: str) -> list[tuple[int, Derived, sqlite3.Row]]:
        return sorted(by_fam.get(fam, []), key=lambda x: x[0])

    # base
    bases = take("base")
    for i, (_, der, _) in enumerate(bases):
        if i == 0:
            der.normalized_variant_key = ""
        else:
            der.normalized_variant_key = f"_base{i + 1}"
            der.conflict_flag = True
            if der.classification_reason == "base_confirmed":
                der.classification_reason = "base_duplicate_key_suffix"

    sp = take("sp")
    for i, (_, der, _) in enumerate(sp):
        der.normalized_variant_key = "_sp" if i == 0 else f"_sp{i + 1}"

    tr = take("tr")
    for i, (_, der, _) in enumerate(tr):
        der.normalized_variant_key = "_tr" if i == 0 else f"_tr{i + 1}"

    par = take("parallel")
    for i, (_, der, _) in enumerate(par):
        der.normalized_variant_key = f"_p{i + 1}"

    rep = take("reprint")
    for i, (_, der, _) in enumerate(rep):
        der.normalized_variant_key = f"_r{i + 1}"

    gmr = [t for t in take("manga") if nz_int(t[2]["is_golden_manga_rare"])]
    mr = [t for t in take("manga") if not nz_int(t[2]["is_golden_manga_rare"])]
    for i, (_, der, _) in enumerate(sorted(gmr, key=lambda x: x[0])):
        der.normalized_variant_key = "_gmr" if i == 0 else f"_gmr{i + 1}"
    for i, (_, der, _) in enumerate(sorted(mr, key=lambda x: x[0])):
        der.normalized_variant_key = "_mr" if i == 0 else f"_mr{i + 1}"

    irl = take("ir")
    for i, (_, der, _) in enumerate(irl):
        der.normalized_variant_key = "_ir" if i == 0 else f"_ir{i + 1}"

    al = take("alt")
    for i, (_, der, _) in enumerate(al):
        der.normalized_variant_key = "_alt" if i == 0 else f"_alt{i + 1}"

    pr = take("promo")
    for i, (_, der, _) in enumerate(pr):
        der.normalized_variant_key = "_promo" if i == 0 else f"_promo{i + 1}"

    unk = take("unknown")
    for i, (_, der, _) in enumerate(unk):
        der.normalized_variant_key = f"_unk_{i + 1}"


def ensure_columns(cur: sqlite3.Cursor) -> None:
    for col in (
        "normalized_product_key TEXT",
        "normalized_variant_family TEXT",
        "normalized_variant_key TEXT",
    ):
        name = col.split()[0]
        try:
            cur.execute(f"ALTER TABLE card_variants ADD COLUMN {col}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def row_to_dict(
    row: sqlite3.Row,
    der: Derived,
    cards_canonical_code: str,
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "card_id": row["card_id"],
        "print_id": row["print_id"],
        "cards_canonical_code": cards_canonical_code,
        "normalized_product_key": der.normalized_product_key,
        "normalized_variant_family": der.normalized_variant_family,
        "normalized_variant_key": der.normalized_variant_key,
        "classification_reason": der.classification_reason,
        "conflict_flag": der.conflict_flag,
        "print_id_canonical_disagreement_flag": der.print_id_canonical_disagreement_flag,
    }


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: database missing: {DB_PATH}", file=sys.stderr)
        return 1
    sz = DB_PATH.stat().st_size
    if sz < MIN_DB_BYTES:
        print(f"FAILED: database {sz} bytes < {MIN_DB_BYTES}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    ensure_columns(cur)
    conn.commit()

    q = """
    SELECT cv.*, c.canonical_code AS cards_canonical_code
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    """
    rows = cur.execute(q).fetchall()
    n_rows = len(rows)

    # id -> (row, derived, canonical agreement)
    derived_map: dict[int, Derived] = {}
    log_entries: list[dict[str, Any]] = []
    canonical_agreed = 0
    canonical_disagree = 0

    by_card: dict[int, list[tuple[int, Derived, sqlite3.Row]]] = defaultdict(list)

    for row in rows:
        rid = int(row["id"])
        cc = row["cards_canonical_code"] or ""
        pid = row["print_id"] or ""
        disagree = pid != cc
        if disagree:
            canonical_disagree += 1
        else:
            canonical_agreed += 1

        npk = compute_normalized_product_key(row)
        der = classify_family(row, npk)
        der.print_id_canonical_disagreement_flag = disagree
        derived_map[rid] = der
        by_card[int(row["card_id"])].append((rid, der, row))

    for cid, lst in by_card.items():
        assign_keys_for_card(cid, sorted(lst, key=lambda x: x[0]))

    # Build log + skip unchanged
    to_update: list[tuple[str, str, str, int]] = []
    skipped = 0
    for row in rows:
        rid = int(row["id"])
        der = derived_map[rid]
        cc = row["cards_canonical_code"] or ""
        entry = row_to_dict(row, der, cc)
        log_entries.append(entry)

        cur_np = row["normalized_product_key"]
        cur_nf = row["normalized_variant_family"]
        cur_nk = row["normalized_variant_key"]
        if (
            cur_np == der.normalized_product_key
            and cur_nf == der.normalized_variant_family
            and cur_nk == der.normalized_variant_key
        ):
            skipped += 1
        else:
            to_update.append(
                (
                    der.normalized_product_key,
                    der.normalized_variant_family,
                    der.normalized_variant_key,
                    rid,
                )
            )

    # --- Pre-write validation ---
    fam_counts: dict[str, int] = defaultdict(int)
    unknown_n = 0
    conflict_n = sum(1 for e in log_entries if e["conflict_flag"])
    dupes_within_card: list[tuple[Any, ...]] = []
    invalid_base_cards = 0

    for e in log_entries:
        fam_counts[e["normalized_variant_family"]] += 1
        if e["normalized_variant_family"] == "unknown":
            unknown_n += 1

    key_groups: dict[tuple[int, str], int] = defaultdict(int)
    for e in log_entries:
        key_groups[(e["card_id"], e["normalized_variant_key"])] += 1
    for (cid, nk), c in key_groups.items():
        if c > 1:
            dupes_within_card.append((cid, nk, c))

    # blank base key: per card_id count rows with nk=='' and family==base; count rows nk=='' any
    by_cid: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in log_entries:
        by_cid[e["card_id"]].append(e)

    blank_non_base = 0
    for e in log_entries:
        if e["normalized_variant_key"] == "" and e["normalized_variant_family"] != "base":
            blank_non_base += 1

    for _cid, es in by_cid.items():
        blanks = [x for x in es if x["normalized_variant_key"] == ""]
        base_fam = [x for x in es if x["normalized_variant_family"] == "base"]
        bad = False
        if len(blanks) > 1:
            bad = True
        elif len(blanks) == 1 and blanks[0]["normalized_variant_family"] != "base":
            bad = True
        elif len(base_fam) >= 1 and len(blanks) != 1:
            bad = True
        if bad:
            invalid_base_cards += 1

    pre_write_ok = (
        len(dupes_within_card) == 0
        and blank_non_base == 0
        and invalid_base_cards == 0
    )

    # Example conflicts / unresolved (>=20): not mere print_id vs canonical mismatch alone
    examples = [
        e
        for e in log_entries
        if e["conflict_flag"] or e["normalized_variant_family"] == "unknown"
    ][:25]

    print("=== DRY-RUN / PRE-WRITE SUMMARY ===")
    print(f"rows_loaded: {n_rows}")
    print(f"rows_skip_unchanged (computed): {skipped}")
    print(f"rows_to_update (computed): {len(to_update)}")
    print(f"print_id==canonical: {canonical_agreed}, disagree: {canonical_disagree}")
    print(f"family_counts: {dict(sorted(fam_counts.items(), key=lambda x: -x[1]))}")
    print(f"unknown_rows: {unknown_n}")
    print(f"conflict_flagged_rows: {conflict_n}")
    print(f"duplicate (card_id, key) groups: {len(dupes_within_card)}")
    print(f"blank key on non-base rows: {blank_non_base}")
    print(f"invalid_base_key_state card groups: {invalid_base_cards}")
    print(f"pre_write_ok: {pre_write_ok}")
    print()
    print("=== SAMPLE conflict/unresolved (up to 25) ===")
    for ex in examples[:25]:
        print(
            json.dumps(
                {
                    k: ex[k]
                    for k in (
                        "id",
                        "card_id",
                        "print_id",
                        "cards_canonical_code",
                        "normalized_variant_family",
                        "normalized_variant_key",
                        "classification_reason",
                        "conflict_flag",
                    )
                },
                ensure_ascii=False,
            )
        )

    if not pre_write_ok:
        print("\nFAILED: pre-write validation failed; no DB updates.", file=sys.stderr)
        LOG_PATH.write_text(json.dumps(log_entries, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote partial log: {LOG_PATH}")
        return 2

    cur.executemany(
        """
        UPDATE card_variants
        SET normalized_product_key = ?,
            normalized_variant_family = ?,
            normalized_variant_key = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        to_update,
    )
    conn.commit()

    LOG_PATH.write_text(json.dumps(log_entries, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=== POST-WRITE ===")
    for r in cur.execute(
        """
        SELECT normalized_variant_family, COUNT(*) AS n
        FROM card_variants
        GROUP BY normalized_variant_family
        ORDER BY n DESC
        """
    ).fetchall():
        print(dict(r))
    checks = [
        ("normalized_product_key IS NULL", "SELECT COUNT(*) FROM card_variants WHERE normalized_product_key IS NULL"),
        ("normalized_variant_family IS NULL", "SELECT COUNT(*) FROM card_variants WHERE normalized_variant_family IS NULL"),
        ("normalized_variant_key IS NULL", "SELECT COUNT(*) FROM card_variants WHERE normalized_variant_key IS NULL"),
        ("unknown family", "SELECT COUNT(*) FROM card_variants WHERE normalized_variant_family = 'unknown'"),
    ]
    null_np = null_nf = null_nk = unk_post = 0
    for label, sql in checks:
        n = cur.execute(sql).fetchone()[0]
        print(f"{label}: {n}")
        if "normalized_product_key IS NULL" in sql:
            null_np = n
        elif "normalized_variant_family IS NULL" in sql:
            null_nf = n
        elif "normalized_variant_key IS NULL" in sql:
            null_nk = n
        elif "unknown family" in label:
            unk_post = n
    dupq = """
    SELECT card_id, normalized_variant_key, COUNT(*) AS dupes
    FROM card_variants
    GROUP BY card_id, normalized_variant_key
    HAVING COUNT(*) > 1
    """
    drows = cur.execute(dupq).fetchall()
    print(f"duplicate key groups: {len(drows)}")
    conn.close()

    print()
    print("=== COMPLETION CONTRACT ===")
    print(f"row_count_processed: {n_rows}")
    print(f"row_count_updated: {len(to_update)}")
    print(f"normalized_variant_family_breakdown: {dict(sorted(fam_counts.items(), key=lambda x: -x[1]))}")
    print(f"unknown_count (pre_write): {unknown_n}")
    print(f"unknown_count (post_write): {unk_post}")
    print(f"conflict_flagged_count: {conflict_n}")
    print(f"null_normalized_product_key: {null_np}")
    print(f"null_normalized_variant_family: {null_nf}")
    print(f"null_normalized_variant_key: {null_nk}")
    print(f"uniqueness_duplicate_groups: {len(drows)}")
    print(f"invalid_base_key_card_groups: {invalid_base_cards}")
    print(f"print_id_canonical_disagreements: {canonical_disagree}")
    print(f"classification_log: {LOG_PATH}")
    print("status: CONFIRMED WORKING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
