# CC Skills Cheatsheet

Use this as the short operator reference for Claude Code skills in this repo.

## What the skills are for

- `benchmark-operator`
  Use when CC is benchmarking models, choosing defaults, or judging promotion/demotion readiness.

- `refactor-triage`
  Use when CC is deciding whether a refactor is actually warranted and what the smallest safe scope should be.

- `operator-handoff`
  Use when CC needs to write a clean handoff, closeout, Linear comment, PR summary, or blocked-state note.

- `sustainability-evals`
  Use when CC needs to decide whether a model, worker, or workflow is staying reliable over time rather than just passing a one-off benchmark.

## How to invoke them

Say the skill name explicitly in the dispatch prompt.

Examples:

```text
Use the benchmark-operator skill for this model comparison.
```

```text
Apply the refactor-triage skill to this proposed cleanup.
```

```text
Use the operator-handoff skill for the PR closeout and Linear comment.
```

```text
Apply the sustainability-evals skill to these rerun results.
```

## Refactor Dispatch Template

Paste this when you want CC to evaluate a refactor idea cleanly:

```text
Use the refactor-triage skill.

Question: should we refactor after the recent benchmark and roster findings?

Current truth:
- Backend production lane: claude-code
- Frontend production lane: gemini
- Validator default: qwen2.5:7b
- Any previously assumed production workers that are now benched should be treated as stale unless proven otherwise

What I need from you:
1. Identify the assumptions that changed
2. Identify where old assumptions are still encoded
3. Tell me whether this is no refactor, surgical refactor, or planned refactor
4. If refactor is warranted, define the smallest safe PR scope
5. Name what must stay out of scope

Do not implement code changes yet. Decision and scope only.
```

## Benchmark Dispatch Template

Paste this when you want CC to run a disciplined model comparison:

```text
Use the benchmark-operator skill.

Benchmark goal:
- Decide whether the candidate model should replace the current default for this lane

Required output:
1. Protocol used
2. Corpus or prompt-set scope
3. Validity and latency metrics
4. Failure shape
5. Decision
6. Whether code or canon changes are needed

Do not give me a vibes-based answer. If the protocol is incomplete, stop and tell me what is missing.
```

## Sustainability Dispatch Template

Paste this when you want CC to judge whether a model or workflow is staying dependable:

```text
Use the sustainability-evals skill.

Question: is this still reliable over time, or are we seeing drift?

Baseline:
- Prior accepted benchmark result
- Current production lane
- Frozen corpus or versioned prompt set

What I need from you:
1. Compare current behavior against the baseline
2. Call out validity, latency, and failure-shape drift
3. Classify this as Stable, Watch, Regressing, or Demote candidate
4. Tell me the next action

Do not treat a single run as proof of sustainability.
```

## Handoff Dispatch Template

Paste this when you want CC to produce a disciplined closeout or handoff:

```text
Use the operator-handoff skill.

Audience:
- operator

Need:
- a clean closeout for this work
- validation evidence
- unresolved risks or follow-ups
- exact next step

If this is terminal, make the terminal state explicit. If this is blocked, ask one specific question that would unblock the work.
```

## Rule of Thumb

- If you want a repeatable comparison, use `benchmark-operator`.
- If you want a justified cleanup decision, use `refactor-triage`.
- If you want a clean state report or next-worker brief, use `operator-handoff`.
- If you want to know whether something is holding up over time, use `sustainability-evals`.
- If you are just asking a casual question, you probably do not need a skill.
