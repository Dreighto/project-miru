"""Read-only: print group_set_mapping.json as sorted tables."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = ROOT / "data" / "tcgcsv" / "group_set_mapping.json"


def main() -> int:
    if not MAPPING_PATH.is_file():
        print(f"FAILED: missing {MAPPING_PATH}", file=sys.stderr)
        return 1

    rows = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("FAILED: expected JSON array", file=sys.stderr)
        return 1

    # Sort by proposed_set_code, then group_id for stable order
    def sort_key(r: dict) -> tuple:
        code = str(r.get("proposed_set_code") or "")
        return (code.lower(), int(r.get("group_id") or 0))

    sorted_rows = sorted(rows, key=sort_key)

    w_id = max(len("group_id"), max(len(str(r.get("group_id"))) for r in rows))
    w_code = max(
        len("proposed_set_code"),
        max(len(str(r.get("proposed_set_code") or "")) for r in rows),
    )
    w_conf = max(len("confidence"), max(len(str(r.get("confidence") or "")) for r in rows))

    sep = f"+-{'-' * w_id}-+-{'-' * w_code}-+-{'-' * w_conf}-+-{'-' * 60}-+"
    header = (
        f"| {'group_id':>{w_id}} | {'proposed_set_code':<{w_code}} | "
        f"{'confidence':<{w_conf}} | {'group_name':<60} |"
    )

    print(sep)
    print(header)
    print(sep)
    for r in sorted_rows:
        gid = r.get("group_id")
        code = str(r.get("proposed_set_code") or "")
        conf = str(r.get("confidence") or "")
        name = str(r.get("group_name") or "")
        if len(name) > 60:
            name = name[:57] + "..."
        print(f"| {gid!s:>{w_id}} | {code:<{w_code}} | {conf:<{w_conf}} | {name:<60} |")
    print(sep)
    print(f"Total entries: {len(rows)}")
    print()

    review = [r for r in sorted_rows if str(r.get("confidence") or "") in ("low", "unknown")]
    print("=" * 80)
    print("NEEDS OPERATOR REVIEW (confidence: low or unknown)")
    print("=" * 80)
    print(sep)
    print(header)
    print(sep)
    for r in review:
        gid = r.get("group_id")
        code = str(r.get("proposed_set_code") or "")
        conf = str(r.get("confidence") or "")
        name = str(r.get("group_name") or "")
        if len(name) > 60:
            name = name[:57] + "..."
        print(f"| {gid!s:>{w_id}} | {code:<{w_code}} | {conf:<{w_conf}} | {name:<60} |")
    print(sep)
    print(f"Review count: {len(review)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
