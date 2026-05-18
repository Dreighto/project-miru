"""Tests for value normalization."""

from __future__ import annotations

from services.shadow_loop.normalize import equal, normalize


def test_none_and_empty_string_both_normalize_to_empty():
    assert normalize(None) == ""
    assert normalize("") == ""
    assert normalize("   ") == ""


def test_integers_normalize_to_bare_string():
    assert normalize(5) == "5"
    assert normalize(0) == "0"
    assert normalize(-3) == "-3"


def test_comma_separated_numbers_strip_commas():
    assert normalize("5,000") == "5000"
    assert normalize("1,234,567") == "1234567"


def test_strings_lowercase_and_collapse_whitespace():
    assert normalize("Red") == "red"
    assert normalize("  Monkey   D.  Luffy  ") == "monkey d. luffy"


def test_equal_handles_numeric_string_mismatch():
    """'5000' (str) vs 5000 (int) should compare equal."""
    assert equal("5000", 5000) is True
    assert equal("5,000", 5000) is True


def test_equal_handles_case_and_whitespace():
    assert equal("Red", "red") is True
    assert equal(" Red ", "Red") is True


def test_unequal_when_values_actually_differ():
    assert equal(5000, 6000) is False
    assert equal("Red", "Blue") is False
    assert equal(None, "Red") is False
