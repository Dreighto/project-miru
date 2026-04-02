#!/usr/bin/env python3
"""
Move _r-suffix variant PNGs under D:\\Miru_Assets to
D:\\Miru_Assets\\<set>\\reprints\\<same filename> (e.g. EB01-003_r1.png).

Only files where get_target_subfolder() is \"reprints\" and the name contains _r+digits are
considered; _p and other variants are ignored (no logging). Default: dry-run. Use --commit to
execute shutil.move and write D:\\Miru_Assets\\variant_move_log.txt. Does not touch card_catalog.db.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil

ASSETS_ROOT = r"D:\Miru_Assets"
LOG_PATH = os.path.join(ASSETS_ROOT, "variant_move_log.txt")

# Folders to skip — base card folders only (same as diag_variant_image_audit.py)
SKIP_SUBFOLDERS = {"base"}


def collect_variant_files() -> list[dict]:
    variant_files: list[dict] = []
    for set_folder in os.listdir(ASSETS_ROOT):
        set_path = os.path.join(ASSETS_ROOT, set_folder)
        if not os.path.isdir(set_path):
            continue
        for subfolder in os.listdir(set_path):
            if subfolder in SKIP_SUBFOLDERS:
                continue
            sub_path = os.path.join(set_path, subfolder)
            if not os.path.isdir(sub_path):
                continue
            for fname in os.listdir(sub_path):
                if fname.endswith(".png"):
                    variant_files.append(
                        {
                            "filename": fname,
                            "current_folder": set_folder,
                            "current_subfolder": subfolder,
                            "current_path": os.path.join(sub_path, fname),
                        }
                    )
    return variant_files


def get_target_subfolder(filename: str) -> str:
    name = filename.replace(".png", "")
    if "_sp" in name:
        return "sp"
    if "_tr" in name:
        return "tr"
    if "_gmr" in name:
        return "alt_art"
    if "_mr" in name:
        return "alt_art"
    if "_ir" in name:
        return "alt_art"
    if "_alt" in name:
        return "alt_art"
    if re.search(r"_r\d+$", name):
        return "reprints"
    if re.search(r"_p\d+$", name):
        return "reprints"
    return "unknown"


def get_base_set(filename: str) -> str | None:
    m = re.match(r"^([A-Z0-9]+-\d+)", filename)
    if m:
        code = m.group(1)
        parts = code.split("-")
        return parts[0]
    return None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _under_assets(path: str) -> bool:
    root = _norm(ASSETS_ROOT)
    ap = _norm(path)
    try:
        return os.path.commonpath([root, ap]) == root
    except ValueError:
        return False


def build_plans(
    variant_files: list[dict],
) -> tuple[list[dict], list[tuple[dict, str]], int, int]:
    plans: list[dict] = []
    errors: list[tuple[dict, str]] = []
    already_in_place = 0
    r_files_found = 0

    for f in variant_files:
        fname = f["filename"]
        name = fname.replace(".png", "")
        target_sub = get_target_subfolder(fname)
        if target_sub != "reprints" or not re.search(r"_r\d+", name):
            continue

        r_files_found += 1
        src = f["current_path"]
        base_set = get_base_set(fname)

        if base_set is None:
            errors.append((f, "unknown code"))
            continue

        dest_dir = os.path.join(ASSETS_ROOT, base_set, target_sub)
        dest_path = os.path.join(dest_dir, fname)

        if _norm(src) == _norm(dest_path):
            already_in_place += 1
            continue

        if not _under_assets(src) or not _under_assets(dest_path):
            errors.append((f, "path outside Miru_Assets"))
            continue

        plans.append(
            {
                "filename": fname,
                "src": src,
                "dest": dest_path,
                "dest_dir": dest_dir,
            }
        )

    return plans, errors, already_in_place, r_files_found


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Move variant images under Miru_Assets to set/subfolder layout (dry-run by default)."
    )
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Execute moves and write variant_move_log.txt under Miru_Assets.",
    )
    args = ap.parse_args()

    if not os.path.isdir(ASSETS_ROOT):
        print(f"ERROR: ASSETS_ROOT not found: {ASSETS_ROOT}")
        return 2

    variant_files = collect_variant_files()
    plans, errors, already_in_place, r_files_found = build_plans(variant_files)

    print(f"Scanned (all non-base PNGs): {len(variant_files)}")
    print(f"Total _r files found: {r_files_found}")
    print(f"Already in correct location (_r only): {already_in_place}")
    print(f"Planning errors (_r only): {len(errors)}")
    print(f"Planned moves (_r only): {len(plans)}")
    print()

    for f, reason in errors:
        print(f"ERROR {f['current_path']} — {reason}")

    if not args.commit:
        for p in plans:
            print(f"PLAN: {p['src']} -> {p['dest']}")
        print()
        print("--- summary (dry-run) ---")
        print(f"moved: 0  skipped: 0  errors: {len(errors)}")
        print(f"planned_moves: {len(plans)}")
        return 0 if not errors else 1

    log_lines: list[str] = []
    for f, reason in errors:
        log_lines.append(f"ERROR {f['current_path']} — {reason} (not moved)")

    moved = skipped = op_errors = 0

    for p in plans:
        src = p["src"]
        dest = p["dest"]
        dest_dir = p["dest_dir"]
        try:
            if not os.path.isfile(src):
                msg = f"ERROR {src} — source missing"
                print(msg)
                log_lines.append(msg)
                op_errors += 1
                continue

            os.makedirs(dest_dir, exist_ok=True)

            if os.path.exists(dest):
                if _norm(src) == _norm(dest):
                    msg = f"SKIP {src} — already at destination"
                else:
                    msg = f"SKIP {src} -> {dest} — destination exists"
                print(msg)
                log_lines.append(msg)
                skipped += 1
                continue

            shutil.move(src, dest)
            msg = f"MOVED {src} -> {dest}"
            print(msg)
            log_lines.append(msg)
            moved += 1
        except OSError as e:
            msg = f"ERROR {src} -> {dest} — {e}"
            print(msg)
            log_lines.append(msg)
            op_errors += 1

    with open(LOG_PATH, "w", encoding="utf-8") as lf:
        lf.write("\n".join(log_lines))
        lf.write("\n")
        lf.write(
            f"\nSUMMARY moved={moved} skipped={skipped} errors={op_errors} planning_errors={len(errors)}\n"
        )

    print()
    print("--- summary (--commit) ---")
    print(f"moved: {moved}")
    print(f"skipped: {skipped}")
    print(f"errors: {op_errors}")
    print(f"log: {LOG_PATH}")
    return 0 if op_errors == 0 and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
