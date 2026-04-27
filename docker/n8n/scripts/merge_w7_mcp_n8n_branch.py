"""One-off merge helper: splice MCP n8n-write approval branch into W7 JSON.

Run from repo root after editing fragments:
  python docker/n8n/scripts/merge_w7_mcp_n8n_branch.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W7 = ROOT / "workflows" / "w7-telegram-callback-handler.json"
NODES_FRAG = ROOT / "workflows" / "fragments" / "w7_mcp_branch.nodes.json"
CONN_FRAG = ROOT / "workflows" / "fragments" / "w7_mcp_branch.connections.json"


def main() -> None:
    data = json.loads(W7.read_text(encoding="utf-8"))
    new_nodes = json.loads(NODES_FRAG.read_text(encoding="utf-8"))
    conn_updates = json.loads(CONN_FRAG.read_text(encoding="utf-8"))

    nodes = data["nodes"]
    # Idempotent: drop any prior MCP branch nodes by id
    drop_ids = {n["id"] for n in new_nodes}
    nodes = [n for n in nodes if n["id"] not in drop_ids]
    idx = next(i for i, n in enumerate(nodes) if n["id"] == "w7-noop-duplicate")
    data["nodes"] = nodes[:idx] + new_nodes + nodes[idx:]

    data["connections"].update(conn_updates)
    W7.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("merged:", W7)


if __name__ == "__main__":
    main()
