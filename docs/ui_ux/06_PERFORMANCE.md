# 06 — Performance

**Applies to:** any change touching lists longer than ~50 items, card grids, images, animation, route loading, or anywhere the app has felt slow.
**Read this when:** INP budget is at risk; a card grid is janking; a page feels slow on a mid-range Android; you're adding a third-party script.
**Skip this when:** one-off copy changes, backend-only tasks.
**Length:** ~10 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [04_PRIMITIVES.md](04_PRIMITIVES.md), [09_TOOLING.md](09_TOOLING.md).

---

## The targets

Core Web Vitals, measured in the field on real users (not lab):

| Metric | Good | Needs work | Poor |
|---|---|---|---|
| **INP** (Interaction to Next Paint) | ≤ 200 ms | 200–500 ms | > 500 ms |
| **LCP** (Largest Contentful Paint) | ≤ 2.5 s | 2.5–4.0 s | > 4.0 s |
| **CLS** (Cumulative Layout Shift) | ≤ 0.1 | 0.1–0.25 | > 0.25 |
| **FCP** (First Contentful Paint) | ≤ 1.8 s | 1.8–3.0 s | > 3.0 s |
| **TTFB** (Time to First Byte) | ≤ 0.8 s | 0.8–1.8 s | > 1.8 s |

Budgets for PM (the p75 we measure on real devices):

- **INP ≤ 200 ms** — tap-to-feedback. This is the north star for mobile feel.
- **LCP ≤ 2.5 s** — the hero image or first card grid renders fast.
- **CLS ≤ 0.05** — stricter than Google's AA — we don't tolerate layout shift; we always reserve space.
- **JS bundle per route ≤ 100 KB gzipped** — what we deliver on initial load.
- **Total JS budget ≤ 250 KB gzipped** — across all routes and lazy chunks.
- **Image budget per screen ≤ 400 KB** — sum of card images visible on first paint.

Source: [web.dev — Core Web Vitals](https://web.dev/articles/vitals), [web.dev — INP is the new metric](https://web.dev/articles/inp), [Chrome Developers — performance budgets](https://developer.chrome.com/docs/devtools/performance/).

---

## INP: the feel metric

INP replaced FID in March 2024 as the interaction metric in Core Web Vitals. It measures the full latency from user input to the next paint — not just the first input, every input, worst-case. Source: [web.dev — Interaction to Next Paint (INP)](https://web.dev/articles/inp).

An INP over 200ms is what users feel as "sluggish." Over 500ms is what gets a 1-star review.

### What makes INP bad

1. **Long tasks on the main thread.** Any task > 50ms blocks interactions.
2. **Heavy render on input.** If tapping a card re-renders the whole page, INP suffers.
3. **Synchronous layout-thrashing in handlers.** `el.offsetHeight` after setting `el.style.height` forces sync layout — 10ms each time, compounds.
4. **Large component trees re-rendering on state change.** Svelte 5 runes are better than React at this, but it's still possible to write an `$effect` that pulls in too much downstream.
5. **Third-party scripts.** Analytics, a/b testing, chat widgets — each can take main-thread time.

### What makes INP good

1. **Small handlers.** Do the minimum in the sync click handler; defer the rest.
2. **`scheduler.yield()` or `requestAnimationFrame` for long work.** Split work over multiple frames.
3. **Avoid layout thrashing.** Batch reads before writes. [web.dev — Avoid large, complex layouts](https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing).
4. **Optimistic UI.** Change the UI immediately on tap; let the network request resolve underneath.
5. **CSS containment.** `contain: content` or `contain: strict` on card tiles isolates their layout cost ([MDN — CSS contain](https://developer.mozilla.org/en-US/docs/Web/CSS/contain)).
6. **Virtualization** (see below) for long lists.

---

## Card grids: virtualization

A single One Piece set has ~120 cards. Rendering 120 card-tile components with images is fine on a desktop and usually fine on a modern phone — **but** when the user searches across multiple sets (500+ results) or scrolls a mega-list, we virtualize.

### When to virtualize

- Lists > 100 items.
- Any list where the sum of DOM nodes exceeds ~5,000.
- Any list where the per-item DOM is heavy (images, multiple interactive elements, nested components).

Below these thresholds, virtualization costs more than it saves — the abstraction tax isn't worth it.

### Which library

See [09_TOOLING.md](09_TOOLING.md) for the full comparison. Short version:

- **virtua** (~3KB, supports grid layouts, first-class Svelte 5 support) — our default. [github.com/inokawa/virtua](https://github.com/inokawa/virtua).
- **@tanstack/svelte-virtual** (~12KB, headless, more control) — use when virtua's grid doesn't fit a bespoke layout. [tanstack.com/virtual](https://tanstack.com/virtual/latest).

### Virtualization checklist

- **Item size known.** If items are dynamic height, use `estimateSize` + measurement. Variable-height virtualization is harder — test scrolling past unmeasured items.
- **Scroll restoration.** If the user navigates away and back, scroll position should restore. SvelteKit handles this if you let it; don't override.
- **Keys are stable.** Using array index as key kills the rendering benefit — a new item at the top re-keys everything.
- **Focus and selection survive scroll.** When a focused row is virtualized away, focus doesn't jump to the next rendered row (screen-reader confusion). Either pin the focus or forward it cleanly.
- **Image sizes reserved.** Every card tile must reserve its image's dimensions (see CLS below).

### Virtualization anti-patterns

- **Virtualizing a 20-item list.** Shipping a dependency and abstraction for a list that fits on screen is pure overhead.
- **Virtualizing a table where some rows expand.** Expanding breaks the item-size contract. Either the expanded row counts as a full separate item, or use a non-virtualized list.
- **Virtualizing an accessibility tree.** Screen readers can't navigate virtualized-offscreen content. Use `aria-setsize` and `aria-posinset` on rendered rows so screen readers know there's more.

---

## Images

Card images are the heaviest single thing PM ships. A single card image from the official source is typically 200–500KB PNG. A page of 60 cards × 300KB = 18MB. We can't ship that.

### Format

- **AVIF first, fallback to WebP, fallback to JPEG.** AVIF is ~50% smaller than JPEG at equivalent quality; WebP is ~30% smaller. Both are supported on 95%+ of our traffic. [caniuse — avif](https://caniuse.com/avif), [caniuse — webp](https://caniuse.com/webp).
- **Use `<picture>`** with `<source type="image/avif">` + `<source type="image/webp">` + `<img src=".jpg">`.

### Sizing

- **Serve the size you need, not the size you have.** A card tile is 160×224px at 2× on the grid. Serve a 320×448 image, not the 1500×2100 master.
- **`srcset` + `sizes`** for responsive images. `srcset="card-160.webp 160w, card-320.webp 320w, card-480.webp 480w"` + `sizes="(max-width: 400px) 160px, 320px"`.
- **Aspect ratio reserved.** `<img width="160" height="224">` attributes (not CSS) so the browser reserves space before load, preventing CLS. [web.dev — Optimize CLS](https://web.dev/articles/optimize-cls).

### Lazy load

`loading="lazy"` on every image below the fold. Native, free, well-supported. [MDN — loading attribute](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/img#loading).

The first screenful of cards (above the fold) get `loading="eager"` and high `fetchpriority="high"`. Every other card is lazy.

### Decode async

`decoding="async"` on all images. Tells the browser to decode off the main thread. On a grid of 60 cards, this alone saves ~120ms on mid-range phones. [MDN — decoding attribute](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/img#decoding).

### `fetchpriority`

`fetchpriority="high"` on the LCP image (the largest image above the fold). `fetchpriority="low"` on cards that are rendered but below the fold (they'll eventually be visible, but let critical resources load first). [web.dev — Fetch Priority](https://web.dev/articles/fetch-priority).

### Use SvelteKit `enhanced-img`

[`@sveltejs/enhanced-img`](https://kit.svelte.dev/docs/images) is first-party. It:

- Generates AVIF, WebP, JPEG variants at build time.
- Emits `srcset` and `sizes`.
- Sets `width` and `height` on `<img>` automatically.
- Adds `loading="lazy"` and `decoding="async"` by default.

This is the default tool for any non-user-uploaded image. For user uploads (or API-sourced card images), run them through a dedicated image CDN (Imgix, Cloudinary, or a self-hosted imgproxy) with the same transformations.

### CDN-transformed images

Card images come from third-party sources (TCGPlayer, official sites). Don't proxy their CDN URLs directly — you lose control over sizing, format, caching, and their CDN may throttle you. Set up an image CDN (self-hosted [imgproxy](https://imgproxy.net/) or [thumbor](https://www.thumbor.org/), or managed Cloudflare Images) and proxy through it.

---

## Animation

The GPU is cheap; the main thread is expensive. If your animation hits the CPU/main thread, it will jank on mid-range Android.

### Only animate compositor-friendly properties

- `transform` (translate, scale, rotate) — GPU-accelerated.
- `opacity` — GPU-accelerated.
- `filter` — GPU-accelerated.
- `clip-path` — GPU-accelerated on most GPUs.

### Do not animate

- `width`, `height` — triggers layout on every frame.
- `top`, `left`, `right`, `bottom` — triggers layout.
- `margin`, `padding` — triggers layout.
- Background gradients — triggers paint.
- Box-shadow — triggers paint.

If you must animate shadow (e.g. card-tile lift on hover), animate `filter: drop-shadow()` instead — GPU-accelerated, visually equivalent in most cases. [web.dev — Animations performance](https://web.dev/articles/animations-guide).

### `will-change` is a precision instrument

Adding `will-change: transform` hints the browser to promote an element to its own layer. This is good when you're about to animate, and bad if you leave it on — each layer costs GPU memory.

**Rule:** add `will-change` on hover/focus/interaction-start, remove on interaction-end. Or don't use it at all and let the browser's heuristic handle it. [MDN — will-change](https://developer.mozilla.org/en-US/docs/Web/CSS/will-change).

### Frame budget: 16ms (60fps) or 8ms (120fps)

iPhones since iPhone 13 Pro support 120Hz. iPads Pro 11" 4th-gen and up, too. Samsung's S-series has had 120Hz since the S20. If you assume 60Hz and ship heavy JS in a tight interaction, 120Hz users get the worst feel — the display is promising twice the responsiveness and you're delivering half.

Budget 8ms per frame for animations you expect to hit 120Hz. It forces discipline.

### Spring physics, sparingly

Spring animations (react-spring, Svelte Motion) feel better for drag release and interactive motion. For simple state changes (a modal opening), CSS `transition: transform 250ms ease-out` is fine — and free.

Don't reach for a spring library unless you need the physics. The pattern "I'll use springs for everything to feel premium" produces an interface that feels squishy, not premium.

---

## CLS (layout shift)

CLS is the metric that catches everything that moves around unexpectedly after load.

### Reserve space

- Every `<img>` has `width` and `height`.
- Every ad/embed has a reserved container.
- Every font has `font-display: swap` **with a `size-adjust` override** to match the web font's metrics to the fallback ([MDN — size-adjust](https://developer.mozilla.org/en-US/docs/Web/CSS/@font-face/size-adjust)). Without this, fonts swap and text reflows.
- Every `<video>` has `poster` + `width` + `height`.

### Insert content above existing content carefully

If you insert a banner at the top of the page after load (error message, promotion, cookie notice), every existing element shifts down. CLS goes up. User is angry because they just tapped where a button was, and now it's somewhere else.

**Fix:**

1. Never insert above content the user is looking at.
2. If you must (e.g. offline notice), push in from the bottom or fade in in place.
3. If a banner might appear, reserve its space on initial render.

### Don't rely on the browser to find this

Test by running Lighthouse on every PR that changes layout. CLS regressions are sneaky; humans miss them.

---

## Route loading

SvelteKit route loading is fast by default. Don't undo it.

### Code-split routes naturally

Each `+page.svelte` is its own chunk. Don't import `Cards` into `Home` — that bundles them together. Use `<a href="/cards">` (SvelteKit prefetches on hover/focus/viewport intersection).

### Prefetch

`<a data-sveltekit-preload-data="hover">` prefetches data on hover/touch start. `data-sveltekit-preload-code="viewport"` prefetches the JS when the link enters viewport. Default behavior is sane; customize only when you need to. [SvelteKit — Link options](https://kit.svelte.dev/docs/link-options).

### Don't block rendering on a network request

Use `load` functions with streaming responses where possible. Show UI for things you have, defer things you don't:

```typescript
export const load = ({ params, fetch }) => ({
  card: fetch(`/api/cards/${params.code}`).then(r => r.json()), // streamed
  relatedCards: fetch(`/api/related/${params.code}`).then(r => r.json()) // streamed
});
```

In the component, `{#await card then card}...{/await}`. The `card` block renders as soon as it's ready; `relatedCards` doesn't block `card`. [SvelteKit — Streaming](https://kit.svelte.dev/docs/load#streaming-with-promises).

---

## Fonts

Every custom font is bytes on the critical path.

### Rules

- **Self-host.** Google Fonts over the network is an extra DNS + TLS + request. Put WOFF2 files in `static/fonts/`.
- **`font-display: swap`** to avoid invisible text during font load. Or `font-display: optional` if the font is truly optional (best CLS).
- **`size-adjust`, `ascent-override`, `descent-override`** on `@font-face` to match web font metrics to fallback ([MDN — CSS Font Loading API](https://developer.mozilla.org/en-US/docs/Web/CSS/@font-face)). Without this, swap causes layout shift.
- **Subset.** If you only use Latin characters, don't ship the Cyrillic + CJK set. [fonttools subset](https://fonttools.readthedocs.io/en/latest/subset/) or use the Google Fonts API's subset URL.
- **Variable fonts where appropriate.** One variable font file can replace 3–5 weight files. Geist is a variable font — we ship one `.woff2` and expose weights 100–900. [web.dev — Variable fonts](https://web.dev/articles/variable-fonts).

### Preload the critical font

`<link rel="preload" as="font" type="font/woff2" href="/fonts/Geist-Variable.woff2" crossorigin>` in the `<head>`. Only preload fonts that render text above the fold. Preloading every font slows everything.

---

## JavaScript bundle discipline

Every dependency is a liability. The rule: **every new dependency must justify its bytes.**

### Before adding any library

1. Is there a native API that does this? (Intersection Observer, Mutation Observer, etc.)
2. Is there a smaller library that covers our specific use case?
3. Will we use more than 20% of the library's surface?
4. Can we lazy-load it on the route that needs it?

If the answer to any of these is "we'd add 30KB for one function," don't.

### Code splitting

Every non-critical library is lazy-loaded:

```typescript
// Not at top of file
// import Fuse from 'fuse.js';

// In the handler that needs it
const { default: Fuse } = await import('fuse.js');
```

Result: fuse.js doesn't hit the bundle of users who never search. Source: [web.dev — Reduce JavaScript payload with code splitting](https://web.dev/articles/reduce-javascript-payloads-with-code-splitting).

### Tree-shake

Use named imports from libraries that support it:

```typescript
// Good: tree-shakes
import { Heart } from 'lucide-svelte';

// Bad: imports everything
import * as LucideIcons from 'lucide-svelte';
```

Verify via `vite build --mode production` and inspect the output bundle. Use [rollup-plugin-visualizer](https://github.com/btd/rollup-plugin-visualizer) or Vite's built-in bundle analyzer.

### Shrink the analytics tail

Third-party scripts: measure them. If Segment/GA/Mixpanel adds 100KB and blocks interaction, replace with server-side capture. A first-party fetch POST to your own endpoint is 0KB and blocks nothing.

---

## Measurement

### Real-user monitoring (RUM)

We collect Core Web Vitals from real users via [web-vitals npm package](https://github.com/GoogleChrome/web-vitals):

```javascript
import { onCLS, onINP, onLCP } from 'web-vitals';
onCLS(metric => sendToAnalytics(metric));
onINP(metric => sendToAnalytics(metric));
onLCP(metric => sendToAnalytics(metric));
```

Aggregate p75 per route. Flag regressions in CI.

### Lab testing

Before shipping:

1. **Lighthouse** (DevTools > Lighthouse) on every touched route.
2. **WebPageTest** ([webpagetest.org](https://www.webpagetest.org/)) for specific device profiles: "Moto G Power" on "4G" is our mid-range Android baseline. Runs are consistent and reproducible in a way local testing isn't.
3. **Chrome DevTools Performance panel** with CPU throttling set to "4× slowdown." Mimics mid-range Android CPU.
4. **Network throttling set to "Slow 3G"** for load-path tests; "Fast 3G" for steady-state.

### CI gates

Our CI runs Lighthouse on every PR. If LCP, CLS, or INP regresses > 10% from the main branch baseline, PR is blocked. Source: our `.github/workflows/lighthouse.yml` (when enabled).

---

## Safari-specific performance notes

Safari (desktop and mobile) has a handful of performance cliffs worth naming.

### Hitches on scroll

Safari's scroll-snap + overscroll combination can hitch on heavy scroll handlers. Mitigations:

- Use **passive event listeners** for scroll / touch: `addEventListener('scroll', handler, { passive: true })`. Without passive, Safari can't scroll while the handler runs.
- Throttle scroll handlers with `requestAnimationFrame`, not `setTimeout`.
- Use CSS `scroll-snap-type` sparingly; it's powerful but can interact badly with virtualization.

### `-webkit-overflow-scrolling: touch`

Was required for momentum scroll on iOS < 13. Not needed on modern Safari. Remove if present — it can cause layer explosion on newer iOS.

### Backdrop-filter

`backdrop-filter: blur(12px)` is gorgeous. It's also the most expensive CSS property on Safari, especially on A-series iPhones pre-A13. Use it on single surfaces (modal backdrop, sticky header), not on every card tile.

If the page has multiple stacked `backdrop-filter` elements, layout cost compounds. Prefer one at the top of the layer stack; the rest can use semi-transparent solid colors.

---

## The performance test before shipping

1. **Lighthouse score** on the touched route: Performance ≥ 90, Accessibility ≥ 95, Best Practices ≥ 95, SEO ≥ 90.
2. **Chrome Performance panel** with "4× CPU slowdown": no long tasks > 100ms during typical interaction.
3. **Bundle size delta** vs main: < 10KB increase per route, or a named justification.
4. **CLS = 0** on the primary user flow.
5. **LCP candidate** is the expected element (not a background gradient, not a font swap artifact).

If you can't say yes to all five, don't ship — find the regression or document why the tradeoff is worth it.
