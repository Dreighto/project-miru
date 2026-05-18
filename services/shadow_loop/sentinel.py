"""Periodic verifier-of-verifier sentinel check (PR-C, PRO-912).

Every SENTINEL_PERIOD ticks, `should_check_sentinel` returns True so the loop
runner can trigger a sentinel pass.  A sentinel pass submits known-correct
catalog values as the primary answer and confirms the verifier returns
`verified-correct` on every HARD field.

Purpose: catch normalization regressions, Bandai-lookup bugs, or schema drift
before they silently corrupt the learning pool.

PRO-908 PR-C.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .field_tiers import FIELD_TIERS, Tier, tier

log = logging.getLogger(__name__)

SENTINEL_PERIOD: int = 50  # Run sentinel check every N ticks

# (canonical_code, print_id) pairs used as sentinel cards.
# OP01 leaders — rich HARD fields, always present in catalog.
SENTINEL_CARD_KEYS: list[tuple[str, str]] = [
    ("OP01-001", "OP01-001"),
    ("OP01-002", "OP01-002"),
]


class _Verifier(Protocol):
    def score(self, card: dict[str, Any], primary_answer: dict[str, Any]) -> dict[str, Any]: ...


def should_check_sentinel(tick_count: int) -> bool:
    """Return True every SENTINEL_PERIOD ticks (including tick 0 on startup)."""
    return tick_count % SENTINEL_PERIOD == 0


def build_sentinel_primary_from_card(card: dict[str, Any]) -> dict[str, Any]:
    """Extract HARD-field values from a catalog row to use as a 'perfect' primary answer.

    Submitting the catalog's own values back to the verifier should always yield
    verified-correct on every scoreable HARD field — this is the self-consistency check.
    """
    return {field: card.get(field) for field, t in FIELD_TIERS.items() if t is Tier.HARD}


def run_sentinel_check(
    verifier: _Verifier,
    card: dict[str, Any],
    primary_answer: dict[str, Any],
) -> dict[str, Any]:
    """Run verifier on one sentinel card with a known-correct primary answer.

    Returns the full verifier result dict, extended with `sentinel_passed` (bool)
    and `sentinel_canonical_code` for traceability.

    A sentinel failure means the verifier produced `verified-wrong` on a HARD field
    when the primary answer was the catalog's own value — that is a verifier bug.
    """
    result = verifier.score(card, primary_answer)
    hard_outcomes = [
        (field, outcome)
        for field, outcome in result["field_outcomes"].items()
        if tier(field) is Tier.HARD and outcome["outcome"] in {"verified-correct", "verified-wrong"}
    ]
    sentinel_passed = bool(hard_outcomes) and all(
        o["outcome"] == "verified-correct" for _, o in hard_outcomes
    )
    canonical_code = card.get("canonical_code", "unknown")
    if not sentinel_passed:
        failures = [
            f"{f}: {o['outcome']} — {o['reason']}"
            for f, o in hard_outcomes
            if o["outcome"] != "verified-correct"
        ]
        log.warning(
            "sentinel FAILED for %s — verifier produced wrong outcomes on its own catalog values: %s",
            canonical_code,
            failures,
        )
    else:
        log.info(
            "sentinel passed for %s (%d hard fields checked)", canonical_code, len(hard_outcomes)
        )

    return {
        **result,
        "sentinel_passed": sentinel_passed,
        "sentinel_canonical_code": canonical_code,
    }
