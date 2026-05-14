# AGENTS.md — Project Miru Overlay

```text
Architecture: MIRU-INSTRUCTIONS-v3
Effective: 2026-05-13
Overlay scope: project-miru only.
```

This file is project-miru's behavioral overlay. The kernel baseline — Operator Communication
Standard, Try Harder Discipline, Copy-paste rules, Context Recovery Protocol, Worker Roles —
lives in `LogueOS-Orchestrator/AGENTS.md`. Workers read that file first. This file adds only
miru-product-specific constraints.

Read `miru-context/THE_ONE_PIECE.md` on every dispatch.

---

## Miru-Specific Never-Touch Rules

These extend the kernel's `Must never` list with constraints specific to this codebase:

- **`card_catalog.db`** — Never write to this file. It is the live card catalog database. Any modification outside the defined ingestion pipeline can corrupt product data.
- **`pm/`** — Frontend storefront code. Cursor/GMI lane only. CC must not touch HTML/CSS/JS templates here.
- **`miru_ai/static/`** — Same as `pm/`. CC must not touch templates or static assets.
- **`.mcp.json`** — Never modify. MCP config files are operator-managed.

## Miru-Specific File Ownership

- **CC owns:** Python backend files, test scripts, verification scripts in this repo.
- **Cursor/GMI own:** HTML/CSS/JS templates, `pm/`, `miru_ai/static/` — frontend lane.
- When CH is active: CH owns CLAUDE.md, AGENTS.md, and all worker rule files by default; CC may edit when operator explicitly authorizes it.

## Miru Context Files

Read these before making product or service decisions that touch miru-specific surfaces:

- **`miru-context/THE_ONE_PIECE.md`** — Current product and service state. Read on every dispatch.
- **`miru-context/miru-protected-constraints.md`** — Hard invariants. Read before touching card catalog, PM, or Miru AI.
- **`miru-context/miru-service-catalog.md`** — Service definitions and ports for miru-specific services.
- **`miru-context/miru-vocab.md`** — Miru-specific terminology.

For all other behavioral rules (Operator Communication Standard, Try Harder Discipline, Worker
Roles, Copy-paste rules, completion contract format), see `LogueOS-Orchestrator/AGENTS.md`.
