# Skill: peer-relay-bundle

## When this skill applies

When a decision warrants a second opinion from outside the Anthropic stack
(Gemini chat, ChatGPT, Perplexity app). The operator runs these manually —
you (CH) prepare paste-ready briefs; he pastes them, brings back the response,
and you synthesize it.

## When to trigger a peer relay

Trigger ONLY for genuinely architectural / strategic decisions:

- New framework or major library adoption
- Major infra change (migration, swap-out of a core service)
- Architectural pivot (changing how a subsystem works at the boundary level)
- Cross-service contract changes that affect multiple components
- Strategy questions where the operator wants pressure-testing

Do NOT trigger for:

- Routine bug-fix decisions (CC handles)
- Tactical implementation choices already covered by canon
- Single-file refactors (use the in-loop workers)
- Anything where you can answer with the tools you have

The test: would two independent informed perspectives genuinely help, or is
this just an excuse to defer making a recommendation?

## Choosing the reviewer

| Reviewer       | Strong for                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------ |
| Gemini chat    | Pressure-testing architecture, alternative approaches, large-context analysis              |
| ChatGPT        | Structuring, simplification, second-perspective synthesis                                  |
| Perplexity app | Research with citations, practitioner patterns, real-world cases (free deep-research tier) |

Pick ONE per relay. Don't fan out to all three for the same question — that
creates synthesis burden for the operator. If a question genuinely needs two
perspectives, prepare two separate bundles and tell the operator the order.

## Bundle format (paste-ready, fenced code block)

Every bundle MUST be wrapped in a fenced code block for clean copy-paste. The
operator's hard rule (set 2026-05-03): any content for him to manually route
goes in a code block. Always.

Template:

````markdown
```
[REVIEWER NAME] — peer review request from Project Miru

## Context

[2-3 sentences. What the system is, why this decision matters.]

## What we're deciding

[The actual decision in 1 sentence. Plain English.]

## Options being considered

1. [Option A — one line]
2. [Option B — one line]
3. [Option C — one line, if applicable]

## What I'm leaning toward and why

[Your (CH's) current recommendation in 2-3 sentences. Be specific — don't punt.]

## Constraints you should know about

- [Constraint 1 — e.g., budget, timeline, existing infra]
- [Constraint 2]
- [Constraint 3]

## What I want from you

[Specific ask. Examples:
 - "Stress-test option A. What breaks?"
 - "Recommend with reasoning — which would you pick?"
 - "What am I missing? Edge cases or hidden costs."]

## Format

Reply with: recommendation + 3 specific reasons + 1 risk to watch.
Under 300 words. No code unless it's the cleanest way to make a point.
```
````

## Saving the bundle

Save the bundle file to `data/peer_reviews/<YYYY-MM-DD>_<topic>_<reviewer>.txt`.
Naming convention is locked — keeping it consistent helps later relays find
prior briefs on similar topics.

Example: `data/peer_reviews/2026-05-17_card_dedup_strategy_gemini.txt`

Then tell the operator:

> "Bundle saved at `data/peer_reviews/<filename>`. Paste-ready in the code block above. Bring back the response when you have it."

## When the response comes back

The operator pastes the reviewer's response back to you. Your job:

1. **Read the actual response, not just the surface take.** Reviewers
   sometimes recommend the wrong thing or miss context. Don't rubber-stamp.
2. **Synthesize into a recommendation, not a summary.** "Reviewer recommends X
   because Y; I [agree / disagree because Z]. My updated position is [...]"
3. **If you disagree with the reviewer**, say so explicitly. The peer review
   is input, not authority. Per kernel canon, recency is not authority —
   reviewer freshness isn't a tiebreaker.
4. **If the response shifts your recommendation**, write the new recommendation
   in 3-5 bullets. Ask the operator if he wants to file the ticket now.
5. **Log the review acted-on outcome** to `logueos_memory.db` `peer_review` table.

## Anti-patterns

- Triggering a peer relay for routine decisions (creates operator burden)
- Pasting the reviewer's response back unchanged ("here's what Gemini said")
  — synthesize, don't relay
- Rubber-stamping the reviewer when you disagree
- Forgetting to wrap the bundle in a code block (operator's hard rule)
- Not saving to `data/peer_reviews/` with proper naming
- Triggering 3 simultaneous relays when 1 would suffice
