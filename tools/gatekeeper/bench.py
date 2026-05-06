#!/usr/bin/env python3
"""
bench.py — Local Governance Gatekeeper bench harness skeleton.

Score candidate Ollama-hosted models against historical routing decisions
to pick a Gatekeeper before dispatcher resurrection. This file is a
*skeleton*: it does not run the bench at import time, and the prompt
template is intentionally a stub the operator will replace once the real
Gatekeeper system prompt is finalized.

Functions
---------
- load_history(path):
      Read `data/routing_history.jsonl` and return raw rows. The file is
      heterogeneous (W2 routing rows, callback-decided rows, dedupe rows,
      etc.); callers filter to whichever subset they want as ground truth.

- call_model(model_name, prompt, grammar_path):
      POST to local Ollama at http://127.0.0.1:11434/api/chat with the
      GBNF grammar attached via `options.grammar`. Returns
      (parsed_json | None, latency_ms).

- score_decision(predicted, ground_truth):
      Per-field comparison on `decision.{worker, mode, tool_profile,
      confidence}` using the PXY-recommended penalty matrix. Returns
      {json_valid, exact_match, cost_weighted_score, mismatches}.

- run_bench(model_name, sample_size, log_file):
      Orchestrate the above. Sample N rows, call the model, score against
      the row, append per-row JSONL to log_file, print summary stats.

CLI
---
    python tools/gatekeeper/bench.py --model llama3.1:8b-instruct --sample 50

Run this from the repo root (or pass --history / --grammar explicitly).
DO NOT run against an actual model until dispatcher resurrection.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys
import time

try:
    import requests
except ImportError:  # pragma: no cover - requests is already vendored across the repo
    requests = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HISTORY_PATH = REPO_ROOT / "data" / "routing_history.jsonl"
REPORT_DIR = REPO_ROOT / "data" / "batch_reports"
GRAMMAR_PATH = pathlib.Path(__file__).resolve().parent / "routing_schema.gbnf"

DEFAULT_TIMEOUT_S = 120

# Fields scored for exact_match and cost-weighting.
SCORED_FIELDS = ("worker", "mode", "tool_profile", "confidence")

# Mode-mismatch penalties are symmetric pairs unless otherwise noted.
# `any -> blocked` and `blocked -> any` both 1.0 (treated as direction-aware
# rules; see _mode_penalty).
_MODE_PAIR_PENALTIES = {
    frozenset({"judgment", "ambiguous"}): 0.2,
    frozenset({"routine", "judgment"}): 0.5,
}
_MODE_BLOCKED_PENALTY = 1.0
_MODE_OTHER_PENALTY = 0.6

_WORKER_MISMATCH_PENALTY = 0.7
_TOOL_PROFILE_MISMATCH_PENALTY = 0.4

# Public-ish dict for operators tuning the bench. Keyed by field name.
PENALTY_MATRIX: dict[str, dict[str, float | str]] = {
    "worker": {"mismatch": _WORKER_MISMATCH_PENALTY},
    "mode": {
        "judgment<->ambiguous": 0.2,
        "routine<->judgment": 0.5,
        "any->blocked": _MODE_BLOCKED_PENALTY,
        "blocked->any": _MODE_BLOCKED_PENALTY,
        "other": _MODE_OTHER_PENALTY,
    },
    "tool_profile": {"mismatch": _TOOL_PROFILE_MISMATCH_PENALTY},
    "confidence": {"mismatch": _MODE_OTHER_PENALTY},  # treat like generic mode-class swap
}


# ---------------------------------------------------------------------------
# load_history
# ---------------------------------------------------------------------------


def load_history(path: pathlib.Path | str = HISTORY_PATH) -> list[dict]:
    """
    Load routing history JSONL. Returns a list of raw row dicts.

    The file is heterogeneous: each row may be a W2 keyword-router decision,
    a callback-decided row, a dedupe row, etc. Callers filter as needed
    (for example: keep rows where chosen_worker is not None and outcome in
    {"dispatched", "shadow-dispatched", "callback-decided", "picker-decided"}).
    """
    p = pathlib.Path(path)
    rows: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------------------
# call_model
# ---------------------------------------------------------------------------


def call_model(
    model_name: str,
    prompt: str,
    grammar_path: pathlib.Path | str = GRAMMAR_PATH,
    *,
    url: str = OLLAMA_URL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[dict | None, float]:
    """
    Call local Ollama with the routing JSON schema enforced via ``format``.

    Returns (parsed_json | None, latency_ms). On any HTTP / JSON parse error
    the first element is None and latency_ms still reflects the wall clock
    cost of the attempt.

    Note on grammar transport
    -------------------------
    The GBNF at ``grammar_path`` is the canonical schema spec. CC verified
    on 2026-05-06 that this Ollama build silently ignores ``options.grammar``
    across model families (qwen2.5:7b, llama3.2:3b, mistral:7b-instruct).
    The ``format`` field with a JSON schema works cleanly. We import the
    schema mirror from ``gatekeeper.core`` so there is exactly one
    schema spec, derived from the GBNF.

    The ``grammar_path`` arg is preserved for forward compatibility with
    Ollama builds that DO honor ``options.grammar`` — pass the path and the
    function will read the GBNF and include it alongside ``format`` so
    grammar-aware backends can use either. Current builds use the schema.
    """
    if requests is None:
        raise RuntimeError(
            "requests is not importable; install it or use a Python with the package vendored "
            "(this repo already uses requests in tools/miru_mcp_gateway)."
        )

    try:
        from gatekeeper.core import ROUTING_JSON_SCHEMA
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from gatekeeper.core import ROUTING_JSON_SCHEMA

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": ROUTING_JSON_SCHEMA,
        "options": {"temperature": 0.0},
    }

    if grammar_path is not None:
        try:
            grammar_text = pathlib.Path(grammar_path).read_text(encoding="utf-8")
            payload["options"]["grammar"] = grammar_text
        except OSError:
            pass

    t0 = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, timeout=timeout_s)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return None, latency_ms

    content = (body.get("message") or {}).get("content")
    if not isinstance(content, str):
        return None, latency_ms

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None, latency_ms

    if not isinstance(parsed, dict):
        return None, latency_ms

    return parsed, latency_ms


# ---------------------------------------------------------------------------
# score_decision
# ---------------------------------------------------------------------------


def _get_decision_field(obj: dict | None, field: str):
    """Pull `decision.<field>` out of either a Gatekeeper-shape dict or a
    flat ground-truth dict. Returns None when the field is missing."""
    if not isinstance(obj, dict):
        return None
    decision = obj.get("decision")
    if isinstance(decision, dict) and field in decision:
        return decision.get(field)
    if field in obj:
        return obj.get(field)
    return None


def _mode_penalty(predicted: str | None, expected: str | None) -> float:
    if predicted == expected:
        return 0.0
    if predicted is None or expected is None:
        return 0.0
    if predicted == "blocked" or expected == "blocked":
        return _MODE_BLOCKED_PENALTY
    pair = frozenset({predicted, expected})
    if pair in _MODE_PAIR_PENALTIES:
        return _MODE_PAIR_PENALTIES[pair]
    return _MODE_OTHER_PENALTY


def _penalty(field: str, predicted, expected) -> float:
    if predicted == expected:
        return 0.0
    if predicted is None or expected is None:
        return 0.0  # missing on either side -> skip (documented behavior)
    if field == "mode":
        return _mode_penalty(predicted, expected)
    if field == "worker":
        return _WORKER_MISMATCH_PENALTY
    if field == "tool_profile":
        return _TOOL_PROFILE_MISMATCH_PENALTY
    if field == "confidence":
        return _MODE_OTHER_PENALTY
    return _MODE_OTHER_PENALTY


def score_decision(predicted: dict | None, ground_truth: dict | None) -> dict:
    """
    Compare a model-emitted Gatekeeper JSON against a ground-truth decision.

    Both `predicted` and `ground_truth` may be either the full Gatekeeper
    schema (with a nested `decision` object) or a flat dict carrying just
    the four scored fields. Missing fields on either side are *skipped*:
    they do not count toward exact_match and do not accrue penalty.

    Returns
    -------
    dict
        json_valid:          predicted is a dict
        exact_match:         all comparable scored fields equal AND at least
                             one comparable field present
        cost_weighted_score: sum of per-field penalties from PENALTY_MATRIX
        mismatches:          list of (field, predicted, expected) tuples
    """
    json_valid = isinstance(predicted, dict)

    mismatches: list[tuple[str, object, object]] = []
    cost = 0.0
    comparable = 0
    all_equal = True

    for field in SCORED_FIELDS:
        p = _get_decision_field(predicted, field)
        e = _get_decision_field(ground_truth, field)
        if p is None or e is None:
            continue
        comparable += 1
        if p != e:
            all_equal = False
            mismatches.append((field, p, e))
            cost += _penalty(field, p, e)

    exact_match = bool(json_valid and comparable > 0 and all_equal)

    return {
        "json_valid": json_valid,
        "exact_match": exact_match,
        "cost_weighted_score": round(cost, 4),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# run_bench
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float | None:
    """Pure-Python percentile (linear interpolation between sorted samples)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _build_prompt(entry: dict) -> str:
    """Build a Gatekeeper-style prompt for a historical routing entry.

    Imports ``GOVERNANCE_PREAMBLE`` from ``gatekeeper.core`` so the
    bench exercises the same governance prefix the production Gatekeeper
    uses. The historical row is presented as the dynamic-tail context
    (ticket id, task type, suggested worker, outcome). Closed-enum
    JSON schema is enforced via ``format`` in ``call_model`` — this
    prompt just provides the context and asks for the decision JSON.
    """
    try:
        from gatekeeper.core import GOVERNANCE_PREAMBLE
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT))
        from gatekeeper.core import GOVERNANCE_PREAMBLE

    ticket_id = entry.get("task_identifier") or entry.get("ticket_id") or "PRO-?"
    task_type = entry.get("task_type") or entry.get("type") or "(unknown)"
    suggested = entry.get("suggested_worker") or entry.get("proposed_worker") or "(none)"
    chosen = entry.get("chosen_worker") or entry.get("selected_worker") or "(none)"
    outcome = entry.get("outcome") or "(unknown)"

    context = (
        f"TICKET: {ticket_id}\n"
        f"TASK TYPE: {task_type}\n"
        f"SUGGESTED WORKER (W2): {suggested}\n"
        f"OUTCOME (historical): {outcome}\n"
        f"GROUND TRUTH (historical chosen worker): {chosen}\n\n"
        "FRONTMATTER: (no frontmatter on historical ticket)\n"
        "GIT STATUS: (clean tree)\n"
        "CONVERSATIONAL DELTA: (no delta — historical entry)\n"
        "DETERMINISTIC CHECKS: all passed\n\n"
        'Emit the routing decision JSON. schema_version "2". trace_id '
        f'must use format "rtr-{ticket_id}-<rand>". '
        "Be conservative on edge cases. ``rejection`` is null when the "
        "dispatch is legitimate, or an object with a ``reason`` enum when not."
    )
    return GOVERNANCE_PREAMBLE + "\n\n---\n\n" + context


def _default_log_file(model_name: str) -> pathlib.Path:
    ts = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_model = model_name.replace("/", "_").replace(":", "_")
    return REPORT_DIR / f"bench_{safe_model}_{ts}.jsonl"


def run_bench(
    model_name: str,
    sample_size: int,
    log_file: pathlib.Path | str | None = None,
    *,
    grammar_path: pathlib.Path | str = GRAMMAR_PATH,
    history_path: pathlib.Path | str = HISTORY_PATH,
    seed: int = 0,
) -> dict:
    """
    Orchestrate: sample history, call model, score, log per-row results,
    return + print summary stats.

    Per-row JSONL written to `log_file` (default
    `data/batch_reports/bench_<model>_<utc-ts>.jsonl`). Summary printed to
    stdout. The function returns the summary dict for programmatic use.
    """
    log_path = pathlib.Path(log_file) if log_file else _default_log_file(model_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    rows = load_history(history_path)
    if not rows:
        raise ValueError(f"Empty history at {history_path}")

    # Filter to rows with a non-null chosen_worker — those are the ones
    # we can actually score against. Shadow-dispatched, callback-decided,
    # and dispatched outcomes all qualify.
    rows = [r for r in rows if r.get("chosen_worker")]
    if not rows:
        raise ValueError(f"No rows with chosen_worker in {history_path}")

    rng = random.Random(seed)
    sample = rng.sample(rows, k=min(sample_size, len(rows)))

    n_valid = 0
    n_exact = 0
    cost_total = 0.0
    latencies: list[float] = []

    with log_path.open("w", encoding="utf-8") as fh:
        for entry in sample:
            prompt = _build_prompt(entry)
            predicted, latency_ms = call_model(model_name, prompt, grammar_path)
            scored = score_decision(predicted, entry)

            if scored["json_valid"]:
                n_valid += 1
            if scored["exact_match"]:
                n_exact += 1
            cost_total += float(scored["cost_weighted_score"])
            latencies.append(latency_ms)

            record = {
                "task_identifier": entry.get("task_identifier") or entry.get("task_id"),
                "trace_id": entry.get("trace_id"),
                "model": model_name,
                "latency_ms": round(latency_ms, 2),
                "json_valid": scored["json_valid"],
                "exact_match": scored["exact_match"],
                "cost_weighted_score": scored["cost_weighted_score"],
                "mismatches": scored["mismatches"],
                "predicted": predicted,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    n = len(sample)
    summary = {
        "model": model_name,
        "sample_size": n,
        "log_file": str(log_path),
        "validity_rate": round(n_valid / n, 4) if n else 0.0,
        "exact_match_rate": round(n_exact / n, 4) if n else 0.0,
        "mean_cost_weighted_score": round(cost_total / n, 4) if n else 0.0,
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
    }
    _print_summary(summary)
    return summary


def _print_summary(summary: dict) -> None:
    print("\n=== Gatekeeper Bench Summary ===")
    print(f"Model            : {summary['model']}")
    print(f"Sample size      : {summary['sample_size']}")
    print(f"Validity rate    : {summary['validity_rate']:.1%}")
    print(f"Exact-match rate : {summary['exact_match_rate']:.1%}")
    print(f"Mean cost score  : {summary['mean_cost_weighted_score']:.4f}")
    p50 = summary["p50_latency_ms"]
    p95 = summary["p95_latency_ms"]
    print(f"p50 latency      : {p50:.1f} ms" if p50 is not None else "p50 latency      : n/a")
    print(f"p95 latency      : {p95:.1f} ms" if p95 is not None else "p95 latency      : n/a")
    print(f"Log file         : {summary['log_file']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bench a candidate Ollama-hosted model as the Local Governance Gatekeeper."
    )
    parser.add_argument(
        "--model", required=True, help="Ollama model tag, e.g. llama3.1:8b-instruct"
    )
    parser.add_argument(
        "--sample", type=int, default=50, help="Number of history rows to sample (default: 50)"
    )
    parser.add_argument(
        "--log-file",
        type=pathlib.Path,
        default=None,
        help=f"Output JSONL path (default: {REPORT_DIR}/bench_<model>_<ts>.jsonl)",
    )
    parser.add_argument(
        "--grammar",
        type=pathlib.Path,
        default=GRAMMAR_PATH,
        help=f"Path to GBNF grammar (default: {GRAMMAR_PATH})",
    )
    parser.add_argument(
        "--history",
        type=pathlib.Path,
        default=HISTORY_PATH,
        help=f"Path to routing history JSONL (default: {HISTORY_PATH})",
    )
    parser.add_argument("--seed", type=int, default=0, help="RNG seed for sampling (default: 0)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if not args.history.exists():
        print(f"ERROR: history not found at {args.history}", file=sys.stderr)
        return 1
    if not args.grammar.exists():
        print(f"ERROR: grammar not found at {args.grammar}", file=sys.stderr)
        return 1

    run_bench(
        model_name=args.model,
        sample_size=args.sample,
        log_file=args.log_file,
        grammar_path=args.grammar,
        history_path=args.history,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
