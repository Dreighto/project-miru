# Worker Performance Scorecard

A lightweight framework for tracking how well workers perform on dispatched jobs.
The purpose is routing improvement — not blame. Patterns across many jobs are the signal;
single-job scores are noise.

Initial implementation: scores are recorded as Linear ticket comments after job close.
No aggregation system yet — Claude Chat reads comments during dispatch planning.

Last updated: 2026-05-01

---

## 1. Scorecard Fields

One scorecard per job. Recorded as a comment on the Linear ticket when the job closes.

| Metric             | Definition                                               | Good                                                                | Needs attention                                                         |
| ------------------ | -------------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Success**        | Did the job reach CONFIRMED_WORKING?                     | Yes                                                                 | INCONCLUSIVE or FAILED                                                  |
| **Accuracy**       | Did it solve the right problem — no rework needed?       | Correct outcome, no follow-up fix required                          | Rework ticket filed because outcome was wrong                           |
| **Retries**        | How many dispatch attempts?                              | 1 (first attempt succeeded)                                         | 2 (one retry) or 3+ (escalated)                                         |
| **Escalations**    | How many operator pings were required?                   | 0 (fully autonomous)                                                | 1+ (required human input beyond self-healing)                           |
| **Efficiency**     | Was time/model choice reasonable for the task type?      | Completed within expected scope                                     | Ran far longer or used a higher model than needed                       |
| **Cost awareness** | Was the cheapest capable model used?                     | Correct model selected for task and budget state                    | Overshot model tier without justification                               |
| **Communication**  | Did it report status clearly and use the correct format? | Heartbeats emitted, terminal state clear, completion marker written | Missing heartbeats, ambiguous terminal state, missing completion marker |

---

## 2. Scorecard Format (Linear comment)

When a job closes, Claude Code or Claude Chat adds a comment to the ticket:

```
SCORECARD
Success: Yes / No
Accuracy: Clean / Rework-required
Retries: 1 / 2 / 3+
Escalations: 0 / 1 / 2+
Efficiency: On-scope / Over-scope
Cost awareness: Appropriate / Overshot
Communication: Clean / Issues ([what was missing])
Note: [optional — one line if something is worth capturing for routing improvement]
```

Short and literal. Not a narrative. Claude Chat reads these without parsing prose.

---

## 3. How Scores Influence Routing

Claude Chat reads recent scorecards when deciding which worker to dispatch next.

**Patterns that matter** (across 3+ jobs, not one):

- Worker consistently fails on a specific task type → route that type to a different worker
- Worker consistently requires operator escalation → reduce autonomous scope for that worker
- Worker consistently uses higher model than needed → note in dispatch brief to use lower tier
- Worker consistently produces clean outcomes with no rework → confidence to expand scope

**Single-job scores** are not routing signals. One bad job can have many causes.
Three bad jobs on the same type of task is a pattern.

**Do not penalize a worker for an ambiguous spec.** If the ticket description was unclear
and the worker emitted INCONCLUSIVE with a specific question, that is correct behavior —
not a failure. Score Accuracy as Clean if the worker's interpretation was reasonable given
the available spec.

---

## 4. What This Does Not Do

- **No automated scoring.** A human (Claude Chat or Claude Code) writes the scorecard comment after each job. There is no scoring pipeline.
- **No aggregation dashboard.** Scores live in Linear comments. Aggregation is deferred to a later implementation task.
- **No punishment.** Workers are tools, not employees. The scorecard exists to improve routing decisions, not to rank or penalize.
- **No real-time feedback.** The scorecard is written at job close — not during execution.

---

## 5. Connection to Canon Promotion

If a pattern in the scorecard reveals something durable (e.g. "Codex consistently
outperforms claude-code on cross-file audit tasks for this codebase"), that finding
is worth promoting to Notion via the canon-contract.md promotion process.

Do not promote single-job findings. Promote patterns that have been observed across
multiple jobs and are confirmed by the scorecard record.
