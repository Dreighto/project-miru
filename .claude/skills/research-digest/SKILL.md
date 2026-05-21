---
name: research-digest
description: Use this skill to run a deep research pass and turn it into a durable digest artifact before building or planning. Triggers include research X, deep dive on Y, look up best practices for Z, gather references, research before we build, perplexity research, digest the research, design research, what do the best apps do. Do NOT use for quick single-fact lookups (use perplexity_ask directly) or for searching the codebase (use the Explore agent).
---

# research-digest

This skill is self-contained. It turns research into a durable, citable artifact instead of one-off context that evaporates at the end of the turn.

## Run the research

- **`perplexity_research`** — deep, multi-source, comprehensive. Slow (30s+). Use it for design direction, best-practice surveys, anything a build will be based on. Set `reasoning_effort` (medium is usually right) and `strip_thinking: true`.
- **`perplexity_ask`** — a quick AI answer with citations, for a focused question, not a survey.
- Scope each query tightly: state the context, the constraints, and ask for **concrete, implementable techniques and named references** — not generalities.

## The large-output gotcha

Deep research often exceeds the tool-output token limit — the result is persisted to a file (`{response: string}` JSON) instead of returned inline. To read it:

- Extract `.response` with `jq -r '.response'` or python, and read it in chunks — slice by character range, or grep the `##` section headers first then read the sections you need.
- Do NOT use Read's `offset`/`limit` on these files — line-based paging cannot chunk a single giant JSON string.
- A subagent can digest it to keep your context clean — but be explicit (give the exact file path, the `{response: string}` schema, and what to return). Subagents sometimes misread the task, especially under plan mode. When in doubt, extract and read it yourself.

## The digest

Synthesize into a structured markdown file and **save it**:

`data/research/YYYY-MM-DD_<topic>.md`

(Research artifacts live in `data/research/` — not Notion, not chat context.) A good digest:

- Is organized by theme, not by source.
- Stays concrete — specific techniques, values, patterns, code shapes — not vague advice.
- Names the references and the one-line takeaway from each.
- Is written to be cited — by a skill, a plan, or a dispatched worker's prompt.

## When NOT to use

- A quick fact — call `perplexity_ask` directly, no digest needed.
- Searching this codebase — that is the Explore agent.
