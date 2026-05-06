# Local Governance Gatekeeper — grammar + bench skeleton

These two files prepare for cutover to the Local Governance Gatekeeper, an
Ollama-hosted Llama 3.1 8B Instruct (or equivalent) model that will validate
and route conversational dispatches from Claude Chat before they hit the
`dispatch_listener`. [`routing_schema.gbnf`](routing_schema.gbnf) is a
llama.cpp / Ollama-compatible GBNF grammar that constrains the model's
output to the closed-enum routing JSON the listener consumes — closed
enums on `decision.{worker, mode, tool_profile, confidence}` and
`rejection.reason`, type-correct bounded numerics on
`validation.self_serve_probability` and `execution.timeout_seconds`,
mutually exclusive `rejection: null | {object}`, and standard JSON-string
escapes everywhere a free-form rationale is expected; the future Gatekeeper
service will load this file at startup and pass its contents to Ollama via
the `options.grammar` field on each `POST /api/chat` (with a JSON-schema
`format` fallback for builds that don't honor `options.grammar`).
[`bench.py`](bench.py) is a stateless harness that scores candidate models
against `data/routing_history.jsonl` using the PXY-recommended cost-weighted
penalty matrix (operators tune via the `PENALTY_MATRIX` constant and the
`score_decision` function); per-row results land in
`data/batch_reports/bench_<model>_<ts>.jsonl` alongside p50/p95 latency,
validity rate, and exact-match rate. The bench is import-clean and
intentionally **not yet wired** to a real model — operators will plug in a
candidate via `python tools/gatekeeper/bench.py --model <tag> --sample N`
after dispatcher resurrection. Until then this directory is design-frozen
infrastructure: no edits to `routing_history.jsonl`, `w2_profile_rules.json`,
or any existing dispatch code.
