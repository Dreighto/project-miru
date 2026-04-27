#!/usr/bin/env python3
"""Validate n8n workflow JSON files for connection integrity and required keys.

Mirrors the deploy-workflow.ps1 connection-integrity check from PRO-27 but runs
at commit time instead of deploy time. Catches the W1 rename bug class upstream.

Usage: validate_n8n_workflow.py <file>...
Exit 0 if valid, 1 if any file fails validation.
"""

import json
import sys
from collections import Counter
from pathlib import Path


def validate_workflow(path: Path) -> list[str]:
    """Return list of validation errors. Empty list = valid."""
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"{path}: invalid JSON: {e}"]
    except OSError as e:
        return [f"{path}: read error: {e}"]

    # Required top-level keys
    for key in ("name", "nodes", "connections"):
        if key not in data:
            errors.append(f"{path}: missing required key '{key}'")
    if errors:
        return errors

    # Build set of node names for connection integrity check
    node_names = {node.get("name") for node in data.get("nodes", []) if node.get("name")}

    # Duplicate node ids (duplicate objects in `nodes` break activation: Conflicting Trigger Path, etc.)
    node_ids = [n.get("id") for n in data.get("nodes", []) if n.get("id")]
    for nid, cnt in Counter(node_ids).items():
        if cnt > 1:
            errors.append(f"{path}: duplicate node id {nid!r} appears {cnt} times")

    # Same webhookId twice in one workflow → n8n blocks activation (Conflicting Trigger Path / URL path taken)
    w_rows: list[tuple[str, str]] = []
    for node in data.get("nodes", []):
        wid = node.get("webhookId")
        if not wid:
            continue
        w_rows.append((str(wid), str(node.get("name") or node.get("id") or "?")))
    for wid, cnt in Counter(w for w, _ in w_rows).items():
        if cnt > 1:
            names = [lbl for w, lbl in w_rows if w == wid]
            errors.append(
                f"{path}: duplicate webhookId {wid!r} on {cnt} nodes {names} "
                "(n8n will not activate: conflicting trigger / webhook path)"
            )

    # At most one Telegram trigger per workflow (W7 contract; also catches bad merges)
    tg = [n for n in data.get("nodes", []) if n.get("type") == "n8n-nodes-base.telegramTrigger"]
    if len(tg) > 1:
        tnames = [n.get("name") for n in tg]
        errors.append(
            f"{path}: {len(tg)} telegram trigger nodes {tnames} — use a single trigger; "
            "import/merge conflicts cause Conflicting Trigger Path on activation"
        )

    # Connection integrity: every connection source + target must reference a real node
    connections = data.get("connections", {})
    if not isinstance(connections, dict):
        errors.append(f"{path}: 'connections' must be an object")
        return errors

    for source_name, conn_data in connections.items():
        if source_name not in node_names:
            errors.append(f"{path}: connection source '{source_name}' is not a node")
        if not isinstance(conn_data, dict):
            continue
        for output_array in conn_data.get("main", []):
            if not isinstance(output_array, list):
                continue
            for edge in output_array:
                target = edge.get("node") if isinstance(edge, dict) else None
                if target and target not in node_names:
                    errors.append(f"{path}: connection target '{target}' is not a node")

    # Settings sanity (if present, must be an object)
    settings = data.get("settings")
    if settings is not None and not isinstance(settings, dict):
        errors.append(f"{path}: 'settings' must be an object if present")

    return errors


def main() -> int:
    files = [Path(p) for p in sys.argv[1:]]
    if not files:
        return 0

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(validate_workflow(f))

    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
