"""OP01 non-base image quality audit and refresh script."""
import os
import re
import sqlite3
import json
import csv
from pathlib import Path
from collections import Counter

IMAGE_ROOT = Path("F:/OPTCG_Images")
DB_PATH = Path("data/card_catalog.db")
OUTPUT_SAMPLING = Path("data/overlays/op01_image_quality_sampling.csv")
OUTPUT_REFRESH = Path("data/overlays/op01_image_quality_refresh_results.csv")
RAW_AUDIT = Path("data/overlays/_op01_image_audit_raw.json")

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def get_dims(full_path):
    if not HAS_PIL:
        return None, None
    try:
        with PILImage.open(full_path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def build_disk_index():
    """Build comprehensive index of all OP01 files on F:/OPTCG_Images."""
    card_files = {}
    for dirpath, _, filenames in os.walk(str(IMAGE_ROOT)):
        for fn in filenames:
            if "OP01" not in fn.upper():
                continue
            m = re.match(r"(OP01-\d+)", fn, re.IGNORECASE)
            if not m:
                continue
            cc = m.group(1).upper()
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, str(IMAGE_ROOT))
            sz = os.path.getsize(full)
            card_files.setdefault(cc, []).append((fn, rel, sz, dirpath))
    return card_files


def normalize(s):
    return (
        s.lower()
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
        .replace("-", "")
        .replace("_", "")
        .replace(".", "")
    )


def _is_low_quality_dir(dirpath):
    low = dirpath.lower()
    return any(t in low for t in ["thumb", "training", "_tmp", "miru_image_training"])


def find_best_file(v, card_files):
    """Find the best available file on disk for a given variant.

    Strategy: score ALL candidate files, prefer high-res over thumbs.
    """
    pid = v["print_id"]
    cc = pid.split("::")[0].split("_")[0].upper()
    vkey = v["variant_key"]
    vlabel = (v["variant_label"] or "").lower().strip()

    candidates = card_files.get(cc, [])
    if not candidates:
        return None, 0, "no_match"

    def match_score(fn, rel, sz, dirp):
        fn_base = os.path.splitext(fn)[0]
        fn_norm = normalize(fn_base)
        cc_norm = normalize(cc)
        score = 0

        if "::" in pid:
            label = pid.split("::")[1].strip().lower()
            label_norm = normalize(label)
            vlabel_norm = normalize(vlabel)

            if fn_norm == cc_norm:
                return -1

            matched = False
            if label_norm and (label_norm in fn_norm or fn_norm.endswith(label_norm)):
                score += 100
                matched = True
            elif vlabel_norm and (vlabel_norm in fn_norm or fn_norm.endswith(vlabel_norm)):
                score += 90
                matched = True

            if not matched:
                return -1

        elif "_p" in pid or "_r" in pid:
            suffix = pid.replace(cc, "").lower()
            fn_check = os.path.splitext(fn.lower())[0]
            if suffix in fn_check:
                score += 100
            else:
                return -1

        elif vkey == "base":
            if fn_norm == cc_norm:
                score += 100
            else:
                return -1
        else:
            return -1

        if not _is_low_quality_dir(dirp):
            score += 50

        if sz > 5_000_000:
            score += 30
        elif sz > 1_000_000:
            score += 20
        elif sz > 100_000:
            score += 10

        ext = os.path.splitext(fn)[1].lower()
        if ext == ".png":
            score += 5
        elif ext in (".jpg", ".jpeg"):
            score += 3
        elif ext == ".webp":
            score += 1

        if "alts" in dirp.lower():
            score += 20

        return score

    scored = []
    for fn, rel, sz, dirp in candidates:
        s = match_score(fn, rel, sz, dirp)
        if s >= 0:
            scored.append((s, fn, rel, sz, dirp))

    if not scored:
        return None, 0, "no_match"

    scored.sort(key=lambda x: -x[0])
    _, best_fn, best_rel, best_sz, best_dir = scored[0]

    if _is_low_quality_dir(best_dir):
        method = "thumb_fallback"
    elif "alts" in best_dir.lower():
        method = "alts_highres"
    elif best_sz > 1_000_000:
        method = "highres_match"
    else:
        method = "lowres_match"

    return best_rel, best_sz, method


def classify_quality(disk_path, disk_size, w, h):
    if not disk_path:
        return "NO_FILE"
    if w and w >= 2000:
        return "GOOD_ENOUGH"
    if w and w >= 1000:
        return "BORDERLINE"
    if w and w >= 500:
        return "LOW_RES"
    if w:
        return "UNACCEPTABLE"
    if disk_size > 1_000_000:
        return "GOOD_ENOUGH"
    if disk_size > 100_000:
        return "BORDERLINE"
    return "UNACCEPTABLE"


def main():
    print(f"PIL available: {HAS_PIL}")
    print(f"Image root: {IMAGE_ROOT}")
    print(f"Image root exists: {IMAGE_ROOT.is_dir()}")
    print()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
    SELECT cv.id as printing_id, cv.print_id, cv.variant_key, cv.variant_label,
           cv.release_set_code, cv.image_path,
           cv.is_alt, cv.is_sp, cv.is_tr, cv.is_manga_rare,
           ia.id as ia_id, ia.local_path as ia_local_path, ia.source_url,
           ia.source_label, ia.checksum, ia.image_confidence
    FROM card_variants cv
    LEFT JOIN image_assets ia ON ia.printing_id = cv.id AND ia.is_primary = 1
    WHERE cv.print_id LIKE 'OP01-%' AND cv.variant_key != 'base'
    ORDER BY cv.print_id, cv.variant_key
    """)
    variants = [dict(r) for r in cur.fetchall()]

    cur.execute("""
    SELECT cv.id as printing_id, cv.print_id, cv.variant_key, cv.variant_label,
           cv.release_set_code, cv.image_path,
           0 as is_alt, 0 as is_sp, 0 as is_tr, 0 as is_manga_rare,
           ia.id as ia_id, ia.local_path as ia_local_path, ia.source_url,
           ia.source_label, ia.checksum, ia.image_confidence
    FROM card_variants cv
    LEFT JOIN image_assets ia ON ia.printing_id = cv.id AND ia.is_primary = 1
    WHERE cv.print_id LIKE 'OP01-%' AND cv.variant_key = 'base'
    ORDER BY cv.print_id
    """)
    base_variants = [dict(r) for r in cur.fetchall()]
    conn.close()

    card_files = build_disk_index()
    total_disk_files = sum(len(v) for v in card_files.values())
    print(f"Total OP01 files on disk: {total_disk_files}")
    print(f"Total non-base variants in DB: {len(variants)}")
    print(f"Total base variants in DB: {len(base_variants)}")
    print()

    # === QUALITY SAMPLING AUDIT ===
    base_sample = base_variants[:: max(1, len(base_variants) // 6)][:7]
    sampling_results = []

    for v in base_sample:
        disk_path, disk_size, match_method = find_best_file(v, card_files)
        w, h = (None, None)
        if disk_path:
            w, h = get_dims(str(IMAGE_ROOT / disk_path))
        quality = classify_quality(disk_path, disk_size, w, h)
        sampling_results.append({
            "card_code": v["print_id"],
            "printing_id": v["printing_id"],
            "treatment_category": "base",
            "local_path": disk_path or "",
            "file_size": disk_size,
            "dimensions": f"{w}x{h}" if w else "",
            "quality_class": quality,
            "notes": f"match={match_method}",
        })

    by_vkey = {}
    for v in variants:
        by_vkey.setdefault(v["variant_key"], []).append(v)

    non_base_sample = []
    for vkey in ["alt", "parallel_1", "parallel_3", "parallel_5", "promo", "sp", "mr", "r1", "tr"]:
        items = by_vkey.get(vkey, [])
        if items:
            non_base_sample.extend(items[:2])

    for v in non_base_sample:
        disk_path, disk_size, match_method = find_best_file(v, card_files)
        w, h = (None, None)
        if disk_path:
            w, h = get_dims(str(IMAGE_ROOT / disk_path))
        quality = classify_quality(disk_path, disk_size, w, h)
        cat = v["variant_key"]
        if v["is_sp"]:
            cat = "sp"
        elif v["is_manga_rare"]:
            cat = "manga"
        elif v["is_tr"]:
            cat = "tr"
        sampling_results.append({
            "card_code": v["print_id"],
            "printing_id": v["printing_id"],
            "treatment_category": cat,
            "local_path": disk_path or "",
            "file_size": disk_size,
            "dimensions": f"{w}x{h}" if w else "",
            "quality_class": quality,
            "notes": f"match={match_method}, label={v['variant_label']}",
        })

    with open(str(OUTPUT_SAMPLING), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "card_code", "printing_id", "treatment_category",
            "local_path", "file_size", "dimensions", "quality_class", "notes",
        ])
        writer.writeheader()
        writer.writerows(sampling_results)
    print(f"Sampling results written to {OUTPUT_SAMPLING}")

    base_samples = [r for r in sampling_results if r["treatment_category"] == "base"]
    nonbase_samples = [r for r in sampling_results if r["treatment_category"] != "base"]
    base_unacceptable = sum(
        1 for r in base_samples if r["quality_class"] in ("UNACCEPTABLE", "NO_FILE")
    )
    nonbase_acceptable = sum(
        1 for r in nonbase_samples if r["quality_class"] == "GOOD_ENOUGH"
    )

    print(f"\n=== SAMPLING AUDIT RESULTS ===")
    print(f"Base images sampled: {len(base_samples)}")
    print(f"  Unacceptable base: {base_unacceptable}")
    print(f"Non-base images sampled: {len(nonbase_samples)}")
    print(f"  Already acceptable (GOOD_ENOUGH): {nonbase_acceptable}")
    print()

    for r in sampling_results:
        tag = "*" if r["quality_class"] in ("UNACCEPTABLE", "NO_FILE") else " "
        print(
            f"  {tag} {r['quality_class']:15s} | {r['treatment_category']:12s}"
            f" | {r['card_code']:30s} | {r['dimensions']:>12s}"
            f" | {r['file_size']:>10} bytes | {r['local_path'][:60]}"
        )

    # === FULL AUDIT ===
    all_results = []
    for v in variants:
        disk_path, disk_size, match_method = find_best_file(v, card_files)
        w, h = (None, None)
        if disk_path:
            w, h = get_dims(str(IMAGE_ROOT / disk_path))
        quality = classify_quality(disk_path, disk_size, w, h)
        all_results.append({
            "printing_id": v["printing_id"],
            "print_id": v["print_id"],
            "variant_key": v["variant_key"],
            "variant_label": v["variant_label"],
            "has_image_asset": bool(v["ia_id"]),
            "ia_source_url": v["source_url"] or "",
            "disk_path": disk_path or "",
            "disk_size": disk_size,
            "width": w,
            "height": h,
            "quality": quality,
            "match_method": match_method,
        })

    q_counts = Counter(r["quality"] for r in all_results)
    m_counts = Counter(r["match_method"] for r in all_results)

    print(f"\n=== FULL AUDIT SUMMARY (all {len(all_results)} non-base) ===")
    for q in ["GOOD_ENOUGH", "BORDERLINE", "LOW_RES", "UNACCEPTABLE", "NO_FILE"]:
        print(f"  {q}: {q_counts.get(q, 0)}")

    print(f"\nMatch method distribution:")
    for m, c in sorted(m_counts.items(), key=lambda x: -x[1]):
        print(f"  {m}: {c}")

    has_src = sum(1 for r in all_results if r["ia_source_url"])
    no_file_no_match = [r for r in all_results if r["quality"] == "NO_FILE"]
    no_file_has_src = sum(1 for r in no_file_no_match if r["ia_source_url"])
    print(f"\nWith OPTCG API source_url: {has_src}")
    print(f"NO_FILE total: {len(no_file_no_match)}")
    print(f"NO_FILE but has source_url (fetchable): {no_file_has_src}")
    print(f"NO_FILE and no source_url (gap): {len(no_file_no_match) - no_file_has_src}")

    with open(str(RAW_AUDIT), "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nRaw audit saved to {RAW_AUDIT}")

    return all_results, sampling_results


if __name__ == "__main__":
    main()
