"""Dummy verifier for PR-A — always returns inconclusive on every field.

PR-B replaces this with the real verifier (catalog + Bandai + TCGPlayer
comparisons, per-field tiered scoring, sanity post-check). The shape of
the return matches the interface contract documented in PRO-911:

    {
        "field_outcomes": {
            "<field>": {"outcome": "verified-correct" | "verified-wrong" | "inconclusive",
                        "reason": str,
                        "catalog_value": Any,
                        "bandai_value": Any,
                        "model_value": Any},
            ...
        },
        "confidence_score": float,  # 0.0 to 1.0
        "all_hard_verified_correct": bool,
    }

PR-A's dummy always reports `inconclusive` with reason="dummy verifier — PR-B
implements real comparison." confidence_score is 0.0 and
all_hard_verified_correct is False.

PRO-908 PR-A.
"""

from __future__ import annotations

from typing import Any

# Fields the primary is asked to answer for. Stay in sync with the
# question template; PR-B uses these to drive its per-field outcomes.
TRACKED_FIELDS: list[str] = [
    "card_name",
    "cost",
    "power",
    "counter",
    "color",
    "card_type",
    "rarity",
    "attribute",
    "life",
    "effect_text",
    "trigger_text",
    "traits",
]


class DummyVerifier:
    """Returns inconclusive on every field. PR-B replaces with real logic."""

    def score(
        self,
        card: dict[str, Any],
        primary_answer: dict[str, Any],
        validator_answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = validator_answer  # dummy ignores validator; real verifier uses it
        field_outcomes: dict[str, dict[str, Any]] = {}
        for field in TRACKED_FIELDS:
            field_outcomes[field] = {
                "outcome": "inconclusive",
                "reason": "dummy verifier — PR-B implements real comparison",
                "catalog_value": card.get(field),
                "bandai_value": None,
                "model_value": primary_answer.get(field),
            }
        return {
            "field_outcomes": field_outcomes,
            "confidence_score": 0.0,
            "all_hard_verified_correct": False,
        }
