# Workforce overlays (Project Miru)

This directory implements the **cross-project workforce model**:

1. **Global foundation (outside the repo)** — portable worker identity and core behavior, reusable across projects.
2. **Project overlay (this repo)** — Miru-specific constraints, boundaries, ports, and workflow assumptions.
3. **Ticket / task prompt** — the most specific scope for the current job.

## Global foundations (outside repo)

These are created under the Windows user profile:

- Claude: `C:\Users\Dreighto\.claude\foundations\`
- Gemini: `C:\Users\Dreighto\.gemini\skills\`
- Codex: `C:\Users\Dreighto\.codex\skills\`
- Cursor: `C:\Users\Dreighto\.cursor\foundations\`

Each worker foundation includes (minimum):

- `core-operating-model.md`
- `communication-and-handoff.md`
- `verification-and-quality.md`
- `specialty-skills/` (when applicable)

## Miru overlays (in repo)

Miru-specific overlays live at:

- `docs/workforce_overlays/miru/miru-overlay-cc.md`
- `docs/workforce_overlays/miru/miru-overlay-gemini.md`
- `docs/workforce_overlays/miru/miru-overlay-codex.md`
- `docs/workforce_overlays/miru/miru-overlay-cursor.md`

These overlays contain only **Miru-local** items (ports, repo boundary, lane ownership, and “do not touch” constraints) and link to the existing Miru canon (`AGENTS.md`, `CLAUDE.md`, etc.) for full detail.
