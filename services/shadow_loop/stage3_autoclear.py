"""Stage 3 auto-clear predicate — BANDAI-TRACE AGREEMENT gate.

Addresses the model-collusion failure mode (PRO-927 / dev-page debrief
section 4.5 Decision 1): when two models share architecture and quantization
constraints they can agree on a wrong answer. Answer-agreement alone is not
sufficient evidence for auto-clearing a row past experimental.

The fix: require that BOTH models independently anchor their answers to the
same Bandai source location (URL + page-section field). If two models
hallucinate identically, they cannot both produce a source trace that points
to real Bandai data corroborating the fabrication.

stage3_autoclear() is called in loop_runner after the verifier completes and
before db_writer commits the row. On advance=False the writer still writes the
row (for operator review) but records the gate-failure reason in
validator_agreement and leaves source_trace_json NULL.

PRO-927.
"""

from __future__ import annotations

from .field_tiers import has_bandai_source


def stage3_autoclear(verifier_result: dict) -> tuple[bool, str | None]:
    """Return (advance, reason).

    advance=True iff for every Bandai-tracked field in the verifier result:
      - primary_answer == validator_answer (both models agree) AND
      - primary_source_trace and validator_source_trace both exist AND
      - primary_source_trace["url"] == validator_source_trace["url"] AND
      - primary_source_trace["field"] == validator_source_trace["field"]

    advance=False otherwise; reason names the first field that failed the gate.
    Fields not in BANDAI_FIELDS are exempt — no source trace required for them.
    """
    field_outcomes = verifier_result.get("field_outcomes", {})

    if not field_outcomes:
        return False, "empty_verifier_result"

    for field, outcome in field_outcomes.items():
        if not has_bandai_source(field):
            continue  # field-tier exemption: non-Bandai fields skip the gate

        primary_v = outcome.get("primary_answer")
        validator_v = outcome.get("validator_answer")

        # Both models must have provided an answer.
        if primary_v is None or validator_v is None:
            return False, f"{field}: missing_answer"

        # Both models must agree on the answer.
        if primary_v != validator_v:
            return False, f"{field}: answer_disagreement"

        primary_trace = outcome.get("primary_source_trace")
        validator_trace = outcome.get("validator_source_trace")

        # Both source traces must exist (means both answers matched Bandai).
        if primary_trace is None or validator_trace is None:
            return False, f"{field}: missing_source_trace"

        # Source traces must point to the same Bandai URL.
        if primary_trace.get("url") != validator_trace.get("url"):
            return False, f"{field}: source_trace_url_mismatch"

        # Source traces must name the same page-section field.
        if primary_trace.get("field") != validator_trace.get("field"):
            return False, f"{field}: source_trace_field_mismatch"

    return True, None
