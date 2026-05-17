"""Bulk-trash bot-filed Duplicate-state tickets via Linear GraphQL.

Reads data/linear_bot_dupes_to_purge.json (operator-authorized list),
calls issueDelete on each. Soft-delete (trash, 30-day recovery window).
Writes a JSONL log per attempt so failures can be retried.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
IDS_PATH = ROOT / "data" / "linear_bot_dupes_to_purge.json"
LOG_PATH = ROOT / "data" / "linear_bot_dupes_purge.log.jsonl"
ENV_PATH = Path(r"D:\dev\LogueOS-Orchestrator\.env")

LINEAR_GRAPHQL = "https://api.linear.app/graphql"

DELETE_MUTATION = """
mutation IssueDelete($id: String!) {
  issueDelete(id: $id) {
    success
  }
}
"""


def load_api_key() -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINEAR_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("LINEAR_API_KEY not found in canonical .env")


def trash_one(api_key: str, identifier: str) -> tuple[bool, str]:
    r = requests.post(
        LINEAR_GRAPHQL,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={"query": DELETE_MUTATION, "variables": {"id": identifier}},
        timeout=30,
    )
    try:
        body = r.json()
    except Exception:
        return False, f"http {r.status_code} non-json"
    if r.status_code != 200:
        return False, f"http {r.status_code} body={body}"
    if "errors" in body:
        return False, f"graphql errors: {body['errors']}"
    ok = body.get("data", {}).get("issueDelete", {}).get("success", False)
    return bool(ok), "ok" if ok else f"success=false body={body}"


def already_done() -> set[str]:
    done: set[str] = set()
    if LOG_PATH.exists():
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("ok"):
                done.add(rec["id"])
    return done


def main() -> int:
    api_key = load_api_key()
    payload = json.loads(IDS_PATH.read_text(encoding="utf-8"))
    all_ids = payload["ids"]
    done = already_done()
    ids = [i for i in all_ids if i not in done]
    print(f"loaded {len(all_ids)} ids, already_done={len(done)}, remaining={len(ids)}", flush=True)

    successes = 0
    failures: list[tuple[str, str]] = []

    with LOG_PATH.open("a", encoding="utf-8") as logf:
        for idx, pid in enumerate(ids, start=1):
            ok, msg = trash_one(api_key, pid)
            logf.write(json.dumps({"id": pid, "ok": ok, "msg": msg, "ts": time.time()}) + "\n")
            logf.flush()
            if ok:
                successes += 1
            else:
                failures.append((pid, msg))
            if idx % 25 == 0:
                print(
                    f"  progress {idx}/{len(ids)}  ok={successes}  fail={len(failures)}", flush=True
                )
            # Linear API limit is ~1500/min for authenticated; 0.3s spacing = 200/min, safe
            time.sleep(0.3)

    print(f"DONE  ok={successes}  fail={len(failures)}  log={LOG_PATH}")
    if failures:
        print("First 10 failures:")
        for pid, msg in failures[:10]:
            print(f"  {pid}: {msg[:120]}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
