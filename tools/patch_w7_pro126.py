#!/usr/bin/env python3
"""
PRO-126: Fix w7008-build-mutation + w7-picker-build-mutation return-shape
inconsistency that lets undefined mutation_body_obj reach the HTTP node.

Root cause: both Code nodes have heterogeneous return shapes:
  - error early-returns: `{ ...data, _build_error: 'msg' }`  (no mutation_body_obj)
  - success return:      `{ ...data, ..., mutation_body_obj }` (no _build_error)

Downstream IF (w7008-error-branch / w7-picker-error-branch) tests
`_build_error` with string.notEmpty + typeValidation: strict. On the success
path, `_build_error` is undefined, and strict-string validation on undefined
is non-deterministic across n8n versions — under some conditions the IF
routes wrong and an item with no mutation_body_obj reaches the HTTP node,
producing the literal string "undefined" as the JSON body and the n8n
parser throws `"undefined" is not valid JSON`.

Fix: make `_build_error` always be a string. Empty string on success,
non-empty string on error. The IF's strict-string notEmpty operator now
behaves deterministically because the value is always a string.

Concretely, this patches the SUCCESS return of each Code node to include
`_build_error: ''`. The error early-returns already have `_build_error`
set to a non-empty string and don't need changes (mutation_body_obj
remaining absent on those paths is fine — the IF will correctly route
them to the error path).
"""

import json
import sys

WORKFLOW_PATH = "docker/n8n/workflows/w7-telegram-callback-handler.json"

# Each entry: (node_name, old_substring_in_jsCode, new_substring)
# Match is exact-substring, must be unique within the jsCode.
PATCHES = [
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
        if old not in js:
            sys.exit(f"old substring not found in {node_name} jsCode")
        if js.count(old) > 1:
            sys.exit(f"old substring matches multiple times in {node_name} jsCode")
        node["parameters"]["jsCode"] = js.replace(old, new, 1)
        print(f"patched {node_name}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {path}")


if __name__ == "__main__":
    patch(WORKFLOW_PATH)
