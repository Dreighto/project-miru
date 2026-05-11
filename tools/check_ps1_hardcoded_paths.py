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

# PowerShell on Windows commonly writes .ps1 files as UTF-16 (especially when
# created via Out-File without -Encoding). UTF-8 read fails with
# UnicodeDecodeError on those files, which would silently bypass this hook.
# CR caught this gap on PR #3 — try UTF-16-LE then UTF-16-BE before giving up.
_ENCODINGS_TO_TRY = ("utf-8", "utf-16", "utf-16-le", "utf-16-be")


def _read_text_robust(path: Path) -> str | None:
    """Try the common PowerShell-on-Windows encodings in order. Returns the
    decoded text on first success, or None if every attempt fails."""
    for encoding in _ENCODINGS_TO_TRY:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


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
