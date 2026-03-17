# Project Miru UI Audit Report

**Date:** Audit-only pass against `docs/UI-CONSTITUTION.md`  
**Scope:** Worktree repo `C:\Users\andre\.codex\worktrees\0814\tcg-watcher`  
**No code was modified.** This is a discovery/reporting pass.

---

## 1. Summary

The audit found **constitution compliance gaps** (missing page roots on 18080, home/training/status page-specific selectors and dev-only selectors left global in 18765), **global CSS leak risks** (dozens of unscoped `.dev*` and home/training/status classes), **missing tokens** (`--space-3xl`, `--text-2xl`; many magic numbers in components), and **dashboard scoping gaps** (no `pageBody` on `<body>`, no route-based page roots). Duplicate or ad-hoc patterns exist that should map to constitution primitives; layout and spacing are inconsistent in places. Mobile risks are mostly mitigated (overflow-x, responsive breakpoints, modal scroll lock) but some fixed widths and percentage widths could cause edge-case overflow. **Pass A** should close token gaps, document convention, and optionally scope a small set of high-impact globals. **Pass B** should add dashboard page roots, migrate dashboard to tokens/primitives, scope all dev-only and home/training/status selectors under page roots, and tighten shared selectors.

---

## 2. Constitution Compliance Gaps

| Gap | Location | Detail |
|-----|----------|--------|
| **Missing page roots (18080)** | `dashboard/app.py` | `<body>` has no class. No `pageBody` or `pageBody--dashboard` / `pageBody--library`. Constitution requires every body to have exactly one page root modifier. |
| **Home-specific selectors global** | `tools/static/miru_ai.css` | `.homeAskCard`, `.homeAskIntro`, `.homeAskFieldBlock`, `.homeActionRow`, `.homeExamples`, `.homeQuickLinks`, `.homeExamples h2`, `.presetGrid--home`, `.homeModeInput`, `.heroCard--home`, `.heroCard--homeAsk`, `.overviewGrid--home` are global. They only apply when `page_key == "home"` (body is `pageBody--home`). Per constitution Layer 4, they must be under `.pageBody--home`. |
| **Training/status/ask-specific selectors global** | `miru_ai.css` | `.trainingProgressCard`, `.trainingProgressIntro`, `.trainingStatGrid`, `.trainingRingCard`, `.intelligenceProgressCard`, `.roadmapCard`, `.roadmapGrid`, `.roadmapBlock`, `.statusCard`, `.statusSpinner`, `.resultCard`, `.errorCard`, `.noticeCard`, `.statusInfoCard`, `.statusHeadingRow`, `.presetGrid`, `.formCard`, `.modeCard`, `.modeBody`, `.modeHint`, `.overviewGrid`, `.miniRouteCard`, `.miniRouteGrid` are used only on specific page_keys (training, status, ask, home). They should be scoped under `.pageBody--training`, `.pageBody--status`, `.pageBody--ask`, `.pageBody--home` as appropriate. |
| **Dev-only selectors global** | `miru_ai.css` | All `.dev*` classes (e.g. `.devConsoleShell`, `.devStatusStrip`, `.devControlDeck`, `.devVoyagePanel`, `.devHeroCard`, `.devVoyageMapDialog`, `.devActivityFeed`, `.devMiniHeader`, …) are global. They only appear when `page_key == "dev"`. Per constitution they must be under `.pageBody--dev`. |
| **Shared styles in wrong layer** | `miru_ai.css` | Layer 3 comment says "shared components" but many component rules are page-specific in effect (home, dev, training). De facto Layer 4 content is mixed into Layer 3. |
| **Magic values instead of tokens** | `miru_ai.css` | Numerous raw values: e.g. `0.72rem`, `0.9rem`, `0.92rem`, `1.05rem`, `0.48rem`, `0.56rem`, `0.68rem`, `0.86rem` (spacing); `0.96rem`, `0.94rem`, `0.78rem` (font-size); `1px solid rgba(...)` (borders); `rgba(120, 53, 15, 0.22)` (statusPill--warn); body background `#07040c`, `#0d0916`, `#140d21`; gradient stops and radii in dev/voyage panels. Constitution: use design tokens; avoid magic numbers. |
| **Missing tokens** | `miru_ai.css` `:root` | Constitution 4.2/4.6 mention `--space-3xl`, `--text-2xl`. These are not defined in current `:root`. |
| **Constitution not referenced in CSS** | `miru_ai.css` | Optional: add one-line comment pointing to `docs/UI-CONSTITUTION.md` (Pass A deliverable). |

---

## 3. Global CSS Risk Areas

| Risk | Selectors / area | Why it leaks |
|------|-------------------|--------------|
| **Generic class names** | `.panelCard`, `.heroCard`, `.infoCard`, `.topNav`, `.navLink`, `.sectionEyebrow`, `.actionRow`, `.statusPill` | Intended shared; but any new page that reuses these gets all current rules. If a rule is actually page-specific (e.g. `.panelCard` margin overrides only for dev), it should live under page root. |
| **Broad element selectors** | `body { background: ... }`, `html, body { margin: 0 }`, `button`, `input`, `a` in reset | Acceptable in Layer 1. No issue. |
| **Dev-only globals** | `.devConsoleShell`, `.devStatusStrip`, `.devControlDeck`, `.devControlDeckRow`, `.devVoyagePanel`, `.devVoyageMapDialog`, `.devVoyageMapDialogCard`, `.devHeroCard`, `.devActivityFeed`, `.devMiniHeader`, `.devEnvStrip`, `.devSurfaceLinks`, `.devSurfaceStatus`, all other `.dev*` | Only used in `page_key == "dev"`. If a new page (e.g. deck builder) adds a class that shares a prefix or name, or if dev markup is ever reused elsewhere, these could apply unintentionally. Must move under `.pageBody--dev`. |
| **Home/ask/training/status globals** | `.homeAskCard`, `.heroCard--home`, `.overviewGrid--home`, `.trainingProgressCard`, `.roadmapCard`, `.statusCard`, `.resultCard`, `.errorCard`, `.noticeCard`, `.formCard`, `.modeCard`, `.presetGrid`, `.miniRouteCard` | Only used on one page_key each. New pages could accidentally get similar class names (e.g. `.trainingCard` on another page) and inherit. Should be scoped. |
| **Modal/header/button rules** | `.devVoyageMapDialog`, `body.hasVoyageMapDialog`, `.topNav`, `.navLink`, `.runButton`, `.utilityButton` | Modal and body scroll lock are dev-specific; others are shared. Risk: adding another modal without a body class could miss scroll lock; dev modal is correctly isolated by `hasVoyageMapDialog`. |
| **Layout rules** | `.appShell` (width, padding) | Shared; OK. Page-specific overrides (e.g. `.pageBody--dev .appShell`) already exist. |

---

## 4. Missing Page Roots / Scoping Gaps

| Surface | Current | Constitution expectation |
|---------|---------|---------------------------|
| **Dashboard (18080)** | `<body>` with no class. Single wrapper `<div class="appFrame">`. Content varies by route (watchlist/home vs library). | Pass B: `<body class="pageBody pageBody--dashboard">` for main view; when `is_library_page` (route `/library`), use `pageBody--library` (or a second modifier) so library-specific styles can be scoped. |
| **Library (18080)** | Same HTML as dashboard; `is_library_page` only changes hero class (`brandHero--slim`, `brandHero--library`) and content. No dedicated body class. | Pass B: Add `pageBody--library` when `request.path.rstrip("/") == "/library"` so selectors like `.libraryPager`, `.libraryThumbWrap`, grid can live under `.pageBody--library`. |
| **Miru AI home (18765)** | Body has `pageBody--home` when `page_key == "home"`. CSS for home is not scoped: `.homeAskCard`, `.homeActionRow`, etc. are global. | Pass B: Scope all home-only selectors under `.pageBody--home`. |
| **Miru AI ask/training/status (18765)** | Body gets `pageBody--ask`, `pageBody--training`, `pageBody--status`. No CSS block targets these; many training/status/ask classes are global. | Pass B: Introduce Layer 4 blocks for `.pageBody--ask`, `.pageBody--training`, `.pageBody--status` and move the corresponding selectors under them. |
| **Leader hub / Card page (18765)** | Already scoped: `.pageBody--leaderHub`, `.pageBody--cardPage` with full blocks. | Compliant. |
| **Dev (18765)** | Partially scoped: a few overrides under `.pageBody--dev` (e.g. `.appShell`, `.topNav`, `.panelCard`, `.devConsoleShell`). Vast majority of `.dev*` rules are still global. | Pass B: Move every `.dev*` selector under `.pageBody--dev`. |

---

## 5. Duplicate Component Patterns

| Pattern | Current implementation | Constitution primitive | Gap |
|---------|------------------------|-------------------------|-----|
| **Panel** | `.panelCard` used widely; header via `.sectionEyebrow`. Some blocks use custom borders/backgrounds (e.g. `.roadmapBlock`, `.devVoyageStatCard`). | `panelCard`; optional `ui-panel-header`, `ui-panel-body` | `.roadmapBlock`, voyage/stat cards use ad-hoc panel-like styling (border, radius, padding) instead of extending panelCard or using tokens. |
| **Hero** | `.heroCard`, `.heroCard--home`, `.heroCard--homeAsk`; dashboard uses `.brandHero`, `.brandHero--slim`, `.brandHero--library` (different naming). | `heroCard` | Dashboard does not use `heroCard`; naming and structure differ. Pass B: align naming or map `.brandHero` to same primitive concept. |
| **Cards** | `.infoCard`, `.panelCard`, `.statusCard`, `.resultCard`, `.errorCard`, `.noticeCard`, `.trainingProgressCard`, `.intelligenceProgressCard`, `.roadmapCard`, `.devIssueCard`, etc. | `heroCard`, `panelCard`, `infoCard` | Many card-like blocks are custom-named and repeat similar styles (border, radius, shadow, padding). Should standardize on panelCard/infoCard + modifiers or tokens. |
| **Action row** | `.actionRow`, `.heroActions`, `.homeActionRow`, `.devControlDeckRow`, `.copyRow`, `.devControlRoomBar` | `actionRow`, `heroActions`, `ui-action-bar` | Multiple names for same pattern; gaps and padding are literal values. Map to `ui-action-bar` + tokens where possible. |
| **Pills/badges** | `.statusPill`, `.statusPill--good|warn|neutral`, `.tipPill`, `.devModeBadge`, `.devModeBadge--dryrun|sandbox|…` | `statusPill`, `tipPill`, `devModeBadge` / generic `chip` | Largely aligned; `statusPill--warn` uses magic `rgba(120, 53, 15, 0.22)` instead of token. |
| **Modal** | `.devVoyageMapDialog` (overlay + card); `.ui-modal-overlay`, `.ui-modal-shell` exist as optional primitives. | `ui-modal-overlay`, `ui-modal-shell`; page-specific shell OK | Current modal uses tokens for z-index. No duplicate modal pattern; optional primitives available for new modals. |
| **Empty/loading** | `.devActivityFeedEmpty`, `.devActivityFeedHint`; loading via `.statusCard` + `.statusSpinner`. No generic `.leaderHubEmpty` content class. | `ui-empty-state`, `ui-loading-state` | `.leaderHubEmpty` (leader hub) and loading states are ad-hoc. Pass B: use `ui-empty-state` / `ui-loading-state` where applicable. |

---

## 6. Layout Inconsistencies

| Area | Inconsistency |
|------|----------------|
| **Spacing** | Mix of `0.48rem`, `0.72rem`, `0.9rem`, `1rem`, `1.05rem`, `0.86rem`, `0.92rem`, `1.5rem` without consistent mapping to `--space-*`. Section margins and card padding vary. |
| **Radius** | Some blocks use `var(--radius-md)` / `var(--radius-lg)`; others use `4px`, `6px`, `8px`, `10px`, `1rem`, `calc(var(--radius-lg) + 2px)`. Not fully token-driven. |
| **Panel/card structure** | Some panels have `.sectionEyebrow` then content; others have custom headers (e.g. `.devCardHeader`, `.devActivityFeedTitle`). Card padding and borders vary (1px, different rgba strokes). |
| **Container widths** | `.appShell`: `min(100%, 760px)`. Dev/voyage panels and modal use different max-widths (e.g. 1100px for modal). Dashboard uses `.appFrame` with `min(1040px, 100%)`. Inconsistent max-width scale. |
| **Section padding** | Padding on panels/sections uses various rem values; not consistently `--space-*`. |
| **Typography** | Many font-size values (0.74rem, 0.78rem, 0.82rem, 0.85rem, 0.9rem, 0.96rem, 1rem) without mapping to `--text-xs` / `--text-sm` / `--text-base` / `--text-lg` / `--text-xl`. |

---

## 7. Mobile Risks

| Risk | Finding | Severity |
|------|---------|----------|
| **Horizontal overflow** | `body { overflow-x: hidden }` and `.appShell` width constraint reduce risk. Some inner elements use `overflow-x: auto` (e.g. voyage route strip) or fixed widths (e.g. `width: 132px`, `112px`, `72px`, `46px`). | Low–medium if viewport is narrow; dev/voyage UI has many fixed-size pieces. |
| **Fixed-width content** | Voyage sprites, icons, and some grids use pixel or rem widths. Could cause overflow in very small viewports. | Low; breakpoints at 720px, 560px, 420px adjust layout. |
| **Header/button row wrapping** | `.topNav`, `.navLinks`, `.heroActions`, `.devControlDeckRow` use flex + wrap. `.devDeckBtn.devControlButton` explicitly kept from full-width on mobile. | Mitigated. |
| **Modal scroll** | `body.hasVoyageMapDialog { overflow: hidden }` locks scroll when modal is open. Modal content can scroll internally. | OK. |
| **Sticky overlap** | `.topNav` has `z-index: 10` (matches `--z-sticky`). No conflicting sticky elements identified. | Low. |
| **Safe area** | `.appShell` uses `env(safe-area-inset-top)` and `env(safe-area-inset-bottom)` in padding. | Good. |

---

## 8. Pass A Completion Items

- Add missing tokens to `:root`: `--space-3xl: 2rem`, `--text-2xl: 1.5rem`.
- Add optional one-line comment in `miru_ai.css` pointing to `docs/UI-CONSTITUTION.md`.
- Verify and document in `UI-FOUNDATION.md` that shared primitives (`ui-section-container`, `ui-panel-header`, `ui-panel-body`, `ui-action-bar`, `ui-empty-state`, `ui-loading-state`, `ui-modal-overlay`, `ui-modal-shell`) exist and are to be used for new pages.
- Do **not** change dashboard (18080) in Pass A.
- Do **not** move existing global selectors in Pass A (defer to Pass B).

---

## 9. Pass B Migration Items

- **Dashboard (18080):** Add `<body class="pageBody pageBody--dashboard">`. When route is `/library`, add `pageBody--library` (or equivalent) so library-specific CSS can be scoped.
- **Dashboard tokens/primitives:** Introduce a token set (inline or small CSS file) aligned with 18765; replace magic numbers in dashboard inline styles; use shared primitive names (e.g. `appShell`, `panelCard`) where feasible.
- **18765 – Scope dev-only:** Move every `.dev*` selector under `.pageBody--dev` (e.g. `.devConsoleShell` → `.pageBody--dev .devConsoleShell`). Leave shared primitives (e.g. `.statusPill` when used inside dev) as-is or scope only the wrapper.
- **18765 – Scope home:** Move all home-only selectors (`.homeAskCard`, `.homeActionRow`, `.heroCard--home`, `.overviewGrid--home`, `.presetGrid--home`, etc.) under `.pageBody--home`.
- **18765 – Scope ask/training/status:** Introduce Layer 4 blocks for `.pageBody--ask`, `.pageBody--training`, `.pageBody--status` and move the corresponding selectors (e.g. `.formCard`, `.modeCard`, `.trainingProgressCard`, `.roadmapCard`, `.statusCard`, `.resultCard`, `.errorCard`, `.noticeCard`) under the appropriate root.
- **Replace magic numbers with tokens:** In Layer 3 and Layer 4, replace spacing, font-size, radius, and border values with `var(--space-*)`, `var(--text-*)`, `var(--radius-*)`, `var(--border-width)`, and semantic color tokens where they exist.
- **Tighten shared selectors:** Where HTML allows, make shared rules more specific (e.g. `.panelCard .sectionEyebrow` instead of bare `.sectionEyebrow` in global blocks) to avoid unintended application on new pages.
- **Map duplicate patterns to primitives:** Use `panelCard` / `infoCard` + modifiers for card-like blocks; use `ui-action-bar` and tokens for action rows; use `ui-empty-state` and `ui-loading-state` for empty and loading UIs where applicable.
- **Modal scroll lock:** Confirm all future modals follow the same pattern (body class + overflow hidden). Current dev modal is correct.

---

## 10. Suggested Implementation Order

1. **Pass A (no visual/behavior change)**  
   - Add `--space-3xl`, `--text-2xl` to `:root`.  
   - Add constitution reference comment in `miru_ai.css`.  
   - Update `UI-FOUNDATION.md` with primitive list and “use under page root” note.

2. **Pass B – 18765 scoping (safest first)**  
   - Add Layer 4 block for `.pageBody--dev` and move all `.dev*` selectors under it (one logical block at a time; test after each batch).  
   - Add Layer 4 block for `.pageBody--home` and move home-only selectors.  
   - Add Layer 4 blocks for `.pageBody--ask`, `.pageBody--training`, `.pageBody--status` and move corresponding selectors.

3. **Pass B – Tokens**  
   - Replace magic numbers in Layer 3 and Layer 4 with tokens (spacing, typography, radius, borders, colors) in batches; regression-test after each.

4. **Pass B – Dashboard**  
   - Add body class and route-based `pageBody--library` (or equivalent).  
   - Introduce tokens and shared primitive names; refactor inline CSS incrementally.

5. **Pass B – Primitives and cleanup**  
   - Use `ui-empty-state` / `ui-loading-state` where it fits.  
   - Tighten shared selectors (e.g. `.panelCard .sectionEyebrow`).  
   - Align duplicate card/panel patterns to constitution primitives where it doesn’t require large HTML changes.

---

## 11. Risk Notes

- **Moving dev/home/training/status selectors under page roots:** Specificity and order stay the same if the only change is adding `.pageBody--dev` (or other) as ancestor. Visual regression risk is low if no selector is renamed or removed. Test all page_keys (home, ask, dossiers, gaps, training, dev, status) and leader hub / card page after each batch.
- **Dashboard body class:** Adding `pageBody pageBody--dashboard` (and `pageBody--library`) with no CSS change is safe. Adding new scoped CSS later must only target these roots so existing layout is unchanged until intended.
- **Token replacement:** Replacing a raw value with a token that has the same computed value is safe (e.g. `0.75rem` → `var(--space-md)` where `--space-md: 0.75rem`). If token value is changed later, all usages change; document token contracts.
- **Tightening shared selectors:** Changing `.sectionEyebrow` to `.panelCard .sectionEyebrow` can remove styles from any `.sectionEyebrow` not inside `.panelCard`. Audit HTML for standalone `.sectionEyebrow` before applying.
- **Audit validation:** Audit was read-only. 18765/dev returned HTTP 200 when checked. 18080 was not confirmed (timeout or not running). No code was modified; no regression introduced by this audit.

---

*End of UI Audit Report.*
