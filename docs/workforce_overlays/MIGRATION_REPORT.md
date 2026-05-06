# Workforce Foundation Migration — Miru

Goal: migrate worker operating foundations to a **cross-project global model** while preserving a **Miru-local overlay**.

This report is **structure + wiring only**. It does not redesign policy intent.

## Precedence model (authoritative)

1. **Global foundation (outside repo)** — reusable across projects
2. **Project overlay (in repo)** — Miru-specific constraints and workflows
3. **Ticket / task prompt** — most specific scope

See [`docs/workforce_overlays/README.md`](README.md).

---

## Inventory + classification

### Repo-level guidance (before)

- `AGENTS.md` — **MIXED**
  - Portable: PR review completion loop, return-to-main discipline, “try harder” discipline
  - Miru-local: references to `miru-context/*`, Miru-specific workflow assumptions
- `CLAUDE.md` — **MIXED**
  - Portable: copy-paste content rule, pre-flight concept, PR completion loop patterns
  - Miru-local: ports, repo boundary, Miru-specific tool profiles and workflows
- `GEMINI.md` — **MIXED**
  - Portable: Gemini role framing, hygiene/verification expectations
  - Miru-local: ports, paths, restart rules, DB rules, “must never” list tied to Miru
- `CODEX.md` — **MIXED**
  - Portable: Codex role framing, hygiene/verification expectations
  - Miru-local: ports, paths, restart rules, DB rules, “must never” list tied to Miru
- `CURSOR.md` — **MIXED**
  - Portable: Cursor role framing, UI verification expectations
  - Miru-local: lane ownership in Miru, paths, tool usage rules, “must never” list tied to Miru
- `.claude/skills/*` — **MIXED**
  - Portable: skill structure + invocation pattern
  - Miru-local: trigger vocab that references Miru lanes/taxonomy
- `.gemini/*` — **LOCAL_OVERLAY**
  - Repo-local Gemini config for Miru
- `miru-context/*` — **LOCAL_OVERLAY**
  - Miru-specific canon, constraints, workflow specs, and service catalog
- `docs/ch_operations/*` — **MIXED**
  - Portable: crisp handoff / refactor triage / benchmark discipline as generic practice
  - Miru-local: some tool names and workflow references in `CH_PLAYBOOK.md`
- `docs/ui_ux/*` — **MIXED**
  - Portable: most UX craft principles
  - Miru-local: Miru design-language specifics (e.g. “Forge aesthetic” section)

If an artifact is uncertain: it remains **MIXED** and is documented rather than force-split in this migration.

---

## Global foundation layout (outside repo)

Created/normalized the following global locations:

### Claude (global)

Root: `C:\Users\Dreighto\.claude\foundations\`

Minimum docs:

- `core-operating-model.md`
- `communication-and-handoff.md`
- `verification-and-quality.md`
- `specialty-skills/`:
  - `benchmark-operator.md`
  - `operator-handoff.md`
  - `refactor-triage.md`
  - `sustainability-evals.md`
  - `cc-skills-cheatsheet.md`
  - `n8n-workflow/` (copied from repo-local skill)

### Gemini (global)

Root: `C:\Users\Dreighto\.gemini\skills\`

Minimum docs:

- `core-operating-model.md`
- `communication-and-handoff.md`
- `verification-and-quality.md`

Note: this path already contained other Gemini skill folders; this migration only added the minimum foundation docs.

### Codex (global)

Root: `C:\Users\Dreighto\.codex\skills\`

Minimum docs:

- `core-operating-model.md`
- `communication-and-handoff.md`
- `verification-and-quality.md`

### Cursor (global)

Root: `C:\Users\Dreighto\.cursor\foundations\`

Minimum docs:

- `core-operating-model.md`
- `communication-and-handoff.md`
- `verification-and-quality.md`

---

## Miru local overlays (in repo)

Created repo-local overlays at `docs/workforce_overlays/miru/`:

- `miru-overlay-cc.md`
- `miru-overlay-gemini.md`
- `miru-overlay-codex.md`
- `miru-overlay-cursor.md`

These overlays intentionally contain only Miru-local items:

- ports
- Miru repo boundary (`D:\dev\miru*`)
- Miru-specific canon entry points (`AGENTS.md`, `CLAUDE.md`, `miru-context/*`)
- lane ownership pointers (Cursor/PM storefront)

---

## Pointer / wiring docs (in repo)

- `docs/workforce_overlays/README.md` — documents precedence and where global foundations live.

---

## Before / after structure (explicit file trees)

### Global (outside repo) — created/updated by this migration

```text
C:\Users\Dreighto\.claude\foundations\
  core-operating-model.md
  communication-and-handoff.md
  verification-and-quality.md
  specialty-skills\
    benchmark-operator.md
    operator-handoff.md
    refactor-triage.md
    sustainability-evals.md
    cc-skills-cheatsheet.md
    n8n-workflow\
      SKILL.md

C:\Users\Dreighto\.gemini\skills\
  core-operating-model.md
  communication-and-handoff.md
  verification-and-quality.md
  specialty-skills\   (created; currently empty)

C:\Users\Dreighto\.codex\skills\
  core-operating-model.md
  communication-and-handoff.md
  verification-and-quality.md
  specialty-skills\   (created; currently empty)

C:\Users\Dreighto\.cursor\foundations\
  core-operating-model.md
  communication-and-handoff.md
  verification-and-quality.md
  specialty-skills\   (created; currently empty)
```

### Local (in repo) — added/normalized by this migration

```text
docs/workforce_overlays/
  README.md
  MIGRATION_REPORT.md
  miru/
    miru-overlay-cc.md
    miru-overlay-gemini.md
    miru-overlay-codex.md
    miru-overlay-cursor.md
```

---

## What moved / copied vs stayed local

### Copied to global foundations (kept local as-is for compatibility)

- `docs/ch_operations/*_SKILL.md` → copied into `C:\Users\Dreighto\.claude\foundations\specialty-skills\*.md`
- `.claude/skills/n8n-workflow/` → copied into `C:\Users\Dreighto\.claude\foundations\specialty-skills\n8n-workflow\`

Rationale: keep repo-local paths intact until the project explicitly switches its loaders/dispatch prompts to the global locations.

### Stayed local (Miru overlay)

- `miru-context/*` (Miru canon + constraints)
- `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CODEX.md`, `CURSOR.md` (still the current Miru operational surfaces; overlays now point to them)

---

## Open gaps / blockers (by worker)

- Claude:
  - The repo-local `.claude/skills/*` wrappers still reference `docs/ch_operations/*`. A follow-on can update wrappers to reference the global `specialty-skills/*` docs once the operator decides how loaders resolve global paths.
- Gemini:
  - Gemini’s native skill loader behavior across repos is not asserted here; this migration only created docs at a stable path.
- Codex:
  - Codex skill-loading support differs by environment/version. This migration uses a docs-first “prompt pack” approach under `C:\Users\Dreighto\.codex\skills\`.
- Cursor:
  - Cursor does not have a native “global foundation loader” in the same way; this migration provides global docs for operators/prompts to reference.

---

## Assumptions

- Global doc paths under `C:\Users\Dreighto\...` are acceptable and stable for this machine/user.
- Existing repo-local canon remains the current source-of-truth for Miru until prompts/routing are updated to reference global foundations directly.
- No attempt is made here to automatically rewire `.mcp.json` or any runtime config for “skill discovery”.
