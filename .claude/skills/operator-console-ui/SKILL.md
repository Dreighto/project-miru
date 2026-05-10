---
name: operator-console-ui
description: Use this skill whenever Gemini is asked to build or refine an operator-facing dashboard, triage screen, dense console, admin panel, monitoring UI, or any surface where high information throughput matters more than airy marketing-style spacing. Triggers include operator console, triage UI, dense dashboard, compact data layout, high-density, mono font, dark console, 1px borders, maximize visible data points, UI triage, theme sync, aesthetic drift, and compact control surface. Do NOT use for storefront, consumer-facing, or broadly visual product surfaces unless the user explicitly wants console density there.
---

# operator-console-ui

This skill is self-contained.

## Purpose

Define the operator-console lane. The user is not a casual browser. The UI should optimize for speed of understanding, compactness, and control.

## Design Philosophy

### Density over whitespace

- Prioritize information throughput.
- Compact does not mean cramped; signal should be dense but organized.
- Visible utility beats decorative air.

### Triage-operator framing

- Treat the user like someone supervising systems, not browsing content leisurely.
- Actions, state, and alerts should surface quickly.

### Borders and grouping

- Prefer crisp grouping with 1px boundaries, tokenized dividers, and strong section rhythm.
- Use spacing and borders to create operator-readable zones.

## Theme Lane

Default operator-console assumptions:

- very dark backgrounds such as #050505 or equivalent existing token
- mono-forward or condensed typography when appropriate
- restrained glow or focus treatment instead of glossy decoration
- strong contrast for status and system health states

Do not force these values onto screens that already belong to a different visual lane.

## Internal Behaviors

### COMP_AUDIT

Audit for operator-console drift:

- too much empty space
- oversized controls
- weak hierarchy
- missing dense summaries
- style inconsistency with console surfaces

### THEME_SYNC

Audit for token drift:

- backgrounds off the console registry
- inconsistent type families
- inconsistent border treatments
- one-off accent colors

### UI_TRIAGE

Reorganize for throughput:

- increase visible data points
- reduce scrolling cost
- group actions near the data they affect
- collapse dead zones
- preserve readability while tightening density

## When to Ask Instead of Guess

Pause if any of these are unclear:

- whether the surface is operator-facing or consumer-facing
- whether mobile density rules should differ from desktop
- whether an existing design system should be preserved

## When NOT to use this lane

- storefront UI
- consumer-facing product surfaces
- marketing pages
- any surface where the user did not ask for dense console behavior

## Anti-Patterns

- applying bunker-console aesthetics to every screen in the app
- confusing density with clutter
- using mono fonts everywhere regardless of context
- forcing dark console rules onto non-console product surfaces
