"""
miru_detect_variant_packages.py

For a given leader, compare stored archetype profiles and surface:
  - shared shell: cards that are 'core' in 2+ archetypes
  - variant packages: cards that are core/flex in one archetype but
    absent or tech in all others
  - optional cost skew and trait skew per archetype

Data source (read-only):
  miru_deck_intel.db  -- archetype_profiles, archetype_profile_cards,
                         archetype_profile_traits, archetype_profile_cost_curve
  miru_dossiers.db    -- cards table for name enrichment (optional)

No DB writes are performed.

Usage:
    python -m tools.miru_detect_variant_packages --leader-code OP01-001
    python -m tools.miru_detect_variant_packages --leader-code OP01-001 \\
        --db-path data/miru_deck_intel.db \\
        --dossiers-db data/miru_dossiers.db \\
        --limit 10
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Confidence tiers (mirrors miru_archetype_preview / miru_bundle_leader_insight)
# ---------------------------------------------------------------------------

_CONFIDENCE_LOW_MAX = 4    # 1-4 decks  -> "low"
_CONFIDENCE_MED_MAX = 14   # 5-14 decks -> "medium"
                            # 15+        -> "strong"


def sample_confidence(n: int) -> str:
    """Return a tiered confidence label based on sample deck count."""
    if n <= _CONFIDENCE_LOW_MAX:
        return "low"
    if n <= _CONFIDENCE_MED_MAX:
        return "medium"
    return "strong"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_INTEL_DB    = Path("data") / "miru_deck_intel.db"
_DEFAULT_DOSSIERS_DB = Path("data") / "miru_dossiers.db"
_DEFAULT_LIMIT       = 10   # max package cards shown per archetype
_TOP_TRAITS          = 4    # traits shown in skew line
_BAR_CHAR            = "#"
_BAR_MAX_WIDTH       = 10   # chars for max-value bar

# Role strength: used for sorting and for deciding "strong" presence
_ROLE_STRENGTH: dict[str, int] = {"core": 2, "flex": 1, "tech": 0}
_STRONG_ROLES:  frozenset[str] = frozenset({"core", "flex"})


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite DB read-only via URI; raises OperationalError on failure."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_names(dossiers_path: Optional[Path]) -> dict[str, str]:
    """
    Return {canonical_code: card_name} from dossiers DB.
    Returns {} silently on any error or if dossiers_path is None / missing.
    """
    if dossiers_path is None or not dossiers_path.exists():
        return {}
    try:
        conn = _open_ro(dossiers_path)
        rows = conn.execute(
            "SELECT canonical_code, card_name FROM cards "
            "WHERE card_name IS NOT NULL AND card_name != ''"
        ).fetchall()
        conn.close()
        return {r["canonical_code"]: r["card_name"] for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def load_profiles(
    conn: sqlite3.Connection, leader_code: str
) -> list[dict]:
    """
    Return archetype_profiles rows for the leader, sorted by archetype_id.
    Each dict has keys: archetype_id, deck_count, avg_similarity.
    """
    rows = conn.execute(
        "SELECT archetype_id, deck_count, avg_similarity "
        "FROM archetype_profiles "
        "WHERE leader_code = ? "
        "ORDER BY archetype_id",
        (leader_code,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_arch_cards(
    conn: sqlite3.Connection, leader_code: str
) -> dict[str, dict[str, str]]:
    """
    Return {archetype_id: {card_code: role_label}} for all archetypes
    of the given leader.
    """
    rows = conn.execute(
        "SELECT archetype_id, card_code, role_label "
        "FROM archetype_profile_cards "
        "WHERE leader_code = ? "
        "ORDER BY archetype_id, card_code",
        (leader_code,),
    ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for r in rows:
        aid = r["archetype_id"]
        if aid not in result:
            result[aid] = {}
        result[aid][r["card_code"]] = r["role_label"]
    return result


def load_traits(
    conn: sqlite3.Connection, leader_code: str
) -> dict[str, list[tuple[str, int]]]:
    """
    Return {archetype_id: [(trait_name, total_copies), ...]}
    sorted by total_copies desc, then trait_name asc for determinism.
    """
    rows = conn.execute(
        "SELECT archetype_id, trait_name, total_copies "
        "FROM archetype_profile_traits "
        "WHERE leader_code = ? "
        "ORDER BY archetype_id, total_copies DESC, trait_name",
        (leader_code,),
    ).fetchall()
    result: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        aid = r["archetype_id"]
        if aid not in result:
            result[aid] = []
        result[aid].append((r["trait_name"], int(r["total_copies"])))
    return result


def load_cost_curves(
    conn: sqlite3.Connection, leader_code: str
) -> dict[str, dict[str, int]]:
    """Return {archetype_id: {cost_bucket: total_copies}}."""
    rows = conn.execute(
        "SELECT archetype_id, cost_bucket, total_copies "
        "FROM archetype_profile_cost_curve "
        "WHERE leader_code = ? "
        "ORDER BY archetype_id, cost_bucket",
        (leader_code,),
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for r in rows:
        aid = r["archetype_id"]
        if aid not in result:
            result[aid] = {}
        result[aid][r["cost_bucket"]] = int(r["total_copies"])
    return result


# ---------------------------------------------------------------------------
# Package detection logic
# ---------------------------------------------------------------------------

def shared_shell(arch_cards: dict[str, dict[str, str]]) -> list[str]:
    """
    Return sorted list of card codes that are 'core' in 2 or more archetypes.

    Rule: a card must appear with role_label='core' in at least 2 different
    archetypes for the same leader to qualify as shared shell.
    """
    if len(arch_cards) < 2:
        return []

    core_count: dict[str, int] = {}
    for cards in arch_cards.values():
        for code, role in cards.items():
            if role == "core":
                core_count[code] = core_count.get(code, 0) + 1

    return sorted(code for code, cnt in core_count.items() if cnt >= 2)


def variant_package(
    arch_id: str,
    arch_cards: dict[str, dict[str, str]],
) -> list[tuple[str, str]]:
    """
    Cards that are 'core' or 'flex' in arch_id, but absent or 'tech' in
    ALL other archetypes.

    Returns [(card_code, role_label), ...] sorted by:
      1. role strength descending (core before flex)
      2. card_code ascending (deterministic)

    A card is considered "strongly present" in an archetype when its
    role_label is 'core' or 'flex'. It qualifies for the variant package
    of arch_id only if no other archetype considers it strongly present.
    """
    my_cards = arch_cards.get(arch_id, {})
    others   = [cards for aid, cards in arch_cards.items() if aid != arch_id]

    result: list[tuple[str, str]] = []
    for code, role in my_cards.items():
        if role not in _STRONG_ROLES:
            continue
        strong_elsewhere = any(other.get(code, "") in _STRONG_ROLES for other in others)
        if not strong_elsewhere:
            result.append((code, role))

    result.sort(key=lambda x: (-_ROLE_STRENGTH.get(x[1], 0), x[0]))
    return result


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _card_name(code: str, names: dict[str, str]) -> str:
    """Return '  <name>' if available, else empty string."""
    name = names.get(code, "")
    return f"  {name}" if name else ""


def _trait_skew_line(traits: list[tuple[str, int]], limit: int = _TOP_TRAITS) -> str:
    """Compact trait summary: 'Strike(28)  Slash(21)  ...'"""
    return "  ".join(f"{t}({n})" for t, n in traits[:limit])


def _bucket_sort_key(bucket: str) -> float:
    """Sort cost_bucket strings numerically; non-numeric (e.g. '8+') sorts last."""
    try:
        return float(bucket)
    except ValueError:
        return 999.0


def _cost_bar(copies: int, max_copies: int) -> str:
    if max_copies <= 0:
        return ""
    width = round(copies * _BAR_MAX_WIDTH / max_copies)
    return _BAR_CHAR * min(width, _BAR_MAX_WIDTH)


def _weighted_avg_cost(cost_curve: dict[str, int]) -> float:
    """Weighted average cost from {cost_bucket: total_copies}."""
    total_copies = 0
    total_cost   = 0.0
    for bucket, copies in cost_curve.items():
        try:
            cost = float(bucket)
        except (ValueError, TypeError):
            continue
        total_copies += copies
        total_cost   += cost * copies
    return total_cost / total_copies if total_copies > 0 else 0.0


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render(
    leader_code:  str,
    profiles:     list[dict],
    arch_cards:   dict[str, dict[str, str]],
    arch_traits:  dict[str, list[tuple[str, int]]],
    arch_costs:   dict[str, dict[str, int]],
    names:        dict[str, str],
    limit:        int,
) -> None:
    """Print the full variant-package report to stdout."""
    n_archetypes = len(profiles)
    total_decks  = sum(p["deck_count"] for p in profiles)
    confidence   = sample_confidence(total_decks)

    print(f"Variant packages for leader {leader_code}")
    print(
        f"  archetypes: {n_archetypes}"
        f"  |  decks sampled: {total_decks}"
        f"  |  confidence: {confidence}"
    )
    print()

    # ---- Edge cases -------------------------------------------------------
    if n_archetypes == 0:
        print("  No archetype profiles stored for this leader.")
        print("  Run miru_store_archetype_profiles first.")
        return

    if n_archetypes == 1:
        aid = profiles[0]["archetype_id"]
        dc  = profiles[0]["deck_count"]
        avs = profiles[0]["avg_similarity"]
        print(f"  Only one archetype stored ({aid}, {dc} deck(s), avg_sim={avs:.2f}).")
        print(
            "  Variant package comparison requires 2+ archetypes.\n"
            "  Add more decks and re-run miru_store_archetype_profiles\n"
            "  to generate a split."
        )
        return

    # ---- Shared shell -----------------------------------------------------
    shell = shared_shell(arch_cards)
    print(f"Shared shell  ({len(shell)} card(s) core across 2+ archetypes)")
    if shell:
        for code in shell:
            print(f"  {code}{_card_name(code, names)}")
    else:
        print("  (none — archetypes share no core cards)")
    print()

    # ---- Per-archetype packages -------------------------------------------
    for prof in profiles:
        aid  = prof["archetype_id"]
        dc   = prof["deck_count"]
        avs  = prof["avg_similarity"]
        conf = sample_confidence(dc)

        pkg        = variant_package(aid, arch_cards)[:limit]
        cost_curve = arch_costs.get(aid, {})
        avg_cost   = _weighted_avg_cost(cost_curve)
        traits     = arch_traits.get(aid, [])

        print(f"Archetype: {aid}  ({dc} deck(s), avg_sim={avs:.2f}, confidence: {conf})")

        # Package cards
        if pkg:
            print(f"  Package cards  ({len(pkg)} card(s) strong here, absent/weak elsewhere)")
            for code, role in pkg:
                print(f"    {code}  [{role}]{_card_name(code, names)}")
        else:
            print("  Package cards  (none — all strong cards are shared with other archetypes)")

        # Cost skew
        if cost_curve:
            sorted_buckets = sorted(cost_curve, key=_bucket_sort_key)
            max_copies     = max(cost_curve.values())
            print(f"  Cost skew: avg {avg_cost:.1f}cp")
            for bucket in sorted_buckets:
                copies = cost_curve[bucket]
                bar    = _cost_bar(copies, max_copies)
                label  = f"{bucket}cp" if bucket not in ("8+",) else " 8+"
                print(f"    {label:<5} {bar}")

        # Trait skew
        if traits:
            print(f"  Trait skew: {_trait_skew_line(traits)}")

        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Detect variant card packages across stored archetypes for a leader. "
            "Read-only — no DB writes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--leader-code",
        required=True,
        metavar="CODE",
        help="Leader card code (e.g. OP01-001).",
    )
    p.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {_DEFAULT_INTEL_DB}).",
    )
    p.add_argument(
        "--dossiers-db",
        default="",
        metavar="PATH",
        help="Path to miru_dossiers.db for card name enrichment (optional).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        metavar="N",
        help=f"Max package cards shown per archetype (default: {_DEFAULT_LIMIT}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    leader_code = args.leader_code.strip().upper()

    db_path = Path(args.db_path) if args.db_path else _DEFAULT_INTEL_DB
    if not db_path.exists():
        print(f"ERROR: intel DB not found: {db_path}", file=sys.stderr)
        return 1

    dossiers_path: Optional[Path] = Path(args.dossiers_db) if args.dossiers_db else None

    try:
        conn = _open_ro(db_path)
    except sqlite3.OperationalError as exc:
        print(f"ERROR: cannot open {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        profiles    = load_profiles(conn, leader_code)
        arch_cards  = load_arch_cards(conn, leader_code)
        arch_traits = load_traits(conn, leader_code)
        arch_costs  = load_cost_curves(conn, leader_code)
    except sqlite3.Error as exc:
        print(f"ERROR: DB query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    names = load_names(dossiers_path)

    render(
        leader_code=leader_code,
        profiles=profiles,
        arch_cards=arch_cards,
        arch_traits=arch_traits,
        arch_costs=arch_costs,
        names=names,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
