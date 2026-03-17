# Project Miru – UI foundation (Pass A)

This document describes the layered styling structure and conventions for the Miru AI surface (port 18765). The worktree Project Miru dashboard (port 18080) uses its own inline styles in `dashboard/app.py` and is out of scope for this file.

**Governing architecture:** See `docs/UI-CONSTITUTION.md` for the full UI constitution (tokens, naming, isolation rules, Pass A/B plans).

## Layers (in `miru_ai.css`)

| Layer | Purpose | Scope |
|-------|---------|--------|
| **0 – Design tokens** | Colors, spacing, radius, shadows, typography, z-index, motion | `:root` only |
| **1 – Base** | Reset (box-sizing, body, buttons, links) | Global |
| **2 – Layout primitives** | Page shell (`.appShell`), section structure | Global shared |
| **3 – Shared components** | Panel, cards, buttons, pills, action bar, modal shell | Global shared |
| **4 – Page-scoped** | Dev, Leader Hub, Card Page overrides | Under `.pageBody--dev`, `.pageBody--leaderHub`, `.pageBody--cardPage` |

## Page-scoping convention

- Any selector that applies to **only one page** MUST be nested under a page root class:
  - `.pageBody--dev` – Dev console and Miru AI home
  - `.pageBody--leaderHub` – Leader hub (`/leader/<code>`)
  - `.pageBody--cardPage` – Card detail page
- Truly reusable primitives (shell, panel, buttons, pills) remain global.
- Do not add new page-specific styles without a page root. This prevents new page work from breaking other surfaces.

## Design tokens (Layer 0)

Use these in both shared and page-scoped styles; avoid magic numbers.

- **Colors**: `--bg`, `--text`, `--text-soft`, `--stroke`, `--purple`, `--good`, `--danger`, etc.
- **Spacing**: `--space-xs` through `--space-3xl`
- **Radius**: `--radius-xs`, `--radius-sm`, `--radius-md`, `--radius-lg`
- **Shadows**: `--shadow-sm`, `--shadow-lg`
- **Borders**: `--border-width`
- **Typography**: `--text-xs`, `--text-sm`, `--text-base`, `--text-lg`, `--text-xl`, `--text-2xl`
- **Z-index**: `--z-sticky`, `--z-dropdown`, `--z-tooltip`, `--z-modal`, `--z-modal-content`
- **Motion**: `--ease-default`, `--ease-out`, `--duration-fast`, `--duration-normal`

## Shared primitives (Layer 3) – confirmed set

Use these classes for consistent layout and components. **Page-specific usage must stay under a page root** (e.g. `.pageBody--dev .panelCard`); do not add page-only styles in global blocks.

| Primitive | Class(es) | Notes |
|-----------|-----------|--------|
| Page shell | `.appShell` | Main content width and padding |
| Section container | `.ui-section-container` | Optional; for new pages |
| Panel | `.panelCard` | Header pattern: first child `.sectionEyebrow` |
| Panel header/body | `.ui-panel-header`, `.ui-panel-body` | Optional; for new pages |
| Cards | `.heroCard`, `.panelCard`, `.infoCard` | Existing |
| Button row / action bar | `.heroActions`, `.actionRow`, `.devControlDeckRow`, `.ui-action-bar` | |
| Pills / chips / badges | `.statusPill`, `.tipPill`, `.devModeBadge` | |
| Empty state | `.ui-empty-state` | Optional; for new pages |
| Loading state | `.ui-loading-state` | Optional; for new pages |
| Modal overlay/shell | `.ui-modal-overlay`, `.ui-modal-shell` | Optional; existing modal: `.devVoyageMapDialog` |

When adding or changing styles for a single page, scope all new selectors under the appropriate `.pageBody--<pageKey>` so they cannot affect other surfaces.

## Risky areas not yet migrated (Pass B targets)

- Many global selectors (e.g. `.panelCard h2`) are still global; page-specific overrides are scoped, but new pages could be affected by global rules until more selectors are scoped or namespaced.
- Dashboard (18080) uses inline CSS in `dashboard/app.py`; no shared token/primitive system there yet.
- Some dev-only components (e.g. `.devConsoleShell`, `.devStatusStrip`) are global by name but only used on the dev page; could be moved under `.pageBody--dev` in a later pass for clarity.
