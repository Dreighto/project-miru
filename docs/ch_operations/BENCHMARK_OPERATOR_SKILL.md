# Benchmark Operator Skill — Claude Code

Use this when Claude Code is asked to benchmark or compare models for Miru production lanes.

## Purpose

This skill exists to stop model-selection work from turning into vibe-based comparisons. The output should be a repeatable decision with clear metrics, clear failure modes, and a recommendation the operator can trust.

## Entry Gate

Use this skill when the question is any variation of:

- Which model should be the default?
- Is model A good enough to replace model B?
- Did this model earn a production lane?
- Did performance drift enough to demote a model?

Do not use it for casual exploration or one-off curiosity checks.

## Required Protocol

Before interpreting results, make sure the benchmark defines:

1. The lane being tested
2. The task contract
3. The fixed corpus or prompt set
4. The output schema or acceptance rules
5. The metrics that matter
6. The promotion or demotion threshold

If any of the above is missing, stop and report the gap before running more tests.

## Minimum Metrics

Capture these at minimum:

- validity rate
- latency distribution, at least `p50`
- failure shape, not just pass/fail
- behavior on required fields, enums, and rejection vocabulary
- sample size used to justify the call

If the lane is hot-path dispatch or governance, prefer the fastest model that is boringly reliable.

## Interpretation Rules

- A model that is fast but omits required semantic fields is not production-ready.
- A model that is richer but materially slower must justify the cost with better decisions, not prettier reasoning.
- Schema-valid output is necessary but not sufficient.
- Closed-enum correctness matters.
- Rejection behavior matters.
- Default-model decisions should be driven by ROI, not novelty.

## Reporting Format

The summary should answer:

1. What was tested?
2. What protocol was used?
3. What were the key numbers?
4. What failed, and how?
5. What is the decision?
6. Does this require a code change, canon update, or no action?

Avoid over-claiming. If the benchmark only proves smoke viability, say so.

## Sustainability Follow-Through

After a winner is chosen, call out the next reliability step:

- frozen corpus reruns
- live-case sampling
- drift checks
- promotion or demotion rules over time

Do not let a one-day benchmark pretend to prove long-term sustainability.

## Good Output Shape

- `Decision: qwen2.5:7b stays as DEFAULT_MODEL.`
- `Reason: 100% validity, best latency-to-correctness ROI, correct rejection vocabulary behavior.`
- `Non-winners: qwen2.5:14b too slow for marginal gain; mistral:7b-instruct fails semantic rejection field population despite valid JSON.`
- `Action: no code change needed.`

## Anti-Patterns

- Choosing on intuition after a handful of runs
- Switching criteria after seeing the results
- Treating schema validity as the whole story
- Promoting a model because it is interesting rather than dependable
- Omitting sample-size or protocol details
