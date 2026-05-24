"""Real verifier for the shadow loop (PR-B).

Replaces DummyVerifier from PR-A. Drives the per-field tiered scoring
documented in PRO-908.

Per-field flow:
  * HARD field with Bandai signal (card_name, rarity):
      Two-source check — catalog AND Bandai must agree on the canonical
      value. If they disagree → `inconclusive` (don't guess).
      If they agree → compare normalized primary answer to canonical.
      Match → `verified-correct`; mismatch → `verified-wrong`.
  * HARD field without Bandai signal (cost, power, etc.):
      Single-source check against catalog. The catalog is operator-touched
      and authoritative; we just lose the two-source safety net for these.
  * SOFT field (effect_text, trigger_text, traits):
      Validator LLM asked: "does this primary text match this catalog text?"
      Returns yes/no/inconclusive; primary's null answer → `inconclusive`.
  * INFERRED field:
      Always `inconclusive` (operator-only adjudication).

confidence_score = correct / (correct + wrong). Inconclusive fields are
NOT counted in the denominator — they're invisible to the score, which
is the strict-promotion-gate semantics from PRO-908.

The deterministic sanity post-check (the "0.5" stage) re-verifies the
HARD-field outcomes against the catalog after the per-field pass. In PR-B
this is structurally a no-op (the hard-field pass IS already
deterministic), but the post-check hook stays present so a future LLM
judge layer can be added without changing the call sites.

PRO-908 PR-B.
PRO-927: score() accepts optional validator_answer; emits primary_source_trace
and validator_source_trace per Bandai-tracked field for Stage 3 auto-clear.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .bandai_source import BandaiSource
from .field_tiers import FIELD_TIERS, Tier, has_bandai_source, tier
from .normalize import equal, normalize

log = logging.getLogger(__name__)


class SemanticJudge(Protocol):
    """Validator LLM in 'is-this-semantic-match' mode. PR-B uses it for SOFT fields."""

    def ask_json(self, user_prompt: str) -> dict[str, Any]: ...


# Sentinel value indicating "no judge available — soft fields become inconclusive."
_NO_JUDGE: SemanticJudge | None = None


class RealVerifier:
    """Per-field tiered verifier. PR-B implementation."""

    def __init__(
        self,
        bandai: BandaiSource,
        judge: SemanticJudge | None = _NO_JUDGE,
    ) -> None:
        self._bandai = bandai
        self._judge = judge

    def score(
        self,
        card: dict[str, Any],
        primary_answer: dict[str, Any],
        validator_answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_code = card.get("canonical_code", "")
        print_id = card.get("print_id", "")
        bandai_row = self._bandai.lookup(canonical_code, print_id)

        field_outcomes: dict[str, dict[str, Any]] = {}
        for field in FIELD_TIERS:
            t = tier(field)
            cat_v = card.get(field)
            ban_v = bandai_row.get(field) if has_bandai_source(field) else None
            primary_v = primary_answer.get(field)

            if t is Tier.HARD:
                outcome, reason = self._score_hard(field, cat_v, ban_v, primary_v)
            elif t is Tier.SOFT:
                outcome, reason = self._score_soft(field, cat_v, primary_v)
            else:  # Tier.INFERRED
                outcome = "inconclusive"
                reason = "inferred field — operator-only adjudication"

            fo: dict[str, Any] = {
                "outcome": outcome,
                "reason": reason,
                "tier": t.value,
                "catalog_value": cat_v,
                "bandai_value": ban_v,
                "model_value": primary_v,
                "primary_answer": primary_v,
            }

            # Validator answer + primary↔validator agreement are recorded for
            # ALL fields, not just Bandai-tracked ones. (The Review UI reads
            # validator_answer per field; gating it on has_bandai_source() left
            # the validator column dark for 10 of 12 fields even when the
            # validator HAD answered.)
            val_v = validator_answer.get(field) if validator_answer is not None else None
            fo["validator_answer"] = val_v
            if primary_v is not None and val_v is not None:
                fo["agree"] = equal(primary_v, val_v)
            else:
                fo["agree"] = None

            # PRO-927: per-field source traces — Bandai-tracked fields only.
            if has_bandai_source(field):
                # Primary source trace: present only when primary's answer
                # matches the Bandai crawl value (outcome == verified-correct
                # and Bandai has data). A hallucinated answer can't anchor to
                # a real Bandai source — that's the collusion-fix invariant.
                if outcome == "verified-correct" and ban_v is not None:
                    fo["primary_source_trace"] = self._bandai.source_trace_for(
                        canonical_code, print_id, field
                    )
                else:
                    fo["primary_source_trace"] = None

                # Validator source trace: independent check of the validator
                # model's answer against the same Bandai value.
                if (
                    validator_answer is not None
                    and val_v is not None
                    and ban_v is not None
                    and equal(val_v, ban_v)
                ):
                    fo["validator_source_trace"] = self._bandai.source_trace_for(
                        canonical_code, print_id, field
                    )
                else:
                    fo["validator_source_trace"] = None

            field_outcomes[field] = fo

        # Deterministic sanity post-check on hard fields. In PR-B this is a
        # no-op (hard pass is already deterministic) but the hook stays so
        # a future LLM-judge layer can be wrapped without changing callers.
        self._sanity_post_check(field_outcomes, card)

        # Score: correct / (correct + wrong). Inconclusive doesn't count.
        verifiable = [
            o
            for o in field_outcomes.values()
            if o["outcome"] in {"verified-correct", "verified-wrong"}
        ]
        correct = [o for o in verifiable if o["outcome"] == "verified-correct"]
        confidence_score = (len(correct) / len(verifiable)) if verifiable else 0.0

        hard_outcomes = [o for f, o in field_outcomes.items() if tier(f) is Tier.HARD]
        all_hard_verified_correct = bool(hard_outcomes) and all(
            o["outcome"] == "verified-correct" for o in hard_outcomes
        )

        return {
            "field_outcomes": field_outcomes,
            "confidence_score": round(confidence_score, 4),
            "all_hard_verified_correct": all_hard_verified_correct,
        }

    def _score_hard(self, field: str, cat_v: Any, ban_v: Any, primary_v: Any) -> tuple[str, str]:
        # Two-source check (only when Bandai has the field).
        if has_bandai_source(field) and ban_v is not None and not equal(cat_v, ban_v):
            return (
                "inconclusive",
                f"two-source disagree: catalog={cat_v!r} bandai={ban_v!r}",
            )
        # Distinguish "primary didn't include the field" (None) from
        # "primary explicitly said empty" (""). Both look the same after
        # .get() but only the explicit empty counts as an answer.
        if primary_v is None:
            return ("inconclusive", f"primary did not answer {field}")
        catalog_blank = cat_v is None or normalize(cat_v) == ""
        primary_blank = normalize(primary_v) == ""
        # Catalog uses "" as the "field doesn't apply" sentinel (e.g. life
        # for non-leaders). An explicit empty from primary against an empty
        # catalog IS the correct answer — both agree the field doesn't apply.
        if catalog_blank and primary_blank:
            return ("verified-correct", f"both empty for {field} (field not applicable)")
        if equal(primary_v, cat_v):
            return ("verified-correct", f"primary matches catalog: {normalize(cat_v)}")
        return (
            "verified-wrong",
            f"primary={primary_v!r} != catalog={cat_v!r}",
        )

    def _score_soft(self, field: str, cat_v: Any, primary_v: Any) -> tuple[str, str]:
        if primary_v is None or normalize(primary_v) == "":
            return ("inconclusive", f"primary did not answer {field}")
        if cat_v is None or normalize(cat_v) == "":
            return ("inconclusive", f"catalog has no value for {field}")
        # Exact-normalized match short-circuits the LLM call.
        if equal(primary_v, cat_v):
            return ("verified-correct", "primary matches catalog after normalization")
        # Ask the validator LLM if they're semantically equivalent.
        if self._judge is None:
            return ("inconclusive", "no semantic judge available")
        try:
            verdict = self._judge.ask_json(_semantic_match_prompt(field, primary_v, cat_v))
        except Exception as exc:
            log.warning("semantic judge failed on %s: %s", field, exc)
            return ("inconclusive", f"semantic judge failed: {exc}")
        answer = str(verdict.get("verdict", "")).strip().lower()
        if answer == "match":
            return ("verified-correct", "validator: semantic match")
        if answer == "no-match":
            return (
                "verified-wrong",
                f"validator: semantic mismatch (primary={primary_v!r}, catalog={cat_v!r})",
            )
        return ("inconclusive", f"validator returned unrecognized verdict: {verdict!r}")

    def _sanity_post_check(
        self, field_outcomes: dict[str, dict[str, Any]], card: dict[str, Any]
    ) -> None:
        """Re-verify hard-field outcomes against catalog. In PR-B this is a no-op
        because the hard pass is already deterministic. Hook present for PR-C and
        future LLM-judge layers."""
        for outcome in field_outcomes.values():
            if outcome["tier"] != Tier.HARD.value:
                continue
            # No-op verification: deterministic comparison would land on the
            # same outcome we already have.
            _ = card


def _semantic_match_prompt(field: str, primary: Any, catalog: Any) -> str:
    return (
        f"You are checking whether two One Piece TCG card-field values mean the same thing.\n\n"
        f"Field: {field}\n"
        f"Primary's answer: {primary!r}\n"
        f"Catalog (ground truth): {catalog!r}\n\n"
        f"Reply ONLY with a JSON object: "
        f'{{"verdict": "match" | "no-match" | "unclear"}}.\n'
        f"`match` means the two values express the same fact (ignore wording, whitespace, capitalization, punctuation).\n"
        f"`no-match` means they assert different facts.\n"
        f"`unclear` if you can't tell."
    )
