"""Value normalization for the shadow-loop verifier.

Different sources phrase the same value differently — `"5,000"` vs `"5000"`,
trailing whitespace, capitalization, integer-vs-string. Normalization
collapses these to a canonical comparable form so the verifier doesn't
mark a correct answer wrong because of formatting.

PRO-908 PR-B.
"""

from __future__ import annotations

import re
from typing import Any

_WHITESPACE_RUN = re.compile(r"\s+")
_INT_LIKE = re.compile(r"^-?\d{1,3}(,\d{3})+$|^-?\d+$")


def normalize(value: Any) -> str:
    """Collapse a value into a canonical comparable string.

    Rules:
      * None and empty-string both → "".
      * Numbers (int / numeric string with commas) → bare integer string.
      * Strings → trimmed, lowercased, internal whitespace collapsed.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    s = str(value).strip()
    if not s:
        return ""
    if _INT_LIKE.match(s):
        return s.replace(",", "")
    return _WHITESPACE_RUN.sub(" ", s).lower()


def equal(a: Any, b: Any) -> bool:
    """True if two values are equal after normalization."""
    return normalize(a) == normalize(b)


def equal_strict_numeric(a: Any, b: Any) -> bool:
    """Stricter equality for numeric fields — treats "5" == "5" but "5" != "five"."""
    na = normalize(a)
    nb = normalize(b)
    if _INT_LIKE.match(na) and _INT_LIKE.match(nb):
        return na.replace(",", "") == nb.replace(",", "")
    return na == nb
