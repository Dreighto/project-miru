"""
validate_instruction_migration.py — verify the v2 instruction architecture migration.

Validates that the migration from monolithic CLAUDE.md/AGENTS.md to a slim core +
overlay/reference architecture preserved all content with no losses or duplicates.

Checks:
  1. Coverage — every paragraph from the pre-split files appears somewhere in
     the new structure (slim core, an overlay, a reference, or AGENTS.md).
  2. No-duplication — no paragraph appears in more than one destination.
  3. Manifest consistency — every overlay/reference declared in the manifest
     exists on disk; every file on disk is declared in the manifest.
  4. Version stamps — every overlay/reference carries the architecture version.

Usage:
    python tools/validate_instruction_migration.py
    python tools/validate_instruction_migration.py --json

Exit codes:
    0 — all checks passed
    1 — coverage gaps, duplicates, or manifest mismatches
    2 — script error (could not run validation)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "docs" / "archive"
OVERLAYS_DIR = REPO_ROOT / ".miru" / "overlays"
REFERENCE_DIR = REPO_ROOT / ".miru" / "reference"
MANIFEST_PATH = REPO_ROOT / ".miru" / "instruction_manifest.json"

EXPECTED_VERSION = "MIRU-INSTRUCTIONS-v2"
# Set to 0 to count all non-structural paragraphs (rely on header/divider filters
# instead of length). Raise this only if you need to suppress short bolded prefixes
# that aren't really rules.
SHORT_PARAGRAPH_THRESHOLD = 0


def _normalize(text: str) -> str:
    """Normalize a paragraph for comparison: collapse whitespace, lowercase, strip markdown emphasis."""
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[*_`]", "", text)
    return text


def _paragraphs(path: Path) -> list[str]:
    """Split a markdown file into substantive paragraphs.

    Code fences are extracted as their own paragraphs (so rule templates inside
    fenced blocks are part of the coverage check). Headers and `---` dividers
    are skipped because they carry no binding rule content.
    """
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")

    # Extract fenced code blocks as their own paragraphs, then remove them from
    # the prose stream so we can split prose paragraphs cleanly.
    paragraphs: list[str] = []
    fence_re = re.compile(r"```[\s\S]*?```", re.MULTILINE)
    for match in fence_re.finditer(content):
        block = match.group(0).strip()
        if block:
            paragraphs.append(block)
    content_without_fences = fence_re.sub("\n\n", content)

    for block in re.split(r"\n\s*\n", content_without_fences):
        block = block.strip()
        if not block:
            continue
        # Skip pure headers (single-line ##/### lines) and `---` dividers.
        if re.match(r"^#+\s", block) and "\n" not in block:
            continue
        if re.match(r"^---+$", block):
            continue
        if SHORT_PARAGRAPH_THRESHOLD and len(block) < SHORT_PARAGRAPH_THRESHOLD:
            continue
        paragraphs.append(block)
    return paragraphs


def _hash_paragraph(p: str) -> str:
    return hashlib.sha1(_normalize(p).encode("utf-8")).hexdigest()[:12]


def _all_destination_files() -> list[Path]:
    files = [REPO_ROOT / "CLAUDE.md", REPO_ROOT / "AGENTS.md"]
    files.extend(sorted(OVERLAYS_DIR.glob("*.md")))
    files.extend(sorted(REFERENCE_DIR.glob("*.md")))
    return files


def check_coverage() -> tuple[list[str], dict[str, list[str]]]:
    """For each paragraph in the archive, find which destination(s) contain it.

    Also flags any paragraph that appears in 2+ destination files, regardless
    of whether it existed in the archive. This catches duplicates introduced
    after the migration as well as ones that survived from the archive.

    Returns (missing_hashes, duplicate_locations).
    missing_hashes: archive paragraph hashes that don't appear in any destination
    duplicate_locations: paragraph hash -> sorted list of destination paths (when > 1)
    """
    archive_files = sorted(ARCHIVE_DIR.glob("*PRE_SPLIT*.md"))
    if not archive_files:
        raise FileNotFoundError(
            "No archive files matching docs/archive/*PRE_SPLIT*.md found."
            " Coverage validation cannot proceed without source snapshots."
        )

    archive_paragraphs: dict[str, str] = {}  # hash -> first 80 chars
    for f in archive_files:
        for p in _paragraphs(f):
            archive_paragraphs[_hash_paragraph(p)] = p[:80].replace("\n", " ")

    # Map hash -> set of unique destination files, plus a preview snippet.
    # A paragraph repeated within the same file is not a duplicate; only a
    # paragraph appearing in TWO different destination files is.
    destination_locations: dict[str, set[str]] = {}
    destination_previews: dict[str, str] = {}
    for dest in _all_destination_files():
        rel = str(dest.relative_to(REPO_ROOT))
        for p in _paragraphs(dest):
            h = _hash_paragraph(p)
            destination_locations.setdefault(h, set()).add(rel)
            destination_previews.setdefault(h, p[:80].replace("\n", " "))

    missing: list[str] = []
    for h, preview in archive_paragraphs.items():
        if not destination_locations.get(h):
            missing.append(f"{h}: {preview}")

    # Cross-destination duplicates: any hash appearing in 2+ files (whether
    # from archive or not).
    duplicates: dict[str, list[str]] = {}
    for h, locations in destination_locations.items():
        if len(locations) > 1:
            preview = archive_paragraphs.get(h, destination_previews.get(h, "(no preview)"))
            duplicates[f"{h}: {preview}"] = sorted(locations)

    return missing, duplicates


def check_manifest() -> list[str]:
    """Verify manifest matches disk state."""
    issues: list[str] = []
    if not MANIFEST_PATH.exists():
        return [f"manifest missing: {MANIFEST_PATH.relative_to(REPO_ROOT)}"]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("architecture_version") != EXPECTED_VERSION:
        issues.append(
            f"manifest version mismatch: expected {EXPECTED_VERSION},"
            f" got {manifest.get('architecture_version')}"
        )

    declared_overlays = set(manifest.get("overlays", {}).keys())
    on_disk_overlays = {p.stem for p in OVERLAYS_DIR.glob("*.md")}
    only_declared = declared_overlays - on_disk_overlays
    only_on_disk = on_disk_overlays - declared_overlays
    if only_declared:
        issues.append(f"overlays in manifest but not on disk: {sorted(only_declared)}")
    if only_on_disk:
        issues.append(f"overlays on disk but not in manifest: {sorted(only_on_disk)}")

    # Verify each declared `path` value actually points to a file on disk.
    for key, entry in manifest.get("overlays", {}).items():
        path_value = (entry or {}).get("path", "")
        if path_value and not (REPO_ROOT / path_value).exists():
            issues.append(f"manifest overlay path missing on disk: {key} -> {path_value}")

    declared_ref = set(manifest.get("reference", {}).keys())
    on_disk_ref = {p.stem for p in REFERENCE_DIR.glob("*.md")}
    only_declared = declared_ref - on_disk_ref
    only_on_disk = on_disk_ref - declared_ref
    if only_declared:
        issues.append(f"reference in manifest but not on disk: {sorted(only_declared)}")
    if only_on_disk:
        issues.append(f"reference on disk but not in manifest: {sorted(only_on_disk)}")

    for key, entry in manifest.get("reference", {}).items():
        path_value = (entry or {}).get("path", "")
        if path_value and not (REPO_ROOT / path_value).exists():
            issues.append(f"manifest reference path missing on disk: {key} -> {path_value}")

    # Core, baseline, and archive paths
    core_path = (manifest.get("core") or {}).get("path", "")
    if core_path and not (REPO_ROOT / core_path).exists():
        issues.append(f"manifest core path missing on disk: {core_path}")
    baseline_path = (manifest.get("baseline") or {}).get("path", "")
    if baseline_path and not (REPO_ROOT / baseline_path).exists():
        issues.append(f"manifest baseline path missing on disk: {baseline_path}")
    for key, archive_path in (manifest.get("archive") or {}).items():
        if archive_path and not (REPO_ROOT / archive_path).exists():
            issues.append(f"manifest archive path missing on disk: {key} -> {archive_path}")

    return issues


def check_version_stamps() -> list[str]:
    """Every overlay and reference file must carry the architecture version stamp."""
    issues: list[str] = []
    for path in list(OVERLAYS_DIR.glob("*.md")) + list(REFERENCE_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        if EXPECTED_VERSION not in content:
            issues.append(f"missing version stamp: {path.relative_to(REPO_ROOT)}")
    # Slim core CLAUDE.md must also carry the version stamp
    core = REPO_ROOT / "CLAUDE.md"
    if core.exists() and EXPECTED_VERSION not in core.read_text(encoding="utf-8"):
        issues.append("missing version stamp: CLAUDE.md")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate instruction migration.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--allow-missing",
        type=int,
        default=0,
        help="Tolerate up to N missing paragraphs (for partial migration during dev)",
    )
    args = parser.parse_args()

    missing, duplicates = check_coverage()
    manifest_issues = check_manifest()
    version_issues = check_version_stamps()

    report = {
        "architecture_version": EXPECTED_VERSION,
        "missing_paragraphs": missing,
        "duplicate_paragraphs": duplicates,
        "manifest_issues": manifest_issues,
        "version_stamp_issues": version_issues,
        "missing_count": len(missing),
        "duplicate_count": len(duplicates),
        "passed": (
            len(missing) <= args.allow_missing
            and not duplicates
            and not manifest_issues
            and not version_issues
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Architecture version: {EXPECTED_VERSION}")
        print(f"Missing paragraphs: {len(missing)}")
        if missing and len(missing) <= 20:
            for m in missing:
                print(f"  MISS: {m}")
        elif missing:
            for m in missing[:10]:
                print(f"  MISS: {m}")
            print(f"  ... and {len(missing) - 10} more")
        print(f"Duplicate paragraphs: {len(duplicates)}")
        for h, locs in list(duplicates.items())[:10]:
            print(f"  DUP: {h}")
            for loc in locs:
                print(f"    -> {loc}")
        print(f"Manifest issues: {len(manifest_issues)}")
        for issue in manifest_issues:
            print(f"  {issue}")
        print(f"Version stamp issues: {len(version_issues)}")
        for issue in version_issues:
            print(f"  {issue}")
        print(f"\nPASSED: {report['passed']}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"validation script error: {exc}", file=sys.stderr)
        sys.exit(2)
