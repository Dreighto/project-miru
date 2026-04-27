"""Splice the MCP n8n-write branch into the W7 Telegram callback workflow JSON.

Inserts `workflows/fragments/w7_mcp_branch.nodes.json` immediately *after* the
`w7007-found-branch` node (the IF that fans out to the MCP path vs the legacy
noop). This anchor is stable: the old approach (insert before `w7-noop-duplicate`)
is equivalent only when that noop is still the very next node in the array; if
the node list is reordered, inserting before the first `w7-noop-duplicate` can
place the MCP chain in the wrong position or interact badly with duplicate
placeholders.

Run from repo root (miru-cursor):
  python docker/n8n/scripts/merge_w7_mcp_n8n_branch.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W7 = ROOT / "workflows" / "w7-telegram-callback-handler.json"
NODES_FRAG = ROOT / "workflows" / "fragments" / "w7_mcp_branch.nodes.json"
CONN_FRAG = ROOT / "workflows" / "fragments" / "w7_mcp_branch.connections.json"

# Splice new nodes after this node (MCP IF + nodes sit between w7007 and the rest).
ANCHOR_AFTER_NODE_ID = "w7007-found-branch"

_TELEGRAM_TRIGGER = "n8n-nodes-base.telegramTrigger"


def _validate_merged_data(data: dict) -> list[str]:
    """Return human-readable problems; empty = OK."""
    errors: list[str] = []
    nodes = data.get("nodes", [])

    ids = [n.get("id") for n in nodes if n.get("id")]
    for nid, cnt in Counter(ids).items():
        if cnt > 1:
            errors.append(f"duplicate node id {nid!r} x{cnt}")

    triggers = [n for n in nodes if n.get("type") == _TELEGRAM_TRIGGER]
    if len(triggers) != 1:
        errors.append(f"expected exactly 1 {_TELEGRAM_TRIGGER}, found {len(triggers)}")

    webhook_pairs: list[tuple[str, str]] = []
    for n in nodes:
        wid = n.get("webhookId")
        if not wid:
            continue
        label = n.get("name") or n.get("id") or "?"
        webhook_pairs.append((wid, label))
    for wid, cnt in Counter(p for p, _ in webhook_pairs).items():
        if cnt > 1:
            dupes = [lbl for w, lbl in webhook_pairs if w == wid]
            errors.append(
                f"duplicate webhookId {wid!r} on {cnt} nodes {dupes} — n8n activation: Conflicting Trigger Path"
            )
    return errors


def main() -> int:
    if not W7.is_file():
        print(f"merge: missing workflow file: {W7}", file=sys.stderr)
        return 1

    data = json.loads(W7.read_text(encoding="utf-8"))
    new_nodes = json.loads(NODES_FRAG.read_text(encoding="utf-8"))
    conn_updates = json.loads(CONN_FRAG.read_text(encoding="utf-8"))

    for n in new_nodes:
        t = n.get("type", "")
        if "Trigger" in t:
            print(
                f"merge: refuse — fragment must not include trigger nodes ({n.get('id')!r} {t})",
                file=sys.stderr,
            )
            return 1
        if t in ("n8n-nodes-base.webhook", "n8n-nodes-base.webhookWait"):
            print(
                f"merge: refuse — fragment must not include webhook trigger nodes ({n.get('id')!r})",
                file=sys.stderr,
            )
            return 1

    drop_ids = {n["id"] for n in new_nodes}
    nodes = [n for n in data["nodes"] if n["id"] not in drop_ids]
    try:
        idx = next(i for i, n in enumerate(nodes) if n["id"] == ANCHOR_AFTER_NODE_ID)
    except StopIteration as exc:  # pragma: no cover
        raise SystemExit(f"merge: anchor node {ANCHOR_AFTER_NODE_ID!r} not in {W7}") from exc

    # Insert after anchor so the MCP chain follows w7007-found-branch in the list.
    data["nodes"] = nodes[: idx + 1] + new_nodes + nodes[idx + 1 :]
    data["connections"] = {**data.get("connections", {}), **conn_updates}

    problems = _validate_merged_data(data)
    if problems:
        print("merge: post-merge validation failed:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    W7.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("merged (OK):", W7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
