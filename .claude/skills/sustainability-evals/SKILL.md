---
name: sustainability-evals
description: Use this skill whenever Claude Code needs to evaluate whether a model, worker, or workflow remains reliable over time in Project Miru. Triggers include sustainability, drift, regression, rerun corpus, repeated eval, over time, weekly check, reliability trend, latency drift, false accept, false reject, frozen corpus, production stability, demotion rule, promotion rule, staying power, or any request to prove a good result will keep holding up later. Do NOT use for one-off smoke tests or first-pass benchmark comparisons.
---

# sustainability-evals

This skill is a thin wrapper. The canonical content lives at:

**`docs/ch_operations/SUSTAINABILITY_EVALS_SKILL.md`** — read this file first.

It covers:

1. How to move from smoke tests to long-term reliability checks
2. Which metrics matter over time
3. How to detect drift instead of reacting to anecdotes
4. How to define promotion retention and demotion rules
5. How to report whether a system is staying dependable or deteriorating

## Companion doc

**`docs/ch_operations/CC_SKILLS_CHEATSHEET.md`** — use this for short operator-facing dispatch wording and paste-ready examples.

## How to use this skill

1. Read `docs/ch_operations/SUSTAINABILITY_EVALS_SKILL.md`.
2. Reuse a fixed corpus or explicitly explain why the corpus changed.
3. Compare trend behavior, not just a single run.
4. Call out demotion or escalation thresholds when they are hit.

## When to NOT use this skill

- The task is a first-pass benchmark to choose an initial candidate.
- The user only wants a quick smoke test.
- The task is a refactor question rather than a reliability-over-time question.
