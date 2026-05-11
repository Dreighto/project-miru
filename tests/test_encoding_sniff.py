"""Tests for tools/_encoding_sniff.decode_bytes_with_utf16_sniffing — the
shared byte-level encoding sniffer extracted in CR R5 on PR #3.

The same logic is exercised end-to-end via tests/test_check_ps1_hardcoded_paths.py
(UTF-16-LE/BE with and without BOM, utf-8). This file adds direct unit tests
on the helper so a regression in the sniffer surfaces here cleanly without
needing the surrounding file-read plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make tools/ importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from _encoding_sniff import decode_bytes_with_utf16_sniffing  # noqa: E402

SAMPLE = "Register-ScheduledTask -Description 'Managed by D:\\dev\\miru\\x.ps1'\n"


def test_utf8_plain_passthrough() -> None:
    """Plain UTF-8 bytes decode unchanged."""
    decoded = decode_bytes_with_utf16_sniffing(SAMPLE.encode("utf-8"))
    assert decoded == SAMPLE


def test_utf16_le_with_bom() -> None:
    """UTF-16-LE bytes prefixed by the FF FE BOM decode as utf-16-le with
    BOM stripped."""
    raw = b"\xff\xfe" + SAMPLE.encode("utf-16-le")
    decoded = decode_bytes_with_utf16_sniffing(raw)
    assert decoded == SAMPLE


def test_utf16_be_with_bom() -> None:
    """UTF-16-BE bytes prefixed by the FE FF BOM decode as utf-16-be with
    BOM stripped."""
    raw = b"\xfe\xff" + SAMPLE.encode("utf-16-be")
    decoded = decode_bytes_with_utf16_sniffing(raw)
    assert decoded == SAMPLE


def test_utf16_le_no_bom() -> None:
    """UTF-16-LE without BOM: null bytes at every odd position (high byte
    of each 16-bit word). The sniffer's null-density heuristic should pick
    utf-16-le."""
    raw = SAMPLE.encode("utf-16-le")
    decoded = decode_bytes_with_utf16_sniffing(raw)
    assert decoded == SAMPLE


def test_utf16_be_no_bom() -> None:
    """UTF-16-BE without BOM: null bytes at every even position."""
    raw = SAMPLE.encode("utf-16-be")
    decoded = decode_bytes_with_utf16_sniffing(raw)
    assert decoded == SAMPLE


def test_empty_bytes_returns_empty_string() -> None:
    """Edge case — zero bytes in, empty string out, no exception."""
    assert decode_bytes_with_utf16_sniffing(b"") == ""


def test_partially_corrupt_utf8_falls_back_to_replace() -> None:
    """If utf-8 decode strict mode fails AND null-byte density doesn't
    trigger UTF-16 detection, the fallback is utf-8 with errors='replace'.
    The decoded result still contains the surrounding readable text — it
    doesn't raise UnicodeDecodeError."""
    # Single invalid utf-8 byte sequence mixed into otherwise-valid text.
    raw = b"valid prefix " + b"\xff\xfe-not-a-valid-bom-here-" + b" valid suffix"
    # Note: raw starts with `b"valid prefix "` not `b"\xff\xfe"`, so BOM
    # detection skips. Null-byte count is 0, so UTF-16 heuristic skips.
    # Falls to utf-8 strict (which raises on \xff\xfe in the middle) and
    # then utf-8 replace.
    decoded = decode_bytes_with_utf16_sniffing(raw)
    assert "valid prefix" in decoded
    assert "valid suffix" in decoded


def test_null_byte_density_threshold() -> None:
    """A file with sparse null bytes (< 25%) should NOT be misclassified
    as UTF-16. The cutoff prevents binary-ish files with occasional nulls
    from being decoded as UTF-16 garbage."""
    # 100 bytes of valid utf-8 ASCII + 1 null byte. 1% null density.
    raw = b"A" * 100 + b"\x00"
    decoded = decode_bytes_with_utf16_sniffing(raw)
    # Should decode as utf-8 (the null byte stays as U+0000 in the output).
    assert decoded.startswith("A" * 100)
    assert "\x00" in decoded
