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
SHORT_PARAGRAPH_THRESHOLD = 30  # paragraphs shorter than this are skipped (headers, dividers)


def _normalize(text: str) -> str:
    """Normalize a paragraph for comparison: collapse whitespace, lowercase, strip markdown emphasis."""
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[*_`]", "", text)
    return text


def _paragraphs(path: Path) -> list[str]:
    """Split a markdown file into substantive paragraphs (skip headers, dividers, code fences)."""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    # Strip code fences first to avoid splitting inside them
    code_blocks = []

    def _stash(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"\n___CODE_BLOCK_{len(code_blocks) - 1}___\n"

    content = re.sub(r"```[\s\S]*?```", _stash, content)

    paragraphs = []
    for block in re.split(r"\n\s*\n", content):
        block = block.strip()
        if not block:
            continue
        # Skip pure headers, dividers, single-line markers
        if re.match(r"^#+\s", block) and "\n" not in block:
            continue
        if re.match(r"^---+$", block):
            continue
        if block.startswith("___CODE_BLOCK_"):
            continue
        if len(block) < SHORT_PARAGRAPH_THRESHOLD:
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

    Returns (missing_hashes, duplicate_locations).
    missing_hashes: paragraph hashes that don't appear in any destination
    duplicate_locations: paragraph hash -> list of destination paths (when > 1)
    """
    archive_files = sorted(ARCHIVE_DIR.glob("*PRE_SPLIT*.md"))
    archive_paragraphs: dict[str, str] = {}  # hash -> first 80 chars
    for f in archive_files:
        for p in _paragraphs(f):
            archive_paragraphs[_hash_paragraph(p)] = p[:80].replace("\n", " ")

    destination_index: dict[str, list[str]] = {}
    for dest in _all_destination_files():
        for p in _paragraphs(dest):
            h = _hash_paragraph(p)
            destination_index.setdefault(h, []).append(str(dest.relative_to(REPO_ROOT)))

    missing: list[str] = []
    duplicates: dict[str, list[str]] = {}
    for h, preview in archive_paragraphs.items():
        locations = destination_index.get(h, [])
        if not locations:
            missing.append(f"{h}: {preview}")
        elif len(locations) > 1:
            duplicates[f"{h}: {preview}"] = locations

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

    declared_ref = set(manifest.get("reference", {}).keys())
    on_disk_ref = {p.stem for p in REFERENCE_DIR.glob("*.md")}
    only_declared = declared_ref - on_disk_ref
    only_on_disk = on_disk_ref - declared_ref
    if only_declared:
        issues.append(f"reference in manifest but not on disk: {sorted(only_declared)}")
    if only_on_disk:
        issues.append(f"reference on disk but not in manifest: {sorted(only_on_disk)}")

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
