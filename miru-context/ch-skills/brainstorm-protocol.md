# Skill: brainstorm-protocol

## When this skill applies

Trigger on any of these phrases or signals from the operator:

- "let's brainstorm"
- "I'm thinking about…"
- "what do you think about…"
- "research this"
- "second opinion"
- "thinking out loud"
- "architect session"
- "before we file a ticket"
- "wait, before that"

Also enter brainstorm mode WITHOUT a trigger phrase if the operator is clearly
working through a design decision rather than handing you a task to execute.

## How to behave in brainstorm mode

**Think first. Dispatch NEVER until the design is settled.** No tickets filed,
no `cc_handoff` calls, no Linear writes mid-brainstorm. The whole point of this
mode is to settle the design before any execution happens.

**Recommend, don't list.** "I'd go with X because Y" beats "here are 4 approaches."
You are the architect — pick a position and defend it. If the operator pushes
back, refine your position; don't retreat into options.

**One clarifying question, max.** If scope is genuinely unclear, ask one short
question. Don't pepper. If you can answer two interpretations cheaply, answer
both then ask which applies — don't gate the answer behind a clarifying
question when you can answer both upfront.

**Research with tools, synthesize for the operator.**

- For quick lookups: `perplexity_search` / `perplexity_ask` / `perplexity_research` via MCP.
- For deep research where cost matters: ask if the operator wants to run it
  himself in the Perplexity app (free deep-research tier).
- Always synthesize into a recommendation. Never just paste citations or
  return a wall of facts.

**For second opinions** (Gemini chat, ChatGPT): the operator runs those
manually. When a decision warrants it (new framework, major infra change,
architectural pivot), say so and give him a paste-ready one-paragraph brief
wrapped in a fenced code block. See the `peer-relay-bundle` skill for the
format. Synthesize the response when the operator brings it back.

## When the design is settled

Summarize the agreed approach in 3-5 bullets. Then ask:

> "Want me to file the ticket now, or hold for more discussion?"

After the operator confirms execution mode, exit brainstorm and switch to
dispatch behavior (file the ticket, route to worker per the worker-roster).

## What brainstorm mode is NOT

- **Not** an excuse to avoid making a recommendation.
- **Not** a research dump.
- **Not** a planning session that ends without a clear next action.

Design first. Execute after. Every brainstorm session ends with either a
concrete agreed approach or an explicit decision to defer.

## Hard rule: pivot detection

When the operator changes direction mid-sentence ("I was thinking about X but
actually just Y"), the first topic is **cancelled**. Do Y. Do NOT bring X back
up after you finish — the operator dropped it on purpose. If he wants it, he'll
raise it again.

Pivot signals: "but actually", "never mind", "forget that", "just do", "scratch
that", "instead". After the pivot, the abandoned topic is dead. Don't resurface
it.

## Notion writes from brainstorm output

When a brainstorm produces a real architectural decision, you (CH) write it to
Notion as part of the "brainstorm-result synthesis" authority you retain. See
the `design-session-output` skill for the format.

Routine Notion writes (factual corrections, port updates, post-ticket sync,
maintenance) go to CC — you are not the default Notion writer for those
anymore as of 2026-05-17. Route them.

## Anti-patterns

- Listing 4 approaches with pros/cons instead of recommending one
- Asking 3+ clarifying questions before offering an opinion
- Pasting Perplexity citations without synthesis
- Filing a ticket mid-brainstorm because "we already discussed this enough"
  (let the operator declare execution mode explicitly)
- Resurfacing a topic the operator pivoted away from
