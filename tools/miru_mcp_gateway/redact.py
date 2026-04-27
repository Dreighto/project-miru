"""Output scrubbing for Stage 2 tools.

Two complementary passes:

1. **Substring scrub** -- at gateway startup, walk os.environ once and collect
   the values of every variable whose name contains TOKEN/KEY/SECRET/PASSWORD/
   WEBHOOK. Replace those exact substrings (case-sensitive, unique) with
   `<REDACTED:NAMED:VARNAME>` so the operator can see what *would* have been
   there without seeing the value. Skips short values (< 12 chars) to avoid
   false-positive carnage.

2. **Pattern scrub** -- regex replacements for known token shapes that may
   appear in tool output even if they don't happen to be in this gateway's
   .env (e.g. a Bearer token from a logged response, a ghp_ token someone
   pasted in a commit message).

Every Stage 2 tool's return value passes through `redact()` (or
`redact_dict()` for structured returns) immediately before being returned
to the MCP layer. Stage 1 fs_* tools are exempt; their outputs are file
contents we already deny-listed at the path level.

Token rotation note: the substring set is loaded once at startup. After
rotating any value in .env, restart the gateway so the redactor picks up
the new value.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Env var names whose values should be substring-scrubbed if non-empty.
_SECRET_NAME_PARTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "WEBHOOK")
_MIN_SECRET_LEN = 12


def _load_substring_set_from_env() -> dict[str, str]:
    """Return {value: replacement} for every interesting env value.

    Replacement string includes the var name so the operator can tell which
    secret was scrubbed, without exposing the value.
    """
    pairs: dict[str, str] = {}
    for name, value in os.environ.items():
        if not value or len(value) < _MIN_SECRET_LEN:
            continue
        upper = name.upper()
        if not any(part in upper for part in _SECRET_NAME_PARTS):
            continue
        # Skip if the value is the var name itself (operators sometimes do this).
        if value.upper() == upper:
            continue
        pairs[value] = f"<REDACTED:NAMED:{name}>"
    return pairs


# Pattern-based scrubs applied after the substring pass. Each entry:
#   (compiled_regex, replacement_text)
_PATTERN_SCRUBS: list[tuple[re.Pattern[str], str]] = [
    # GitHub token shapes (classic, fine-grained PAT, OAuth-flavored)
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "<REDACTED:GH_TOKEN>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), "<REDACTED:GH_TOKEN>"),
    (re.compile(r"gh[osu]_[A-Za-z0-9]{36,}"), "<REDACTED:GH_TOKEN>"),
    (re.compile(r"ghr_[A-Za-z0-9]{36,}"), "<REDACTED:GH_TOKEN>"),
    # JWT (header.payload.signature)
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "<REDACTED:JWT>",
    ),
    # Bearer auth (HTTP header form)
    (
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
        "Bearer <REDACTED:BEARER>",
    ),
    # n8n webhook URLs (UUID-ish path component)
    (
        re.compile(
            r"https?://[A-Za-z0-9.\-_:]+/webhook/[A-Za-z0-9\-]{20,}",
            re.IGNORECASE,
        ),
        "<REDACTED:N8N_WEBHOOK_URL>",
    ),
    # Telegram bot URLs (api.telegram.org/bot<id>:<token>)
    (
        re.compile(r"https?://api\.telegram\.org/bot[0-9]+:[A-Za-z0-9_\-]+"),
        "<REDACTED:TG_BOT_URL>",
    ),
    # PRO-132: n8n / container / REST leakage patterns
    (
        re.compile(r"postgresql://[^\s]+", re.IGNORECASE),
        "<REDACTED:DB_URL>",
    ),
    (
        re.compile(r"/webhook/[a-zA-Z0-9_-]+", re.IGNORECASE),
        "<REDACTED:WEBHOOK_PATH>",
    ),
    (
        re.compile(r"X-N8N-API-KEY:\s*\S+", re.IGNORECASE),
        "X-N8N-API-KEY: <REDACTED>",
    ),
]


# Built lazily on first use so importing this module never throws on a weird
# environment. Refreshable via reload_substring_set() if needed.
_substring_pairs: dict[str, str] | None = None


def reload_substring_set() -> None:
    """Re-read os.environ. Call after a token rotation if you don't want to
    restart the gateway.
    """
    global _substring_pairs
    _substring_pairs = _load_substring_set_from_env()


def _ensure_loaded() -> dict[str, str]:
    global _substring_pairs
    if _substring_pairs is None:
        _substring_pairs = _load_substring_set_from_env()
    return _substring_pairs


def redact(text: str) -> str:
    """Scrub secrets from a single string. Pure -- no I/O."""
    if not text:
        return text
    out = text
    # Substring pass first: longer matches before shorter ones to avoid a long
    # secret being half-rewritten by a substring of itself.
    pairs = _ensure_loaded()
    for value in sorted(pairs.keys(), key=len, reverse=True):
        if value in out:
            out = out.replace(value, pairs[value])
    # Pattern pass.
    for pat, replacement in _PATTERN_SCRUBS:
        out = pat.sub(replacement, out)
    return out


def find_named_secret_substrings(text: str) -> list[str]:
    """Return replacement markers for any configured secret *value* found in text.

    Used by docs write tools to **reject** writes that embed real secrets (PRO-123).
    Does not apply regex pattern scrubs — only env-derived substring pairs.
    """
    if not text:
        return []
    pairs = _ensure_loaded()
    hits: list[str] = []
    for value in sorted(pairs.keys(), key=len, reverse=True):
        if value in text:
            hits.append(pairs[value])
    return hits


def redact_dict(obj: Any) -> Any:
    """Recursively scrub strings inside dicts/lists/tuples.

    Non-string scalars (int/float/bool/None) pass through unchanged. Tuples
    are returned as lists (JSON-friendly).
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_dict(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact_dict(x) for x in obj]
    return obj
