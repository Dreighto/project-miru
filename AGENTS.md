# AGENTS.md — Project Miru Overlay

```text
Architecture: MIRU-INSTRUCTIONS-v3
Last reviewed: 2026-07-21
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

- **`pm/`**: frontend storefront code. (Corrected 2026-07-21: the "Cursor/GMI lane only, CC must not touch" restriction is removed. The CC=backend / GMI=frontend lane split was retired by operator directive 2026-05-23, and GMI holds no standing designer lane. Cursor is the `designer`-lane default, but that is a routing preference, not a write restriction on CC.) Read `.logueos/overlays/domain-ui.md` in the orchestrator before touching frontend code here.
- **`miru_ai/static/`**: same as `pm/`: no lane restriction, same craft-guide trigger.
- **`.mcp.json`** — Never modify. MCP config files are operator-managed.

## Miru-Specific File Ownership

- **No exclusive frontend owner.** (Corrected 2026-07-21, replacing "Cursor/GMI own HTML/CSS/JS templates, `pm/`, `miru_ai/static/`, the frontend lane".) Routing is by lane, not by nickname, and the lane split this rule encoded was retired 2026-05-23. UI work in this repo routes to the `designer` lane, whose default is **Cursor**; CC and Gemini are the other `designer` candidates. CC is a generalist and may own Python backend, tests, verification scripts, and frontend alike. Lane defaults and candidates live in `~/dev/LogueOS-Orchestrator/.logueos/roles.yaml`, which is the authority here, not this file.
- **CC owns CLAUDE.md, AGENTS.md, and all worker rule files by default** — permanent as of the operator's 2026-07-12 SOP shift moving canon ownership from CH to CC. CH is no longer the active canon owner or session driver.

## Miru Context Files

Read these before making product or service decisions that touch miru-specific surfaces:

- **`miru-context/miru-protected-constraints.md`** — Hard invariants. Read before touching card catalog, PM, or Miru AI.
- **`miru-context/miru-service-catalog.md`** — Service definitions and ports for miru-specific services.
- **`miru-context/miru-vocab.md`** — Miru-specific terminology.

For all other behavioral rules (Operator Communication Standard, Try Harder Discipline, Worker
Roles, Copy-paste rules, completion contract format), see `LogueOS-Orchestrator/AGENTS.md`.
