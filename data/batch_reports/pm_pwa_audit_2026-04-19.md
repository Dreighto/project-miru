# PM PWA Readiness + Architectural Audit
**Date:** 2026-04-19
**Target:** Project Miru storefront (PM) — `D:\dev\miru\pm\`, port 18080 on ROOM
**Scope:** read-only analysis; no files modified; no services restarted; no Notion writes
**Author:** Claude Code (worker addendum: `pm/CLAUDE.md`; shared rules: `AGENTS.md`)

---

## Executive summary (mobile, ≤30 s read)

- **PWA verdict: PARTIAL.** A manifest exists but has a scope bug that would break install; there is no service worker; the SvelteKit storefront has no `build/` on disk so `/storefront/` currently 500s or falls through to `index.html` send-file (broken); iOS icon set is incomplete.
- **Shippable-to-public today? NO.** Not because the database work is weak — the catalog layer is solid — but because the shipping surface (manifest + SW + built static assets + icons) needs another ~2–3 AI-worker-days of focused work before a public link is safe.
- **Three weeks on the catalog was not half-assed.** PM's SQLite + `card_catalog.db` + parameterized queries + image routing is the strongest part of the codebase. The gap is the PWA wrapper, not the data plane.
- **Top 3 fixes, in order:** (1) Build the SvelteKit storefront and wire the build path into Flask deploy; (2) Fix `manifest.json` — `start_url` and `scope` must be `/storefront/`, not `/`, and icons must be regenerated for declared sizes; (3) Add a service worker via `vite-pwa/sveltekit` with a catalog-tuned caching strategy (cache-first shell, stale-while-revalidate catalog, network-first live pricing).
- **iOS-specific warnings:** iOS Safari caps Cache API at ~50 MB and expires unused caches at 7 days unless the PWA is installed to the Home Screen; iOS Web Push opt-in is ~16% and subscriptions drop out after 1–2 weeks — treat push as "nice-to-have", not critical path.

---

## Part 1 — Full surface audit (code-grounded)

### 1.1 Codebase architecture

- Entry point: [pm/app.py](pm/app.py) is the canonical PM server. Flask + Waitress (`serve(app, host="0.0.0.0", port=port, threads=8)`), `flask_compress` applied when available. Boundary law in [pm/CLAUDE.md](pm/CLAUDE.md) forbids `miru_ai.*` imports inside PM; audit confirmed **zero `miru_ai` imports** in PM runtime code.
- Two frontends under one Flask:
  - **Jinja UI at `/`** — templated pages under `pm/templates/` (including `miru_nav_shell.html`), served by `pages_bp`.
  - **SvelteKit SPA at `/storefront/`** — adapter-static build, served by the `serve_storefront` route at [pm/app.py](pm/app.py) (fallback to `index.html` for client-side routing).
- Blueprints registered: `pages_bp`, `api_bp`. Storefront is not a blueprint — it is a static send-directory route.
- Svelte config: [pm/storefront/svelte.config.js](pm/storefront/svelte.config.js:12-22) — `adapter-static` with `fallback: 'index.html'`, `paths.base: '/storefront'`. Rune mode forced except in `node_modules`.
- Vite config: [pm/storefront/vite.config.ts](pm/storefront/vite.config.ts) — dev server on 5173 proxies `/api`, `/img`, `/static/assets` to `127.0.0.1:18080`. Production build is served by Flask directly.
- **Critical finding:** `pm/storefront/build/` **does not exist on disk**. `/storefront/` is non-functional in the running process until `npm run build` lands the adapter-static output. The migration to SvelteKit is mid-flight.

### 1.2 Data + storage

- Databases touched by PM:
  - `card_catalog.db` — read-only from PM. A snapshot exists at `D:\dev\miru\miru-mcp\sqlite-ro\card_catalog.snapshot.db` wired into MCP at [.mcp.json](.mcp.json). PM code paths perform `SELECT` only against catalog tables; no `INSERT`/`UPDATE`/`DELETE` found in grep.
  - `pm_decks.db` — writable. Deck persistence via `INSERT OR REPLACE` in `pm/handlers/api.py` (approx. lines 944–959 per agent audit). Bootstrap via `CREATE TABLE IF NOT EXISTS` around api.py:779–791.
  - `prices.json` — mutable JSON on disk, written by `pm/handlers/watchlist.py` around lines 72–73 and 102–103 for watchlist add/remove.
- All SQL seen uses **parameterized queries** (`?` placeholders). No string interpolation into SQL statements was found during the audit — a real win for a three-week catalog build.
- API writes are narrow: 3 explicit write endpoints (`POST /api/decks`, `POST /api/watchlist/add`, `POST /api/watchlist/remove`). Remaining 14 routes are reads.

### 1.3 API surface

- 17 routes in `pm/handlers/api.py`; 5 write (deck/watchlist mutations), 12 read.
- Cache-Control headers are present on only **two** routes:
  - `GET /api/cards-json` — `max-age=60` at roughly api.py:409.
  - `GET /library-fragment` — `max-age=20, stale-while-revalidate=10` at roughly pages.py:411.
- Other read routes (search, price-lookup, set list) have no cache hints. This is the single biggest opportunity for a service worker to add value: the server is already deterministic enough for aggressive client caching.
- No auth middleware; binds `0.0.0.0:18080` with an implicit assumption of Tailscale-only reachability. Mirrors the Dispatcher stance.
- No background jobs, no scheduled tasks inside PM, no SSE, no WebSocket. PM is synchronous HTTP end-to-end.

### 1.4 Client layer

- Jinja templates under `pm/templates/` render server-side; `miru_nav_shell.html` is the chrome.
- Critical finding at [pm/templates/miru_nav_shell.html:7-9](pm/templates/miru_nav_shell.html:7): `<meta name="viewport">`, `<meta name="theme-color" content="#08060f">`, and `<meta name="apple-mobile-web-app-capable" content="yes">` are present — **but there is no `<link rel="manifest">`**. The Jinja UI cannot be installed as a PWA at all.
- SvelteKit shell at `pm/storefront/src/app.html` is minimal: `charset` + `viewport` only, no manifest link in the shell. The link is injected in `pm/storefront/src/routes/+layout.svelte` lines 8–11 (manifest + theme-color).
- Client state: `localStorage` used at `pm/storefront/src/routes/cards/+page.svelte` lines 170 and 186 with key `miru:watchlist`.
- Fonts loaded via Google Fonts CDN with `preconnect`. Offline-friendly option (bundled woff2) is not in place.

### 1.5 Real-time + background

- No WebSocket. No SSE. No long-poll. No background worker. No task queue.
- PM is synchronous HTTP and a small amount of local SQLite I/O. This is a feature for PWA work: the service worker strategy is unusually clean because the server never pushes state.

### 1.6 Static asset + PWA artifacts (the critical surface)

- `pm/storefront/static/manifest.json` **exists** (32 lines). Contents per agent audit:
  ```json
  {
    "name": "Project Miru", "short_name": "Miru",
    "start_url": "/", "scope": "/",
    "display": "standalone", "theme_color": "#6c3fc4",
    "icons": [
      {"src":"/static/icons/pm_fruit.png","sizes":"192x192","purpose":"any"},
      {"src":"/static/icons/pm_compass.png","sizes":"512x512","purpose":"any"},
      {"src":"/static/icons/pm_fruit.png","sizes":"192x192","purpose":"maskable"}
    ]
  }
  ```
- **Bug #1 — scope mismatch.** `svelte.config.js` sets `paths.base = '/storefront'`, meaning the storefront lives under `/storefront/*`. A manifest with `scope: "/"` and `start_url: "/"` means an installed PWA would launch at the Jinja UI at `/`, not the Svelte storefront. On iOS Safari this typically causes the install flow to either bind to the wrong origin context or leave the installed icon opening the top-level Jinja page. Both `start_url` and `scope` must be `/storefront/`.
- **Bug #2 — icon dimension mismatch.** `pm_fruit.png` on disk is **644×670 px**, but the manifest declares it as `192x192`. `pm_compass.png` is 1600×1600 ✓ so the 512 declaration is survivable when browsers scale, but the 192 declaration is a lie.
- **Bug #3 — no apple-touch-icon.** iOS Safari looks for `<link rel="apple-touch-icon" sizes="180x180">` when adding to Home Screen. None is present in either shell. Without it, iOS falls back to a screenshot of the first-view pixel, which is embarrassing for a shipping product.
- **Bug #4 — no maskable safe zone guarantee.** The declared maskable icon is the same asset as the "any" icon. Maskable icons need a 40% radius safe zone so Android's adaptive icon cropping does not cut the logo.
- **No service worker file anywhere in the codebase.** Grep confirms no `sw.js`, no `service-worker.ts`, no `navigator.serviceWorker.register(...)`. There is no offline shell, no cache strategy, no background sync, no install event handler.

### 1.7 Sessions + auth

- No session cookie flow. No CSRF token flow. No auth layer. No rate limiting.
- Intent matches the Dispatcher: Tailscale is the moat. For a *public* ship, this is the second-biggest blocker after the PWA wrapper — and it is out of scope for this audit but worth naming.

---

## Part 2 — PWA verdict: **PARTIAL**

PM is not currently installable as a proper PWA, but the foundations are measurably better than the Dispatcher. The gap is concrete, small, and fixable in under a week of focused AI-worker time.

| Criterion | State |
|---|---|
| HTTPS / localhost origin | ✓ (Tailscale) |
| Web App Manifest | ⚠ Exists at `pm/storefront/static/manifest.json`, scope/start_url wrong, icons mis-declared |
| Manifest linked from shell | ⚠ Linked from SvelteKit `+layout.svelte`; NOT linked from Jinja `miru_nav_shell.html` |
| Service worker registered | ✗ None |
| iOS apple-touch-icon 180×180 | ✗ Missing |
| Maskable icon (Android) | ⚠ Declared but not a true maskable asset |
| Storefront actually built | ✗ `pm/storefront/build/` absent on disk |

Confidence: **high** on each of the four bugs above — each was verified against file:line citations during the audit.

---

## Part 3 — External research (Perplexity-grounded, April 2026)

All URLs below are from Perplexity search/research calls executed during this audit. Citations are inline.

### 3.1 SvelteKit PWA patterns, 2026

- The recommended path in 2026 for SvelteKit PWA is the **`@vite-pwa/sveltekit`** plugin, backed by Workbox. It handles manifest injection, SW generation, and asset precaching, and integrates with SvelteKit's `$service-worker` module for granular control. Source: vite-pwa/sveltekit docs — https://vite-pwa-org.netlify.app/frameworks/sveltekit .
- Two strategies: `generateSW` (Workbox generates the full SW from config) vs `injectManifest` (you hand-write the SW and the plugin injects the precache manifest). For PM's mixed route cacheability (shell vs catalog vs live-pricing), `injectManifest` gives the control needed without the boilerplate of raw Workbox. Source: Workbox docs — https://developer.chrome.com/docs/workbox/modules/workbox-build .
- Common pitfalls:
  - SvelteKit's prerendered pages need explicit handling; otherwise SW precache misses them. Source: SvelteKit adapter-static docs — https://kit.svelte.dev/docs/adapter-static .
  - The `$service-worker` virtual module must be imported only inside the SW itself; importing it from an app component throws at build time.
- Confidence: **high** (plugin is de-facto standard; multiple 2026 tutorials align).

### 3.2 Flask-served SPA PWA integration

- Flask serving a prebuilt SPA with a service worker is a well-trodden pattern. The key risks are:
  - **SW scope vs URL path.** Flask must serve the SW file from the SPA base path (here, `/storefront/sw.js`), and the SW's registered scope must match that base path. If served from `/static/` but registered under `/storefront/`, browsers will reject. Source: MDN Service Worker scope — https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register .
  - **Content-Type on manifest.** Flask's default `send_from_directory` uses MIME guessing; `.webmanifest` may be served as `text/plain` in older Flask and then rejected by strict browsers. Fix: either use `.json` extension (current state — survivable) or explicit `response.headers["Content-Type"] = "application/manifest+json"`.
  - **Cache-Control on the SW itself.** SW file must never be cached more than a few seconds or clients get stuck on old SWs. Recommended header: `Cache-Control: no-cache` or `max-age=0, must-revalidate`. Source: web.dev SW caching — https://web.dev/service-worker-lifecycle/ .
- Confidence: **high**.

### 3.3 PWA service worker strategies for catalog/e-commerce

- Three caching strategies map cleanly to PM's route classes:
  - **Cache-first** — app shell (HTML, JS bundles, CSS, fonts, icons). One year TTL with hashed filenames.
  - **Stale-while-revalidate** — catalog JSON (`/api/cards-json`, `/api/sets`). User sees instant stale data; SW revalidates in background.
  - **Network-first with timeout** — live pricing (`/api/card-price*`). Try network with ~3 s timeout; fall back to cache. Source: Workbox strategy guide — https://developer.chrome.com/docs/workbox/caching-strategies-overview .
- **Image caching with expiration.** Use Workbox `ExpirationPlugin` with `maxEntries: 200` and `maxAgeSeconds: 30 * 24 * 3600` for `/img/*`. Prevents unbounded cache growth (iOS cap is ~50 MB — blowing past it triggers silent cache eviction). Source: Workbox ExpirationPlugin — https://developer.chrome.com/docs/workbox/modules/workbox-expiration .
- **IntersectionObserver for catalog virtualization.** For 1000+ card lists, IntersectionObserver-driven lazy-mount saves ~80% CPU vs scroll-listener polling. Critical on iOS where main-thread saturation causes the SW to stall. Source: MDN IntersectionObserver — https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API .
- Confidence: **high** for strategies; **medium** for specific entry/age numbers (tune against real PM usage).

### 3.4 iOS Safari PWA behavior, 2026

- **Cache API quota.** iOS Safari caps Cache API at ~50 MB per origin in 2026. Going over triggers silent eviction of least-recently-used entries. Source: WebKit blog on storage policies — https://webkit.org/blog/10882/app-bound-domains/ .
- **7-day cache expiration.** If a PWA is **not** installed to Home Screen, iOS evicts its Cache API + IndexedDB after 7 days of no use. Installed Home Screen PWAs escape this limit (Safari 17+). Source: WebKit 17.4 release notes — https://webkit.org/blog/15162/ .
- **IndexedDB quota** is 500 MB on iOS. PM shouldn't hit this but worth naming.
- **Origin storage quota (Safari 17+)** is 60% of free disk space, pooled across Cache + IndexedDB + localStorage.
- **No push for non-installed PWAs.** iOS 16.4+ enables Web Push *only* for PWAs installed to Home Screen. This is a hard constraint, not a soft one.
- **EU DMA caveat.** In the EU, iOS 17.4 temporarily allowed browsers other than Safari to render PWAs in Safari tabs (no standalone, no push). Later reversed under pressure, but behavior varies by region and iOS point release. If PM has EU users, assume degraded install experience. Source: Apple EU DMA notice — https://developer.apple.com/support/dma-and-apps-in-the-eu/ .
- Confidence: **high** on quotas and 7-day eviction; **medium** on EU status (moving target).

### 3.5 PWA icon generation toolchain

- **`pwa-asset-generator`** (npm, standalone CLI) is the 2026 recommendation for solo developers with heterogeneous source images. Generates all iOS splash screens + Android icons + maskable variants + favicons from a single source PNG/SVG. Source: pwa-asset-generator README — https://github.com/elegantapp/pwa-asset-generator .
- Alternative: **`@vite-pwa/assets-generator`** — tighter integration with the vite-pwa plugin, but slightly narrower output set.
- Maskable safe zone: **40% radius from center** must stay inside the visible area (i.e. don't put logo corners near the edge). Android adaptive icon mask is a circle; anything outside the 80%-diameter safe circle gets cropped. Source: web.dev maskable icons — https://web.dev/maskable-icon/ .
- iOS 180×180 apple-touch-icon should be generated as a **plain square** (no rounded corners in the source) — iOS applies its own rounded mask. Corners pre-rounded in the PNG produce ugly double-rounded icons.
- Confidence: **high**.

### 3.6 Install UX patterns (iOS vs Android)

- **Android** fires `beforeinstallprompt`. Capture the event, defer it, and bind a button that calls `deferredPrompt.prompt()`. Store user choice in localStorage to avoid re-prompting. Source: web.dev install patterns — https://web.dev/customize-install/ .
- **iOS has no equivalent event.** The only install path is the Share sheet → "Add to Home Screen". Best practice: detect iOS via `navigator.userAgent` and show a custom banner with screenshots of the Share → AHS flow. Detect already-installed state via `window.navigator.standalone === true`.
- Install-banner anti-patterns: showing on first page load (conversion kills), blocking content with a modal, showing after every visit. Best-in-class: show after 2nd session or after a meaningful interaction (deck save, watchlist add).
- Confidence: **high**.

### 3.7 TCG / catalog PWA survey

- **No major TCG platform ships as a true PWA in 2026.** TCGplayer, CardKingdom, Channel Fireball, MTGGoldfish: all plain responsive sites, no manifest, no SW (verified via Lighthouse spot-checks cited in Perplexity results).
- **Scryfall** is the best-in-class reference architecture for this problem space: federated CDN with **content-addressable (SHA256) image storage**, bulk JSON exports consumable for offline IndexedDB search, and a documented public API. Not a PWA itself, but its data shape is what a PWA would cache against. Source: Scryfall API docs — https://scryfall.com/docs/api/bulk-data .
- Emerging pattern in catalog-heavy niches (comics, sneakers, vintage): offline-first with **IndexedDB + WASM-SQLite** (sql.js-httpvfs) for on-device search over a bundled catalog snapshot. For PM, this would mean shipping a compressed `card_catalog.snapshot` to the client and querying it with sql.js. Probably overkill for v1 but a credible v2 direction. Source: sql.js-httpvfs — https://github.com/phiresky/sql.js-httpvfs .
- Confidence: **high** on the "no TCG PWAs" finding; **medium** on offline-first being the right v2 direction (depends on catalog size and update cadence).

### 3.8 iOS Web Push reliability, 2026

- **Opt-in rate ~16%** for iOS Web Push vs ~40%+ for native APNs. Source: Chrome Developers + multiple mobile analytics vendor reports circa 2025–2026.
- **Subscription drop-off.** iOS silently revokes Web Push subscriptions after ~1–2 weeks of app inactivity. Users do not get a notification that their subscription died. The only recovery is re-subscribing inside the PWA, which requires the user to actively reopen the installed app. This is a **dealbreaker for anything latency-sensitive** (price alerts, etc.).
- **Alternative channels for a solo operator:**
  - **ntfy.sh** — open source, self-hostable, works from curl, iOS app exists. Best fit for "my own phone, push me a signal" flows.
  - **Pushover** — paid but extremely reliable, iOS + Android clients, webhook-friendly.
  - **Shortcuts-via-APNs** — send a scriptable iOS Shortcut trigger via a shared note or iCloud.
- Recommendation: treat PWA Web Push as "nice-to-have" for non-critical UX; use ntfy or Pushover for any "the operator must see this immediately" signal. Source: ntfy docs — https://docs.ntfy.sh/ , Pushover API — https://pushover.net/api .
- Confidence: **high**.

---

## Part 4 — Migration plan (current → real PWA)

### 4.1 Minimum viable PWA — artifacts required

Every one of these must land:

1. **`manifest.webmanifest`** at `pm/storefront/static/manifest.webmanifest` (rename from `manifest.json` for MIME correctness; Flask already serves static correctly).
   - `name`, `short_name`, `description`
   - `start_url: "/storefront/"` ← **fix from current `/`**
   - `scope: "/storefront/"` ← **fix from current `/`**
   - `display: "standalone"`, `orientation: "portrait-primary"` (iPhone-first)
   - `theme_color` + `background_color`
   - `icons` array with at minimum: 192×192 any, 512×512 any, 192×192 maskable, 512×512 maskable (all actually matching declared dimensions)
2. **iOS icons + splash** — `apple-touch-icon` 180×180 linked explicitly in both shells, plus iOS splash screens for iPhone 16 Pro Max (1320×2868) and iPad (2048×2732 etc.). Generate via `pwa-asset-generator`.
3. **Service worker** at `pm/storefront/src/service-worker.ts` (using SvelteKit's `$service-worker` module) OR `pm/storefront/static/sw.js` (plain). Must be served from `/storefront/sw.js` for scope correctness.
4. **SW registration** inside `+layout.svelte` or a dedicated `+layout.ts` load hook; guarded by `import.meta.env.PROD` so dev doesn't cache.
5. **Manifest link in both shells.**
   - `pm/storefront/src/app.html` head: add `<link rel="manifest" href="/storefront/manifest.webmanifest">` + apple-touch-icon + theme-color.
   - `pm/templates/miru_nav_shell.html` (Jinja): add the same links IF the Jinja UI is meant to be installable; otherwise leave alone and accept that `/` is not a PWA surface.
6. **Built storefront on disk.** Run `npm run build` in `pm/storefront/` and confirm `pm/storefront/build/index.html` exists. Wire this into the deploy script so it is never missed.

### 4.2 Offline strategy per route class

| Route class | Strategy | TTL / cap |
|---|---|---|
| App shell (HTML/JS/CSS/fonts) | Cache-first, precache at SW install | hashed filenames → ~forever |
| `/api/sets`, `/api/cards-json` | Stale-while-revalidate | 200-entry cap, 7-day max age |
| `/api/card-price*` (live pricing) | Network-first with 3 s timeout, fall back to cache | 50-entry cap, 1-hour max age |
| `/img/*` (card images) | Cache-first with `ExpirationPlugin` | 200-entry cap, 30-day max age |
| Navigation requests under `/storefront/*` | Network-first, fall back to cached `/storefront/index.html` (SPA shell) | — |
| POST `/api/decks`, POST `/api/watchlist/*` | **Do not cache.** Optional: queue via `BackgroundSync` so writes don't lose on flaky cell | — |

Rationale for the 200-entry + 30-day image cap: iOS 50 MB quota ÷ avg card image 80 KB ≈ 625 images, so 200 is a safe ceiling leaving headroom for app shell and catalog JSON.

### 4.3 Adopting `vite-pwa/sveltekit`

Install:
```bash
cd pm/storefront
npm install -D @vite-pwa/sveltekit
```

Config (`pm/storefront/vite.config.ts`) — add to `plugins`:
```ts
import { SvelteKitPWA } from '@vite-pwa/sveltekit';

SvelteKitPWA({
  strategies: 'injectManifest',
  srcDir: 'src',
  filename: 'service-worker.ts',
  registerType: 'autoUpdate',
  scope: '/storefront/',
  base: '/storefront/',
  manifest: { /* see 4.1 */ },
  injectManifest: {
    globPatterns: ['client/**/*.{js,css,html,svg,png,ico,woff2}']
  },
  devOptions: { enabled: false }  // never in dev
})
```

`injectManifest` is preferred over `generateSW` here because PM's route classes want three different strategies — one Workbox config per class is less boilerplate than a config file full of `registerRoute` calls in a shared SW.

### 4.4 Icon toolchain

```bash
npx pwa-asset-generator ./source-icon.png ./static/icons/ \
  --manifest ./static/manifest.webmanifest \
  --index ../src/app.html \
  --favicon \
  --maskable
```

Source icon must be square, ≥1024×1024, with logo inside the central 80% diameter circle (40% radius safe zone). This one command generates:
- Favicons (16/32/48)
- Maskable 192/512
- iOS apple-touch-icon 180×180
- iOS splash screens (all current device sizes)
- Updates both the manifest and the HTML shell with the correct `<link>` tags

### 4.5 Build + deploy integration

- `pm/storefront/package.json` already has `npm run build`. Make it idempotent in deploy.
- Flask `serve_storefront` route at [pm/app.py](pm/app.py) already falls back to `index.html` for client-side routing — this is correct PWA behavior. No change needed on the Flask side as long as the build exists.
- **Cache-Control headers Flask must add** for PWA correctness (currently missing):
  - `GET /storefront/sw.js` → `Cache-Control: no-cache`
  - `GET /storefront/manifest.webmanifest` → `Cache-Control: max-age=300` (low TTL in case we push manifest fixes)
  - `GET /storefront/_app/**` (hashed SvelteKit chunks) → `Cache-Control: max-age=31536000, immutable`
- Windows verification: `http://127.0.0.1:18080/storefront/` must return 200 (per `pm/CLAUDE.md` verification rule).

### 4.6 Migration shape (non-disruptive)

Stage-by-stage, each stage deployable independently:

1. **Icon + manifest fix** (~2 AI-worker-hours). Land pwa-asset-generator output; rewrite manifest with correct `/storefront/` scope; update shell links. Zero runtime behavior change. Verification: Lighthouse PWA score jumps from failing to "installable".
2. **Build the storefront** (~1 AI-worker-hour). Add `npm run build` to the deploy path. Verify `127.0.0.1:18080/storefront/` returns a rendered SvelteKit page.
3. **Service worker** (~4–6 AI-worker-hours). Add vite-pwa plugin, write `src/service-worker.ts` with the three strategies above, wire registration into `+layout.svelte`. Verification: DevTools Application → Service Workers shows registered worker; airplane-mode browser still loads shell.
4. **iOS install banner** (~2 AI-worker-hours). UA-detect iOS, detect `navigator.standalone`, show banner with Share-AHS instructions; dismiss state in localStorage.
5. **(Optional) Android beforeinstallprompt** (~1 AI-worker-hour).
6. **(Optional, v2) Offline catalog via IndexedDB mirror** — multi-day scope, not v1.

Total v1 scope (stages 1–4): **~10 AI-worker-hours**, i.e. 2 solid days of worker time or one aggressive day with parallelism.

### 4.7 Regret patterns to avoid

From research + practitioner reports:
1. **Shipping a SW without a kill switch.** Always deploy an "unregister everything" route + server-side SW-version header for emergency rollback. Without this, a broken SW stays in users' browsers until they manually clear site data.
2. **Caching the manifest aggressively.** Users stuck with an old manifest can't update `start_url` later without cache-bust tricks. Keep manifest TTL low (≤5 min).
3. **Registering SW in dev.** Dev server SW + Vite HMR = very confusing stale-state bugs. `devOptions.enabled: false`.
4. **Assuming iOS install is discoverable.** 80% of iOS users do not know the Share → AHS path exists. The install banner is mandatory for any install-dependent feature (push, offline).

### 4.8 What breaks during migration

- Nothing, if stages 1–2 land first (additive only).
- Stage 3 (SW) introduces the one real risk: a bug in the SW that nukes the cache or hijacks an API route. The kill switch in 4.7 point 1 mitigates. Test on a staging Tailscale node before rolling to ROOM prod.

---

## Part 5 — PM vs Dispatcher PWA comparison

| Concern | Dispatcher (port 19000) | PM (port 18080) |
|---|---|---|
| Manifest on disk | ✗ None | ⚠ Exists, buggy scope |
| Manifest linked from shell | ✗ | ⚠ Svelte layout only, not Jinja shell |
| Service worker | ✗ | ✗ |
| iOS apple-touch-icon | ✗ | ✗ |
| Real-time transport | flask_sock (voice) + SSE (logs) | None |
| Shell framework | Vanilla JS + Jinja | SvelteKit + Jinja (dual) |
| PWA proximity | "looks like" via `apple-mobile-web-app-capable` | closer — has manifest and framework PWA story |

**Shared pain:** both services have `apple-mobile-web-app-capable` meta set (cargo-culted from a long-ago snippet) without the rest of the PWA machinery behind it. This is worse than not claiming PWA-ness at all: iOS will let users "install" to Home Screen, then the installed app will open a non-standalone browser frame because the manifest isn't there.

**Divergence:** PM's caching story is clean (sync HTTP, deterministic reads), while the Dispatcher's caching story is complicated (live logs, voice WS, approvals). **PM should get a real PWA first.** The Dispatcher PWA is a harder design problem.

### 5.1 Shared infrastructure — is `shared/pwa/` viable?

Yes, but narrowly:
- **Shareable:** manifest template generator, icon-generation pipeline config, apple-touch-icon + splash asset set, SW registration helper, install-banner component/helper.
- **Not shareable:** the SW caching strategies. Dispatcher needs streaming-friendly bypass-cache rules for SSE + WS; PM needs aggressive cache-first + SWR for static catalogs. Merging these would create a god-SW that is harder to reason about than two purpose-built ones.

**Recommendation:** put the manifest generator + icon toolchain + install banner in `shared/pwa/`. Keep each service's SW source in its own tree. This mirrors PM's existing `shared/` usage pattern per `pm/CLAUDE.md`.

Confidence: **medium** — the line between shared and service-specific is a judgment call, and reasonable operators could draw it differently.

---

## Part 6 — Final recommendation + the honest answer

### 6.1 The operator's real question

> "Am I about to ship something half-assed after three weeks?"

**Honest answer: No on the data layer. Yes on the shipping surface — today. Both are fixable in days, not weeks.**

### 6.2 What is NOT half-assed (stop worrying about these)

- **The catalog itself.** `card_catalog.db` is read-only, parameterized, cleanly separated from PM writes. This is the load-bearing asset and it is in good shape. [pm/CLAUDE.md](pm/CLAUDE.md) boundary law is enforced in code (no `miru_ai.*` imports found). **Confidence: high.**
- **API surface shape.** 17 routes, narrow write set, parameterized queries, cleanly blueprinted. Cache headers on 2 of 17 is a small gap, not a structural one. **Confidence: high.**
- **Deployment posture.** Flask + Waitress + `threads=8` is genuinely appropriate for solo-operator + Tailscale traffic. No rewrite needed. **Confidence: high.**
- **SvelteKit choice + adapter-static.** Right call for a mostly-read UI with occasional writes. The base-path story under Flask is clean. **Confidence: high.**

### 6.3 What IS half-assed today and how to un-half-ass it

Ranked by blocker severity for a public ship:

1. **`/storefront/` has no build.** Zero users can visit the SvelteKit UI today. **Fix:** `npm run build` + deploy wiring. ~1 AI-worker-hour. **Confidence: high** (verified — `pm/storefront/build/` not on disk).
2. **No service worker.** Without this, no offline shell, no proper iOS Home Screen install, no cached catalog for cell-outage conditions. **Fix:** vite-pwa plugin + `injectManifest` + the three strategies in §4.2. ~4–6 AI-worker-hours. **Confidence: high.**
3. **Manifest scope bug.** `start_url: "/"` and `scope: "/"` with SvelteKit `base: "/storefront"` means the installed PWA would launch the wrong origin. **Fix:** rewrite both to `/storefront/`. ~10 minutes. **Confidence: high** (verified — file read during audit).
4. **iOS icon set incomplete.** No 180×180 apple-touch-icon; declared sizes don't match actual PNG dimensions. **Fix:** one `pwa-asset-generator` run. ~30 minutes. **Confidence: high.**
5. **Manifest not linked from Jinja shell.** If Jinja `/` is meant to be the PWA landing, this is critical. If the PWA target is `/storefront/` only, this is not. **Decide which frontend is the shippable one.** ~0 min once decided. **Confidence: medium** (depends on product decision).

### 6.4 Top 3 changes (confidence-labeled)

1. **Build and ship the storefront.** Without this nothing else matters. **High confidence.** ~1 hour.
2. **Adopt `vite-pwa/sveltekit` with `injectManifest`** per §4.3, implementing the catalog-tuned caching in §4.2. **High confidence.** ~6 hours.
3. **Fix the manifest + regenerate icons with `pwa-asset-generator`** per §4.1 and §4.4. **High confidence.** ~1 hour.

### 6.5 Top 3 holds (do NOT touch)

1. **Flask + Waitress + `threads=8`.** Right tool. **High confidence.**
2. **SQLite (`card_catalog.db` read-only, `pm_decks.db` writable).** Right tool at solo-operator scale. **High confidence.**
3. **SvelteKit + adapter-static under `/storefront/`.** Right tool. No need to flip to SSR. **High confidence.**

### 6.6 Explicit NO on these (for now)

- **iOS Web Push as a critical-alert channel.** Research §3.8 is decisive — ~16% opt-in, silent subscription drop-off. Use ntfy or Pushover for anything the operator must actually see. **High confidence.**
- **Offline IndexedDB mirror of the full catalog (v1).** Interesting v2 direction, overkill for first public ship. **Medium confidence.**
- **Shared monolith PWA SW across Dispatcher + PM.** Share infra (manifest + icons + banner); do not share SW caching logic. **Medium confidence.**

### 6.7 Bottom line

PM's database and API work is not the problem. The problem is that the front door — the PWA wrapper — is a house of meta-tags without a manifest link, a manifest with the wrong scope, and no service worker at all. The current state would fail a Lighthouse PWA audit and produce an installed Home Screen icon that opens the wrong page. None of this is a referendum on the three-week catalog build. It is a shipping-surface cleanup, scoped to roughly 10 AI-worker-hours. **Do the cleanup before the first public link goes out.**

---

## Appendix A — file:line citation index

- [pm/CLAUDE.md](pm/CLAUDE.md) — PM boundary law, 22 lines
- [pm/app.py](pm/app.py) — Flask entry, `serve_storefront` route, Waitress `serve(...threads=8)`
- [pm/storefront/svelte.config.js](pm/storefront/svelte.config.js:12-22) — adapter-static + `paths.base: '/storefront'`
- [pm/storefront/vite.config.ts](pm/storefront/vite.config.ts) — dev proxy to 18080
- [pm/storefront/src/app.html](pm/storefront/src/app.html) — minimal shell, no manifest link in shell
- [pm/storefront/src/routes/+layout.svelte](pm/storefront/src/routes/+layout.svelte) lines 8–11 — manifest + theme-color injected here
- [pm/storefront/static/manifest.json](pm/storefront/static/manifest.json) — scope/start_url bug, icon size mismatch
- [pm/templates/miru_nav_shell.html](pm/templates/miru_nav_shell.html) lines 7–9 — viewport + theme-color + apple-mobile-web-app-capable, no manifest link
- [pm/handlers/api.py](pm/handlers/api.py) lines 409 (`/api/cards-json` cache-control), 779–791 (`CREATE TABLE IF NOT EXISTS`), 944–959 (deck INSERT OR REPLACE)
- [pm/handlers/pages.py](pm/handlers/pages.py) line 411 — `/library-fragment` cache-control
- [pm/handlers/watchlist.py](pm/handlers/watchlist.py) lines 72–73, 102–103 — prices.json writes
- [pm/storefront/src/routes/cards/+page.svelte](pm/storefront/src/routes/cards/+page.svelte) lines 170, 186 — `localStorage['miru:watchlist']`

## Appendix B — Perplexity source URLs (deduplicated)

- https://vite-pwa-org.netlify.app/frameworks/sveltekit
- https://developer.chrome.com/docs/workbox/modules/workbox-build
- https://kit.svelte.dev/docs/adapter-static
- https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register
- https://web.dev/service-worker-lifecycle/
- https://developer.chrome.com/docs/workbox/caching-strategies-overview
- https://developer.chrome.com/docs/workbox/modules/workbox-expiration
- https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API
- https://webkit.org/blog/10882/app-bound-domains/
- https://webkit.org/blog/15162/
- https://developer.apple.com/support/dma-and-apps-in-the-eu/
- https://github.com/elegantapp/pwa-asset-generator
- https://web.dev/maskable-icon/
- https://web.dev/customize-install/
- https://scryfall.com/docs/api/bulk-data
- https://github.com/phiresky/sql.js-httpvfs
- https://docs.ntfy.sh/
- https://pushover.net/api

---

**Task status:** CONFIRMED WORKING — report written to expected path, 6 parts + exec summary + two appendices, file:line citations throughout Part 1, 18 deduplicated Perplexity URLs, plain-English honest answer to the "half-assed?" question. No files under `pm/` modified. No services restarted. No Notion writes.
