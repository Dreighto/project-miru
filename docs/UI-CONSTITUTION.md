# Project Miru UI Constitution

**Worktree repo only:** `C:\Users\andre\.codex\worktrees\0814\tcg-watcher`  
**Ports:** 8080 = main (do not touch), 18080 = worktree dashboard, 18765 = Miru AI / Dev.

This document is the authoritative blueprint for UI architecture. It is implementation-oriented so a coding agent can apply it directly.

---

## 1. Summary

- **Goal:** Safe UI architecture so new pages/features cannot accidentally break unrelated surfaces. Style must stay sleek, modern, data-focused; One Piece theming subtle (~10–15%).
- **Principle:** Every page has a single root class. All page-specific CSS is scoped under that root. Shared primitives and tokens live in defined layers. No framework migration required.
- **Surfaces:** Worktree dashboard (18080, inline CSS in `dashboard/app.py`), Miru AI (18765, `tools/static/miru_ai.css` + `tools/templates/`). Pass A is partially done in 18765; dashboard and full migration are Pass B.

---

## 2. Proposed CSS Architecture

Four layers in a single canonical stylesheet (or one main + page-specific overrides). Order matters: later layers override earlier only via specificity or same-layer order.

| Layer | Name | Purpose | Scope | Where |
|-------|------|---------|--------|--------|
| **0** | Base / tokens | Design tokens only (`:root`). No selectors. | Global | Top of main CSS |
| **1** | Base / reset | Normalize box-sizing, body, buttons, inputs, links. Minimal. | Global | After tokens |
| **2** | Layout | Page shell, section container, grid/flex layout primitives. | Global (shared) | After reset |
| **3** | Components / primitives | Panel, card, action row, chips, modal shell, nav. | Global (shared) | After layout |
| **4** | Page-scoped | All page-specific overrides. | Under page root only | After components |

**Rules:**

- Layer 0: Only CSS custom properties in `:root`. No class or element selectors.
- Layers 1–3: No page-specific selectors. Only truly reusable, cross-page building blocks.
- Layer 4: Every selector must start with a page root class (e.g. `.pageBody--dev .panelCard`). No bare `.panelCard` in Layer 4.
- New pages get a new page root and a dedicated block in Layer 4. Do not add page-specific styles outside Layer 4.

**File layout (18765):** Single file `tools/static/miru_ai.css` with clear section comments for each layer. Optional: in Pass B, split Layer 4 into `miru_ai_pages.css` or keep in same file with `/* ----- Page: dev ----- */` blocks.

**Dashboard (18080):** Currently inline in `dashboard/app.py`. Pass B should introduce a page root on `<body>` and, if possible, a small external CSS that uses the same token names so both surfaces align.

---

## 3. Naming Conventions

### 3.1 Page root classes

- **Pattern:** `pageBody pageBody--<pageKey>`
- **Required:** Every `<body>` must have exactly one page root modifier.
- **Allowed values:**

| pageKey | Surface | Where used |
|---------|---------|------------|
| `home` | Miru AI home (ask) | 18765, miru_ai.html |
| `dev` | Dev console | 18765, miru_ai.html |
| `leaderHub` | Leader hub | 18765, leader_hub.html |
| `cardPage` | Card detail | 18765, card_page.html |
| `dashboard` | Worktree dashboard (home/watchlist) | 18080, app.py (Pass B) |
| `library` | Library list | 18080, app.py (Pass B) |
| `set` | Set detail | 18080 (Pass B) |
| `deckBuilder` | Deck builder (future) | New page |

- **Example:** `<body class="pageBody pageBody--cardPage">`

### 3.2 Page shell

- **Class:** `appShell`
- **Role:** Single main wrapper for page content (max-width, padding, safe-area). One per page.
- **HTML:** `<main class="appShell">` (or `<div class="appShell">` if not semantic main).

### 3.3 Panels

- **Primary:** `panelCard` – raised container with border, shadow, radius (uses tokens).
- **Optional header pattern:** First child with `sectionEyebrow` (label) or a dedicated `.panelCardHeader` / `.ui-panel-header`.
- **Body:** Content after header; optional `.panelCardBody` or `.ui-panel-body` for new markup.
- **Variants:** `panelCard--compact`, `panelCard--flat` if needed; keep naming BEM-like.

### 3.4 Cards

- **Hero:** `heroCard` – top-level promo block (e.g. home ask).
- **Content:** `panelCard` – generic content panel (forms, sections, status).
- **Info:** `infoCard` – small info block.
- **Page-specific cards:** Prefixed by page, e.g. `leaderHubHeader`, `cardHeader`, `devConsoleShell`. Still scoped under page root in CSS.

### 3.5 Action rows

- **Classes:** `actionRow`, `heroActions`, `ui-action-bar`.
- **Role:** Horizontal group of buttons/actions. Use tokens for gap (`var(--space-md)`).

### 3.6 Chips / badges / pills

- **Status:** `statusPill` with modifiers `statusPill--good`, `statusPill--warn`, `statusPill--neutral`.
- **Tip:** `tipPill`.
- **Mode/state:** `devModeBadge` (dev page) or generic `chip`, `badge` for new components.
- **Naming:** Prefer `--modifier` for state (e.g. `chip--active`).

### 3.7 Modal structures

- **Overlay:** `ui-modal-overlay` (full-screen, fixed, z-index from token).
- **Shell:** `ui-modal-shell` or page-specific e.g. `devVoyageMapDialog` for the dialog content box.
- **Body class when open:** e.g. `hasVoyageMapDialog` to lock scroll. Use one class per modal to avoid conflicts.

---

## 4. Design Tokens

Define in `:root` only. Use tokens everywhere; avoid magic numbers in components and page-scoped styles.

### 4.1 Colors

```css
:root {
    color-scheme: dark;
    /* Backgrounds */
    --bg: #08050f;
    --bg-soft: rgba(19, 12, 31, 0.9);
    --panel-top: rgba(28, 18, 44, 0.95);
    --panel-bottom: rgba(11, 8, 19, 0.98);
    --panel-alt: rgba(16, 11, 26, 0.92);
    /* Borders */
    --stroke: rgba(192, 167, 255, 0.18);
    --stroke-strong: rgba(216, 204, 255, 0.42);
    /* Text */
    --text: #faf7ff;
    --text-soft: #dbd2ef;
    --text-faint: #aa9fc7;
    /* Brand / semantic */
    --purple: #c4adff;
    --purple-strong: #8b5cf6;
    --yellow: #facc15;
    --yellow-soft: rgba(250, 204, 21, 0.12);
    --good: #86efac;
    --good-bg: rgba(22, 101, 52, 0.18);
    --danger: #fda4af;
    --danger-bg: rgba(66, 17, 31, 0.9);
    --neutral-bg: rgba(124, 58, 237, 0.14);
    --cyan: #67e8f9;
    --cyan-strong: #22d3ee;
    --sea-bg: rgba(34, 211, 238, 0.12);
}
```

### 4.2 Spacing

```css
--space-xs: 0.25rem;
--space-sm: 0.5rem;
--space-md: 0.75rem;
--space-lg: 1rem;
--space-xl: 1.25rem;
--space-2xl: 1.5rem;
--space-3xl: 2rem;
```

### 4.3 Border radius

```css
--radius-xs: 8px;
--radius-sm: 14px;
--radius-md: 18px;
--radius-lg: 24px;
```

### 4.4 Shadows

```css
--shadow-sm: 0 12px 26px rgba(6, 4, 12, 0.24);
--shadow-lg: 0 22px 46px rgba(3, 2, 8, 0.42);
```

### 4.5 Borders

```css
--border-width: 1px;
```

### 4.6 Typography scale

```css
--text-xs: 0.74rem;
--text-sm: 0.85rem;
--text-base: 1rem;
--text-lg: 1.1rem;
--text-xl: 1.25rem;
--text-2xl: 1.5rem;
```

### 4.7 Z-index layers

```css
--z-base: 0;
--z-sticky: 10;
--z-dropdown: 20;
--z-tooltip: 30;
--z-modal: 60;
--z-modal-content: 1;  /* relative inside modal overlay */
```

### 4.8 Motion

```css
--ease-default: ease;
--ease-out: ease-out;
--duration-fast: 140ms;
--duration-normal: 240ms;
```

---

## 5. Shared Reusable UI Primitives

These are the building blocks. New pages must use these (or approved variants) so the site stays consistent and safe.

| Primitive | Class(es) | Usage |
|-----------|-----------|--------|
| Page shell | `appShell` | Single main wrapper; width, padding, safe-area. |
| Section container | `ui-section-container` | Optional wrapper for a section; margin-bottom from token. |
| Panel | `panelCard` | Raised container; use with `sectionEyebrow` or `ui-panel-header` for title. |
| Panel header/body | `ui-panel-header`, `ui-panel-body` | Optional; for new markup. |
| Hero block | `heroCard` | Top hero (e.g. home ask). |
| Info block | `infoCard` | Small info card. |
| Action row | `actionRow`, `heroActions`, `ui-action-bar` | Row of buttons/actions. |
| Buttons | `runButton`, `clearButton`, `utilityButton`, `ctaButton`, `devDeckBtn` | Use existing; new buttons use tokens. |
| Pills/chips | `statusPill`, `statusPill--good|warn|neutral`, `tipPill` | Status and tags. |
| Empty state | `ui-empty-state` | Centered message when no data. |
| Loading state | `ui-loading-state` | Centered loading message/spinner. |
| Modal overlay | `ui-modal-overlay` | Full-screen overlay; z-index `var(--z-modal)`. |
| Modal shell | `ui-modal-shell` | Content box inside overlay. |
| Nav | `topNav`, `navLinks`, `navLink`, `brandLink` | Top nav; preserve structure. |

---

## 6. Page Isolation Strategy (Isolation Rules)

- **Watchlist / home (dashboard, 18080):** Pass B: add `<body class="pageBody pageBody--dashboard">` (or `pageBody--home` for home). All inline styles that are specific to that view must eventually be moved to selectors under `.pageBody--dashboard` (or a shared dashboard CSS file that uses tokens).
- **Library (18080):** Pass B: add `pageBody--library`. Scoped selectors for library grid, pager, tiles under `.pageBody--library`.
- **Card page (18765):** Already isolated: `pageBody--cardPage`. All card-detail-only styles under `.pageBody--cardPage` (e.g. `.pageBody--cardPage .cardHeader`).
- **Leader hub (18765):** Already isolated: `pageBody--leaderHub`. All leader-only styles under `.pageBody--leaderHub`.
- **Dev page (18765):** Already isolated: `pageBody--dev`. All dev-console-only styles under `.pageBody--dev`. Consider moving dev-only component classes (e.g. `devConsoleShell`) under `.pageBody--dev` in Pass B to reduce global leakage.
- **Deck builder / future pages:** New page = new key. Add `pageBody--deckBuilder` (or similar). All styles for that page in Layer 4 under that root. Do not add global selectors that only affect one page.

**Rule:** If a selector is intended for a single page, it MUST be scoped under the page root. No exceptions.

---

## 7. Regression Checklist for Any Future UI Task

Before merging UI changes, verify:

- [ ] **Page root:** New or touched pages have correct `pageBody pageBody--<key>` on `<body>`.
- [ ] **Scoping:** New/changed CSS for one page is under `.pageBody--<key>` (Layer 4). No new global selectors that affect only one page.
- [ ] **Tokens:** New values use design tokens (spacing, color, radius, shadow, z-index, motion) instead of magic numbers.
- [ ] **Primitives:** New blocks use shared primitives (panel, card, action row, chip, modal) where applicable.
- [ ] **18080:** If dashboard was changed: home, library, watchlist, sets still render; no horizontal overflow; mobile tap targets OK.
- [ ] **18765:** If Miru AI was changed: home, /dev, leader hub, card page still render; modal scroll locked when open; no overflow.
- [ ] **One Piece:** Theming remains subtle (~10–15%); no gimmicky or overwhelming changes.
- [ ] **Performance:** No heavy new assets or layout thrash; fast load and mobile usability preserved.

---

## 8. Pass A Plan (UI Safety Foundation)

Pass A is partially done for 18765. Complete and document only.

1. **Tokens (18765):** Ensure `tools/static/miru_ai.css` has full token set (colors, spacing, radius, shadows, borders, typography, z-index, motion). Already in place; add any missing (e.g. `--space-3xl`, `--text-2xl`) if needed.
2. **Layers:** Keep clear Layer 0–4 comments and convention block at top of `miru_ai.css`. No structural change.
3. **Primitives:** Ensure shared primitive classes exist: `ui-section-container`, `ui-panel-header`, `ui-panel-body`, `ui-action-bar`, `ui-empty-state`, `ui-loading-state`, `ui-modal-overlay`, `ui-modal-shell`. Already added; verify and document in UI-FOUNDATION.md.
4. **Page scoping:** All existing page-specific blocks remain under `.pageBody--dev`, `.pageBody--leaderHub`, `.pageBody--cardPage`. Document in this constitution.
5. **Dashboard (18080):** No change in Pass A. Only document that 18080 will get page roots and token alignment in Pass B.
6. **Constitution and checklist:** This document and the regression checklist are the deliverables. Optionally add a one-line comment in `miru_ai.css` pointing to `docs/UI-CONSTITUTION.md`.

---

## 9. Pass B Plan (Full-Site Migration / Refactor)

1. **Dashboard page roots:** In `dashboard/app.py`, add `<body class="pageBody pageBody--dashboard">` (and `pageBody--library` for library route if different template). Use a single root for dashboard if all views share one layout.
2. **Dashboard tokens:** Extract or duplicate token set into a small dashboard CSS block or file; replace inline magic numbers with token refs. Align color/spacing names with 18765 where possible.
3. **Dashboard primitives:** Introduce shared class names for dashboard panels/cards (e.g. `panelCard`, `appShell`) so both surfaces use the same primitive names. May require adding a shared minimal CSS file for dashboard that mirrors token + primitive patterns.
4. **Scope dev-only globals (18765):** Move selectors that only affect the dev page under `.pageBody--dev` (e.g. `.devConsoleShell` → `.pageBody--dev .devConsoleShell`). Reduces risk of leaking into new pages.
5. **Tighten shared selectors:** Where HTML allows, make shared component selectors more specific (e.g. `.panelCard .sectionEyebrow` instead of bare `.sectionEyebrow` in global blocks) so new pages don’t inherit unintended styles.
6. **New pages:** Any new route (e.g. deck builder) gets a new page root and a dedicated Layer 4 block. No new global page-specific rules.
7. **Modal scroll lock:** Ensure all modals use body class (e.g. `body.hasVoyageMapDialog { overflow: hidden; }`) and that no modal markup leaks outside page root.

---

## 10. Example Code Snippets

### 10.1 Token definitions (Layer 0)

```css
:root {
    /* Spacing */
    --space-md: 0.75rem;
    --space-lg: 1rem;
    /* Radius */
    --radius-md: 18px;
    /* Z-index */
    --z-modal: 60;
    --z-modal-content: 1;
    /* Motion */
    --duration-fast: 140ms;
}
```

### 10.2 Page root scoping (Layer 4)

```css
/* ----- Page: cardPage ----- */
.pageBody--cardPage .appShell {
    display: block;
}

.pageBody--cardPage .cardHeader {
    margin-bottom: var(--space-lg);
    padding: var(--space-xl);
}

.pageBody--cardPage .cardPageTitle {
    font-size: var(--text-xl);
    color: var(--text);
}
```

### 10.3 Page shell HTML structure

```html
<body class="pageBody pageBody--cardPage">
    <main class="appShell">
        <nav class="topNav" aria-label="Miru navigation">
            <!-- brand + nav links -->
        </nav>
        <div class="cardPageContent">
            <header class="cardHeader panelCard">...</header>
            <section class="cardImageSection panelCard">...</section>
        </div>
    </main>
</body>
```

### 10.4 Shared panel structure

```html
<section class="panelCard">
    <div class="sectionEyebrow">Section label</div>
    <h2 class="sectionCopy">Title</h2>
    <div class="panelCardBody">
        <p>Content.</p>
    </div>
</section>
```

Or with optional primitives:

```html
<section class="panelCard">
    <div class="ui-panel-header">Section label</div>
    <div class="ui-panel-body">Content.</div>
</section>
```

### 10.5 Sample isolated page stylesheet (Layer 4 block)

```css
/* ---------------------------------------------------------------------------
   Page: deckBuilder (future)
   All selectors scoped under .pageBody--deckBuilder.
   --------------------------------------------------------------------------- */

.pageBody--deckBuilder .appShell {
    max-width: min(100%, 900px);
}

.pageBody--deckBuilder .deckBuilderToolbar {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-md);
    margin-bottom: var(--space-lg);
}

.pageBody--deckBuilder .deckBuilderList {
    display: grid;
    gap: var(--space-sm);
}

.pageBody--deckBuilder .deckBuilderCard {
    padding: var(--space-md);
    border-radius: var(--radius-sm);
    border: var(--border-width) solid var(--stroke);
    background: var(--panel-alt);
}
```

---

*End of Project Miru UI Constitution. Implement Pass A first, then Pass B per plan above.*
