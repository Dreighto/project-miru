#!/usr/bin/env python3
"""
Replay corpus extractor for LLM Router T1+ evaluation (PRO-200).

Reads data/routing_history.jsonl, pairs each w2_manual_label_emit row with its
preceding triage row via shared trace_id, and writes data/replay_corpus.jsonl.

Usage:
    python dispatcher/router/build_corpus.py
"""

import hashlib
import json
import pathlib
import sys

ROUTING_HISTORY = pathlib.Path("data/routing_history.jsonl")
CORPUS_OUT = pathlib.Path("data/replay_corpus.jsonl")


def _synopsis_hash(task_identifier: str) -> str:
    return hashlib.sha256(task_identifier.encode()).hexdigest()[:16]


def build_corpus() -> list[dict]:
    rows = []
    with open(ROUTING_HISTORY) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # Index non-manual rows by trace_id (first/triage row per trace)
    triage_index: dict[str, dict] = {}
    for row in rows:
        if row.get("source") == "w2_manual_label_emit":
            continue
        tid = row.get("trace_id")
        if tid and tid not in triage_index:
            triage_index[tid] = row

    manual_rows = [r for r in rows if r.get("source") == "w2_manual_label_emit"]

    corpus = []
    for ml in manual_rows:
        tid = ml.get("trace_id")
        triage = triage_index.get(tid)

        # W2's proposed worker — from triage row when available
        w2_chosen = triage.get("chosen_worker") if triage else None
        w2_confidence = triage.get("confidence") if triage else None
        w2_risk = triage.get("risk") if triage else None
        extracted_signals = triage.get("extracted_signals") if triage else None

        # Top ranked candidate score for chosen worker
        w2_score = None
        if triage and triage.get("ranked_candidates") and w2_chosen:
            for c in triage["ranked_candidates"]:
                if c.get("worker") == w2_chosen:
                    w2_score = c.get("score")
                    break

        task_id = ml.get("task_identifier", "")
        corpus.append(
            {
                "task_identifier": task_id,
                "task_id": ml.get("task_id"),
                "trace_id": tid,
                "synopsis_hash": _synopsis_hash(task_id) if task_id else None,
                # W2 router output (null when triaged_first=false)
                "w2_chosen_worker": w2_chosen,
                "w2_confidence": w2_confidence,
                "w2_score": w2_score,
                "w2_risk": w2_risk,
                # Gold label: operator's actual choice
                "operator_chosen_worker": ml.get("chosen_worker"),
                "triaged_first": ml.get("triaged_first", False),
                "has_w2_data": triage is not None,
                "extracted_signals": extracted_signals,
                "w2_workflow_version": ml.get("w2_workflow_version"),
                "timestamp": ml.get("timestamp"),
            }
        )

    return corpus


def main() -> None:
    corpus = build_corpus()

    paired = sum(1 for r in corpus if r["has_w2_data"])
    print(f"Corpus: {len(corpus)} entries total, {paired} with full W2 triage data")
    print(f"  Gold-only (no triage row): {len(corpus) - paired}")

    workers: dict[str, int] = {}
    for row in corpus:
        w = row["operator_chosen_worker"] or "unknown"
        workers[w] = workers.get(w, 0) + 1
    print("Operator label distribution:")
    for w, count in sorted(workers.items(), key=lambda x: -x[1]):
        print(f"  {w}: {count}")

    if len(corpus) == 0:
        print("ERROR: empty corpus", file=sys.stderr)
        sys.exit(1)

    CORPUS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CORPUS_OUT, "w") as fh:
        for row in corpus:
            fh.write(json.dumps(row) + "\n")

    print(f"Written: {CORPUS_OUT} ({len(corpus)} rows)")

    if paired < 30:
        print(
            f"VOLUME WARNING: only {paired} fully-paired rows (need >=30 for cutover gate). "
            "Corpus grows as W2 triage + manual-label decisions accumulate.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
