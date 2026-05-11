#!/usr/bin/env python3
"""add_mcps.py — surgical add of Docker + PoshMCP entries to CC's config.

NOTICE: This script targets Claude Code only. Direct edits to Gemini's
.gemini/settings.json DO NOT work — Gemini CLI strips entries it didn't
register through its own flow when it next launches. For Gemini, use:

    pwsh -ExecutionPolicy Bypass -File tools/mcp/register_gemini_mcps.ps1

which calls `gemini mcp add` for each entry — that persists.

This script still handles Claude Code's ~/.claude.json correctly because
CC reads its mcpServers list verbatim and doesn't strip unknown entries.

Idempotent: re-running on a config that already has the entries is a no-op.
Backups are written to <config>.backup-<timestamp> before each modification.

Targets (CC only):
  - ~/.claude.json (mcpServers section)

MCP additions:
  - docker          : uvx mcp-server-docker (ckreiling/mcp-server-docker)
  - scheduled-tasks : pwsh + PoshMCP with whitelisted ScheduledTasks cmdlets

Run from anywhere:
    python tools/mcp/add_mcps.py            # apply
    python tools/mcp/add_mcps.py --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

CLAUDE_CONFIG = Path.home() / ".claude.json"
# GEMINI_CONFIG intentionally removed — Gemini strips direct edits.
# Use tools/mcp/register_gemini_mcps.ps1 instead.

# POSH_MCP_CONFIG is derived from this script's location so any clone of
# the repo gets a working path. Previously hardcoded as D:\dev\miru\... ,
# which broke on machines or clone locations that didn't match. CR R1
# finding on PR #190.
POSH_MCP_CONFIG = str(Path(__file__).resolve().parent / "posh-mcp-config.json")

DOCKER_ENTRY = {
    "command": "uvx",
    "args": ["mcp-server-docker"],
    "env": {},
    "trust": False,
}

POSH_MCP_ENTRY = {
    "command": "pwsh",
    "args": [
        "-NoProfile",
        "-NoLogo",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        f"Import-Module PoshMCP; Start-PoshMcp -ConfigPath '{POSH_MCP_CONFIG}'",
    ],
    "env": {},
    "trust": False,
}

ENTRIES = {
    "docker": DOCKER_ENTRY,
    "scheduled-tasks": POSH_MCP_ENTRY,
}

# Per-entry prerequisite files. The script refuses to write an entry
# whose prerequisite is missing — pointing an MCP at a non-existent
# config file just produces a server that exits immediately on every
# launch, which is harder to debug than a missing entry.
ENTRY_PREREQUISITES: dict[str, list[str]] = {
    "scheduled-tasks": [POSH_MCP_CONFIG],
}


def _backup(path: Path) -> Path:
    """Copy path to path.backup-<timestamp> and return the backup path."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".backup-{ts}")
    shutil.copy2(path, backup)
    return backup


def _missing_prereqs(name: str) -> list[str]:
    """Return list of prerequisite files that are missing for entry `name`."""
    prereqs = ENTRY_PREREQUISITES.get(name, [])
    return [p for p in prereqs if not Path(p).exists()]


def _add_entries(path: Path, dry_run: bool) -> tuple[list[str], list[str], list[str]]:
    """Add ENTRIES to path's mcpServers section.

    Returns (added, skipped, errors). `errors` is a non-empty list of
    short strings if the config was unreadable, structurally invalid, or
    an entry's prerequisite file was missing — these surface non-zero
    exits via main(). CR R2 findings on PR #190 (MAJOR lines 66 + 113).
    """
    errors: list[str] = []

    if not path.exists():
        msg = f"{path}: NOT FOUND"
        print(f"[add_mcps] {msg} — refusing to apply", file=sys.stderr)
        errors.append(msg)
        return [], [], errors
    with path.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            msg = f"{path}: invalid JSON ({exc})"
            print(f"[add_mcps] ERROR — {msg}. Refusing to modify.", file=sys.stderr)
            errors.append(msg)
            return [], [], errors
    if not isinstance(data, dict):
        msg = f"{path}: top-level JSON must be an object, got {type(data).__name__}"
        print(f"[add_mcps] ERROR — {msg}. Refusing to modify.", file=sys.stderr)
        errors.append(msg)
        return [], [], errors
    existing_servers = data.get("mcpServers")
    if existing_servers is None:
        data["mcpServers"] = {}
    elif not isinstance(existing_servers, dict):
        msg = (
            f"{path}: mcpServers exists but is not an object "
            f"(got {type(existing_servers).__name__})"
        )
        print(f"[add_mcps] ERROR — {msg}. Refusing to modify.", file=sys.stderr)
        errors.append(msg)
        return [], [], errors
    mcp_servers = data["mcpServers"]

    added: list[str] = []
    skipped: list[str] = []
    for name, entry in ENTRIES.items():
        if name in mcp_servers:
            skipped.append(name)
            continue
        # CR R2 (MAJOR line 66): refuse to write an entry whose
        # prerequisite file is missing. For scheduled-tasks that's the
        # PoshMCP config; pointing the MCP at a missing path produces a
        # silent immediate-exit server that's harder to debug than a
        # gap in the config.
        missing = _missing_prereqs(name)
        if missing:
            msg = f"{name}: missing prerequisite file(s): {missing}"
            print(f"[add_mcps] ERROR — {msg}. Refusing to add this entry.", file=sys.stderr)
            errors.append(msg)
            continue
        if not dry_run:
            mcp_servers[name] = entry
        added.append(name)

    if added and not dry_run:
        backup = _backup(path)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        print(f"[add_mcps] {path}: added {added}; backup at {backup}")
    elif added and dry_run:
        print(f"[add_mcps] {path}: WOULD add {added} (dry-run)")
    if skipped:
        print(f"[add_mcps] {path}: already present {skipped} — no change")

    return added, skipped, errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Add Docker + PoshMCP entries to Claude Code's ~/.claude.json. "
        "For Gemini, use tools/mcp/register_gemini_mcps.ps1 (direct edits are stripped by Gemini)."
    )
    p.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = p.parse_args(argv)

    print(f"Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()
    _, _, errors = _add_entries(CLAUDE_CONFIG, args.dry_run)
    print()

    if errors:
        # CR R2 (MAJOR line 113): config-missing / structurally-invalid /
        # missing-prereq must surface as a non-zero exit so automation
        # doesn't silently treat a no-op or partial run as success.
        print(f"FAILED with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Done. Operator action required:")
    print("  - Restart Claude Code to load the new MCPs into CC's session.")
    print("  - For Gemini, run the separate script (direct edits get stripped):")
    print("      pwsh -ExecutionPolicy Bypass -File tools/mcp/register_gemini_mcps.ps1")
    print("  - First-time invocation of PoshMCP requires PowerShell 7 (pwsh).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
