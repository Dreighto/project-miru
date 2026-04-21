# PM Mobile Strategy Research

**Date:** 2026-04-20  
**Target:** Project Miru storefront/mobile web strategy  
**Scope:** Codebase read + external research. Only this report file was written.

---

## 1. Executive summary

**Recommendation: confirm the operator's hypothesis, with one caveat.** Do not make a true PWA the next critical path. Ship the best mobile website PM can be first: correct the `/storefront` routing/build surface, add route-class HTTP caching, polish bottom-nav/card-detail gestures, and handle notifications outside the web app. Then consider an IndexedDB catalog snapshot as a second stage if real users need faster repeat lookup or weak-network catalog access.

Top three findings:

1. **TCG users reward fast, trusted data more than installability.** Scryfall, EDHREC, Moxfield, Archidekt, Limitless OP, Card Kaizoku, OPTCG.GG, OnePiece.gg, and TCGMatchmaking win through search quality, deck/meta depth, pricing, tournament data, and community trust. Direct site checks show several expose manifests, but the public evidence that TCG users choose them because they are PWAs is thin. Scryfall's API docs explicitly encourage caching and local processing, and Similarweb estimates show millions of monthly Scryfall visits driven largely by direct/search behavior, not app-store-style distribution ([Scryfall API](https://scryfall.com/docs/api), [Scryfall Similarweb](https://www.similarweb.com/website/scryfall.com/)).
2. **PM's current blockers are more basic than PWA strategy.** The audit's main blockers still reproduce: no built storefront, wrong manifest scope/start URL, no service worker, incomplete iOS icon story. The live code adds two more immediate mobile-web issues: `+layout.svelte` links `/manifest.json` instead of `/storefront/manifest.json`, and SvelteKit bottom-nav hrefs are root-absolute even though `paths.base` is `/storefront`.
3. **iOS reduces the payoff of bundling install/offline/push into the web app.** Web Push on iOS requires a Home Screen web app and service workers, and WebKit's seven-day cap applies to script-writeable storage for non-Home-Screen sites, including IndexedDB and service worker cache ([WebKit Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/), [WebKit tracking prevention](https://webkit.org/tracking-prevention/)). A true PWA can be useful, especially on Android, but it adds lifecycle/cache risk before PM has validated that users care.

The one thing that could flip the recommendation: **real usage showing that players need reliable venue/subway offline catalog access or that Android users strongly adopt Home Screen install.** If that happens, move to the true PWA or IndexedDB-plus-service-worker path.

---

## 2. Codebase state vs audit

### What the audit got right

The audit's core PWA-readiness claims still match the live checkout:

- `pm/storefront/build/` does not exist. `Test-Path D:\dev\miru\pm\storefront\build` returned `False`, so `/storefront/` still cannot serve the adapter-static build.
- `pm/storefront/svelte.config.js` sets `paths.base: '/storefront'`, while `pm/storefront/static/manifest.json` still uses `"start_url": "/"` and `"scope": "/"`.
- No service worker artifact was found under `pm/storefront/`.
- `pm/storefront/src/app.html` has viewport/text-scale only, not manifest or iOS icon links.
- `pm/storefront/src/routes/+layout.svelte` injects a manifest link, but it is currently `href="/manifest.json"`, which is also wrong for the `/storefront` base.
- `pm/templates/miru_nav_shell.html` has `viewport`, `theme-color`, and `apple-mobile-web-app-capable`, but no manifest link.
- `pm_fruit.png` is actually `644x670` while the manifest declares it as `192x192`; `pm_compass.png` is `1600x1600`, so the 512 declaration can be scaled but is not a generated 512 asset.
- Only two live routes set `Cache-Control`: `/api/cards-json` at `pm/routes/api.py:409` and `/library-fragment` at `pm/routes/pages.py:411`.
- No `miru_ai.*` imports were found under `pm/`, matching `pm/CLAUDE.md`'s boundary law.

The audit was also right about what is **not** the problem: the PM service shape is sensible. Flask + Waitress, SQLite-backed catalog reads, a small writable deck DB, SvelteKit adapter-static under Flask, and the catalog/API separation are not the strategic bottleneck.

### What changed or was wrong

The requested file paths `pm/handlers/api.py`, `pm/handlers/pages.py`, and `pm/handlers/watchlist.py` do not exist in the current checkout. The live equivalents are:

- `pm/routes/api.py`
- `pm/routes/pages.py`
- `pm/services/watchlist.py`

Given their file timestamps predate the audit date, this looks more like audit path drift than a post-audit code move.

The audit said "17 routes" and that part is correct for API decorators:

- 17 `@api_bp.*` decorators in `pm/routes/api.py`
- 11 `@pages_bp.*` decorators in `pm/routes/pages.py`, counting both `/cards` and `/library` decorators on the same handler

The write-route count needs a correction:

- There are 4 POST API routes: `/api/watchlist/add`, `/api/watchlist/remove`, `/api/decks`, and `/api/decks/<deck_id>/validate`.
- Only 3 obviously mutate storage: watchlist add, watchlist remove, and deck create/update.
- `/api/decks/<deck_id>/validate` is POST but appears read-only.
- `_ensure_decks_schema()` creates `pm_decks.db` schema on module import, so importing the API module can create/write the deck DB. That is fine for runtime, but worth remembering in "read-only" audits.

### Additional codebase findings not emphasized by the audit

PM's mobile web foundation exists, but it is early:

- `pm/storefront/src/app.css` already has safe-area bottom padding via `env(safe-area-inset-bottom)` and `overscroll-behavior-y: contain`.
- `BottomNav.svelte` has thumb-sized bottom navigation and `touch-manipulation`.
- `PageShell.svelte` uses a fixed top bar and safe-area top padding.
- Cards and deck-builder pages use lazy images and load-more pagination.
- There is no virtualized list, no gesture action, no swipe-to-close/back, no `content-visibility`, no skeleton layout beyond text loading states, and no `IndexedDB`.
- `cards/+page.svelte` stores only a local watchlist in `localStorage` under `miru:watchlist`.
- `pm/storefront/package.json` already depends on `motion`, but the storefront source does not currently use Motion or gesture tooling.

The bigger mobile-web bug: `BottomNav.svelte` defines hrefs like `/`, `/cards`, and `/deck-builder`. With SvelteKit `paths.base = '/storefront'`, those links will navigate to the Jinja routes rather than `/storefront/cards`, unless SvelteKit rewrites them in a way this code should not rely on. PM should import `base` from `$app/paths` or use relative/base-aware route helpers before any public storefront test.

**What this means for PM:** the no-PWA strategy is not an excuse to ignore the audit. It means fix the web surface first: build output, base-aware links, HTTP cache headers, mobile interactions, and notification channels. A service worker should not be added until these simpler layers are stable.

---

## 3. Research area A - TCG/catalog web app landscape

### What the winners have in common

The most respected TCG tools are not primarily respected for being installable. They are respected for one or more of:

- Fast, expressive search
- Complete and trusted card data
- Deck-building depth
- Meta/tournament coverage
- Price history or marketplace liquidity
- Community trust and creator/Discord loops
- Low-friction mobile access

Similarweb estimates are imperfect, but they are useful for relative scale. Search results for March 2026 showed Scryfall at roughly 7.3M monthly visits with high pages per visit and long visit duration; EDHREC at roughly 9.3M; Moxfield at roughly 8.7M; Archidekt around 2.9M; and marketplace/commercial peers such as TCGplayer and Cardmarket much larger because commerce creates repeat traffic ([Scryfall](https://www.similarweb.com/website/scryfall.com/), [EDHREC](https://www.similarweb.com/website/edhrec.com/), [Moxfield](https://www.similarweb.com/website/moxfield.com/), [Archidekt](https://www.similarweb.com/website/archidekt.com/), [Cardmarket](https://www.similarweb.com/website/cardmarket.com/)).

### Landscape table

| Site | Type and mobile posture | Offline/notifications | Traffic and why it wins | Praise/complaints signal |
|---|---|---|---|---|
| Scryfall | Manifest-equipped responsive web catalog. Direct fetch showed a manifest, apple touch icon, preconnect to image CDN, and search-focused mobile shell ([home](https://scryfall.com/), [manifest](https://scryfall.com/manifest.webmanifest)). | Scryfall's public value is API/bulk data, not user-facing offline. Bulk data is exported daily and Scryfall asks API consumers to cache/process data locally for at least 24 hours ([bulk data](https://scryfall.com/docs/api/bulk-data), [API](https://scryfall.com/docs/api)). | Similarweb estimates millions of visits/month, driven by direct and organic search. Wins through search syntax, clean card pages, API, image CDN, and trust ([Similarweb](https://www.similarweb.com/website/scryfall.com/)). | Praised as the canonical MTG card search. Weak evidence that users care whether it is a PWA. |
| EDHREC | Responsive Next-style website, no manifest seen in first fetched head, strong preconnects to its data/image origins ([EDHREC](https://edhrec.com/)). | No obvious user-facing offline or notifications. | Millions of visits/month. Wins because it turns deck corpus data into Commander recommendations ([EDHREC guide](https://edhrec.com/guides/how-to-use-edhrec), [Similarweb](https://www.similarweb.com/website/edhrec.com/)). | Users accept some update lag because recommendation depth is unique. |
| Moxfield | Web app/deck builder. Fetch was blocked, but traffic/source data and community usage are strong. | No clear offline story from public sources. | Millions of visits/month. Wins through modern deck builder UX, public deck discovery, collection/deck workflows ([Similarweb](https://www.similarweb.com/website/moxfield.com/)). | Praised for modern UX; complaints usually about deck-builder features, sync, or workflow details, not installability. |
| Archidekt | Manifest-equipped web deck builder. Direct fetch showed `manifest.json`, apple touch icon, mobile app meta, and `display: standalone` ([home](https://archidekt.com/), [manifest](https://archidekt.com/manifest.json)). | Manifest does not prove offline. No public evidence found that offline is central. | Millions of visits/month. Wins through visual deck building, EDHREC/Scryfall integrations, collection/playtest workflow ([EDHREC/Archidekt article](https://edhrec.com/articles/digital-deckbuilding-card-searches-on-edhrec-and-archidekt), [Similarweb](https://www.similarweb.com/website/archidekt.com/)). | Strong deck-builder reputation; feature depth matters more than distribution. |
| TCGplayer | Plain marketplace web/app ecosystem. Fetch showed a heavy JS/commercial shell ([TCGplayer](https://www.tcgplayer.com/)). | Marketplace notifications are app/account/email oriented, not catalog offline. | Very high traffic because buying/selling and price lookup create repeat intent. | Community complaints tend to center on commerce, sellers, fees, shipping, and speed. |
| CardKingdom | Retailer/marketplace. Fetch blocked by robots, so no direct PWA claim. | Retail/account email, not offline catalog. | Strong retailer traffic. Wins on inventory, trust, buylist, search. | Mobile UX matters, but inventory/pricing trust dominates. |
| ChannelFireball | Content/marketplace site. | No meaningful offline/PWA evidence found. | Traffic comes from content, events, brand, and commerce. | Users tolerate ordinary web if content/commerce is useful. |
| MTGGoldfish | Responsive content/prices/decks site. Direct fetch showed RSS/Atom and apple icons, not a manifest in the fetched head ([MTGGoldfish](https://www.mtggoldfish.com/)). | RSS is a useful pull-notification channel. No offline app story. | Traffic from meta, deck lists, price data, finance/speculation, and articles. | Dense but trusted. Users come for data/content, not native feel. |
| Limitless TCG / Limitless OP | Manifest-equipped OP tournament/meta/card database. Direct fetch showed `app.webmanifest`, module preloads, and the tagline "The most comprehensive One Piece Card Game database" ([home](https://onepiece.limitlesstcg.com/), [manifest](https://onepiece.limitlesstcg.com/app.webmanifest), [advanced search](https://onepiece.limitlesstcg.com/cards/advanced)). | Manifest uses `display: minimal-ui`; no direct SW/offline evidence found. | OP side gets authority from tournament results, decklists, card database, and current formats. Search result showed major events like Regional Lille with 1536 players and Treasure Cup Pomona with 1024 players ([decks/events](https://onepiece.limitlesstcg.com/decks)). | Competitive users respect event/meta coverage and freshness. |
| Card Kaizoku / deckbuilder.cardkaizoku.com | Manifest-equipped React-style OP web apps. Direct fetch showed manifests, apple touch icon, ad script, JS root app, and standalone display ([home](https://www.cardkaizoku.com/), [manifest](https://www.cardkaizoku.com/manifest.json), [deckbuilder manifest](https://deckbuilder.cardkaizoku.com/manifest.json)). | Manifest only. No direct offline/notification evidence found. | Wins through OP-specific tools: rankings, deck builder, event calendar, proxy/tooling. | Direct OP-specific competitor for PM. Ads and JS-only shells are places PM can feel better. |
| TCGMatchmaking | Shopify/community/tournament platform. Direct fetch showed copy advertising OPBounty, tournaments, stats, guides, early access, and Discord community ([TCGMatchmaking](https://tcgmatchmaking.com/), [tournaments](https://tcgmatchmaking.com/pages/tcg-tournaments-page)). | Discord/community is core. No offline web story. | Wins through organized play, ranked data, tournaments, premium/community loops ([power rankings](https://egmanevents.com/one-piece-op14-tcg-matchmaking-power-rankings)). | Competitive users respect stats and tournament access. |
| OPTCG.GG | Next-style OP card search. Direct fetch showed a card search page with many Next chunks and no manifest in the fetched head ([OPTCG.GG search](https://www.optcg.gg/search)). | No offline/notification evidence found. | Wins if its card search/filtering is fast and complete. | PM can compete on speed, mobile ergonomics, and watchlist pricing. |
| OnePiece.gg | WordPress/content/meta/deck builder ecosystem. Fetch showed a heavy content/ad/sidebar shell ([OnePiece.gg](https://onepiece.gg/)). | Content RSS/onsite, no offline app story found. | Wins through SEO content, meta articles, tier lists, prices, and products ([meta](https://onepiece.gg/meta/), [cards](https://onepiece.gg/cards/)). | Good for discovery, but heavy pages are a mobile opening for PM. |
| Official Bandai OP site | Official responsive website/card list/product/event source ([official site](https://en.onepiece-cardgame.com/), [card list](https://en.onepiece-cardgame.com/cardlist/)). | Official news, no offline app story found. | Wins by authority and official updates. | Users need it for official truth, but community sites win on tools and meta. |
| Native OP deck apps | OP Deck Builder appears in App Store/Google Play results as a native companion app with database, filters, deck building, stats, and TCGplayer buy tab ([App Store](https://apps.apple.com/us/app/op-tcg-deck-builder/id6451478386), [Google Play](https://play.google.com/store/apps/details?id=app.biiscuit.opdb&hl=en_US)). | Native apps can do local storage better. | App stores help discovery for deck-building intent. | PM should respect that "mobile app feel" has native competition even if web tools dominate search/discovery. |

### What the OPTCG community would respect PM for

The community would respect PM for:

- Scryfall-like card lookup speed for OP, especially on mobile.
- Fresh OP data: new sets, variants, images, bans/restrictions, and prices.
- Clean mobile card inspection: big art, fast variant switch, readable effect text, thumb-reachable actions.
- Better watchlist/pricing than generic OP sites.
- Export/import compatibility with community deck lists, sims, and Discord.
- Transparent data provenance and update timestamps.
- A notification model that meets players where they already are: Discord, email digest, RSS, and in-app inbox.

### What the OPTCG community would reject PM for

The community would reject PM for:

- Stale or wrong card data.
- Missing variants, poor images, or untrusted price mapping.
- Heavy ads, slow initial load, janky scroll, or modal traps.
- A mobile site that looks good but breaks basic back navigation or deep links.
- Forced install flows, early notification permission prompts, or Web Push complexity with little payoff.
- Copying Card Kaizoku/Limitless/OPTCG.GG without a clearer edge.

**What this means for PM:** PWA install should not be the product thesis. The product thesis should be trusted OP data, speed, mobile ergonomics, and community-aware notification channels.

---

## 4. Research area B - Great mobile website, not a PWA

### The pattern

A great mobile website without a PWA feels app-like because it removes waiting, preserves scroll/touch expectations, and makes navigation feel local. It does not need to be installable to do this.

The common production techniques are:

- **HTTP cache first, service worker later.** Use `Cache-Control`, `ETag`, `Last-Modified`, `stale-while-revalidate`, and `immutable` on the server. This works through the browser HTTP cache without a service worker ([MDN Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control), [web.dev stale-while-revalidate](https://web.dev/articles/stale-while-revalidate), [RFC 8246 immutable](https://httpwg.org/specs/rfc8246.html)).
- **Stable layout before content arrives.** Skeletons shaped like cards beat text spinners because users can orient while data loads.
- **Avoid huge DOMs.** `content-visibility: auto`, `contain-intrinsic-size`, CSS containment, pagination, and virtualization all reduce main-thread work. web.dev notes that large DOMs hurt interaction latency; list virtualization keeps only the visible window in the DOM ([content-visibility](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/content-visibility), [contain-intrinsic-size](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/contain-intrinsic-size), [DOM size and INP](https://web.dev/articles/dom-size-and-interactivity), [virtualized lists](https://web.dev/articles/virtualize-long-lists-react-window)).
- **Use native scroll as much as possible.** Avoid hijacking pull-to-refresh or momentum scroll. Use `overscroll-behavior` to stop scroll chaining in drawers/modals ([MDN overscroll behavior](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/overscroll-behavior)).
- **Use Pointer Events and `touch-action` deliberately.** Custom swipe/pan regions should not fight vertical scroll. `touch-action: pan-y` or `manipulation` is often better than blanket `none` ([Pointer Events](https://developer.mozilla.org/en-US/docs/Web/API/Pointer_events), [`touch-action`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/touch-action)).
- **Make navigation thumb-native.** Bottom nav, 44px+ tap targets, safe-area padding, and no text overflow.
- **Preload/prefetch carefully.** Use preload for critical assets, prefetch/speculation rules for likely next routes, and avoid spending mobile bandwidth on low-confidence guesses ([preload](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/rel/preload), [prefetch](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/rel/prefetch), [Speculation Rules](https://developer.mozilla.org/en-US/docs/Web/API/Speculation_Rules_API)).
- **Use bfcache-friendly lifecycle code.** Avoid `unload`; preserve browser back/forward speed ([web.dev bfcache](https://web.dev/articles/bfcache)).
- **Respect iOS notches and browser chrome.** `viewport-fit=cover` plus `env(safe-area-inset-*)` is the correct primitive for bottom nav and sheets ([WebKit iPhone X safe areas](https://webkit.org/blog/7929/designing-websites-for-iphone-x/), [MDN `env()`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env)).

### What works for PM

PM should prioritize:

- Add route-class HTTP caching before a service worker.
- Keep catalog pages paginated, but add `content-visibility: auto` and `contain-intrinsic-size` to card grids/cards.
- Convert "Loading..." text to skeleton rows/cards for sets, cards, and drawer detail.
- Add `rel=preload` for the most important CSS/JS/art assets only after the build exists.
- Use base-aware SvelteKit links and preserve ordinary browser back behavior.
- Add lightweight swipe-to-close for card detail bottom sheets and swipe-back from set cards to set list.
- Use native image lazy loading plus fixed aspect ratios.
- Add simple optimistic UI for watchlist toggles, with rollback if server-backed watchlist later fails.

### What does not work

Users immediately notice and reject:

- Scroll hijacking.
- Back button traps.
- Heavy filters/blurs during scroll.
- Full-screen spinners for known card/list shapes.
- Layout shifts after card images load.
- Infinite scroll that grows the DOM forever.
- Bottom nav or drawers that collide with iPhone safe areas.
- Notification/install banners before they have received value.

**What this means for PM:** the "great website, not PWA" path is technically real. It needs disciplined HTTP caching and interaction polish, not just dark CSS and a bottom nav.

---

## 5. Research area C - Gesture libraries and frameworks in 2026

### Survey

| Option | Status and fit | PM recommendation |
|---|---|---|
| Native Pointer Events + CSS | Best baseline. Pointer Events unify mouse/touch/pen; `touch-action` tells the browser which gestures stay native ([Pointer Events](https://developer.mozilla.org/en-US/docs/Web/API/Pointer_events), [`touch-action`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/touch-action)). | Use first for simple drawer close, swipe-back, long-press, and tap affordances. |
| `@svelte-put/swipeable` | Svelte action, small, swipe-focused, documented as `use:swipeable` / `swipe` ([npm](https://www.npmjs.com/package/@svelte-put/swipeable), [docs](https://svelte-put.vnphanquang.com/docs/swipeable)). | Best library candidate for PM's simple horizontal swipes. |
| `svelte-gestures` | Svelte-native recognizers for pan, pinch, press, rotate, swipe, tap; supports `touchAction` options ([GitHub](https://github.com/Rezi/svelte-gestures), [Made with Svelte](https://madewithsvelte.com/svelte-gestures)). | Use if PM needs richer pan/press composition than `@svelte-put/swipeable`. |
| `@use-gesture/vanilla` | Active, powerful, framework-agnostic, supports drag/pinch/scroll/wheel/move. Docs stress setting `touchAction` to prevent touch glitches ([npm](https://www.npmjs.com/package/@use-gesture/vanilla), [docs](https://use-gesture.netlify.app/docs/)). | Use only if PM needs complex drag/pinch physics. More machinery than simple card drawers need. |
| Svelte `svelte/motion` | Built-in springs/tweens; useful for fling-like motion and reduced-motion handling ([Svelte docs](https://svelte.dev/docs/svelte/svelte-motion)). | Pair with native/svelte gestures for animation. |
| Motion / Motion One | PM already has `motion` dependency. Good animation API; not a full Svelte gesture solution ([Motion](https://motion.dev/), [JS gestures](https://motion.dev/tutorials/js-gestures)). | Fine for polished transforms, but do not introduce it just for swipe detection. |
| GSAP Draggable/Inertia | Powerful, production-grade, heavier. Good for complex canvases/carousels ([Draggable](https://gsap.com/docs/v3/Plugins/Draggable/), [Inertia](https://gsap.com/docs/v3/Plugins/InertiaPlugin/)). | Overkill for PM v1. Consider only for advanced card gallery physics. |
| Hammer.js | Legacy. Open GitHub issues describe lack of maintenance ([issue 1197](https://github.com/hammerjs/hammer.js/issues/1197), [issue 1278](https://github.com/hammerjs/hammer.js/issues/1278)). | Avoid. Native pointer events and modern Svelte actions replace it. |

### iOS Safari jank rules

- Do not call `preventDefault()` broadly on touchmove; use `touch-action` and passive listeners where possible.
- Keep gesture animation to `transform` and `opacity`.
- Do not animate filters/backdrop filters in scroll regions.
- Preserve vertical scroll; horizontal swipes should have thresholds and velocity checks.
- Keep drawer/back gestures reversible and obvious.
- Honor `prefers-reduced-motion`.

**What this means for PM:** use native Pointer Events or `@svelte-put/swipeable` for the first gesture pass. Save Motion/GSAP for visual polish after the interaction model is proven.

---

## 6. Research area D - HTTP caching without a service worker

### Best-in-class route classes for PM

The audit found only 2 of 17 API routes with explicit `Cache-Control`. If PM drops the service worker path, HTTP caching becomes the speed story.

The relevant browser primitives are stable and service-worker-independent: `Cache-Control` defines freshness and stale behavior, `stale-while-revalidate` lets caches serve stale responses while revalidating, and `immutable` is meant for versioned resources that will not change during their freshness lifetime ([MDN Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control), [web.dev stale-while-revalidate](https://web.dev/articles/stale-while-revalidate), [RFC 8246](https://httpwg.org/specs/rfc8246.html)).

Recommended starting point:

| PM route/resource class | Suggested header | Notes |
|---|---|---|
| `/storefront/` HTML shell | `private, max-age=60, stale-while-revalidate=60` or `no-cache` during active development | Short TTL avoids stale shells while still helping repeat views. |
| `/storefront/_app/**` hashed SvelteKit assets | `public, max-age=31536000, immutable` | Safe only for hashed filenames. |
| `/storefront/manifest.json` or future webmanifest | `public, max-age=300, must-revalidate` | Keep low while correcting manifest scope/icons. |
| `/static/icons/*` unversioned icons | `public, max-age=86400` plus ETag | Prefer hashed/generated icon URLs before using immutable. |
| `/img/**` card images | `public, max-age=604800, stale-while-revalidate=86400` plus ETag | Move to content-hashed image URLs before one-year immutable. |
| `/api/cards-json` | `private, max-age=300, stale-while-revalidate=3600` | Currently `private, max-age=60`. This payload excludes live prices. |
| `/api/sets` | `private, max-age=3600, stale-while-revalidate=86400` | Set list changes slowly. |
| `/api/cards?set/color/type/page` | `private, max-age=300, stale-while-revalidate=1800` | Deterministic paginated catalog reads. |
| `/api/cards/<code>` and `/api/card-detail/<code>` | `private, max-age=3600, stale-while-revalidate=86400` | Static card text/variants are stable until catalog refresh. |
| `/api/card-variants/<code>` | `private, max-age=60, stale-while-revalidate=300` | Includes pricing fields, so keep short unless split variants from prices. |
| `/api/card-price/<code>` and `/api/card-prices` | `private, max-age=15, stale-while-revalidate=60` | Live-ish price data. |
| `/library-fragment` | Existing `private, max-age=20, stale-while-revalidate=10` is reasonable. | Could align with catalog route once Jinja/Svelte split is decided. |
| `/api/decks*`, `/api/watchlist/*`, API POSTs | `no-store` | User-specific/mutating; avoid cache ambiguity. |

For a public deployment behind a CDN, add `s-maxage` to shared-cache-safe reads. For Tailscale/private-only PM, browser cache is the main beneficiary.

### ETag and Last-Modified

Use:

- **ETag** for JSON responses where PM can compute a cheap hash or version string. If unchanged, clients can receive `304 Not Modified` instead of the full payload ([MDN ETag](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/ETag), [If-None-Match](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-None-Match)).
- **Last-Modified** where PM has an underlying file/db snapshot timestamp ([If-Modified-Since](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-Modified-Since)).
- Both are useful; `If-None-Match` takes precedence over `If-Modified-Since` when both are present.

For PM, the simplest high-value ETag keys are:

- catalog DB file mtime + size
- prices JSON mtime + size
- generated response hash for `/api/cards-json`

### CDN even for single-origin Flask

A CDN still helps a one-server Flask app when:

- PM becomes public beyond Tailscale.
- Card images dominate bandwidth.
- Catalog JSON is shared across users.
- Geographic latency matters.

It does less for private per-user deck/watchlist routes. If PM remains operator-only or tiny-community-only, route headers plus browser cache may be enough.

**What this means for PM:** HTTP caching is the first mobile performance layer. It is simpler, observable, and future-compatible with a service worker.

---

## 7. Research area E - Offline survival without a service worker

### What can work

Without a service worker, a mobile website can still survive some offline cases:

- **Back/forward cache:** recently visited pages can restore instantly with JS/DOM state while the tab remains eligible and memory is available ([web.dev bfcache](https://web.dev/articles/bfcache)).
- **HTTP cache:** cached assets/API responses may be reused if the browser permits, but the app cannot intercept arbitrary navigation/fetches or define custom fallbacks ([web.dev HTTP cache](https://web.dev/articles/http-cache), [service worker and HTTP cache](https://web.dev/articles/service-worker-caching-and-http-caching)).
- **localStorage:** good for tiny preferences/watchlists, roughly 5 MB class storage, synchronous and not suitable for catalog search.
- **IndexedDB:** good for structured catalog snapshots, local indexes, and larger data, within browser quota/eviction rules ([MDN storage quotas](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria), [web.dev storage](https://web.dev/articles/storage-for-the-web)).
- **First-load snapshot hydration:** fetch a compressed `cards.json.gz` when online, decompress/parse in a worker, batch into IndexedDB, and query locally later.

Scryfall demonstrates the data-product pattern: it provides bulk data files, changes their download URIs over time, updates bulk data on a schedule, and encourages consumers to cache/process locally ([Scryfall bulk data](https://scryfall.com/docs/api/bulk-data), [Scryfall API](https://scryfall.com/docs/api)). PM can copy the pattern for OP data without copying MTG details.

### What cannot work

Without a service worker:

- A cold offline navigation to `/storefront/` is not reliable.
- The app cannot reliably serve its own shell when the network is absent.
- The app cannot implement runtime cache strategies for image/API requests.
- Failed POSTs cannot be queued in the background unless PM builds its own foreground IndexedDB queue and retries only while the page is open.
- Push notifications are not available in the no-PWA/no-service-worker model.

### The iOS seven-day nuance

The operator's "7-day iOS eviction trap" is real, but it is not only a service-worker issue. WebKit documentation says the cap applies to script-writeable storage for sites without user interaction in Safari. WebKit's tracking-prevention documentation explicitly includes IndexedDB, localStorage/sessionStorage, service worker registrations, and service worker cache in script-writeable storage. Home Screen web apps are treated differently: WebKit says their first-party domain is exempt from the seven-day cap and has a separate use counter ([WebKit 2020 storage policy](https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/), [WebKit tracking prevention](https://webkit.org/tracking-prevention/)).

Therefore:

- Option 2 with no IndexedDB avoids building on script-writeable offline storage, but it has no real offline catalog.
- Option 3 with IndexedDB is useful for speed and short offline use, but it does **not** escape iOS eviction for non-installed users.
- A true installed PWA can improve persistence on iOS, but it reintroduces install friction and service-worker lifecycle risk.

### Middle path: no SW + IndexedDB snapshot

This is viable if PM frames it honestly:

- "Fast repeat lookup and partial offline catalog after first online load."
- Not "guaranteed offline app."

Implementation shape:

1. Expose a versioned snapshot endpoint, for example `/api/catalog-snapshot.json.gz` or `/storefront/data/cards-<hash>.json.gz`.
2. Include snapshot metadata: version, generated_at, card_count, schema_version.
3. On first load, ask StorageManager for quota estimate, then hydrate IndexedDB in batches.
4. Query IndexedDB for card search/browse; refresh in foreground when version changes.
5. Keep live prices network-first; do not bake them into long-lived snapshots.
6. If a later service worker arrives, it can reuse the IndexedDB cache.

**What this means for PM:** IndexedDB is a good second-stage acceleration layer, not a first-stage replacement for fixing the web surface and HTTP cache headers.

---

## 8. Research area F - Notifications handled separately

### Operator self-monitoring

For the operator's own phone, choose boring reliability:

| Channel | Fit | Recommendation |
|---|---|---|
| Pushover | Purpose-built personal push, simple API, paid but cheap, reliable ([Pushover](https://pushover.net/), [pricing](https://pushover.net/pricing), [API](https://pushover.net/api)). | Best "buzz my phone" option. |
| ntfy.sh | Open source, self-hostable, curl-friendly, iOS/Android apps ([ntfy](https://ntfy.sh/), [docs](https://docs.ntfy.sh/), [iOS app](https://apps.apple.com/us/app/ntfy/id1625396347)). | Best hacker/operator option, especially if self-hosting matters. |
| Discord webhook | Excellent audit trail in a private channel; free; already TCG-native ([Discord webhooks](https://discord.com/developers/docs/resources/webhook)). | Good if the operator lives in Discord. Less ideal for urgent personal alerts. |
| Telegram bot | Solid if the operator already uses Telegram ([Telegram Bot API](https://core.telegram.org/bots/api)). | Fine, but less aligned with Western TCG communities than Discord. |
| iOS Shortcuts webhook | Possible, but brittle and Apple-device-specific. | Avoid as primary alerting. |
| iCloud shared notes | Sync artifact, not a notification system. | Not recommended. |

### Public users

For TCG players, separate channels by intent:

| Channel | Fit | Recommendation |
|---|---|---|
| Discord bot/server posts | Strongest TCG fit. TCG Sniper sells Discord price-drop alerts; PokeNotify uses Discord for retail/deal monitoring; TCGMatchmaking and OPTCG SIM instructions lean on Discord communities ([TCG Sniper](https://tcgsniper.com/discord-alerts), [PokeNotify](https://pokenotify-1.gitbook.io/pokenotify/getting-started/publish-your-docs), [TCGMatchmaking Discord](https://discord.com/invite/tcgmatchmaking), [CardCapital OPTCG SIM](https://cardcapital.shop/en/blogs/tipps/optcg-sim-download-instructions-installation)). | Ship first for community alerts, price drops, new set updates, and meta notices. |
| In-app notification inbox | Pull model; works without browser permissions; good for "what changed since last visit." | Add once PM has accounts or persistent user state. |
| Email digest | Good for weekly watchlist movements, meta summaries, and low-urgency updates. | High-value, low-risk second channel. |
| RSS | Low-friction for power users and content/meta updates. | Cheap win. |
| SMS | Expensive, invasive, compliance-heavy ([Twilio Messaging](https://www.twilio.com/en-us/messaging)). | Only for urgent tournament logistics, not watchlist chatter. |
| OneSignal/Courier/Knock | Useful abstractions after PM needs multi-channel routing, preferences, templates, and scale ([OneSignal](https://onesignal.com/), [Courier](https://www.courier.com/), [Knock](https://www.knock.app/)). | Overkill until PM has enough users and channels. |
| Web Push | iOS requires Home Screen web app + service worker; opt-in fatigue; silent failure modes ([WebKit Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)). | Keep out of the web-app critical path. |

### What TCG players actually seem to want

Evidence is strongest for:

- Discord communities and bots.
- Price/deal alerts.
- Tournament/meta updates.
- Creator/community content loops.

Evidence is weak for:

- Desire for generic browser Web Push.
- Desire for SMS outside tournament logistics.
- Desire for installing a catalog PWA solely for notifications.

**What this means for PM:** the operator's separation is sound. Treat notifications as a channel product: Discord first, email/RSS/in-app next, Pushover/ntfy for the operator, and no dependency on iOS Web Push.

---

## 9. Research area G - Honest cost comparison

### Path 1: Ship the PWA as originally planned

Estimated effort: **about 10 worker-hours**, assuming no surprises.

User gets:

- Installable app surface when manifest/icons/scope are correct.
- Standalone display when installed.
- Service-worker-controlled offline shell and runtime caching ([MDN service workers](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers), [service worker and HTTP cache](https://web.dev/articles/service-worker-caching-and-http-caching)).
- Potential Web Push on supporting platforms, including iOS Home Screen web apps ([WebKit Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)).

User friction:

- iOS users must use Share -> Add to Home Screen; Apple/WebKit documentation frames iOS Web Push around Home Screen web apps ([Apple WWDC web apps](https://developer.apple.com/videos/play/wwdc2023/10120/)).
- Web Push requires permission and installed web app on iOS.
- Broken service-worker updates can strand stale clients.
- Cache limits/eviction require careful strategy and a kill switch.

Maintenance cost: highest. PM must own service-worker versioning, runtime cache rules, update prompts, purge/rollback paths, and iOS edge cases.

### Path 2: Great mobile website, no PWA

Estimated effort: **about 3-4 worker-hours** after build/base-path repair, focused on headers and gesture polish. Realistically, PM should budget a bit more if skeletons and base-aware links are included.

User gets:

- No install friction.
- Fast repeat loads through HTTP cache.
- Normal browser sharing/deep linking.
- Better mobile interactions.
- Notifications through Discord/email/RSS/in-app/Pushover rather than Web Push.

User loses:

- No reliable offline shell.
- No Home Screen app identity.
- No Web Push from the web app.
- No background sync.

Maintenance cost: lowest. It uses ordinary HTTP, browser cache, Svelte components, and server headers.

### Path 3: Great mobile website + IndexedDB catalog snapshot, no service worker

Estimated effort: **about 5-6 worker-hours** for a minimal snapshot hydrate if the snapshot format is straightforward; more if search indexing, compression workers, migrations, and stale cleanup are robust.

User gets:

- Fast repeat catalog lookup after first hydration.
- Partial offline catalog while the app shell/page is already available.
- Better low-signal browsing of stable card data.
- A future-compatible cache layer if a service worker is added later.

User loses:

- No guaranteed cold offline navigation.
- No durable iOS persistence for non-installed users beyond WebKit's storage rules.
- No image offline guarantee unless images are separately cached by HTTP cache or later SW.
- No Web Push.

Maintenance cost: medium. PM must version snapshots, migrate IndexedDB, cap storage, and recover from corruption/eviction.

---

## 10. The three paths compared

| Dimension | Path 1: true PWA | Path 2: mobile website, no PWA | Path 3: mobile website + IndexedDB snapshot |
|---|---|---|---|
| Worker-hours to ship | ~10h | ~3-4h for cache/gesture polish; add time for base/build fixes | ~5-6h for minimal snapshot; more for robust local search |
| User value | Install, standalone, offline shell, possible push | Fast, shareable, no install friction, app-like interactions | Path 2 plus faster repeat catalog and partial offline card data |
| iOS | Best only after Home Screen install; Web Push requires installed web app; storage rules improve for Home Screen apps | Works as ordinary Safari site; no push/offline shell | Works, but IndexedDB still subject to non-installed storage eviction |
| Android | Strongest PWA platform: install prompts, SW offline, push | Works well in Chrome; no install/push | Good IndexedDB support; no install/push |
| Desktop | Installable in Chromium/Edge; standard web otherwise | Standard web | Standard web with local catalog cache |
| Offline | Best if SW is correct | Opportunistic only: bfcache/HTTP cache | Catalog data after hydration, but shell/navigation not guaranteed |
| Notifications | Possible but not recommended as critical path | External channels | External channels |
| Maintenance | High: SW lifecycle, cache cleanup, update/rollback | Low | Medium: IDB schema/version/storage |
| What breaks | Bad SW can cache wrong things; manifest scope bugs launch wrong route; stale clients | Offline and install expectations | Users may think it is "offline" when only data is cached |
| Upgrade path | Can later add richer IDB/native shell | Cleanly upgrades to Path 3 or Path 1 | Cleanly upgrades to Path 1 by adding SW shell/runtime caching |
| Dead end? | No, but front-loads complexity | No | No |

---

## 11. Recommendation + dissent

### Recommendation

Ship **Path 2 now**: great mobile website, no PWA as the critical path.

Do this in order:

1. Build and serve the SvelteKit storefront.
2. Fix `/storefront` base-path issues: manifest href, nav hrefs, any route assumptions.
3. Add HTTP caching by route class.
4. Add mobile polish: skeletons, stable image/card dimensions, `content-visibility`, safe-area checks, and simple swipe-to-close/back gestures.
5. Move notifications to separate channels: Pushover or ntfy for operator alerts; Discord bot/webhook plus email/RSS/in-app inbox for public users.
6. Measure actual mobile behavior before adding service-worker complexity.

Then consider **Path 3** if users actually need faster repeat catalog lookup or weak-network browsing. Keep it framed as a local catalog accelerator, not durable offline on iOS.

### Strongest case against this recommendation

The strongest dissent is that PM is a catalog app, and catalog apps are unusually well-suited to offline-first design. If PM users are tournament players in poor-signal venues, a reliable installed PWA with cached shell, cached card database, and cached art may feel meaningfully better than a web-only experience. Android users in particular could benefit from install prompts and service-worker offline. Several respected competitors expose manifests; PM may look less "app-like" if it refuses install forever.

That dissent becomes decisive if PM sees:

- High Android mobile share.
- Repeated requests for Home Screen install.
- Venue usage with unreliable service.
- Users opening PM primarily as a reference app, not via search/social links.
- Catalog snapshot size small enough to cache comfortably with images.

Until then, PWA is a later upgrade, not the next front door.

---

## 12. Sources

### Local code and audit

- `data/batch_reports/pm_pwa_audit_2026-04-19.md`
- `pm/CLAUDE.md`
- `pm/app.py`
- `pm/storefront/svelte.config.js`
- `pm/storefront/vite.config.ts`
- `pm/storefront/src/app.html`
- `pm/storefront/src/routes/+layout.svelte`
- `pm/storefront/static/manifest.json`
- `pm/templates/miru_nav_shell.html`
- `pm/routes/api.py`
- `pm/routes/pages.py`
- `pm/services/watchlist.py`
- `pm/storefront/src/app.css`
- `pm/storefront/src/lib/components/BottomNav.svelte`
- `pm/storefront/src/lib/components/PageShell.svelte`
- `pm/storefront/src/routes/cards/+page.svelte`
- `pm/storefront/src/routes/deck-builder/+page.svelte`
- `pm/storefront/src/lib/api/client.ts`

### TCG/catalog landscape

- https://scryfall.com/
- https://scryfall.com/manifest.webmanifest
- https://scryfall.com/docs/api
- https://scryfall.com/docs/api/bulk-data
- https://scryfall.com/docs/api/rate-limits
- https://www.similarweb.com/website/scryfall.com/
- https://www.similarweb.com/website/edhrec.com/
- https://www.similarweb.com/website/moxfield.com/
- https://www.similarweb.com/website/archidekt.com/
- https://www.similarweb.com/website/cardmarket.com/
- https://www.similarweb.com/website/scryfall.com/competitors/
- https://www.similarweb.com/website/moxfield.com/competitors/
- https://edhrec.com/
- https://edhrec.com/guides/how-to-use-edhrec
- https://edhrec.com/articles/digital-deckbuilding-card-searches-on-edhrec-and-archidekt
- https://archidekt.com/
- https://archidekt.com/manifest.json
- https://www.tcgplayer.com/
- https://www.cardkingdom.com/
- https://www.channelfireball.com/
- https://www.mtggoldfish.com/
- https://onepiece.limitlesstcg.com/
- https://onepiece.limitlesstcg.com/app.webmanifest
- https://onepiece.limitlesstcg.com/decks
- https://onepiece.limitlesstcg.com/cards/advanced
- https://www.cardkaizoku.com/
- https://www.cardkaizoku.com/manifest.json
- https://deckbuilder.cardkaizoku.com/
- https://deckbuilder.cardkaizoku.com/manifest.json
- https://tcgmatchmaking.com/
- https://tcgmatchmaking.com/pages/tcg-tournaments-page
- https://egmanevents.com/one-piece-op14-tcg-matchmaking-power-rankings
- https://onepiece.gg/
- https://onepiece.gg/meta/
- https://onepiece.gg/cards/
- https://www.optcg.gg/search
- https://en.onepiece-cardgame.com/
- https://en.onepiece-cardgame.com/cardlist/
- https://apps.apple.com/us/app/op-tcg-deck-builder/id6451478386
- https://play.google.com/store/apps/details?id=app.biiscuit.opdb&hl=en_US

### Mobile web patterns and browser APIs

- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control
- https://web.dev/articles/stale-while-revalidate
- https://web.dev/articles/http-cache
- https://httpwg.org/specs/rfc8246.html
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/ETag
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-None-Match
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-Modified-Since
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/content-visibility
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/contain-intrinsic-size
- https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Containment
- https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API
- https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API/Using
- https://developer.mozilla.org/en-US/docs/Web/API/Pointer_events
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/touch-action
- https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Scroll_snap
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/overscroll-behavior
- https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/rel/preload
- https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/rel/prefetch
- https://developer.mozilla.org/en-US/docs/Web/API/Speculation_Rules_API
- https://developer.chrome.com/docs/web-platform/implementing-speculation-rules
- https://webkit.org/blog/7929/designing-websites-for-iphone-x/
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env
- https://web.dev/articles/bfcache
- https://web.dev/articles/virtualize-long-lists-react-window
- https://web.dev/articles/dom-size-and-interactivity

### Gesture libraries

- https://github.com/hammerjs/hammer.js/issues/1197
- https://github.com/hammerjs/hammer.js/issues/1278
- https://www.npmjs.com/package/@use-gesture/vanilla
- https://use-gesture.netlify.app/docs/
- https://use-gesture.netlify.app/docs/gestures/
- https://use-gesture.netlify.app/docs/options/
- https://github.com/Rezi/svelte-gestures
- https://madewithsvelte.com/svelte-gestures
- https://www.npmjs.com/package/@svelte-put/swipeable
- https://svelte-put.vnphanquang.com/docs/swipeable
- https://svelte.dev/docs/svelte/svelte-motion
- https://motion.dev/
- https://motion.dev/tutorials/js-gestures
- https://motion.dev/docs/react-gestures
- https://gsap.com/docs/v3/Plugins/Draggable/
- https://gsap.com/docs/v3/Plugins/InertiaPlugin/

### Offline, storage, PWA, and push

- https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/
- https://webkit.org/tracking-prevention/
- https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- https://web.dev/articles/storage-for-the-web
- https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- https://developer.apple.com/videos/play/wwdc2023/10120/
- https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers
- https://web.dev/articles/service-worker-caching-and-http-caching
- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest
- https://kit.svelte.dev/docs/adapter-static
- https://vite-pwa-org.netlify.app/frameworks/sveltekit

### Notifications and community channels

- https://docs.ntfy.sh/
- https://ntfy.sh/
- https://apps.apple.com/us/app/ntfy/id1625396347
- https://pushover.net/
- https://pushover.net/pricing
- https://pushover.net/api
- https://core.telegram.org/bots/api
- https://discord.com/developers/docs/resources/webhook
- https://discord.com/invite/optcg
- https://discord.com/invite/tcgmatchmaking
- https://tcgsniper.com/discord-alerts
- https://pokenotify-1.gitbook.io/pokenotify/getting-started/publish-your-docs
- https://cardcapital.shop/en/blogs/tipps/optcg-sim-download-instructions-installation
- https://onesignal.com/
- https://www.courier.com/
- https://www.knock.app/
- https://www.twilio.com/en-us/messaging

---

## Completion status

**CONFIRMED WORKING** - report written to `data/batch_reports/pm_mobile_strategy_research_2026-04-20.md`; all seven research areas covered; PM code spot-checked against the audit; route count/cache headers verified; recommendation and dissent stated. No PM code files, services, databases, or Notion pages were modified.
