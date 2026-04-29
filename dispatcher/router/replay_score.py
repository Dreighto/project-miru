#!/usr/bin/env python3
"""
Replay scoring script for LLM Router T1+ evaluation (PRO-200).

Given a router callable, runs it against data/replay_corpus.jsonl and emits:
  - Agreement rate vs operator gold labels
  - Confusion matrix (router → operator)
  - Confidence-accuracy correlation (for entries where router returns confidence)

Usage:
    python dispatcher/router/replay_score.py [--corpus PATH]

To evaluate a custom router, import run_replay and pass a callable:

    from dispatcher.router.replay_score import run_replay

    def my_router(entry: dict) -> dict:
        # entry has: task_identifier, extracted_signals, w2_chosen_worker, etc.
        # return: {"worker": "claude-code", "confidence": 0.85}
        ...

    report = run_replay(my_router)

Built-in demo mode (no --router flag) runs the deterministic W2 keyword scorer
baseline so the script is self-contained and runnable without wiring an LLM.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from collections.abc import Callable

CORPUS_PATH = pathlib.Path("data/replay_corpus.jsonl")

# ---------------------------------------------------------------------------
# Baseline: deterministic W2 keyword-scorer (mirrors w2007 Code node logic)
# Used when no router is provided so the script runs stand-alone.
# ---------------------------------------------------------------------------
_UI_KEYWORDS = {"ui", "svelte", "html/css", "ui iteration"}
_ARCH_KEYWORDS = {
    "architecture",
    "architectural",
    "careful implementation",
    "multi-step",
}
_RESEARCH_SIGNAL = {"research"}


def _w2_deterministic(entry: dict) -> dict:
    """Simplified deterministic baseline — matches W2 keyword-scorer heuristics."""
    signals = entry.get("extracted_signals") or {}
    keywords = set(signals.get("surface_keywords") or [])
    research = signals.get("research_signal", False)

    if research:
        return {"worker": "triage", "confidence": 0.0}
    if keywords & _UI_KEYWORDS:
        return {"worker": "cursor", "confidence": 0.95}
    if keywords & _ARCH_KEYWORDS:
        return {"worker": "claude-code", "confidence": 0.95}
    # Baseline: no signal → triage
    return {"worker": "triage", "confidence": 0.0}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def load_corpus(path: pathlib.Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_replay(
    router: Callable[[dict], dict] | None = None,
    corpus_path: pathlib.Path = CORPUS_PATH,
) -> dict:
    """
    Run router against the replay corpus. Returns a report dict.

    router(entry) → {"worker": str, "confidence": float}
    If router is None, the deterministic W2 baseline is used.
    """
    if router is None:
        router = _w2_deterministic

    corpus = load_corpus(corpus_path)
    if not corpus:
        raise ValueError(f"Empty corpus at {corpus_path}")

    # --- per-entry scoring ---
    results = []
    for entry in corpus:
        gold = entry.get("operator_chosen_worker") or "unknown"
        prediction = router(entry)
        predicted_worker = prediction.get("worker") or "unknown"
        confidence = prediction.get("confidence")
        correct = predicted_worker == gold
        results.append(
            {
                "task_identifier": entry.get("task_identifier"),
                "gold": gold,
                "predicted": predicted_worker,
                "confidence": confidence,
                "correct": correct,
                "has_w2_data": entry.get("has_w2_data", False),
            }
        )

    # --- aggregate metrics ---
    n = len(results)
    n_correct = sum(1 for r in results if r["correct"])
    agreement_rate = n_correct / n if n else 0.0

    # Confusion matrix: {predicted: {gold: count}}
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        confusion[r["predicted"]][r["gold"]] += 1

    # Confidence-accuracy correlation (Pearson r, requires scipy; fall back to manual)
    conf_acc_corr = None
    conf_results = [r for r in results if r["confidence"] is not None]
    if len(conf_results) >= 2:
        xs = [float(r["confidence"]) for r in conf_results]
        ys = [1.0 if r["correct"] else 0.0 for r in conf_results]
        n_c = len(xs)
        mean_x = sum(xs) / n_c
        mean_y = sum(ys) / n_c
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False)) / n_c
        std_x = (sum((x - mean_x) ** 2 for x in xs) / n_c) ** 0.5
        std_y = (sum((y - mean_y) ** 2 for y in ys) / n_c) ** 0.5
        if std_x > 0 and std_y > 0:
            conf_acc_corr = cov / (std_x * std_y)

    # W2 vs operator agreement (only for paired rows)
    paired = [
        r
        for r in results
        if next((e for e in corpus if e.get("task_identifier") == r["task_identifier"]), {}).get(
            "has_w2_data"
        )
    ]
    w2_agreement = None
    if paired:
        w2_correct = sum(1 for r in paired if r["correct"])
        w2_agreement = w2_correct / len(paired)

    report = {
        "corpus_size": n,
        "paired_count": sum(1 for e in corpus if e.get("has_w2_data")),
        "agreement_rate": round(agreement_rate, 4),
        "correct_count": n_correct,
        "w2_paired_agreement": round(w2_agreement, 4) if w2_agreement is not None else None,
        "confidence_accuracy_correlation": (
            round(conf_acc_corr, 4) if conf_acc_corr is not None else None
        ),
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "results": results,
    }
    return report


def print_report(report: dict) -> None:
    print("\n=== Replay Score Report ===")
    print(f"Corpus size     : {report['corpus_size']} entries")
    print(f"W2-paired       : {report['paired_count']} (full triage+label pairs)")
    print(
        f"Agreement rate  : {report['agreement_rate']:.1%} ({report['correct_count']}/{report['corpus_size']})"
    )
    if report.get("w2_paired_agreement") is not None:
        print(
            f"W2 paired agr.  : {report['w2_paired_agreement']:.1%} (on {report['paired_count']} paired rows)"
        )
    if report.get("confidence_accuracy_correlation") is not None:
        print(f"Conf-acc corr   : {report['confidence_accuracy_correlation']:.4f}")
    else:
        print("Conf-acc corr   : N/A (need >=2 entries with confidence)")

    print("\nConfusion matrix (predicted -> gold):")
    cm = report["confusion_matrix"]
    all_workers = sorted(set(cm) | {g for v in cm.values() for g in v})
    header = f"{'predicted':>15} | " + " | ".join(f"{w:>12}" for w in all_workers)
    print(header)
    print("-" * len(header))
    for pred in sorted(cm.keys()):
        row = f"{pred:>15} | " + " | ".join(f"{cm[pred].get(g, 0):>12}" for g in all_workers)
        print(row)

    if report["paired_count"] < 30:
        print(
            f"\nVOLUME WARNING: {report['paired_count']} paired rows < 30 cutover gate floor. "
            "Agreement rates will be unreliable until corpus grows."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay scorer for LLM Router")
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=CORPUS_PATH,
        help=f"Path to corpus JSONL (default: {CORPUS_PATH})",
    )
    args = parser.parse_args()

    if not args.corpus.exists():
        print(f"ERROR: corpus not found at {args.corpus}", file=sys.stderr)
        print("Run: python dispatcher/router/build_corpus.py", file=sys.stderr)
        sys.exit(1)

    report = run_replay(corpus_path=args.corpus)
    print_report(report)


if __name__ == "__main__":
    main()
