# Sustainability Evals Skill — Claude Code

Use this when Claude Code needs to determine whether a model, worker, or workflow remains dependable over time in Miru.

## Purpose

This skill exists to stop one good benchmark day from being mistaken for long-term production readiness.

## Entry Gate

Use this when the question is any variation of:

- Is this still holding up over time?
- Did this model regress?
- Is the worker reliable enough to keep in production?
- Do we have drift?
- Should this model or worker be demoted?

Do not use it for the first smoke test or the first head-to-head benchmark.

## Required Inputs

Before evaluating sustainability, make sure you have:

1. A fixed or clearly versioned corpus
2. Prior benchmark baselines
3. The current run metrics
4. A defined lane and contract
5. A threshold for concern

If those are missing, report that the sustainability claim is underpowered.

## Minimum Metrics Over Time

Track at least:

- validity rate trend
- latency trend
- failure-shape trend
- required-field fidelity
- false accept / false reject pattern where applicable
- operator babysitting cost if known

## Interpretation Rules

- Stable correctness with stable latency is the goal.
- Rising babysitting cost counts as deterioration even if raw outputs still look okay.
- Regression is about trend, not one bad anecdote.
- Drift should be named with dates or run windows whenever possible.
- Demotion rules should be explicit before a model is blessed as sustainable.

## Output Contract

The result should answer:

1. What baseline are we comparing against?
2. What changed or stayed stable?
3. Is this noise, warning, or real deterioration?
4. What action follows?

## Classification

Classify the current state as:

- `Stable`
- `Watch`
- `Regressing`
- `Demote candidate`

## Good Output Shape

- `Status: Stable. qwen2.5:7b continues to meet 100% validity with no meaningful latency drift across the frozen corpus reruns. Keep as default and rerun weekly.`
- `Status: Watch. Mistral remains schema-valid but continues to miss rejection.reason, so it is not suitable for promotion despite acceptable speed.`

## Anti-Patterns

- Declaring sustainability from one run
- Changing corpus every time and pretending the trend is comparable
- Ignoring semantic failures because the JSON validates
- Demoting on frustration without evidence
