"""Shared byte-level encoding sniffer for PowerShell-on-Windows files.

The pre-commit hooks `check_ps1_hardcoded_paths.py` and `pre_pr_review.py`
both need to decode `.ps1` files that may be saved as UTF-16 LE/BE (with or
without BOM). The naive utf-8-first ordered fallback doesn't work because
utf-8 decode of UTF-16 bytes succeeds silently — U+0000 (the high byte of
each ASCII codepoint in UTF-16) is a valid UTF-8 codepoint, so
UnicodeDecodeError never raises.

This module factors the byte-level sniffer out so both hooks share one
implementation. CR R5 on PR #3 caught the duplication and asked for the
extraction.

Detection order (matches the original inline logic):
  1. UTF-16-LE BOM (`\\xff\\xfe`) → strip BOM, decode utf-16-le
  2. UTF-16-BE BOM (`\\xfe\\xff`) → strip BOM, decode utf-16-be
  3. > 25% null bytes total: BOM-less UTF-16. LE vs BE decided by counting
     null-byte density at odd vs even byte positions. ASCII-encoded UTF-16-LE
     has 0x00 at every odd byte (the high byte of each 16-bit word);
     UTF-16-BE has 0x00 at every even byte.
  4. Otherwise: UTF-8 (with errors='replace' as a last resort so partial
     corruption still surfaces something to the detectors).
"""

from __future__ import annotations


def decode_bytes_with_utf16_sniffing(raw: bytes) -> str:
    """Detect the right encoding for a (probably PowerShell) file via BOM +
    null-byte heuristics and return the decoded text.

    Always returns a str — never raises UnicodeDecodeError. The utf-8 fallback
    uses errors='replace' so even partially-corrupt files yield scannable
    text rather than disappearing silently.
    """
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    null_count = raw.count(b"\x00")
    if null_count > 0 and null_count > len(raw) // 4:
        odd_nulls = sum(1 for i in range(1, len(raw), 2) if raw[i] == 0)
        even_nulls = sum(1 for i in range(0, len(raw), 2) if raw[i] == 0)
        if odd_nulls > even_nulls:
            return raw.decode("utf-16-le", errors="replace")
        return raw.decode("utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")
