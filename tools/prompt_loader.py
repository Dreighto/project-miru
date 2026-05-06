"""
Prompt loader / injection pre-filter for the LLM Router (PRO-201).

Structural guarantee enforced by code structure
================================================
build_api_call(system_prompt, ticket_body, ...) always places *ticket_body*
in the user turn ONLY.  The *system_prompt* parameter is a static string that
the caller constructs independently of ticket content — this module has no
mechanism to derive it from ticket data, and no code path appends, formats,
or interpolates ticket_body into the system field.

Pre-filter
==========
scan_for_injection(text) runs BEFORE build_api_call.  On a hit the caller
routes to triage and logs via log_injection_rejection().  The pre-filter is
intentionally conservative (few patterns, each targeted) to keep the
false-positive rate low on legitimate technical tickets; see ticket PRO-201
for the >10% false-positive stop-and-ask rule.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Injection pattern registry — OWASP LLM Top 10 / LLM01 patterns
# ---------------------------------------------------------------------------
# Each entry: (label, raw_regex_string)
# All compiled case-insensitively except where noted.
#
# False-positive notes per pattern are inline.  The operator stop-and-ask
# threshold is >10% hit rate on a legitimate ticket sample (PRO-201 spec).

_RAW_PATTERNS: list[tuple[str, str]] = [
    # 1. Classic "ignore previous/prior/above instructions" injection
    #    FP risk: very low — unusual phrasing in legitimate technical tickets.
    (
        "ignore_instructions",
        r"ignore\s+(previous|prior|all\s+previous|the\s+above)\s+"
        r"(instructions?|directions?|prompts?|context|rules?|constraints?)",
    ),
    # 2. Persona hijack: "you are now a/an/the ..."
    #    FP risk: very low — "you are now" is rare in bug/feature tickets.
    (
        "persona_you_are_now",
        r"you\s+are\s+now\s+(a\b|an\b|the\b)",
    ),
    # 3. Persona hijack: "pretend you are / pretend to be"
    #    FP risk: very low.
    (
        "persona_pretend",
        r"pretend\s+(you\s+are|to\s+be)\b",
    ),
    # 4. Persona hijack: "act as a/an" or "act as if you are/were"
    #    FP risk: low — might match "act as a proxy" but "act as if you are"
    #    is injection-specific enough to be safe.
    (
        "persona_act_as",
        r"\bact\s+as\s+(if\s+you\s+(are|were)\b|a\b|an\b)",
    ),
    # 5. XML delimiter injection: <system> or </system>
    #    FP risk: low — could appear in XML schema tickets, but the full tag
    #    form "<system>" is rarely literal prose in a Linear ticket.
    (
        "xml_system_tag",
        r"<\s*/?\s*system\s*>",
    ),
    # 6. Role-delimiter injection: bare "system:" at the start of a line
    #    (attempting to inject a fake system turn).
    #    FP risk: medium — "System: Windows 11" in a bug report is legitimate.
    #    Kept because it is an explicit OWASP LLM-01 pattern; false-positive
    #    rate should be monitored per PRO-201 stop-and-ask rule.
    (
        "role_delimiter_system",
        r"(?m)^\s*system\s*:",
    ),
    # 7. "disregard (all) (previous|prior|above|your) (instructions|training...)"
    #    FP risk: very low.
    (
        "disregard_instructions",
        r"disregard\s+(all\s+)?(previous|prior|above|your)\s+"
        r"(instructions?|training|guidelines?|rules?|constraints?)",
    ),
    # 8. "forget (all) (previous|your [previous]|the above) (instructions...)"
    #    FP risk: very low.  "your(\s+previous)?" handles the common
    #    "forget your previous instructions" phrasing.
    (
        "forget_instructions",
        r"forget\s+(all\s+)?(previous|prior|your(\s+previous)?|the\s+above)\s+"
        r"(instructions?|training|context|rules?|constraints?)",
    ),
    # 9. LLaMA / Mistral instruction delimiters injected into body
    #    FP risk: very low — these tokens are model-specific and would not
    #    appear in ordinary Linear ticket prose.
    (
        "llama_inst_delimiter",
        r"\[INST\]|\[/INST\]",
    ),
]

# Compiled patterns (case-insensitive; role_delimiter_system uses inline (?m))
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in _RAW_PATTERNS
]


class InjectionMatch(NamedTuple):
    pattern_name: str
    matched_text: str


def scan_for_injection(text: str) -> list[InjectionMatch]:
    """Scan *text* for injection-trigger phrases.

    Returns a list of InjectionMatch (one per fired pattern).  An empty list
    means the text is clean.  Any non-empty list is a rejection signal — the
    caller should route to triage and log via log_injection_rejection().

    The pre-filter is intentionally cheap: pure regex, no LLM, runs
    synchronously before any API call is attempted.
    """
    hits: list[InjectionMatch] = []
    for name, pat in INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(InjectionMatch(pattern_name=name, matched_text=m.group(0)))
    return hits


# ---------------------------------------------------------------------------
# Structural loader — ticket body is ALWAYS in the user turn, never in system
# ---------------------------------------------------------------------------


def build_api_call(
    *,
    system_prompt: str,
    ticket_body: str,
    model: str,
    max_tokens: int = 1024,
    extra_user_prefix: str = "",
) -> dict:
    """Build an Anthropic Messages API payload with structural injection safety.

    The structural guarantee is enforced by this function's signature and
    implementation:

    * *system_prompt* is a plain string passed by the caller.  This function
      does not modify it, does not append to it, and does not derive it from
      ticket content.  The only code path that touches *system_prompt* is the
      direct assignment ``"system": system_prompt`` below.

    * *ticket_body* is placed exclusively in the user turn.  There is no
      branch, format string, or helper call that could move ticket content
      into the system field.

    Args:
        system_prompt:      Static system instruction string.  Must be a
                            literal or a value constructed by the caller
                            independently of any ticket/user input.
        ticket_body:        Raw ticket text from operator / user input.
                            Placed in the user turn only.
        model:              Anthropic model identifier string.
        max_tokens:         Maximum tokens for the completion.
        extra_user_prefix:  Optional static prefix prepended before
                            ticket_body in the user turn (e.g.
                            ``"Ticket content:\n"``).  Must be a static
                            literal — never derived from ticket content.

    Returns:
        dict payload ready for ``json.dumps()`` and POST to the Anthropic
        Messages API endpoint.
    """
    user_content = (extra_user_prefix + ticket_body) if extra_user_prefix else ticket_body

    return {
        "model": model,
        "max_tokens": max_tokens,
        # ---- STRUCTURAL BOUNDARY ----------------------------------------
        # system_prompt is the static instruction surface.
        # ticket_body never appears here.  If you are reading this while
        # adding a feature: do NOT add any ticket-derived value to "system".
        "system": system_prompt,
        # ---- USER TURN ONLY contains ticket content ----------------------
        "messages": [
            {"role": "user", "content": user_content},
        ],
    }


# ---------------------------------------------------------------------------
# Rejection logger — append-only write to routing_history.jsonl
# ---------------------------------------------------------------------------

# dispatcher/router/prompt_loader.py
# parents[0] = dispatcher/router/
# parents[1] = dispatcher/
# parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_HISTORY = _REPO_ROOT / "data" / "routing_history.jsonl"


def log_injection_rejection(
    *,
    trace_id: str,
    task_id: str | None,
    task_identifier: str | None,
    matches: list[InjectionMatch],
) -> None:
    """Append one routing_history.jsonl row for an injection-rejected ticket.

    Uses strict append mode ("a") — this function never reads or rewrites the
    file, preserving the append-only invariant (CLAUDE.md hard rule).

    The row carries ``operator_disposition="auto_triage_injection"`` so
    downstream analytics can distinguish injection rejections from normal
    triage decisions.
    """
    row = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": trace_id,
        "task_id": task_id,
        "task_identifier": task_identifier,
        "source": "prompt_loader_prefilter",
        "chosen_worker": "triage",
        "operator_disposition": "auto_triage_injection",
        "outcome": "triage",
        "injection_patterns_matched": [
            {"pattern_name": m.pattern_name, "matched_text": m.matched_text} for m in matches
        ],
    }
    with _ROUTING_HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
