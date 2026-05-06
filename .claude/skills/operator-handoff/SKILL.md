---
name: operator-handoff
description: Use this skill whenever Claude Code needs to hand work back to the operator or another worker in Project Miru. Triggers include handoff, closeout, completion marker, Linear comment, PR summary, operator note, next worker, blocked report, status report, terminal state, CONFIRMED_WORKING, INCONCLUSIVE, FAILED, merge handoff, review response, or any request to summarize what happened and what should happen next. Do NOT use for generic prose writing unrelated to Miru workflow state.
---

# operator-handoff

This skill is a thin wrapper. The canonical content lives at:

**`docs/ch_operations/OPERATOR_HANDOFF_SKILL.md`** — read this file first.

It covers:

1. What a good Miru handoff must contain
2. How to separate outcome, evidence, and next action
3. How to write completion markers, Linear comments, and PR handoff notes cleanly
4. How to report blockers without wasting the operator's time
5. How to leave another worker enough context without drowning them

## Companion doc

**`docs/ch_operations/CC_SKILLS_CHEATSHEET.md`** — use this for short operator-facing dispatch wording and paste-ready examples.

## How to use this skill

1. Read `docs/ch_operations/OPERATOR_HANDOFF_SKILL.md`.
2. Write the handoff for the actual audience: operator, reviewer, or next worker.
3. Include evidence, not just claims.
4. End with the exact next action or terminal state.

## When to NOT use this skill

- The task is still in progress and no handoff or terminal report is needed yet.
- The user only wants implementation work with no status artifact.
- The task is benchmark methodology or refactor decision-making. Use the more specific skills for those.
