#!/usr/bin/env python3
"""add_mcps.py — surgical add of Docker + PoshMCP entries to CC + Gemini configs.

Idempotent: re-running on a config that already has the entries is a no-op.
Backups are written to <config>.backup-<timestamp> before each modification.

Targets:
  - ~/.claude.json (mcpServers section)
  - D:\\dev\\miru\\.gemini\\settings.json (mcpServers section)

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
GEMINI_CONFIG = Path("D:/dev/miru/.gemini/settings.json")
POSH_MCP_CONFIG = "D:\\dev\\miru\\tools\\mcp\\posh-mcp-config.json"

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


def _backup(path: Path) -> Path:
    """Copy path to path.backup-<timestamp> and return the backup path."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".backup-{ts}")
    shutil.copy2(path, backup)
    return backup


def _add_entries(path: Path, dry_run: bool) -> tuple[list[str], list[str]]:
    """Add ENTRIES to path's mcpServers section. Returns (added, skipped)."""
    if not path.exists():
        print(f"[add_mcps] {path}: NOT FOUND — skipping", file=sys.stderr)
        return [], []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    mcp_servers = data.setdefault("mcpServers", {})

    added: list[str] = []
    skipped: list[str] = []
    for name, entry in ENTRIES.items():
        if name in mcp_servers:
            skipped.append(name)
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

    return added, skipped


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Add Docker + PoshMCP entries to CC + Gemini configs")
    p.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = p.parse_args(argv)

    print(f"Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()
    for target in [CLAUDE_CONFIG, GEMINI_CONFIG]:
        _add_entries(target, args.dry_run)
        print()

    print("Done. Operator action required:")
    print("  - Restart Claude Code to load the new MCPs")
    print("  - Restart Gemini CLI (or re-spawn it) to load the new MCPs")
    print("  - First-time invocation of PoshMCP requires PowerShell 7 (pwsh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
