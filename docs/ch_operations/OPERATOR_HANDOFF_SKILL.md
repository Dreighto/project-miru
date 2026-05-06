# Operator Handoff Skill — Claude Code

Use this when Claude Code needs to hand work back to the operator, a reviewer, or another worker in Miru.

## Purpose

This skill exists to make handoffs crisp, evidence-backed, and low-friction. A good handoff should let the next human or worker move immediately without reconstructing the whole session.

## Core Rule

A handoff is not a diary entry. It is a decision surface.

Every handoff should answer:

1. What happened?
2. What evidence proves it?
3. What still matters?
4. What should happen next?

## Audience Modes

Choose the audience before writing:

- `Operator`
  Needs the outcome, risk, and exact next action.

- `Next worker`
  Needs entry points, watch-outs, and unresolved edges.

- `Reviewer / PR thread`
  Needs what changed, what was validated, and how findings were handled.

## Minimum Good Handoff

At minimum include:

- current state
- what changed or what was decided
- validation evidence
- unresolved risks or follow-ups
- exact next step

If any of those are missing, the handoff is weak.

## Completion Marker Discipline

When a completion marker is required, the handoff should match the marker:

- terminal state must be explicit
- summary must match the actual outcome
- test evidence must be concrete
- follow-up tickets should be named, not implied

Do not claim `CONFIRMED_WORKING` if the key verification gate is still unresolved.

## Linear / PR Comment Discipline

For Linear comments and PR notes:

- lead with the outcome
- keep the body evidence-dense
- separate what was fixed from what was deferred
- name blockers only if they are real blockers
- do not force the operator to infer what you want them to do next

## Good Shapes

- `Decision: qwen2.5:7b stays default. Evidence: 100% validity, p50 28s, correct rejection vocabulary. Next: move to sustainability checks.`
- `PR ready for operator merge. Validation: required greps zero-match, import clean, pre-commit green. Residual issue: broad pytest selector still fails during unrelated collection.`

## Bad Shapes

- `I finished a bunch of stuff and I think it is good.`
- `There were some issues but Claude fixed them.`
- `Waiting on review.`

Those waste operator time.

## If Blocked

If the result is `INCONCLUSIVE`, the handoff must say:

- what was tried
- why it failed
- one specific question that unblocks progress

No vague uncertainty language.
