#!/usr/bin/env python3
"""
PRO-126: Make w7008-build-mutation + w7-picker-build-mutation return shapes
fully uniform.

Two-part fix (one PR, two commits):

  Part 1: success path always sets `_build_error: ''` (string, not undefined).
          Makes the downstream IF (string.notEmpty + typeValidation: strict)
          behave deterministically — fixes the "undefined" is not valid JSON
          failure observed in execution 3242 (2026-04-27).

  Part 2: error early-returns also set `mutation_body_obj: null`. Defense in
          depth: if any future change misroutes an error item to the HTTP
          node, we send `null` (Linear API rejects with a clear error)
          rather than the literal string "undefined" (n8n parser throws).

Final contract — both Code nodes return EXACTLY one of:
  - error:   { ...data, _build_error: '<msg>',  mutation_body_obj: null }
  - success: { ...data, _build_error: '',       mutation_body_obj: {...}, ... }

`_build_error` is always a string. `mutation_body_obj` is always an object
or null — never undefined. Downstream IF tests `_build_error notEmpty`;
strict-string typing now holds for every code path.

Idempotent: rerunnable; patches already applied are skipped.
"""

import json
import sys

WORKFLOW_PATH = "docker/n8n/workflows/w7-telegram-callback-handler.json"

# Each entry: (node_name, old_substring, new_substring).
# Match must be unique inside the named node's jsCode.
PATCHES = [
    # ── Part 1: success-path returns ─────────────────────────────────────────
    (
        "w7008-build-mutation",
        "return { json: { ...data, action_label: actionLabel, decided_at, next_label_ids: nextIds, mutation_body_obj } };",
        "return { json: { ...data, action_label: actionLabel, decided_at, next_label_ids: nextIds, mutation_body_obj, _build_error: '' } };",
    ),
    (
        "w7-picker-build-mutation",
        "return { json: { ...data, picker_label_name: labelName, picker_display: display, override_flag_picker: overrideFlag, decided_at, next_label_ids: nextIds, mutation_body_obj } };",
        "return { json: { ...data, picker_label_name: labelName, picker_display: display, override_flag_picker: overrideFlag, decided_at, next_label_ids: nextIds, mutation_body_obj, _build_error: '' } };",
    ),
    # ── Part 2: error early-returns (defense in depth) ───────────────────────
    # w7008-build-mutation: 3 error paths
    (
        "w7008-build-mutation",
        "return { json: { ...data, _build_error: 'pending-approval label id missing from labels_map' } };",
        "return { json: { ...data, _build_error: 'pending-approval label id missing from labels_map', mutation_body_obj: null } };",
    ),
    (
        "w7008-build-mutation",
        "return { json: { ...data, _build_error: 'worker label id for \"' + chosenWorker + '\" missing from labels_map' } };",
        "return { json: { ...data, _build_error: 'worker label id for \"' + chosenWorker + '\" missing from labels_map', mutation_body_obj: null } };",
    ),
    (
        "w7008-build-mutation",
        "return { json: { ...data, _build_error: 'unknown action code: ' + data.action } };",
        "return { json: { ...data, _build_error: 'unknown action code: ' + data.action, mutation_body_obj: null } };",
    ),
    # w7-picker-build-mutation: 3 error paths
    (
        "w7-picker-build-mutation",
        "return { json: { ...data, _build_error: 'unknown picker action: ' + data.action } };",
        "return { json: { ...data, _build_error: 'unknown picker action: ' + data.action, mutation_body_obj: null } };",
    ),
    (
        "w7-picker-build-mutation",
        "return { json: { ...data, _build_error: 'picker label not in labels_map: ' + labelName + ' (ensure label exists in Linear so w2006 picks it up)' } };",
        "return { json: { ...data, _build_error: 'picker label not in labels_map: ' + labelName + ' (ensure label exists in Linear so w2006 picks it up)', mutation_body_obj: null } };",
    ),
    (
        "w7-picker-build-mutation",
        "return { json: { ...data, _build_error: 'pending-approval label id missing from labels_map' } };",
        "return { json: { ...data, _build_error: 'pending-approval label id missing from labels_map', mutation_body_obj: null } };",
    ),
]


def patch(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        workflow = json.load(f)

    nodes_by_name = {n["name"]: n for n in workflow["nodes"]}

    for node_name, old, new in PATCHES:
        node = nodes_by_name.get(node_name)
        if node is None:
            sys.exit(f"node not found: {node_name}")
        js = node["parameters"].get("jsCode")
        if js is None:
            sys.exit(f"node has no jsCode: {node_name}")
        if new in js:
            print(f"already applied: {node_name}: {old[:60]}...")
            continue
        if old not in js:
            sys.exit(f"old substring not found in {node_name}: {old[:60]}...")
        if js.count(old) > 1:
            sys.exit(f"old substring matches multiple times in {node_name}")
        node["parameters"]["jsCode"] = js.replace(old, new, 1)
        print(f"patched {node_name}: {old[:60]}...")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {path}")


if __name__ == "__main__":
    patch(WORKFLOW_PATH)
