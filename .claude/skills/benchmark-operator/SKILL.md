---
name: benchmark-operator
description: Use this skill whenever the user asks Claude Code to benchmark, bench, compare, score, evaluate, smoke test, or choose between models for a Miru lane. Triggers include benchmark, benching, smoke test, eval, evaluator, model comparison, A/B model test, qwen, mistral, ollama, format=json_schema, schema-valid JSON, latency, p50, p95, validity, enums, rejection vocab, DEFAULT_MODEL, production candidate, promotion, demotion, sustainability, drift, corpus rerun, or any request to justify a model choice with repeatable evidence. Do NOT use for generic code benchmarks unrelated to model selection.
---

# benchmark-operator

This skill is a thin wrapper. The canonical content lives at:

**`docs/ch_operations/BENCHMARK_OPERATOR_SKILL.md`** — read this file first.

It covers:

1. Benchmark entry gates and when a casual smoke test is not enough
2. Fixed protocol requirements: corpus, repetitions, metrics, and pass/fail rules
3. How to separate smoke validity from production promotion
4. How to report results without over-claiming
5. Sustainability follow-through after a winner is chosen

## Companion doc

**`docs/ch_operations/CC_SKILLS_CHEATSHEET.md`** — use this for the short operator-facing dispatch format and paste-ready examples.

## How to use this skill

1. Read `docs/ch_operations/BENCHMARK_OPERATOR_SKILL.md`.
2. Apply the benchmark workflow exactly unless the operator explicitly changes the protocol.
3. Treat missing metrics, inconsistent runs, or shifting criteria as a stop-and-report condition.
4. If benchmark results change worker roster, model defaults, or production lane policy, call out the canon impact explicitly.

## When to NOT use this skill

- The user just wants a quick opinion about a model without measurement.
- The task is a code performance benchmark rather than an LLM/model selection benchmark.
- The task is a refactor decision. Use `refactor-triage` instead.
