"""
miru_run_sandbox_cycle.py

Bounded sandbox orchestration for Project Miru.

Runs the full approved local-file intake + intelligence pipeline in a safe,
deterministic, auditable order.  All inputs must be local files supplied
explicitly on the CLI.  No autonomous web fetching, no background daemons,
no writes outside the worktree data paths.

Stage order
-----------
1. Card CSV intake     miru_import_card_csv  -> snapshot JSON
                       miru_refresh_official_snapshot  -> dossiers DB
2. Banlist staging     miru_fetch_banlist  -> staging CSV (DB write deferred)
3. Deck fetch+import   miru_fetch_decklist -> staging CSV
                       miru_import_decklist -> deck_intel DB
4. Summarize stats     miru_summarize_deck_stats
5. Deck signals        miru_compute_deck_signals
6. Cost curves         miru_compute_cost_curves
7. Trait signals       miru_compute_trait_signals

A stage is SKIP when its required inputs are absent.
A stage is WARN when it completes with non-fatal issues.
A stage is FAIL when it exits non-zero.  Stages 4-7 are skipped on
any FAIL in stage 3 or earlier to avoid computing over corrupt data.

Usage
-----
    python -m tools.miru_run_sandbox_cycle \\
        --card-files  data/intake/eb04_cards.csv \\
        --banlist-files data/banlist/op_format.json \\
        --deck-files  data/decks/op01_top8/*.json \\
        --dry-run

    python -m tools.miru_run_sandbox_cycle \\
        --card-dir  data/intake \\
        --deck-dir  data/decks \\
        --db-path   data/miru_deck_intel.db \\
        --dossiers-db data/miru_dossiers.db
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"
DEFAULT_STAGING_DIR = ROOT / "data" / "staging"

# Ensure project root is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Stage result tracking
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    name: str
    status: str = "SKIP"      # OK | SKIP | WARN | FAIL
    count: int = 0             # primary unit processed (files, decks, etc.)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def ok(self, count: int = 0, note: str = "") -> None:
        self.status = "OK"
        self.count = count
        if note:
            self.notes.append(note)

    def warn(self, msg: str) -> None:
        if self.status not in ("FAIL",):
            self.status = "WARN"
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        self.status = "FAIL"
        self.errors.append(msg)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"

    @property
    def skipped(self) -> bool:
        return self.status == "SKIP"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Miru sandbox pipeline end-to-end using approved local input files. "
            "No web fetching. No writes outside the worktree data paths."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    inp = parser.add_argument_group("input files / directories (at least one group needed)")
    inp.add_argument(
        "--card-files",
        nargs="*",
        metavar="CSV",
        default=[],
        help="Card CSV file(s) to ingest via miru_import_card_csv.",
    )
    inp.add_argument(
        "--card-dir",
        default="",
        metavar="DIR",
        help="Directory; all *.csv files inside are treated as card intake files.",
    )
    inp.add_argument(
        "--banlist-files",
        nargs="*",
        metavar="JSON",
        default=[],
        help="Banlist JSON file(s) to stage via miru_fetch_banlist.",
    )
    inp.add_argument(
        "--banlist-dir",
        default="",
        metavar="DIR",
        help="Directory; all *.json files inside are treated as banlist files.",
    )
    inp.add_argument(
        "--deck-files",
        nargs="*",
        metavar="JSON",
        default=[],
        help="Decklist JSON file(s) to ingest via miru_fetch_decklist + miru_import_decklist.",
    )
    inp.add_argument(
        "--deck-dir",
        default="",
        metavar="DIR",
        help="Directory; all *.json files inside are treated as decklist files.",
    )

    db = parser.add_argument_group("database paths")
    db.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {DEFAULT_DB_PATH}).",
    )
    db.add_argument(
        "--dossiers-db",
        default="",
        metavar="PATH",
        help=f"Path to miru_dossiers.db (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    db.add_argument(
        "--staging-dir",
        default="",
        metavar="DIR",
        help=f"Directory for intermediate staging files (default: {DEFAULT_STAGING_DIR}).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate and preview each stage without writing to any database. "
            "Snapshot JSONs and staging CSVs are still written to --staging-dir."
        ),
    )
    parser.add_argument(
        "--stop-on-warn",
        action="store_true",
        help="Treat WARN stages as hard failures and stop the cycle.",
    )
    return parser


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "db_path": Path(args.db_path) if args.db_path else DEFAULT_DB_PATH,
        "dossiers_db": Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH,
        "staging_dir": Path(args.staging_dir) if args.staging_dir else DEFAULT_STAGING_DIR,
    }


def collect_files(explicit: list[str], dir_path: str, glob: str) -> list[Path]:
    files: list[Path] = [Path(p) for p in explicit if p]
    if dir_path:
        d = Path(dir_path)
        if d.is_dir():
            files.extend(sorted(d.glob(glob)))
    return [f for f in files if f.exists() and f.is_file()]


# ---------------------------------------------------------------------------
# Input approval gate
# ---------------------------------------------------------------------------

# Any input that starts with one of these is a URL and must be rejected.
_URL_SCHEMES = (
    "http://", "https://", "ftp://", "ftps://",
    "sftp://", "ws://", "wss://", "file://",
)


def _approve_input(path_str: str, *, is_dir: bool = False) -> str | None:
    """
    Validate a single explicit input argument.

    Returns a human-readable rejection reason string if the input should be
    rejected, or None if it is acceptable as a local filesystem path.

    Rejects:
      - URL-like strings (any recognised scheme://)
      - Any string containing '://' (catches unknown URI schemes)
      - Bare words with no path separator and no file extension
        (these look like source registry keys, e.g. 'optcgdb', 'official')
        -- skipped for directory arguments where bare names are valid
    Does NOT check existence; the caller does that separately.
    """
    p = path_str.strip()
    if not p:
        return "empty string"

    lower = p.lower()

    # Explicit URL scheme check
    for scheme in _URL_SCHEMES:
        if lower.startswith(scheme):
            return "URL input rejected -- only local filesystem paths are accepted"

    # Catch any other URI scheme (foo://) but not Windows drive letters (C:/)
    if "://" in p:
        scheme_part = p.split("://")[0]
        # Windows absolute path 'C:/...' has a single-char drive letter; skip those
        if len(scheme_part) > 1 and scheme_part.isalpha():
            return (
                f"URI scheme '{scheme_part}://' rejected -- "
                "use a plain local path without a URI scheme"
            )

    # Ambiguous bare-word check (file inputs only, not directory arguments)
    # A path like 'optcgdb' or 'official-cardlist' has no extension and no
    # directory component -- it looks like a source key, not a file path.
    if not is_dir:
        path_obj = Path(p)
        if not path_obj.suffix and str(path_obj.parent) in (".", ""):
            return (
                f"ambiguous input '{p}' -- "
                "provide a full local path with an extension "
                f"(e.g. ./data/intake/{p}.csv)"
            )

    return None


def _preflight(
    card_args: list[str],
    banlist_args: list[str],
    deck_args: list[str],
    card_dir: str,
    banlist_dir: str,
    deck_dir: str,
) -> list[str]:
    """
    Run the input approval gate over all explicit CLI inputs.

    Returns a list of human-readable rejection messages (one per rejected
    input).  An empty list means every input passed the gate.

    Explicit file paths that don't exist on disk are also rejected here so
    the caller gets a single consolidated error report before any stages run.
    """
    rejections: list[str] = []

    def _check_files(paths: list[str], kind: str) -> None:
        for p in paths:
            reason = _approve_input(p, is_dir=False)
            if reason is not None:
                rejections.append(f"[{kind}] {reason}: {p!r}")
            elif not Path(p).exists():
                rejections.append(
                    f"[{kind}] file not found: {p!r} -- "
                    "supply an existing local file path"
                )

    def _check_dir(d: str, kind: str) -> None:
        if not d:
            return
        reason = _approve_input(d, is_dir=True)
        if reason is not None:
            rejections.append(f"[{kind}-dir] {reason}: {d!r}")
        elif not Path(d).is_dir():
            rejections.append(
                f"[{kind}-dir] directory not found: {d!r} -- "
                "supply an existing local directory path"
            )

    _check_files(card_args, "card")
    _check_files(banlist_args, "banlist")
    _check_files(deck_args, "deck")
    _check_dir(card_dir, "card")
    _check_dir(banlist_dir, "banlist")
    _check_dir(deck_dir, "deck")

    return rejections


# ---------------------------------------------------------------------------
# Captured-output helper
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _capture():
    """Capture stdout from a sub-call; yield the StringIO buffer."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old_stdout


def _run_tool(
    stage: StageResult,
    fn: Callable[[list[str]], int],
    argv: list[str],
    label: str = "",
    echo: bool = True,
) -> int:
    """Call a tool's main(argv) and capture the return code + output."""
    try:
        with _capture() as buf:
            rc = fn(argv)
        output = buf.getvalue()
        if echo and output.strip():
            for line in output.strip().splitlines():
                print(f"    {line}")
        if rc != 0:
            stage.fail(f"{label or fn.__module__} exited {rc}")
        return rc
    except SystemExit as exc:
        code = int(exc.code) if exc.code is not None else 1
        if code != 0:
            stage.fail(f"{label or fn.__module__} raised SystemExit({code})")
        return code
    except Exception as exc:
        stage.fail(f"{label or fn.__module__} raised {type(exc).__name__}: {exc}")
        return 1


# ---------------------------------------------------------------------------
# Stage 1 -- Card CSV intake
# ---------------------------------------------------------------------------


def stage_card_intake(
    card_files: list[Path],
    staging_dir: Path,
    dossiers_db: Path,
    dry_run: bool,
) -> StageResult:
    result = StageResult("card_intake")
    if not card_files:
        result.notes.append("no card CSV files supplied -- skipped")
        return result

    from tools.miru_import_card_csv import main as csv_main
    from dashboard.miru_intel_db import MiruIntelRepository
    from dashboard.miru_snapshot_refresh import OfficialSnapshotRefresher

    t0 = time.monotonic()
    processed = 0

    for csv_path in card_files:
        print(f"  [card] {csv_path.name}")
        snapshot_path = staging_dir / (csv_path.stem + "_snapshot.json")

        # Step A: CSV -> snapshot JSON
        csv_argv = [str(csv_path), "-o", str(snapshot_path)]
        if dry_run:
            csv_argv.append("--dry-run")
        if dossiers_db.exists():
            csv_argv += ["--db-path", str(dossiers_db)]

        rc = _run_tool(result, csv_main, csv_argv, label="miru_import_card_csv")
        if rc != 0:
            continue  # already recorded as fail; try remaining files

        if dry_run:
            result.notes.append(f"{csv_path.name}: dry-run only -- snapshot not written")
            processed += 1
            continue

        if not snapshot_path.exists():
            result.warn(f"{csv_path.name}: snapshot JSON not produced at {snapshot_path}")
            continue

        # Step B: snapshot JSON -> dossiers DB
        print(f"    -> refreshing dossier from {snapshot_path.name}")
        try:
            repository = MiruIntelRepository(str(dossiers_db))
            refresher = OfficialSnapshotRefresher(repository)
            refresh_result = refresher.refresh_from_export_path(str(snapshot_path))
            summary = refresh_result.get("summary", {}) if isinstance(refresh_result, dict) else {}
            cards_touched = (
                summary.get("updated", 0) + summary.get("created", 0)
                if summary else "?"
            )
            print(f"    -> dossier refresh: {cards_touched} card(s) touched")
        except Exception as exc:
            result.warn(f"{csv_path.name}: dossier refresh failed -- {exc}")
            continue

        processed += 1

    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok(processed)
    return result


# ---------------------------------------------------------------------------
# Stage 2 -- Banlist staging
# ---------------------------------------------------------------------------


def stage_banlist(
    banlist_files: list[Path],
    staging_dir: Path,
    dry_run: bool,
) -> StageResult:
    result = StageResult("banlist_staging")
    if not banlist_files:
        result.notes.append("no banlist JSON files supplied -- skipped")
        return result

    from tools.miru_fetch_banlist import main as banlist_main

    t0 = time.monotonic()
    processed = 0

    for bl_path in banlist_files:
        print(f"  [banlist] {bl_path.name}")
        out_path = staging_dir / (bl_path.stem + "_banlist_intake.csv")
        argv = [str(bl_path), "-o", str(out_path)]

        if dry_run:
            result.notes.append(f"{bl_path.name}: dry-run -- staging CSV not written")
            processed += 1
            continue

        rc = _run_tool(result, banlist_main, argv, label="miru_fetch_banlist")
        if rc == 0:
            processed += 1

    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok(processed)
        result.notes.append(
            "Banlist data staged to CSV only. "
            "DB write (ban_status facts) requires miru_import_banlist (not yet implemented)."
        )
    return result


# ---------------------------------------------------------------------------
# Stage 3 -- Deck fetch + import
# ---------------------------------------------------------------------------


def stage_deck_import(
    deck_files: list[Path],
    staging_dir: Path,
    db_path: Path,
    dry_run: bool,
) -> StageResult:
    result = StageResult("deck_import")
    if not deck_files:
        result.notes.append("no decklist JSON files supplied -- skipped")
        return result

    from tools.miru_fetch_decklist import main as fetch_main
    from tools.miru_import_decklist import main as import_main

    t0 = time.monotonic()
    imported = 0
    skipped_dup = 0

    for deck_path in deck_files:
        print(f"  [deck] {deck_path.name}")
        staging_csv = staging_dir / (deck_path.stem + "_staging.csv")

        # Step A: JSON -> staging CSV
        fetch_argv = [str(deck_path), "-o", str(staging_csv)]
        rc = _run_tool(result, fetch_main, fetch_argv, label="miru_fetch_decklist")
        if rc != 0:
            continue

        # Step B: staging CSV -> DB
        import_argv = [str(staging_csv), "--db-path", str(db_path)]
        if dry_run:
            import_argv.append("--dry-run")

        with _capture() as buf:
            try:
                rc2 = import_main(import_argv)
            except SystemExit as exc:
                rc2 = int(exc.code) if exc.code is not None else 1

        output = buf.getvalue()
        if output.strip():
            for line in output.strip().splitlines():
                print(f"    {line}")

        if rc2 != 0:
            result.fail(f"{deck_path.name}: miru_import_decklist exited {rc2}")
            continue

        # Parse output to count imported vs skipped
        if "Skipped" in output or "skipped" in output:
            skipped_dup += 1
        else:
            imported += 1

    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok(imported)
        if skipped_dup:
            result.notes.append(f"{skipped_dup} deck(s) already in DB -- skipped (duplicate uid)")
    return result


# ---------------------------------------------------------------------------
# Stages 4-7 -- Intelligence recompute
# ---------------------------------------------------------------------------


def stage_summarize(db_path: Path, dry_run: bool) -> StageResult:
    result = StageResult("summarize_deck_stats")
    if not db_path.exists():
        result.notes.append(f"deck intel DB not found at {db_path} -- skipped")
        return result

    from tools.miru_summarize_deck_stats import main as summarize_main

    t0 = time.monotonic()
    argv = ["--db-path", str(db_path)]
    if dry_run:
        argv.append("--dry-run")

    rc = _run_tool(result, summarize_main, argv, label="miru_summarize_deck_stats")
    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok()
    return result


def stage_deck_signals(db_path: Path, dry_run: bool) -> StageResult:
    result = StageResult("compute_deck_signals")
    if not db_path.exists():
        result.notes.append("deck intel DB not found -- skipped")
        return result

    from tools.miru_compute_deck_signals import main as signals_main

    t0 = time.monotonic()
    argv = ["--db-path", str(db_path)]
    if dry_run:
        argv.append("--dry-run")

    rc = _run_tool(result, signals_main, argv, label="miru_compute_deck_signals")
    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok()
    return result


def stage_cost_curves(db_path: Path, dossiers_db: Path, dry_run: bool) -> StageResult:
    result = StageResult("compute_cost_curves")
    if not db_path.exists():
        result.notes.append("deck intel DB not found -- skipped")
        return result

    from tools.miru_compute_cost_curves import main as curves_main

    t0 = time.monotonic()
    argv = ["--db-path", str(db_path), "--dossiers-db", str(dossiers_db)]
    if dry_run:
        argv.append("--dry-run")

    rc = _run_tool(result, curves_main, argv, label="miru_compute_cost_curves")
    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok()
    return result


def stage_trait_signals(db_path: Path, dossiers_db: Path, dry_run: bool) -> StageResult:
    result = StageResult("compute_trait_signals")
    if not db_path.exists():
        result.notes.append("deck intel DB not found -- skipped")
        return result

    from tools.miru_compute_trait_signals import main as traits_main

    t0 = time.monotonic()
    argv = ["--db-path", str(db_path), "--dossiers-db", str(dossiers_db)]
    if dry_run:
        argv.append("--dry-run")

    rc = _run_tool(result, traits_main, argv, label="miru_compute_trait_signals")
    result.duration_s = time.monotonic() - t0
    if not result.failed:
        result.ok()
    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

_STATUS_ICON = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}


def print_summary(
    stages: list[StageResult],
    dry_run: bool,
    elapsed_s: float,
    manifest: dict[str, int] | None = None,
) -> None:
    """
    Print the end-of-cycle summary table.

    manifest keys (all optional, default 0):
        card_files, banlist_files, deck_files, decks_imported,
        stats_recomputed
    """
    m = manifest or {}
    print()
    print("=" * 66)
    mode_note = "  [DRY-RUN]" if dry_run else ""
    print(f"  Miru Sandbox Cycle Summary{mode_note}")
    print(f"  Source policy: LOCAL-ONLY -- no web fetching, no auto-discovery")
    print("=" * 66)

    # Input manifest block
    print(f"  Inputs accepted (local files only):")
    print(f"    card CSV files   : {m.get('card_files', 0)}")
    print(f"    banlist files    : {m.get('banlist_files', 0)}")
    print(f"    deck files       : {m.get('deck_files', 0)}")
    print()

    total_warnings = 0
    total_failures = 0

    for s in stages:
        icon = _STATUS_ICON.get(s.status, "?     ")
        dur = f"{s.duration_s:.1f}s" if s.duration_s > 0 else ""
        count_note = f"({s.count} processed)" if s.count and s.status not in ("SKIP", "FAIL") else ""
        print(f"  {icon}  {s.name:<30} {count_note:<20} {dur}")
        for n in s.notes:
            print(f"         note : {n}")
        for w in s.warnings:
            print(f"         WARN : {w}")
        for e in s.errors:
            print(f"         FAIL : {e}")
        total_warnings += len(s.warnings)
        total_failures += 1 if s.status == "FAIL" else 0

    print("-" * 66)
    # Output manifest (what was actually produced)
    decks_imported = m.get("decks_imported", 0)
    stats_recomputed = m.get("stats_recomputed", 0)
    if decks_imported or stats_recomputed:
        print(f"  Produced:")
        if decks_imported:
            print(f"    decks imported          : {decks_imported}")
        if stats_recomputed:
            print(f"    intel stages recomputed : {stats_recomputed}")
    overall = "FAILED" if total_failures else ("WARN" if total_warnings else "OK")
    print(
        f"  Overall: {overall}  |  {total_warnings} warning(s)  |  "
        f"{total_failures} failure(s)  |  {elapsed_s:.1f}s  |  LOCAL-ONLY"
    )
    print("=" * 66)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = resolve_paths(args)

    dry_run: bool = args.dry_run
    stop_on_warn: bool = args.stop_on_warn

    # ------------------------------------------------------------------
    # Input approval gate  (runs before any file collection or stages)
    # ------------------------------------------------------------------
    rejections = _preflight(
        args.card_files or [],
        args.banlist_files or [],
        args.deck_files or [],
        args.card_dir,
        args.banlist_dir,
        args.deck_dir,
    )
    if rejections:
        print(
            "ERROR: input approval gate rejected the following source(s):",
            file=sys.stderr,
        )
        for r in rejections:
            print(f"  REJECTED: {r}", file=sys.stderr)
        print(
            "\nMiru sandbox mode only accepts local filesystem paths.\n"
            "URLs, URI schemes, and ambiguous source identifiers are not permitted.\n"
            "Provide absolute or relative paths to existing local files or directories.",
            file=sys.stderr,
        )
        return 1

    # Resolve input file lists (after gate passes)
    card_files = collect_files(args.card_files or [], args.card_dir, "*.csv")
    banlist_files = collect_files(args.banlist_files or [], args.banlist_dir, "*.json")
    deck_files = collect_files(args.deck_files or [], args.deck_dir, "*.json")

    if not card_files and not banlist_files and not deck_files:
        print(
            "ERROR: No input files found. Supply at least one of:\n"
            "  --card-files FILE [FILE ...]  or  --card-dir DIR\n"
            "  --banlist-files FILE [FILE ...]  or  --banlist-dir DIR\n"
            "  --deck-files FILE [FILE ...]  or  --deck-dir DIR",
            file=sys.stderr,
        )
        return 1

    # Ensure staging dir exists
    paths["staging_dir"].mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"Miru sandbox cycle starting  [{mode}]  {ts}")
    print(f"  source policy  : LOCAL-ONLY (no web fetching)")
    print(f"  deck intel DB  : {paths['db_path']}")
    print(f"  dossiers DB    : {paths['dossiers_db']}")
    print(f"  staging dir    : {paths['staging_dir']}")
    print(f"  card files     : {len(card_files)}")
    print(f"  banlist files  : {len(banlist_files)}")
    print(f"  deck files     : {len(deck_files)}")
    print()

    t_start = time.monotonic()
    stages: list[StageResult] = []

    def _should_stop(stage: StageResult) -> bool:
        if stage.failed:
            return True
        if stop_on_warn and stage.status == "WARN":
            return True
        return False

    def _make_manifest(s3: StageResult | None = None) -> dict[str, int]:
        intel_stages = [
            s for s in stages
            if s.name in (
                "summarize_deck_stats", "compute_deck_signals",
                "compute_cost_curves", "compute_trait_signals",
            )
            and s.status == "OK"
        ]
        return {
            "card_files": len(card_files),
            "banlist_files": len(banlist_files),
            "deck_files": len(deck_files),
            "decks_imported": (s3.count if s3 and not s3.skipped else 0),
            "stats_recomputed": len(intel_stages),
        }

    # Stage 1: card intake
    print("Stage 1: card CSV intake")
    s1 = stage_card_intake(card_files, paths["staging_dir"], paths["dossiers_db"], dry_run)
    stages.append(s1)
    if _should_stop(s1):
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest())
        return 1

    # Stage 2: banlist staging
    print("Stage 2: banlist staging")
    s2 = stage_banlist(banlist_files, paths["staging_dir"], dry_run)
    stages.append(s2)
    if _should_stop(s2):
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest())
        return 1

    # Stage 3: deck fetch + import
    print("Stage 3: deck fetch + import")
    s3 = stage_deck_import(deck_files, paths["staging_dir"], paths["db_path"], dry_run)
    stages.append(s3)

    # Hard stop before intel stages if deck import failed
    intake_failed = s1.failed or s3.failed
    if _should_stop(s3) or (intake_failed and not s3.skipped):
        for name in (
            "summarize_deck_stats",
            "compute_deck_signals",
            "compute_cost_curves",
            "compute_trait_signals",
        ):
            sr = StageResult(name)
            sr.notes.append("skipped due to upstream failure")
            stages.append(sr)
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest(s3))
        return 1

    # Stage 4: summarize
    print("Stage 4: summarize deck stats")
    s4 = stage_summarize(paths["db_path"], dry_run)
    stages.append(s4)
    if _should_stop(s4):
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest(s3))
        return 1

    # Stage 5: deck signals
    print("Stage 5: compute deck signals")
    s5 = stage_deck_signals(paths["db_path"], dry_run)
    stages.append(s5)
    if _should_stop(s5):
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest(s3))
        return 1

    # Stage 6: cost curves
    print("Stage 6: compute cost curves")
    s6 = stage_cost_curves(paths["db_path"], paths["dossiers_db"], dry_run)
    stages.append(s6)
    if _should_stop(s6):
        print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest(s3))
        return 1

    # Stage 7: trait signals
    print("Stage 7: compute trait signals")
    s7 = stage_trait_signals(paths["db_path"], paths["dossiers_db"], dry_run)
    stages.append(s7)

    print_summary(stages, dry_run, time.monotonic() - t_start, _make_manifest(s3))

    any_fail = any(s.failed for s in stages)
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
