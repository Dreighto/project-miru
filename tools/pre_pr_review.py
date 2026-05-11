#!/usr/bin/env python3
"""pre_pr_review.py — pre-push static-analysis pass for CR's common patterns.

Looks at the working tree (or `--from-ref/--to-ref` range) for patterns
that CodeRabbit consistently flags. Goal: catch them locally before
opening or pushing to a PR so we don't burn review rounds on issues
we could have seen ourselves.

The patterns codified here are derived from actual CR findings across
LOS-10's PRs (#181/#182/#184/#187). Each finding becomes one detector.
Detectors are intentionally conservative — they flag suspicious patterns
for human review, NOT auto-fix. False positives are acceptable; false
negatives are not.

USAGE:

    # Scan all staged + unstaged changes vs HEAD
    python tools/pre_pr_review.py

    # Scan changes since main (typical pre-PR check)
    python tools/pre_pr_review.py --from-ref main

    # Scan specific files only
    python tools/pre_pr_review.py path/to/file.py path/to/other.sh

    # Strict mode: exit 1 if any finding (for CI integration later)
    python tools/pre_pr_review.py --strict

    # JSON output instead of human-readable
    python tools/pre_pr_review.py --json

DETECTOR CATALOG (current — match DETECTORS list at the bottom):

    P1 (path-traversal)        — user-controlled strings used in filenames
                                  without validation. Pattern: f-string into
                                  filename where the variable came from argv
                                  / argparse / os.environ.
    P2 (fsync-rename)          — atomic-rename pattern (os.replace / os.rename
                                  after write) without a follow-up directory
                                  fsync. POSIX requires the dir fsync for
                                  rename durability.
    P3 (fsync-fd)              — os.fsync called on a file object opened
                                  read-only. Raises EBADF on Windows.
    P5 (relative-after-cd)     — variable assigned a relative path BEFORE a
                                  `cd` in shell, then referenced AFTER.
    P8 (corrupt-vs-empty)      — return tuple shape that conflates "file
                                  empty" with "file corrupt". Both cases
                                  return same sentinel (None, None) without
                                  a corrupt-distinguishing flag.
    P9 (dash-only-not-rejected)— argument validation that only rejects `--*`
                                  values, missing single-dash flags like `-h`.
    P10 (ps1-hardcoded-paths)  — a PowerShell `-Description "..."` argument
                                  embeds a hardcoded absolute path under a
                                  dev/work root. Caught by CR on PR #3 when
                                  register_restart_tasks.ps1 embedded an
                                  absolute path into a scheduled-task
                                  description.

NOT-YET-IMPLEMENTED (reserved identifiers — add detectors before re-listing):

    P4 (hardcoded-env)         — string literal that looks like an env-derived
                                  constant (e.g. 'worktrees', '127.0.0.1')
                                  used where a configurable value is expected.
    P6 (origin-clash)          — `cp -r ... .git`-style operations followed by
                                  `git remote add origin` without removing the
                                  inherited origin.
    P7 (untracked-not-caught)  — bash `git diff --quiet && git diff --cached
                                  --quiet` patterns that miss untracked files.

Exit codes:
    0 — clean (no findings) OR findings printed but --strict not set
    1 — findings printed AND --strict set
    2 — usage / I/O error
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


@dataclass
class Finding:
    detector: str
    severity: str  # "warn" | "high" — high mirrors CR's "Major+"
    path: str
    line: int
    title: str
    snippet: str


# ---------------------------------------------------------------------------
# Detector implementations
# ---------------------------------------------------------------------------


def _var_is_validated(var: str, upstream: str) -> bool:
    """Return True if `var` shows a validation marker in `upstream`.

    Recognized validation patterns:
      1. Direct: re.fullmatch / re.match / _parse_* / _validate_* called on var
      2. Reassignment from a parser: var = _parse_X(...)
      3. Two-step round-trip: parsed_X = _parse_Y(var); var = parsed_X.strftime(...)
         — this is the canonical "validate then re-render" pattern for
         timestamps. The parse function rejects malformed input; the
         strftime re-render produces a known-shape string that can't
         contain path separators or `..` segments.
    """
    if re.search(
        rf"(?:re\.fullmatch|re\.match|_parse_|_validate_)\s*\([^)]*\b{re.escape(var)}\b",
        upstream,
    ):
        return True
    # Variable was assigned from a parser/validator return
    if re.search(
        rf"\b{re.escape(var)}\s*=\s*(?:_parse_\w*|_validate_\w*)",
        upstream,
    ):
        return True
    # Two-step pattern: some_parsed = _parse_*(var); var = some_parsed.strftime(...)
    # If anywhere upstream we see `parsed_*` or similar followed by
    # reassignment of var from its method, that's validation.
    for m in re.finditer(rf"\b{re.escape(var)}\s*=\s*([A-Za-z_]\w*)\.\w+\(", upstream):
        parsed_var = m.group(1)
        # Was parsed_var sourced from a _parse_* / _validate_* call that
        # consumed our var?
        if re.search(
            rf"\b{re.escape(parsed_var)}\s*=\s*(?:_parse_\w*|_validate_\w*)\s*\([^)]*\b{re.escape(var)}\b",
            upstream,
        ):
            return True
    return False


def _trace_var_source(var: str, upstream: str, max_hops: int = 3) -> str | None:
    """Follow assignment chains to find the originating source variable.

    String-cleanup methods (.replace, .strip, .lower, .upper, .split) don't
    sanitize path-traversal chars (`/`, `..`) — so a var assigned from
    `x.replace(...)` is still vulnerable to anything x was vulnerable to.
    Walk the chain up to max_hops, returning the deepest source name.
    """
    current = var
    for _ in range(max_hops):
        # Find the most recent assignment to `current`
        assign_re = re.compile(
            rf"\b{re.escape(current)}\s*=\s*([^\n]+)",
        )
        matches = list(assign_re.finditer(upstream))
        if not matches:
            return current
        rhs = matches[-1].group(1).strip()
        # If RHS is `someVar.replace(...)` / `.strip(...)` / etc., trace
        # back to someVar.
        hop = re.match(r"([A-Za-z_]\w*)\.(?:replace|strip|lower|upper|split)\b", rhs)
        if hop:
            current = hop.group(1)
            continue
        return current
    return current


def _detect_path_traversal(path: str, content: str) -> list[Finding]:
    """P1 — variable interpolated into a filename without visible validation.

    Catches both direct interpolation AND interpolation of a string that
    was "cleaned" via .replace / .strip / .lower from a user-input source.
    (Those string methods don't sanitize path-traversal chars — they
    preserve `/` and `..`.)

    Intentionally noisier than "obvious user input only" because path
    traversal often arrives via function-parameter chains (argparse →
    main() → helper(arg=...) → filename) that simple heuristics miss.
    False positives are OK; false negatives cost a CR round.
    """
    findings: list[Finding] = []
    if not path.endswith(".py"):
        return findings
    pattern = re.compile(
        r"(\w*(?:name|path|file)\w*)\s*=\s*f[\"'][^\"']*\{([^}]+)\}[^\"']*[\"']",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        var = m.group(2).strip()
        if var.isupper():
            continue
        if "." in var:
            continue  # attribute access
        if var.startswith("self."):
            continue
        line_num = content[: m.start()].count("\n") + 1
        upstream = content[max(0, m.start() - 4000) : m.start()]

        # Trace the var back through .replace/.strip etc. chains
        source = _trace_var_source(var, upstream)

        # If either the direct var OR its traced source is validated, trust it.
        if _var_is_validated(var, upstream) or (
            source and source != var and _var_is_validated(source, upstream)
        ):
            continue

        # Is the (traced) source user-input or a function parameter?
        check_name = source or var
        is_likely_user_input = bool(
            re.search(
                rf"\b{re.escape(check_name)}\s*=.*(?:args\.|argv\[|os\.environ|getenv)",
                upstream,
            )
        ) or bool(
            re.search(
                rf"def\s+\w+\([^)]*\b{re.escape(check_name)}\b[^)]*\)",
                upstream,
            )
        )
        if not is_likely_user_input:
            continue
        trace_note = f" (traced from '{source}')" if source != var else ""
        findings.append(
            Finding(
                "P1",
                "high",
                path,
                line_num,
                f"f-string filename interpolates '{var}'{trace_note} without visible validation — possible path traversal",
                m.group(0)[:120],
            )
        )
    return findings


def _detect_fsync_rename(path: str, content: str) -> list[Finding]:
    """P2 — os.replace / os.rename followed by no parent dir fsync.

    Flags the rename if the next 20 lines in the same function don't
    contain a directory fsync (os.fsync(os.open(parent, O_RDONLY))) or
    a call to a known helper like `_fsync_dir`.
    """
    findings: list[Finding] = []
    if not path.endswith(".py"):
        return findings
    lines = content.splitlines()
    rename_re = re.compile(r"\bos\.(?:replace|rename)\s*\(")
    dir_fsync_indicators = re.compile(
        r"(?:os\.fsync\([^)]*O_RDONLY|_fsync_dir|fsync_directory|fsync_parent)",
    )
    # CR R1 finding on PR #188: previous 25-line window could spill into
    # the NEXT function definition, so a `_fsync_dir` call in an unrelated
    # subsequent function would suppress a real finding here. That's a
    # false negative in one of the core detectors. Fix: stop the lookahead
    # window at the next function-def boundary (lines starting with
    # `def `, `async def `, or `class ` at any indent).
    func_or_class_re = re.compile(r"^\s*(?:async\s+def|def|class)\s+\w+")
    for i, line in enumerate(lines):
        if not rename_re.search(line):
            continue
        # Build the lookahead window, stopping at the next function/class
        # definition. The 25-line cap is preserved as a safety net.
        window_lines: list[str] = []
        for candidate in lines[i + 1 : i + 1 + 25]:
            if func_or_class_re.match(candidate):
                break
            window_lines.append(candidate)
        window = "\n".join(window_lines)
        if dir_fsync_indicators.search(window):
            continue
        findings.append(
            Finding(
                "P2",
                "warn",
                path,
                i + 1,
                "os.replace/rename without follow-up directory fsync",
                line.strip()[:120],
            )
        )
    return findings


def _detect_fsync_readonly(path: str, content: str) -> list[Finding]:
    """P3 — os.fsync(fileno) on a file opened "rb" / read-only. Raises
    EBADF on Windows."""
    findings: list[Finding] = []
    if not path.endswith(".py"):
        return findings
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if "os.fsync" not in line:
            continue
        upstream = "\n".join(lines[max(0, i - 8) : i])
        # Look for a recent `open("rb")` or `path.open("rb")`
        opened_readonly = re.search(r"\.open\(\s*[\"']rb?[\"']", upstream) or re.search(
            r"open\([^,]+,\s*[\"']rb?[\"']", upstream
        )
        if opened_readonly:
            findings.append(
                Finding(
                    "P3",
                    "warn",
                    path,
                    i + 1,
                    "os.fsync on read-only file descriptor — raises EBADF on Windows",
                    line.strip()[:120],
                )
            )
    return findings


def _detect_relative_after_cd(path: str, content: str) -> list[Finding]:
    """P5 — shell var assigned a relative path BEFORE a `cd`, then used AFTER."""
    findings: list[Finding] = []
    if not path.endswith(".sh"):
        return findings
    # Find $VAR = "..." assignments without absolute path or $(pwd)
    var_assign_re = re.compile(
        r"^(\w+)=[\"']([^\"']+)[\"']\s*(?:#|$)",
        re.MULTILINE,
    )
    cd_re = re.compile(r"^\s*cd\s+", re.MULTILINE)
    for m in var_assign_re.finditer(content):
        var, val = m.group(1), m.group(2)
        # Skip absolute paths and command-substituted paths
        if val.startswith("/") or "$(pwd" in val or "$(cd" in val or val.startswith("$"):
            continue
        assign_line = content[: m.start()].count("\n") + 1
        # Is there a `cd` AFTER this assignment but BEFORE the var is used?
        rest = content[m.end() :]
        cd_match = cd_re.search(rest)
        if not cd_match:
            continue
        cd_pos = m.end() + cd_match.start()
        # Is the var used AFTER the cd?
        after_cd = content[cd_pos:]
        if re.search(rf"\${{?{re.escape(var)}\b", after_cd):
            findings.append(
                Finding(
                    "P5",
                    "high",
                    path,
                    assign_line,
                    f"shell var ${var} assigned relative path before cd, used after — path will resolve differently",
                    f"${var}={val}",
                )
            )
    return findings


def _detect_dash_only_check(path: str, content: str) -> list[Finding]:
    """P9 — bash arg validation that only rejects `--*` patterns, missing
    `-h` and other single-dash flags.

    CR R1 finding on PR #188: the previous regex `\\$\\w+\\s*==\\s*["']--\\*["']`
    only matched bare `$var == "--*"`. Real-world bash patterns include:
      - [[ "$1" == --* ]]        (quoted left side)
      - [[ "${arg}" == --* ]]    (curly-brace expansion)
      - [[ $arg == --* ]]        (unquoted right side)
      - [[ $arg == "--*" ]]      (quoted right side)
    All of those are equally susceptible to the same bug — they reject
    only double-dash flags. Broadened regex matches all variants.
    """
    findings: list[Finding] = []
    if not path.endswith(".sh"):
        return findings
    # Match \$var or "$var" or ${var} or "${var}" on the left,
    # == operator, then --* literal (with or without surrounding quotes).
    pattern = re.compile(
        r"""(?x)                       # verbose mode
        ["']?                          # optional opening quote
        \$ (?: \{ \w+ \} | \w+ )       # $var or ${var}
        ["']?                          # optional closing quote
        \s* == \s*                     # ==
        ["']? --\* ["']?               # --* with optional surrounding quotes
        """,
    )
    for m in pattern.finditer(content):
        line_num = content[: m.start()].count("\n") + 1
        findings.append(
            Finding(
                "P9",
                "warn",
                path,
                line_num,
                "arg-value check rejects only '--*', misses single-dash flags like -h",
                m.group(0)[:120],
            )
        )
    return findings


def _detect_corrupt_vs_empty(path: str, content: str) -> list[Finding]:
    """P8 — a function that returns (None, None) for both "empty" and
    "corrupt" cases without a distinguishing flag."""
    findings: list[Finding] = []
    if not path.endswith(".py"):
        return findings
    # Look for functions returning (None, None) in more than one branch
    # of code that reads files / parses content.
    func_re = re.compile(
        r"def\s+(\w*(?:read|parse|scan|state|tail|head)\w*)\s*\([^)]*\)\s*->[^:]*:",
        re.IGNORECASE,
    )
    lines = content.splitlines()
    for m in func_re.finditer(content):
        func_start = content[: m.start()].count("\n") + 1
        # Read the next ~80 lines (function body, roughly)
        body_lines = lines[func_start : func_start + 80]
        body = "\n".join(body_lines)
        # Count return (None, None) occurrences
        none_returns = len(re.findall(r"return\s+None\s*,\s*None\b", body))
        if none_returns >= 2:
            findings.append(
                Finding(
                    "P8",
                    "warn",
                    path,
                    func_start,
                    f"function '{m.group(1)}' returns (None, None) in {none_returns} branches — consider distinct corrupt-vs-empty sentinels",
                    m.group(0),
                )
            )
    return findings


def _detect_ps1_hardcoded_paths(path: str, content: str) -> list[Finding]:
    """P10 — a `-Description "..."` argument in a PowerShell script contains a
    hardcoded absolute path. Caught by CR on PR #3 (LogueOS-Orchestrator) when
    `register_restart_tasks.ps1` embedded `D:\\dev\\LogueOS-Orchestrator\\windows\\
    startup_all.ps1` into a scheduled-task description — wrong on every machine
    where the install lives elsewhere. The correct shape uses a dynamic
    reference (`$startupScript`, `$PSScriptRoot`, `$windowsDir`).

    Detection is intentionally narrow: only `-Description` args, only common
    dev-root absolute paths. `Test-Path` / `Set-Content` / etc. with literal
    absolute paths are NOT flagged (legitimate file-existence checks).
    """
    findings: list[Finding] = []
    # CR R4 (PR #3): case-insensitive .ps1 suffix check — Windows file systems
    # are case-insensitive, so `.PS1` / `.Ps1` are the same file as `.ps1` and
    # must trigger the same detector path.
    if not path.lower().endswith(".ps1"):
        return findings
    # CR R3 (PR #3): regex now accepts either ' or " (PowerShell supports both)
    # and DOTALL spans multi-line back-tick-continued descriptions. The opening
    # quote is captured in group(1) and matched via backreference \1, so the
    # body group(2) is tight against mixed-quote false positives.
    desc_re = re.compile(r"""-Description\s+(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
    # CR R2 on PR #3: match both backslash and forward-slash Windows paths.
    # `D:/dev/...` is legal and was previously a silent bypass.
    win_path_re = re.compile(r"[A-Za-z]:[\\/]dev[\\/]\S+", re.IGNORECASE)
    posix_path_re = re.compile(r"/(home|var|opt|tmp|Users)/\S+", re.IGNORECASE)
    # CR R3 (PR #3): finditer over the whole content (not line-by-line) since
    # DOTALL means the regex spans newlines. Line number is derived from the
    # match's start offset.
    for desc_m in desc_re.finditer(content):
        body = desc_m.group(2)
        line_num = content.count("\n", 0, desc_m.start()) + 1
        # Snippet: the matched -Description line itself (first line of the body
        # if multi-line). Keeps the output familiar despite the underlying
        # regex now being whole-content.
        snippet_line = content[
            content.rfind("\n", 0, desc_m.start()) + 1 : content.find("\n", desc_m.start())
        ].strip()
        for hit in win_path_re.finditer(body):
            findings.append(
                Finding(
                    "P10",
                    "high",
                    path,
                    line_num,
                    f"-Description embeds hardcoded absolute path {hit.group(0)!r} — replace with $startupScript / $PSScriptRoot / $windowsDir",
                    snippet_line,
                )
            )
        for hit in posix_path_re.finditer(body):
            findings.append(
                Finding(
                    "P10",
                    "high",
                    path,
                    line_num,
                    f"-Description embeds hardcoded absolute path {hit.group(0)!r} — replace with $startupScript / $PSScriptRoot / $windowsDir",
                    snippet_line,
                )
            )
    return findings


DETECTORS = [
    _detect_path_traversal,
    _detect_fsync_rename,
    _detect_fsync_readonly,
    _detect_relative_after_cd,
    _detect_dash_only_check,
    _detect_corrupt_vs_empty,
    _detect_ps1_hardcoded_paths,
]


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------


class GitInvocationError(Exception):
    """A git subprocess failed in a way that should NOT be treated as
    'zero changed files'. The previous helpers collapsed every git error
    into [], which silently disabled the scan — a bad --from-ref, missing
    `git` on PATH, or running outside a checkout would all report a clean
    run with exit 0. CR R1 finding on PR #188. This exception is caught
    in main() and converted to exit 2 (usage error).
    """


def _changed_files_against(base_ref: str) -> list[Path]:
    """List files changed vs base_ref. Includes staged + unstaged.

    Raises GitInvocationError if git fails — propagated to main() rather
    than silently returning [] (which would falsely report a clean scan).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitInvocationError(f"git not found on PATH: {exc}") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise GitInvocationError(f"git diff failed: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise GitInvocationError(
            f"git diff --name-only {base_ref} returned {result.returncode}: {stderr}"
        )
    return [Path(p.strip()) for p in result.stdout.splitlines() if p.strip()]


def _changed_files_working_tree() -> list[Path]:
    """Files modified vs HEAD (staged + unstaged + untracked).

    Raises GitInvocationError on git failure (see _changed_files_against
    docstring for rationale).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitInvocationError(f"git not found on PATH: {exc}") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise GitInvocationError(f"git status failed: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise GitInvocationError(f"git status --porcelain returned {result.returncode}: {stderr}")
    paths: list[Path] = []
    for raw in result.stdout.splitlines():
        if len(raw) < 4:
            continue
        # Porcelain format: "XY filename"; the filename starts at column 3
        rest = raw[3:].strip()
        # Renames look like "old -> new" — take the new path
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        paths.append(Path(rest))
    return paths


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "Pre-PR review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "paths",
        nargs="*",
        help="Specific files to scan (default: changed files in working tree)",
    )
    p.add_argument(
        "--from-ref",
        default=None,
        help="Compare against this git ref (e.g. main). Default: working tree.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable output",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any findings (default: exit 0 even with findings)",
    )

    args = p.parse_args(argv)

    if args.paths:
        files = [Path(s) for s in args.paths]
    elif args.from_ref:
        try:
            files = _changed_files_against(args.from_ref)
        except GitInvocationError as exc:
            print(f"[pre_pr_review] git error: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            files = _changed_files_working_tree()
        except GitInvocationError as exc:
            print(f"[pre_pr_review] git error: {exc}", file=sys.stderr)
            return 2

    # Filter to files that exist and are not deleted
    files = [f for f in files if f.exists() and f.is_file()]

    findings: list[Finding] = []
    for file_path in files:
        # Skip vendored / generated files
        path_str = str(file_path)
        if any(skip in path_str for skip in (".venv", "node_modules", "__pycache__")):
            continue
        # CR R4 (PR #193 + PR #3): for .ps1 files, byte-level encoding
        # sniffing. The previous ordered-fallback chain (utf-8, utf-16,
        # utf-16-le, utf-16-be) DOES NOT detect BOM-less UTF-16: utf-8
        # decode of UTF-16 bytes succeeds silently with garbage because
        # U+0000 is a valid UTF-8 codepoint. Instead: check for BOM,
        # then null-byte density at odd vs even positions to discriminate
        # LE / BE / UTF-8. Non-.ps1 files keep the lenient utf-8+replace
        # read since they're not the regression surface and we don't want
        # to misclassify legitimate binary blobs as UTF-16 by accident.
        content: str | None = None
        if path_str.lower().endswith(".ps1"):
            try:
                raw = file_path.read_bytes()
            except OSError:
                continue
            if raw.startswith(b"\xff\xfe"):
                content = raw[2:].decode("utf-16-le", errors="replace")
            elif raw.startswith(b"\xfe\xff"):
                content = raw[2:].decode("utf-16-be", errors="replace")
            elif raw.count(b"\x00") > len(raw) // 4:
                # BOM-less UTF-16 — decide LE vs BE by null-byte positions.
                odd_nulls = sum(1 for i in range(1, len(raw), 2) if raw[i] == 0)
                even_nulls = sum(1 for i in range(0, len(raw), 2) if raw[i] == 0)
                enc = "utf-16-le" if odd_nulls > even_nulls else "utf-16-be"
                content = raw.decode(enc, errors="replace")
            else:
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    content = raw.decode("utf-8", errors="replace")
        else:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        for detector in DETECTORS:
            findings.extend(detector(path_str.replace("\\", "/"), content))

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2, sort_keys=True))
    else:
        if not findings:
            print("[pre_pr_review] clean — no patterns matched.")
        else:
            print(f"[pre_pr_review] {len(findings)} finding(s):")
            print()
            # Sort by severity (high first), then by path
            findings.sort(key=lambda f: (0 if f.severity == "high" else 1, f.path, f.line))
            for f in findings:
                badge = "HIGH" if f.severity == "high" else "WARN"
                print(f"  [{badge}] {f.detector}  {f.path}:{f.line}")
                print(f"         {f.title}")
                if f.snippet:
                    print(f"         > {f.snippet}")
                print()

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
