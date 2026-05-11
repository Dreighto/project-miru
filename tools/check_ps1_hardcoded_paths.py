#!/usr/bin/env python3
"""check_ps1_hardcoded_paths.py — pre-commit hook that rejects hardcoded
absolute paths inside PowerShell `-Description "..."` arguments.

WHY (2026-05-11): CodeRabbit flagged a regression on the orchestrator-side
`register_restart_tasks.ps1` where the scheduled-task Description embedded a
literal `D:\\dev\\LogueOS-Orchestrator\\windows\\startup_all.ps1`. That makes
the description wrong on any machine where the install path differs (different
drive letter, different parent dir, WSL, fresh box). The correct shape is a
dynamic reference — `$startupScript`, `$PSScriptRoot`, `$windowsDir` — which
PowerShell expands at runtime.

This hook is intentionally NARROW: it only inspects `-Description "..."`
string-literal arguments to scheduled-task cmdlets. Other absolute-path
references in PowerShell (e.g., `Test-Path 'D:\\dev\\...'`, comment headers,
deliberate examples in help strings) are NOT flagged — too many false
positives, and `-Description` is the demonstrated regression surface.

Detection rule:
  - Find every `-Description "..."` (case-insensitive) on each line.
  - Inside the captured description body, flag any match of:
      Windows absolute path:  `[A-Za-z]:\\dev\\<rest>`
      POSIX absolute path:    `/(home|var|opt|tmp|Users)/<rest>`
  - At least one match in any staged .ps1 file => exit code 1.

Bypass:
  This is a structural style check. `git commit --no-verify` skips it.
  Document the reason in the commit message ("HYGIENE BYPASS: ...") when
  bypassing — see CLAUDE.md ## Hygiene gate.

Exit codes:
  0 — clean (no offending paths in -Description values among staged .ps1 files)
  1 — at least one offending path detected (prints file:line + match + fix hint)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# When this script is invoked as `python tools/check_ps1_hardcoded_paths.py`
# (the pre-commit framework's `entry: python tools/...py` form), Python adds
# the script's directory (tools/) to sys.path, so the sibling helper module
# is importable by its bare name. Same trick works for pre_pr_review.py.
from _encoding_sniff import decode_bytes_with_utf16_sniffing

# Capture every -Description <quoted-body> arg. CR R6 (PR #3) flagged that
# the previous simple `(["'])(.*?)\1` pattern was naive about PowerShell's
# actual quote rules:
#
# 1. Parameter binding: `-Description "x"` AND `-Description:"x"` both work.
#    The colon-separated form is legal; we accept either via `\s*[:\s]\s*`
#    between the parameter name and the opening quote.
#
# 2. Double-quoted strings: backtick (`) escapes the next char, including
#    inner quotes. `"foo `"bar`""` is one string with body `foo "bar"`.
#    The naive regex would bail at the first inner `"`, missing any
#    hardcoded paths after the escape.
#    Pattern: `[^"`]` = any char that isn't `"` or `` ` ``;
#             `` `. `` = backtick + any char.
#
# 3. Single-quoted strings: PowerShell escapes a single quote by doubling
#    it. `'foo ''bar'''` has body `foo 'bar'`. Pattern: `[^']` = any
#    non-quote char; `''` = doubled quote.
#
# Alternation: try double-quoted first, then single. Mutually exclusive at
# each match site so exactly one of group("double") / group("single") is
# populated; the other is None. Caller picks whichever is present.
DESCRIPTION_RE = re.compile(
    r"""
    -Description                            # parameter name
    \s*[:\s]\s*                             # space or colon separator
    (?:
        "(?P<double>(?:[^"`]|`.)*)"         # double-quoted, backtick-escapes
        |
        '(?P<single>(?:[^']|'')*)'          # single-quoted, doubled-quote
    )
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

# Windows absolute paths under <drive>:[\\/]dev[\\/]<...>. Tight on dev/ to avoid
# false positives on paths like C:\Windows\System32 that legitimately appear in
# help text. Matches both backslash form (D:\dev\...) and forward-slash form
# (D:/dev/...) — both are legal Windows paths and CR flagged that the
# forward-slash variant was a silent bypass on PR #3.
WIN_PATH_RE = re.compile(r"[A-Za-z]:[\\/]dev[\\/]\S+", re.IGNORECASE)

# POSIX absolute paths under the common user-facing roots. Same false-positive
# discipline — we look for paths that would belong to a developer's working dir,
# not arbitrary system paths.
POSIX_PATH_RE = re.compile(r"/(home|var|opt|tmp|Users)/\S+", re.IGNORECASE)

# PowerShell on Windows commonly writes .ps1 files as UTF-16. The naive
# utf-8-first ordered fallback does NOT work for UTF-16-LE/BE without a BOM —
# utf-8 decode of UTF-16 bytes succeeds with garbage (every other byte is
# U+0000, which is a valid UTF-8 codepoint), so it never raises
# UnicodeDecodeError. CR R5 on PR #3 extracted the byte-level encoding sniffer
# into the shared `_encoding_sniff` module; this function is a thin wrapper.


def _read_text_robust(path: Path) -> str | None:
    """Read `path` as bytes, sniff its encoding, return decoded text. Returns
    None only on OSError (file truly unreadable). The shared sniffer never
    raises UnicodeDecodeError — falls back to utf-8 with errors='replace' so
    even partially-corrupt files surface scannable text."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return decode_bytes_with_utf16_sniffing(raw)


def check_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_num, offending_match) tuples for one file."""
    findings: list[tuple[int, str]] = []
    text = _read_text_robust(path)
    if text is None:
        # Unreadable file (genuine OSError, or no supported encoding worked);
        # treat as clean and let other hooks complain.
        return findings
    # CR R3 (PR #3): DOTALL-enabled regex spans newlines, so we finditer
    # over the whole text rather than per-line. The match's start offset
    # is mapped back to a 1-indexed line number by counting newlines
    # before it — gives the line where the -Description token appears
    # (not necessarily where the offending path is, but close enough for
    # operator triage).
    for desc_match in DESCRIPTION_RE.finditer(text):
        # CR R6 (PR #3): the alternation in DESCRIPTION_RE populates exactly
        # one of the named groups — `double` for `"..."`, `single` for `'...'`.
        # The other is None. Pick whichever matched.
        body = (
            desc_match.group("double")
            if desc_match.group("double") is not None
            else desc_match.group("single")
        )
        line_num = text.count("\n", 0, desc_match.start()) + 1
        findings.extend([(line_num, m.group(0)) for m in WIN_PATH_RE.finditer(body)])
        findings.extend([(line_num, m.group(0)) for m in POSIX_PATH_RE.finditer(body)])
    return findings


def main(argv: list[str]) -> int:
    files = [Path(p) for p in argv[1:]]
    if not files:
        return 0
    exit_code = 0
    for f in files:
        if f.suffix.lower() != ".ps1":
            continue
        for line_num, offending in check_file(f):
            print(
                f"{f}:{line_num}: hardcoded absolute path inside -Description string: "
                f"{offending!r}",
                file=sys.stderr,
            )
            print(
                "  fix: replace the absolute path with a dynamic reference such as "
                "`$startupScript`, `$PSScriptRoot`, or `$windowsDir` so the description "
                "stays correct across install locations.",
                file=sys.stderr,
            )
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
