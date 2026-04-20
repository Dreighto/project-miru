# 09 — Tooling

**Applies to:** decisions about adding, removing, or replacing a frontend library or tool.
**Read this when:** you want to install a new npm package; you're evaluating a library you saw in a tutorial; you're wondering why we don't use X.
**Skip this when:** you're using tools that are already in the stack as documented.
**Length:** ~8 pages.
**Related docs:** [06_PERFORMANCE.md](06_PERFORMANCE.md) for bundle discipline, [04_PRIMITIVES.md](04_PRIMITIVES.md) for what we build ourselves.

---

## The rule

Every dependency is a liability: bundle bytes, security surface, long-term maintenance, transitive updates. Adding one must pay for itself.

**Before any `npm install`:**

1. **Can a web platform API do this?** Intersection Observer, Mutation Observer, View Transitions, Clipboard, Notification — all native, zero bundle.
2. **Can we write it in < 100 lines?** Small utilities don't deserve a dependency.
3. **Will we use > 20% of the library's surface?** If we use one function from a 40KB lib, extract that function.
4. **Can we lazy-load it on the route that needs it?** If yes, the cost is bounded.
5. **Is it actively maintained?** Commits in the last 12 months; responsive issues; compatible with our stack's current major versions.

Fail any of 1–5 → reject, propose alternative, or defer.

---

## Approved stack

The tools we've committed to. If you're touching these areas, use these tools.

### Framework: SvelteKit 2 + Svelte 5

- **Why:** Svelte 5's fine-grained reactivity (`$state`, `$derived`, `$effect`) beats React/Vue for our mobile performance targets. Runtime overhead is near zero. SvelteKit's file-based routing and load functions are pragmatic and fast.
- **Version:** Svelte ^5.0, SvelteKit ^2.0. Update when a patch lands; major upgrades via proposal.
- **Link:** [svelte.dev](https://svelte.dev/docs/svelte/overview), [kit.svelte.dev](https://kit.svelte.dev/docs)

### Styling: Tailwind CSS v4

- **Why:** utility-first matches our primitive philosophy. Tailwind v4 is CSS-first (`@theme` blocks, no PostCSS config required), resolves in < 20ms on large projects, and the `@reference` / `@apply` escape hatch is clean.
- **Version:** ^4.0.
- **Link:** [tailwindcss.com/blog/tailwindcss-v4](https://tailwindcss.com/blog/tailwindcss-v4)
- **Notes:** our theme tokens live in `pm/storefront/src/app.css` in a single `@theme` block. Never hardcode colors outside that block.

### Icons: lucide-svelte

- **Why:** 1,500+ icons, consistent stroke, Svelte-native, tree-shakeable. Alternative (Heroicons, Tabler, Phosphor) work — lucide won on breadth + consistency.
- **Size:** ~1KB per icon, tree-shaken.
- **Link:** [lucide.dev/guide/packages/lucide-svelte](https://lucide.dev/guide/packages/lucide-svelte)
- **Rule:** do not mix icon libraries. One style, one stroke, one family.

### Image pipeline: `@sveltejs/enhanced-img`

- **Why:** first-party, handles AVIF/WebP/JPEG, generates `srcset`, sets intrinsic dimensions, lazy by default.
- **Size:** build-time, zero runtime cost.
- **Link:** [kit.svelte.dev/docs/images](https://kit.svelte.dev/docs/images)
- **Rule:** every static image through this plugin. API-sourced images go through a separate CDN (imgproxy / Cloudflare Images) that does the same transformations.

### Core Web Vitals: `web-vitals`

- **Why:** official Google library, ~2KB, accurate INP/LCP/CLS measurement.
- **Link:** [github.com/GoogleChrome/web-vitals](https://github.com/GoogleChrome/web-vitals)

---

## Install (use these today)

### Virtualization: **virtua**

- **Why:** smallest (~3KB), supports grid layouts (rare in virtual-scroll libs), first-class Svelte 5 support.
- **Size:** 3KB gzipped.
- **Link:** [github.com/inokawa/virtua](https://github.com/inokawa/virtua), [virtua.vercel.app](https://virtua.vercel.app/)
- **When to use:** card grids > 100 items, long vertical lists. See [06_PERFORMANCE.md §Virtualization](06_PERFORMANCE.md#card-grids-virtualization).
- **Rejected alternatives:**
  - **@tanstack/svelte-virtual** (12KB, headless) — more flexible but heavier. Keep in mind for when we need fully-custom virtual behavior.
  - **svelte-virtual-list** (abandoned since 2023) — no.
  - **svelte-window** (2KB but only vertical, no grid) — fine if we never need a grid, but virtua's grid support future-proofs.

### Fuzzy search: **Fuse.js**

- **Why:** 24KB, well-tested, does what we need (fuzzy card name search), zero maintenance burden.
- **Size:** 24KB gzipped.
- **Link:** [fusejs.io](https://www.fusejs.io/)
- **When to use:** local fuzzy search on <5,000 items. Card search on a loaded set.
- **Lazy load:** yes — only on routes that search.
- **Rejected alternatives:**
  - **Orama** (42KB gzipped, full-text search with typo tolerance) — bigger than we need. Reconsider if we need server-grade search.
  - **MiniSearch** (29KB, supports prefix + fuzzy + filters) — close call with Fuse. Fuse wins on simpler API for our use case.
  - **uFuzzy** (3KB, fast but exact-match biased) — good for palettes, too strict for "Luffy" → "Monkey D. Luffy."
- **Source:** [microsoft/vscode decision thread](https://github.com/microsoft/vscode/issues/106096) for fuzzy-search tradeoffs in a similar constraint.

### Sheet / Modal: **custom, using `<dialog>`**

- **Why:** native `<dialog>` is sufficient. No library.
- **See:** [03_SUB_PAGE_ARCHITECTURE.md §`<dialog>` element](03_SUB_PAGE_ARCHITECTURE.md#-element).
- **Rejected alternatives:**
  - **vaul** — React only.
  - **bits-ui / shadcn-svelte** — adds 30KB for shapes we can build in 200 lines. Reconsider if we need 10+ pre-built surfaces.

### Date handling: **Temporal polyfill** or native

- **Why:** most date arithmetic we do is "hours/days ago" — a 50-line helper covers it. For more, the [Temporal API polyfill](https://github.com/tc39/proposal-temporal) is the modern answer.
- **Rejected alternatives:**
  - **moment.js** (deprecated, 72KB) — no.
  - **date-fns** (tree-shakable, but still 15–30KB in use) — reconsider if we find ourselves re-implementing formatters.
  - **day.js** (2KB, moment-compatible API) — fine as a fallback before Temporal is supported widely.

### Form validation: **custom or Zod**

- **Why:** Zod (8KB core, schema-first) works if we're validating on both client and server. For simple one-field validations, inline logic is fine.
- **Link:** [zod.dev](https://zod.dev)
- **When:** any form with 3+ fields or cross-field rules.
- **Rejected alternatives:**
  - **Yup** — Zod is more modern and more TS-friendly.
  - **valibot** (2KB, similar API) — interesting, keep an eye on it. If we need Zod and want the smaller bundle, swap.

---

## Reject (not in scope, don't install)

### UI library kits

- **shadcn-svelte / bits-ui** — great ecosystem, but their primitives expect certain patterns we've already spec'd differently ([04_PRIMITIVES.md](04_PRIMITIVES.md)). Writing our own is cheaper than fighting their conventions.
- **Skeleton (svelte-skeleton-ui)** — opinionated Tailwind + Svelte component set. Good for rapid MVPs. Not for a design-system-native product.
- **Carbon Components Svelte** — IBM's design system. Big, opinionated, not ours.

### CSS-in-JS

- **styled-components, emotion, stitches** — these solve React's styling problem. Svelte's scoped styles already solve it without runtime cost. Don't.
- **Panda CSS, Stylex** — interesting at scale. Overkill for us. Revisit only if Tailwind runs out of room.

### State management

- **Redux / Zustand / Jotai / Recoil** — React. Not ours.
- **XState** — powerful for complex state machines. Currently overkill. If we build a complex workflow (e.g. multi-step deck drafting), revisit.
- **Svelte stores (`writable`, `readable`)** — use these when shared state across components is needed. Svelte 5 runes often obsolete stores.

### Animation

- **Framer Motion (React)** — no.
- **Motion One** — web-standards animation library (~4KB). Keep in mind if we outgrow CSS transitions. Not in today.
- **GSAP** — powerful, heavy, commercial licensing. Never for product UI. Only for marketing pages if at all.

### Data fetching

- **TanStack Query / SWR** — cache-first fetching libs. SvelteKit's load + `invalidate` handles our patterns; adding a cache library is redundant. Revisit if we need long-lived cross-route caches.
- **Apollo** — GraphQL client. We're REST; no.

### Analytics

- **Segment** — 50KB+ runtime overhead; paid. Reject.
- **Mixpanel** — 30KB+ runtime; paid. Reject for now.
- **Google Analytics (GA4)** — free, but privacy/consent overhead and bundle cost. If we need analytics, prefer server-side event tracking (Plausible self-hosted, or custom endpoint).

---

## Defer (maybe later, not now)

These might become right over time. Currently we don't need them.

### Motion One

- **Why maybe:** 4KB web-standards animation API, more powerful than CSS for sequenced + interactive motion.
- **Trigger to revisit:** when we build interactive micro-interactions (drag + snap, swipe + reveal with custom easing) that CSS transitions can't express cleanly.
- **Link:** [motion.dev](https://motion.dev/)

### Dexie.js (IndexedDB wrapper)

- **Why maybe:** we currently use localStorage for the watchlist and deck drafts (small, synchronous). When we grow beyond ~1MB or need indexed queries client-side (offline deck search), Dexie (~20KB) becomes compelling.
- **Trigger to revisit:** when localStorage usage on first-visit exceeds 500KB or we add offline-first sync.
- **Link:** [dexie.org](https://dexie.org/)

### Workbox

- **Why maybe:** service-worker framework. Currently we use SvelteKit's built-in service worker hooks. When offline strategy grows complex (multi-route caching, background sync), Workbox's precaching is well-tested.
- **Trigger to revisit:** when we need full offline-first for more than just static assets.
- **Link:** [developer.chrome.com/docs/workbox](https://developer.chrome.com/docs/workbox)

### Histoire / Storybook

- **Why maybe:** a visual primitive catalog ([04_PRIMITIVES.md](04_PRIMITIVES.md) says stories are mandatory). Histoire is Svelte-native; Storybook is bigger but cross-framework.
- **Trigger to revisit:** when the primitive library grows beyond ~10 components and we need a design review surface.
- **Links:** [histoire.dev](https://histoire.dev/), [storybook.js.org](https://storybook.js.org/)

### Playwright (UI tests)

- **Why maybe:** end-to-end browser tests including gesture simulation. Useful once we have stable IA.
- **Trigger:** after v1 launch, when we need regression tests.
- **Link:** [playwright.dev](https://playwright.dev/)

### Vitest (unit tests)

- **Status:** actually we should install this now for primitive logic tests. Bare-minimum viable — mark as pending for the primitive library rollout.
- **Link:** [vitest.dev](https://vitest.dev/)

### enhanced-img alternatives (if Svelte's plugin hits limits)

- **unpic** — image-CDN-agnostic image component. Works across Cloudinary, imgix, Contentful, etc. Worth considering for API-sourced card images.
- **Trigger to revisit:** when `@sveltejs/enhanced-img` can't handle our external image sourcing cleanly.
- **Link:** [unpic.pics/img/svelte](https://unpic.pics/img/svelte/)

---

## Dev tooling

### Vite

- **Shipped with SvelteKit.** Fast HMR, fast builds. Don't change.

### TypeScript

- **Strict mode.** `"strict": true` in `tsconfig.json`. Non-negotiable. Adds zero runtime, catches a huge class of bugs at edit time.

### ESLint + Prettier

- **ESLint:** `eslint-config-svelte` + `typescript-eslint` rules. Enforce in CI.
- **Prettier:** single source of truth for formatting. Config committed.
- **Rule:** no prettier-ignore comments without justification.

### Editor: VS Code with Svelte for VS Code extension

- **Extensions:** Svelte for VS Code, Tailwind CSS IntelliSense, ESLint, Prettier, Error Lens.
- **Settings:** format on save, organize imports on save.

### Lighthouse CI

- **Install when CI is set up.** Blocks PRs that regress Core Web Vitals > 10%.
- **Link:** [github.com/GoogleChrome/lighthouse-ci](https://github.com/GoogleChrome/lighthouse-ci)

---

## MCP & AI-agent tooling (this repo)

The repo uses Claude Code / MCP tools for various tasks. For UI-related work:

- **Claude Preview MCP** — the preview tools (`preview_start`, `preview_click`, `preview_snapshot`, `preview_screenshot`) are used to verify UI changes in a browser. See the `preview_tools` section of the repo's system prompt.
- **shadcn MCP** — we don't currently use shadcn components in PM, but the MCP is available if we ever consider a specific shadcn primitive for reference.
- **Playwright MCP** — for browser automation in test scenarios.

These aren't npm packages; they're available in-session via `mcp__*` tool names.

---

## The "how to evaluate a new tool" checklist

When someone (a worker, a tutorial, a Hacker News post) suggests adding a library, run:

1. **What problem does it solve?** Write it in one sentence.
2. **Does a web API solve the same problem?** Yes → reject, use the API.
3. **Can we solve it in < 100 lines?** Yes → reject, write it.
4. **What's its size?** gzipped. > 10KB → needs a real justification.
5. **What's its maintenance health?** GitHub stars are a lie; look at commit frequency, open issue count, last merged PR.
6. **Is it compatible with Svelte 5 + SvelteKit 2 + Tailwind 4?** Peer deps version range.
7. **What's the transitive dependency count?** A 2KB library that drags in 200KB of deps is not a 2KB library.
8. **What's the a11y story?** Screen reader tested? `aria-*` defaults? Do we have to wrap it to make it accessible?
9. **Is there a smaller alternative that covers 80% of the need?** Often yes.
10. **Can we lazy-load it?** If yes, the cost is local. If no, it's global.

If you can't confidently answer all ten for a library, you don't know enough about it to install it.

---

## Bundle watchdog

We currently have a hard budget (per [06_PERFORMANCE.md §Targets](06_PERFORMANCE.md#the-targets)):

- **Initial route JS bundle ≤ 100 KB gzipped.**
- **Total JS budget ≤ 250 KB gzipped across lazy chunks.**

Check the budget on any PR that adds a dep:

```bash
# After build
bun run build
# Check bundle output
ls -lh .svelte-kit/output/client/_app/immutable/chunks/
```

Or use `rollup-plugin-visualizer`:

```bash
bun add -d rollup-plugin-visualizer
# Add to vite.config.js, rebuild, open stats.html
```

If a PR busts the budget, either the dep is too big or we have a budget discussion — not a silent creep.

---

## Package manager: bun

- **Why:** fast install, built-in TypeScript, compatible with npm registry. Used in this repo.
- **Alternative:** npm, pnpm. Bun is the current default; don't switch without a reason.
- **Rule:** don't commit both `bun.lockb` and `package-lock.json`. Pick one. (We're on `bun.lockb`.)

---

## When to write a library ourselves

Build, don't buy, when:

- **The problem is small.** < 100 lines.
- **The problem is specific.** Our domain has rules no library knows (TCG card grid with variant-swipe, deck validator with leader-color rules).
- **The dependency would lock us in.** Replacing a library later is expensive.
- **The library adds risk.** Security surface, auth-adjacent, payment-adjacent.

Buy, don't build, when:

- **The problem is large.** Virtualization, fuzzy search, form validation at scale.
- **The problem is solved.** We won't outperform battle-tested libraries by writing our own.
- **The maintenance cost is high.** Date/time, internationalization, accessibility primitives.

The line is judgment. When in doubt, start by writing a 100-line version and see if it's enough. Most of the time it is.
