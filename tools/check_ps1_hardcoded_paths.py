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

# Capture every -Description <quoted-body> arg. PowerShell accepts both
# double- and single-quoted strings here (and even back-tick-continued
# multi-line strings), so we match either quote style and span across
# newlines via re.DOTALL. The opening quote is captured in group(1) and
# the closing quote is matched via backreference \1, which keeps the
# body group(2) tight against mixed-quote false positives.
#
# CR R3 (PR #3): only double quotes was previously a silent bypass for
# any -Description '...' single-quoted variant — PowerShell scripts in
# the wild use both forms. DOTALL adds multi-line coverage too.
DESCRIPTION_RE = re.compile(
    r"""-Description\s+(["'])(.*?)\1""",
    re.IGNORECASE | re.DOTALL,
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
# UnicodeDecodeError. CR R4 on PR #193 caught the bypass. The correct
# approach is byte-level sniffing: detect a BOM if present, otherwise count
# null-byte positional density to distinguish UTF-16-LE / UTF-16-BE / UTF-8.


def _read_text_robust(path: Path) -> str | None:
    """Detect the right encoding for a PowerShell file via BOM + null-byte
    heuristics, then decode. Returns the decoded text, or None on OSError.

    Detection order:
      1. UTF-16-LE BOM (FF FE)  → strip BOM, decode utf-16-le
      2. UTF-16-BE BOM (FE FF)  → strip BOM, decode utf-16-be
      3. > 25% null bytes total: BOM-less UTF-16. LE vs BE is decided by
         counting null-byte density at odd vs even byte positions.
         ASCII-encoded UTF-16-LE has 0x00 at every odd byte (the high byte
         of each 16-bit word); UTF-16-BE has 0x00 at every even byte.
      4. Otherwise: UTF-8 (with errors='replace' as a last resort so we
         still scan most of the file even on partially-corrupt input).
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
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
        body = desc_match.group(2)
        line_num = text.count("\n", 0, desc_match.start()) + 1
        for p in WIN_PATH_RE.finditer(body):
            findings.append((line_num, p.group(0)))
        for p in POSIX_PATH_RE.finditer(body):
            findings.append((line_num, p.group(0)))
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
