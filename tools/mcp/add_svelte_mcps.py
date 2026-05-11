#!/usr/bin/env python3
"""add_svelte_mcps.py — add SvelteKit-stack MCPs to Gemini's config.

Adds the 5 MCPs that Gemini needs for the LogueOS-Console SvelteKit
frontend work, per the research agent's report. Idempotent; backups
are written before each modification.

MCPs added (Gemini only — frontend lane):
  - svelte             : @sveltejs/mcp (OFFICIAL, includes svelte-autofixer)
  - shadcn-svelte      : @jpisnice/shadcn-ui-mcp-server --framework svelte
  - lucide-icons       : SeeYangZhi/lucide-icons-mcp
  - a11y-scanner       : JustasMonkev/mcp-accessibility-scanner (Playwright + axe-core)
  - vitest             : @djankies/vitest-mcp (LLM-safety-aware, no full-runs by default)

NOT added: Tailwind MCP — the research found no candidate that actually
reads tailwind.config.ts theme tokens. Revisit if a real one ships.

For shadcn-svelte: GH_TOKEN env var avoids rate limits on the
upstream GitHub API. Set in your environment OR the script falls back
to the no-token path (works, just rate-limited).

Run:
    python tools/mcp/add_svelte_mcps.py            # apply
    python tools/mcp/add_svelte_mcps.py --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

GEMINI_CONFIG = Path("D:/dev/miru/.gemini/settings.json")

ENTRIES = {
    "svelte": {
        "command": "npx.cmd",
        "args": ["-y", "@sveltejs/mcp"],
        "env": {},
        "trust": False,
    },
    "shadcn-svelte": {
        "command": "npx.cmd",
        "args": [
            "-y",
            "@jpisnice/shadcn-ui-mcp-server",
            "--framework",
            "svelte",
        ],
        "env": {
            # GH_TOKEN raises the GitHub API rate limit from 60 req/hour
            # (unauthenticated) to 5000 req/hour. Falls back gracefully if
            # unset — workers will just hit rate limits faster.
            "GITHUB_PERSONAL_ACCESS_TOKEN": "${env:GITHUB_TOKEN}",
        },
        "trust": False,
    },
    "lucide-icons": {
        "command": "npx.cmd",
        "args": ["-y", "lucide-icons-mcp"],
        "env": {},
        "trust": False,
    },
    "a11y-scanner": {
        "command": "npx.cmd",
        "args": ["-y", "mcp-accessibility-scanner"],
        "env": {},
        "trust": False,
    },
    "vitest": {
        "command": "npx.cmd",
        "args": ["-y", "@djankies/vitest-mcp"],
        "env": {},
        "trust": False,
    },
}


def _backup(path: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".backup-{ts}")
    shutil.copy2(path, backup)
    return backup


def _add_entries(path: Path, dry_run: bool) -> tuple[list[str], list[str]]:
    if not path.exists():
        print(f"[add_svelte_mcps] {path}: NOT FOUND — skipping", file=sys.stderr)
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
        print(f"[add_svelte_mcps] {path}: added {added}; backup at {backup}")
    elif added and dry_run:
        print(f"[add_svelte_mcps] {path}: WOULD add {added} (dry-run)")
    if skipped:
        print(f"[add_svelte_mcps] {path}: already present {skipped} — no change")

    return added, skipped


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Add SvelteKit-stack MCPs to Gemini's config")
    p.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = p.parse_args(argv)

    print(f"Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()
    _add_entries(GEMINI_CONFIG, args.dry_run)
    print()
    print("Done. Notes:")
    print("  - Restart Gemini CLI to load the new MCPs.")
    print("  - shadcn-svelte: set GITHUB_TOKEN env var to avoid rate limits.")
    print("    Without it, the MCP still works but at 60 req/hour.")
    print("  - Tailwind MCP was deliberately omitted — no current candidate")
    print("    reads tailwind.config.ts theme tokens. Revisit later.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
