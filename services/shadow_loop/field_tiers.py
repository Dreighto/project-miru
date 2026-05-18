"""Per-field tier classification for the shadow-loop verifier.

Three tiers (PRO-908):
  * HARD — catalog (and where available, Bandai) is ground truth. Boolean
    correct/wrong via deterministic comparison after normalization.
  * SOFT — catalog has the field but matching is fuzzy. Validator LLM
    decides semantic match.
  * INFERRED — no ground truth available. Operator-only adjudication.
    Cannot auto-promote.

`BANDAI_FIELDS` lists fields the OP01 Bandai crawl actually carries
(from `data/bandai_op01_crawl.json` per PRO-904). Hard fields with no
Bandai signal fall back to catalog-only verification — still authoritative
since the catalog is operator-touched, just without the two-source safety.

PRO-908 PR-B.
"""

from __future__ import annotations

from enum import Enum


class Tier(Enum):
    HARD = "hard"
    SOFT = "soft"
    INFERRED = "inferred"


FIELD_TIERS: dict[str, Tier] = {
    # Hard fields — catalog is ground truth.
    "card_name": Tier.HARD,
    "card_type": Tier.HARD,
    "color": Tier.HARD,
    "rarity": Tier.HARD,
    "cost": Tier.HARD,
    "power": Tier.HARD,
    "counter": Tier.HARD,
    "attribute": Tier.HARD,
    "life": Tier.HARD,
    # Soft fields — semantic match required.
    "effect_text": Tier.SOFT,
    "trigger_text": Tier.SOFT,
    "traits": Tier.SOFT,
}

# Fields the Bandai OP01 crawl provides. Other hard fields fall back to
# catalog-only verification.
BANDAI_FIELDS: frozenset[str] = frozenset({"card_name", "rarity"})


def tier(field: str) -> Tier:
    """Return the tier for a field. Defaults to INFERRED for unknown fields."""
    return FIELD_TIERS.get(field, Tier.INFERRED)


def has_bandai_source(field: str) -> bool:
    """True if the OP01 Bandai crawl carries authoritative data for this field."""
    return field in BANDAI_FIELDS
