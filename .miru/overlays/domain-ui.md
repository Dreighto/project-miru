# Overlay — domain-ui

```
Overlay: domain-ui
Architecture: MIRU-INSTRUCTIONS-v2
Load when: touching frontend code (pm/, miru_ai/static/, templates), or building any user-facing UI.
Last reviewed: 2026-05-08
```

This overlay carries the craft guide trigger list. The actual craft guides
live in `docs/ui_ux/` and `docs/pm/`. This overlay tells you which ones to
read for the work you're doing — so you don't have to load the entire library
upfront.

---

## Craft Guides — load on demand

The repo has two craft-guide libraries at:

- `docs/ui_ux/` — universal frontend craft (applies to any Miru surface: PM, Dispatcher, Dev Review Hub, future work)
- `docs/pm/` — PM-specific craft (only applies to `pm/storefront/` work; layers on top of ui_ux)

Do not load the full library. Load on demand.

**Hard triggers — read the matching doc before writing code:**

- Building or changing any mobile / PWA behavior → read `docs/ui_ux/01_MOBILE_PWA.md`
- Wiring a gesture (swipe, long-press, drag, pinch) → read `docs/ui_ux/02_GESTURES.md` + `docs/pm/05_GESTURES_PM.md` if PM
- Adding a new screen / modal / sheet → read `docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md`
- Building a reusable component (button, input, chip, card tile) → read `docs/ui_ux/04_PRIMITIVES.md` + `docs/pm/02_PM_PRIMITIVES.md` if PM
- Accessibility work (focus, contrast, ARIA, keyboard, screen reader) → read `docs/ui_ux/05_ACCESSIBILITY.md`
- Performance work (card grids, images, animation, lists >50 items) → read `docs/ui_ux/06_PERFORMANCE.md`
- Adding a library / dependency → read `docs/ui_ux/09_TOOLING.md`

**PM-specific hard triggers:**

- Watchlist / meter / pricing UI → read `docs/pm/04_WATCHLIST_AND_METER.md`
- Tab landing page work (Home, Cards, Deck Builder, Leaders, Profile) → read `docs/pm/01_TAB_LANDINGS.md`
- Adding any Miru-generated output (insight, suggestion, ambient filter) → read `docs/pm/03_MIRU_LAYER.md`
- Writing copy for Miru or PM → read `docs/pm/00_PRINCIPLES.md` + `docs/pm/03_MIRU_LAYER.md`
- Before shipping any new PM feature → run the 10-question gut-check in `docs/pm/08_PM_ANTI_PATTERNS.md`

**Soft triggers — consult if relevant:**

- Visual / styling decision → `docs/pm/06_DESIGN_LANGUAGE.md`
- Card tile changes → `docs/pm/02_PM_PRIMITIVES.md`
- Understanding how PM differs from competitors → `docs/pm/07_OPTCG_STUDY.md`
- Designing a pattern from scratch → `docs/ui_ux/07_COMPETITIVE_STUDY.md`
- Pre-ship sanity check → `docs/ui_ux/08_ANTI_PATTERNS.md` + `docs/pm/08_PM_ANTI_PATTERNS.md`

**Skip entirely for:**
typo fixes, one-line style tweaks, bugfixes that don't change interaction model, backend-only work (routes, data, scrapers).

**When craft guides conflict with CLAUDE.md / operator directives:** operator directives win, always. Flag the conflict; don't silently override.
