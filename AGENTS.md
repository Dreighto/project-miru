# AGENTS.md — Project Miru Overlay

```text
Architecture: MIRU-INSTRUCTIONS-v3
Last reviewed: 2026-06-22
Effective: 2026-05-13
Overlay scope: project-miru only.
```

This file is project-miru's behavioral overlay. The kernel baseline — Operator Communication
Standard, Try Harder Discipline, Copy-paste rules, Context Recovery Protocol, Worker Roles —
lives in `LogueOS-Orchestrator/AGENTS.md`. Workers read that file first. This file adds only
miru-product-specific constraints.

---

## Miru-Specific Never-Touch Rules

These extend the kernel's `Must never` list with constraints specific to this codebase:

- **`pm/`** — Frontend storefront code. Cursor/GMI lane only. CC must not touch HTML/CSS/JS templates here.
- **`miru_ai/static/`** — Same as `pm/`. CC must not touch templates or static assets.
- **`.mcp.json`** — Never modify. MCP config files are operator-managed.

## Miru-Specific File Ownership

- **CC owns:** Python backend files, test scripts, verification scripts in this repo.
- **Cursor/GMI own:** HTML/CSS/JS templates, `pm/`, `miru_ai/static/` — frontend lane.
- **CC owns CLAUDE.md, AGENTS.md, and all worker rule files by default** — permanent as of the operator's 2026-07-12 SOP shift moving canon ownership from CH to CC. CH is no longer the active canon owner or session driver.

## Miru Context Files

Read these before making product or service decisions that touch miru-specific surfaces:

- **`miru-context/miru-protected-constraints.md`** — Hard invariants. Read before touching card catalog, PM, or Miru AI.
- **`miru-context/miru-service-catalog.md`** — Service definitions and ports for miru-specific services.
- **`miru-context/miru-vocab.md`** — Miru-specific terminology.

For all other behavioral rules (Operator Communication Standard, Try Harder Discipline, Worker
Roles, Copy-paste rules, completion contract format), see `LogueOS-Orchestrator/AGENTS.md`.
