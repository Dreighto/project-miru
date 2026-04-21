# Project Miru Framework Research - 2026-04-20

## 1. Executive Summary

**Recommendation: keep SvelteKit 2 / Svelte 5 for the storefront, use shadcn-svelte + Bits UI + a small set of custom mobile primitives, and keep the existing Flask + SQLite backend.**

Top three reasons:

1. **PM's real bottleneck is not the framework.** The app needs virtualized catalog views, disciplined image loading, better HTTP caching, touch-aware sheets/drawers, optimistic state, and mobile route transitions. SvelteKit, React, Vue, and Solid can all do that, but PM already has a working SvelteKit static deployment path under Flask.
2. **The current backend and database match the job.** PM has a small read-heavy catalog, 17 API routes, SQLite files on local disk, and a Windows home-server deployment. Rewriting Flask or SQLite would not make card search, swipe sheets, or image browsing feel more native.
3. **SvelteKit is a better fit for the stated constraints than Next.js full-stack.** SvelteKit's `adapter-static` is designed to emit static files for any server [SvelteKit adapter-static](https://svelte.dev/docs/kit/adapter-static), and Svelte 5 runes give explicit, local reactivity without React's larger runtime model [Svelte runes](https://svelte.dev/docs/svelte/what-are-runes). PM does not need app-store distribution, SEO, Vercel, React Server Components, edge functions, or server actions.

**The thing that could flip the recommendation:** visual velocity. If one focused design sprint in Svelte cannot produce a sleek, app-feeling catalog surface quickly, switch early to **Vite + React Router SPA + shadcn/ui + Vaul + Motion**, while keeping Flask and SQLite. React's component and AI tooling ecosystem is the strongest case for rewriting.

## 2. Shortlist

| Rank | Stack | Why it ranks here | Main risk |
|---:|---|---|---|
| 1 | **SvelteKit 2 + Svelte 5 + shadcn-svelte/Bits UI + Motion/native gestures + Flask + SQLite** | Best match to current repo, static `/storefront` serving, small runtime, simple deployment, no backend rewrite. shadcn-svelte is already configured locally and is built on Bits UI [shadcn-svelte docs](https://www.shadcn-svelte.com/docs), [Bits UI docs](https://bits-ui.com/docs). | React still has more templates, mobile drawer/sheet examples, AI training coverage, and polished "copy this dashboard" material. |
| 2 | **Vite + React Router SPA + shadcn/ui + Vaul + Motion + Flask + SQLite** | Best escape hatch if visual quality and AI delegation beat deployment minimalism. Keeps a static bundle under Flask while unlocking React's shadcn/ui, Radix, Vaul, Motion, and template market. React Router supports SPA/data/framework modes, and v7 brought Remix features back into React Router [React Router v7](https://remix.run/blog/react-router-v7). | Adds React runtime and rebuild cost; still requires PM-specific mobile primitives and catalog performance work. |
| 3 | **Next.js 15 + shadcn/ui/Magic UI/Aceternity + Flask API or Node runtime + SQLite** | Deepest ecosystem, best shadcn defaults, most AI examples, and widest hiring/training surface. Static export can run on any web server [Next static export](https://nextjs.org/docs/app/building-your-application/deploying/static-exports); self-hosting is documented [Next self-hosting](https://nextjs.org/docs/15/app/guides/self-hosting). | Over-solves PM. Static export removes middleware/proxy/dynamic server features; full App Router adds Node process, RSC client/server boundaries, and more deployment surface. |

Stacks eliminated as primary choices:

- **Astro:** excellent for content and low-JS pages, but PM is an interactive catalog/deck tool, not a content site.
- **Nuxt 3:** polished, especially with Nuxt UI, but switching to Vue buys less than switching to React and discards existing Svelte work.
- **SolidStart:** excellent fine-grained performance, weaker component and AI ecosystem for this operator.
- **Qwik/QwikCity:** resumability is interesting, but PM's catalog screen will be interactive immediately; ecosystem and AI coverage are thinner.
- **TanStack Start:** technically exciting, but still RC as of the official docs and heavier than PM needs [TanStack Start overview](https://tanstack.com/start/latest/docs/framework/react/overview).

**Repo facts used for this recommendation:**

- `pm/storefront/package.json` already has SvelteKit 2, Svelte 5, Vite, Tailwind v4, and `motion`.
- `pm/storefront/components.json` already points at the shadcn-svelte registry.
- `pm/storefront/src/lib/components/` currently contains only `BottomNav.svelte` and `PageShell.svelte`; the framework decision is still early.
- `pm/app.py` serves the static storefront build at `/storefront/`.
- `pm/routes/api.py` exposes 17 API routes; `pm/routes/pages.py` keeps the legacy Jinja surface.
- `pm/db.py` uses SQLite for `card_catalog.db` and `pm_decks.db`; prices are disk-backed JSON.

## 3. Deep Dive Per Area

### 1. Frontend Framework Landscape For Catalog/Mobile Apps In 2026

| Framework | Mobile performance fit | Gesture/app-feel ergonomics | Ecosystem and AI coverage | PM verdict |
|---|---|---|---|---|
| **SvelteKit 2 + Svelte 5** | Strong. Svelte compiles components, and Svelte 5 runes are explicit compiler syntax for local reactive state [Svelte runes](https://svelte.dev/docs/svelte/what-are-runes). Static build under Flask is first-class [SvelteKit adapter-static](https://svelte.dev/docs/kit/adapter-static). | Good with native pointer events, Svelte actions, CSS `touch-action`, Svelte transitions, View Transitions API, and headless components. | Smaller than React but active. State of JS 2024 shows Svelte has high positive opinion while React leads usage [State of JS 2024](https://2024.stateofjs.com/en-US/libraries/front-end-frameworks/). | **Recommended.** PM's constraints favor static, small, focused UI work over framework breadth. |
| **Next.js 15 App Router** | Strong if used carefully, but App Router, RSC, caching, client/server boundaries, and image behavior add mental load. Static export is supported but has unsupported server features [Next static export](https://nextjs.org/docs/app/building-your-application/deploying/static-exports). | React ecosystem is best-in-class for polished drawers, sheets, animations, and demos. | Best overall. shadcn/ui, Radix, Vaul, Motion, Magic UI, Aceternity, Vercel templates, v0 examples, and broad AI training data. | **Not primary.** Choose only if React/shadcn visual velocity is worth the added complexity. |
| **React Router v7** | Good. Can be a simple SPA router or a full framework. v7 added Remix-style framework features [React Router v7](https://remix.run/blog/react-router-v7). | Very good with React libraries; official docs include View Transitions topics and SPA mode [React Router docs](https://reactrouter.com/start/framework/installation). | Strong React coverage, less template gravity than Next. | **Best rewrite target.** Use Vite + React Router SPA, not full-stack mode, if PM pivots. |
| **SolidStart** | Excellent raw UI performance potential due to fine-grained reactivity. | Good in principle, thinner ecosystem for mobile sheets/drawers and fewer ready-made catalog examples. | Smaller AI and component coverage. | Not worth the solo-operator risk for PM. |
| **Qwik / QwikCity** | Resumability helps delayed interactivity pages, but PM's main screen is immediately interactive. | Fewer production-grade gesture primitives and templates. | Thin compared with React/Svelte/Vue. | Not recommended. |
| **Nuxt 3** | Strong Vue meta-framework; good deployment options. | Nuxt UI is polished, VueUse Motion helps. | Good, but not better than React for AI/examples and not connected to current code. | Viable but not compelling. |
| **Astro** | Excellent for static/content and islands. | App-like catalog workflows need persistent client state and lots of interactive islands. | Good docs and growth, weaker for this specific app shape. | Wrong center of gravity. |
| **TanStack Start** | Promising full-stack React framework powered by TanStack Router and Vite; official docs say Release Candidate and feature-complete but not bug-free [TanStack Start overview](https://tanstack.com/start/latest/docs/framework/react/overview). | Could become strong because TanStack Router/Query/Virtual fit catalog apps. | Growing, but less common than Next/React Router for AI workers. | Watch later; do not bet PM on it now. |
| **Vite + React Router SPA** | Fast, simple, static, and avoids Next server machinery. | Excellent because it can use shadcn/ui, Vaul, Motion, @use-gesture, and Radix. | Strong enough and easier than full Next for this project. | **Credible alternative.** Best path if Svelte visual sprint fails. |

Benchmarks: the best public raw DOM benchmark source is the Krause JS framework benchmark, which publishes current Chrome result sets and warns that comparisons across browser versions need care [js-framework-benchmark](https://krausest.github.io/js-framework-benchmark/). It is useful for reminding us that Solid/Svelte-style reactive systems can be very fast, but it is not a direct proxy for PM. PM's perceived speed will be dominated by image sizing, cache headers, virtualized lists, and avoiding scroll jank.

**What this means for PM:** do not rewrite for theoretical framework speed. Rewrite only if React's component and AI ecosystem materially improves visual delivery speed.

### 2. Component Libraries - What Actually Looks Good

| Library | Framework center | Visual quality out of the box | Mobile/sheet/drawer story | Maintenance burden | PM take |
|---|---|---|---|---|---|
| **shadcn/ui** | React | Excellent modern baseline; de facto aesthetic benchmark. It is a code distribution approach, not a packaged component library [shadcn CLI](https://ui.shadcn.com/docs/cli), [shadcn registry](https://ui.shadcn.com/docs/registry). | Needs Vaul/Radix/custom mobile composition. Many examples skew dashboard/form/desktop. | Medium: you own copied code. | Best visual ecosystem, strongest rewrite argument. |
| **shadcn-svelte** | Svelte | Strong. It explicitly ports shadcn/ui to Svelte/SvelteKit and is powered by Bits UI, Formsnap, Paneforge, and Vaul Svelte [shadcn-svelte about](https://www.shadcn-svelte.com/docs/about). | Viable. Drawer and headless primitives exist, but fewer examples than React. | Medium: same copied-code model, smaller community. | Best fit if staying Svelte. |
| **Magic UI** | React/shadcn | Great for animated accents, hero-style polish, and delight. | Not a mobile interaction library; use sparingly in a catalog. | Medium-high if overused. | Reference for motion taste, not the core UI system. |
| **Aceternity UI** | React | High-impact, trendy, visually loud. | Mostly showcase/marketing-style components. | High if PM needs restraint and performance. | Useful inspiration, not foundation. |
| **Park UI** | Multi-framework via Ark UI/Panda CSS | Polished and framework-agnostic. | Ark primitives help; styling stack differs from PM's Tailwind setup. | Medium-high due to Panda/CSS architecture. | Not worth switching styling systems now. |
| **Skeleton UI** | Svelte | Svelte-first, production-friendly, complete design system. | Good app shell primitives; visual style differs from shadcn. | Lower than headless-only. | Good backup if shadcn-svelte feels too thin. |
| **Bits UI** | Svelte | Headless, not styled. | Strong accessibility foundation for dialogs, popovers, tabs, etc. [Bits UI](https://bits-ui.com/docs). | Requires design work. | Core primitive layer for Svelte path. |
| **Melt UI** | Svelte | Headless. | Useful, but shadcn-svelte has standardized on Bits UI. | Medium. | Prefer Bits unless a specific Melt primitive is better. |
| **Tailwind UI / Catalyst** | React/Tailwind | Very polished, conservative, paid. | Good forms/layouts; not a gesture library. | Low-medium. | Could inspire patterns; not enough reason to rewrite. |
| **Nuxt UI / Nuxt UI Pro** | Vue/Nuxt | Polished and coherent. | Good Vue app UI. | Low-medium in Nuxt. | Only matters if choosing Nuxt, which is not recommended. |
| **Mantine** | React | Strong defaults, broad component coverage. | Drawers/modals good, but aesthetic is Mantine's system rather than shadcn. | Low-medium. | Good productivity stack, less "cool/sleek" than shadcn ecosystem for PM. |
| **Radix + Tailwind** | React | Whatever you design. shadcn/ui builds on this idea. | Strong accessible primitives, no visual system by itself [Radix primitives](https://www.radix-ui.com/primitives). | Higher DIY burden. | Use through shadcn unless PM needs custom primitives. |

**What this means for PM:** the Svelte stack can meet the visual requirement, but it will need taste and a small custom primitive kit. React's advantage is not raw capability; it is the density of polished examples and AI-copyable prior art.

### 3. Gesture And Animation Libraries Per Framework

| Stack | Best gesture tools | Best animation/page-transition tools | Bottom sheets / swipe-to-close / long-press difficulty | PM judgment |
|---|---|---|---|---|
| **SvelteKit** | Native Pointer Events, Svelte actions, CSS `touch-action`, shadcn-svelte/Bits/Vaul Svelte. | Svelte `transition:` / `animate:`, Motion package, CSS View Transitions API. MDN marks View Transitions as Baseline 2025 [MDN ViewTransition](https://developer.mozilla.org/en-US/docs/Web/API/ViewTransition). | Medium. Build a small `BottomSheet`, `SwipeRow`, `LongPress`, and `PressableCard` layer once. | Good enough and lightweight. |
| **React / Vite / React Router** | @use-gesture, Vaul, Radix, native Pointer Events. | Motion/Framer Motion, React Router View Transitions, GSAP for complex cases. | Low-medium. More examples and battle-tested packages. | Best off-the-shelf gesture ecosystem. |
| **Next.js** | Same as React. | Same as React, but App Router route boundaries can complicate page transitions. | Low-medium for component gestures; medium for route choreography. | Great, but only if accepting Next complexity. |
| **Nuxt/Vue** | VueUse gestures/composables, native Pointer Events. | VueUse Motion, Nuxt transitions. | Medium. | Viable, weaker ecosystem fit for PM. |
| **Solid/SolidStart** | Native gestures, Solid primitives, Ark UI where available. | Motion One-style patterns and Solid transition tools. | Medium-high due to fewer examples. | Technically strong, solo-operator risk. |
| **Qwik/Astro** | Mostly native CSS/JS. | View Transitions, island-specific animation. | High for app-shell interactions. | Not ideal for gesture-native catalog. |

Important mobile implementation notes:

- Use CSS `touch-action` so the browser knows which gestures belong to native scroll and which belong to the app [MDN touch-action](https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action).
- Avoid JS-driven scrolling unless absolutely necessary. Let iOS momentum scrolling and native overflow do the heavy lifting.
- Treat Apple sheets as the target mental model: detents, grabber, swipe-to-dismiss, and context preservation [Apple HIG sheets](https://developer.apple.com/design/human-interface-guidelines/sheets).
- GSAP Draggable is excellent for complex dragging, snapping, and inertia, but it is overkill for ordinary card swipes and bottom sheets [GSAP Draggable](https://gsap.com/docs/v3/Plugins/Draggable).

**What this means for PM:** if staying Svelte, invest early in four primitives: `BottomSheet`, `SwipeableRow`, `PressableCard`, and `VirtualCardGrid`. That is where "feels like an app" will come from.

### 4. Backend - Does PM Need To Change?

**Current PM backend:** Flask + Waitress, synchronous HTTP, static SvelteKit storefront under `/storefront`, Jinja fallback/pages, SQLite, disk JSON prices. This is coherent for a Windows home server. Waitress is a pure-Python WSGI server and supports Windows directly [Flask Waitress deployment](https://flask.palletsprojects.com/en/stable/deploying/waitress/).

Options:

- **Stay Flask:** nothing breaks if the frontend stays SvelteKit or moves to a static React SPA. Flask can keep serving `/api/*`, `/cards`, `/deck-builder`, images, and `/storefront/*`.
- **Next.js full-stack:** useful if PM wants React Server Components, server actions, and Next-native data fetching. But PM would add a Node server or accept static export limits. Next's static export docs explicitly distinguish static hosting from server features [Next static export](https://nextjs.org/docs/app/building-your-application/deploying/static-exports), while self-hosting assumes a Next server for dynamic features [Next self-hosting](https://nextjs.org/docs/15/app/guides/self-hosting).
- **Node/Nitro/Hono/Express:** realistic on Windows, but duplicates working Python logic and adds another runtime.
- **Rust Axum / Go:** excellent performance, not justified by a 2,500-card catalog and local SQLite.
- **FastAPI:** the only credible Python backend rewrite. It buys OpenAPI, Pydantic models, async ergonomics, and better typed client generation [FastAPI](https://fastapi.tiangolo.com/). It does not directly improve mobile UI.
- **BaaS/Supabase/Neon/PlanetScale:** useful for public auth, hosted dashboards, and managed Postgres. It cuts against the home-server/Tailscale/local-disk simplicity and does not respect the existing "sacred" SQLite catalog without extra sync work.

**What this means for PM:** keep Flask. If public users arrive and API contracts start hurting, add OpenAPI-style schemas or migrate route-by-route to FastAPI later. Do not tie the frontend framework decision to a backend rewrite.

### 5. Database - Is SQLite Still Right?

SQLite is still the right database for PM's current shape:

- The catalog is small and read-heavy.
- SQLite is explicitly designed for local, self-contained application data and works well for low-to-medium traffic websites; the official "When to use SQLite" page says sites below 100K hits/day are generally fine, and that number is conservative [SQLite when to use](https://www.sqlite.org/whentouse.html).
- The current split is pragmatic: read-only `card_catalog.db`, writable `pm_decks.db`, and JSON for price/watchlist support.
- The file-based deployment matches a Windows mini PC and simple backups.

Limitations to respect:

- SQLite is not ideal if many machines write directly to the same database over a network filesystem [SQLite when to use](https://www.sqlite.org/whentouse.html).
- Public user accounts, social features, team decks, comments, or high-concurrency writes could justify Postgres later.
- Disk JSON prices are acceptable for a solo tool but may become the first data layer to formalize into SQLite.

Alternatives:

- **Postgres:** stronger for public multi-user writes, hosted dashboards, replication, and analytics. Not needed now.
- **Hybrid:** keep card catalog in SQLite, move user/account data to Postgres only when public accounts become real.
- **libSQL/Turso:** interesting if PM wants SQLite-compatible edge replication, but Tailscale/home-server access does not need it now.
- **Litestream:** a practical backup/replication layer for SQLite that runs as a separate process and continuously replicates WAL changes [Litestream](https://litestream.io/), [Litestream how it works](https://litestream.io/how-it-works/). Best applied later to `pm_decks.db`.

**What this means for PM:** keep SQLite. Add indexes/cache headers before changing databases. Consider Litestream for the writable DB once PM matters enough to need point-in-time recovery.

### 6. AI Tooling Coverage Per Framework

Evidence here is thinner than for framework docs. I found adoption and tooling signals, but no rigorous public dataset that measures Claude Code/Cursor/Copilot hallucination rates per framework.

Practical signal:

- **React/Next/shadcn wins AI coverage.** React has the broadest usage base in surveys and examples; shadcn's CLI, registry, docs lookup, templates, and ecosystem give AI workers more copyable targets [shadcn CLI](https://ui.shadcn.com/docs/cli), [Stack Overflow Survey 2025](https://survey.stackoverflow.co/2025/technology/).
- **SvelteKit is good but has a newer syntax trap.** Svelte 5 runes are official and clear [Svelte runes](https://svelte.dev/docs/svelte/what-are-runes), but AI workers can still mix in Svelte 3/4 `$:` and `export let` habits unless tasks point them to local patterns.
- **Next.js is not hallucination-proof.** AI workers often mix Pages Router/App Router, server/client components, server actions, and static export assumptions. Next's breadth is an advantage and a source of mistakes.
- **TanStack Start is unusually LLM-conscious but early.** The docs expose markdown/LLM affordances and list LLM Optimization, but the framework is still RC [TanStack Start overview](https://tanstack.com/start/latest/docs/framework/react/overview). Nice signal, not enough for PM.

Mitigation if staying Svelte:

- Create a tiny local convention doc before building primitives: Svelte 5 runes only, shadcn-svelte component placement, `$app/paths` for base paths, no old Svelte event syntax in new files.
- Generate or copy the first primitives from shadcn-svelte, then make AI workers follow those files.
- Keep tasks narrow: one primitive, one route, one interaction, one Playwright mobile check.

**What this means for PM:** AI velocity is the strongest anti-Svelte argument. It is not strong enough to force a rewrite today, but it should be a decision gate after the first UI sprint.

### 7. Honest Migration Cost

PM is early enough that a rewrite is possible. It is not early enough that a rewrite is free: route structure, API client logic, base-path handling, card/deck UI behavior, Tailwind tokens, and deployment scripts still have to be recreated and checked.

Cost estimates below are AI-worker-hours, assuming focused agents with repo context and human review.

| Path | One-time migration cost | UI build/polish still needed | Backend/DB change | Ongoing cost | Notes |
|---|---:|---:|---|---|---|
| **Stay SvelteKit** | 0-4h cleanup | 20-35h for real mobile primitives and catalog polish | None | Low | Must fix current storefront issues, base paths, cache headers, primitive set. |
| **Vite + React Router SPA + shadcn/ui** | 12-22h | 16-28h | None | Medium | Best rewrite if React visual ecosystem is decisive. Static bundle can still be served by Flask. |
| **Next.js static export + shadcn/ui** | 16-30h | 16-28h | None, if Flask API stays | Medium-high | Static export works, but PM must avoid unsupported server features and configure images/base paths carefully. |
| **Next.js full-stack** | 35-70h | 16-28h | Likely partial API migration | High | Most ecosystem, most moving parts. Not aligned with home-server simplicity. |
| **React Router v7 framework mode** | 18-32h | 16-28h | Optional Node server | Medium-high | Better than Next for routing/data clarity, but full-stack mode is unnecessary for PM. |
| **FastAPI backend rewrite only** | 12-22h | 0h UI benefit | Flask to FastAPI | Medium | Good for OpenAPI and types, not for mobile feel. |
| **Postgres migration** | 16-32h | 0h UI benefit | SQLite to Postgres | Medium-high | Only justified by public multi-user writes/accounts. |

**What this means for PM:** the cheapest path to a better app is not a framework rewrite. It is a mobile UI sprint plus HTTP/cache/image/list work. The cheapest credible rewrite is React SPA under Flask, not Next full-stack.

## 4. Migration Cost Comparison

| Criterion | Stay SvelteKit + Flask + SQLite | React SPA + Flask + SQLite | Next.js + shadcn + Flask/Node + SQLite |
|---|---|---|---|
| Worker-hours to ship framework decision | 0-4h cleanup | 12-22h migration | 16-30h static, 35-70h full-stack |
| Worker-hours to ship polished mobile UI | 20-35h | 16-28h | 16-28h |
| Ongoing maintenance | Low | Medium | Medium-high |
| iOS | Works as normal web app; must build sheets/gestures carefully | Works; best package support for sheets/gestures | Works; beware App Router client/server boundaries and static export limits |
| Android | Works | Works | Works |
| Desktop | Works; Jinja can remain fallback | Works; Jinja can remain fallback | Works; may split app between Flask and Node/Next |
| What breaks | Existing Svelte bugs remain until fixed; AI may mix Svelte syntax | Svelte storefront discarded; React app needs base-path/static serving | Static export loses middleware/proxy/dynamic features; full-stack adds process/deploy complexity |
| Upgrade path | Can later add service worker, IndexedDB, or React rewrite if needed | Can later move to Next or React Router framework mode | Can move deeper into full-stack Next, but harder to simplify |
| Best reason to choose | PM constraints, speed, deployment simplicity | Visual velocity and AI/tooling ecosystem | Maximum React ecosystem and templates |
| Best reason not to choose | Smaller visual example pool | Adds rewrite cost and React runtime | Overkill for Tailscale home-server catalog |

## 5. Recommendation + Dissent

### Recommended Stack

Keep:

- **Frontend:** SvelteKit 2 + Svelte 5, static adapter, served under Flask at `/storefront`.
- **UI:** shadcn-svelte for styled primitives, Bits UI for headless primitives, Skeleton UI only if shadcn-svelte proves too thin.
- **Gestures:** native Pointer Events, CSS `touch-action`, Svelte actions, Vaul Svelte/shadcn-svelte drawer where appropriate, CSS/View Transitions for route polish.
- **Backend:** Flask + Waitress.
- **Database:** SQLite, with `card_catalog.db` preserved and `pm_decks.db` kept local.

Decision gate:

1. Build one Svelte design sprint before committing months of work.
2. Target five primitives: `BottomNav`, `VirtualCardGrid`, `CardDetailSheet`, `FilterSheet`, `WatchlistSwipeAction`.
3. Verify on mobile viewport screenshots.
4. If the result does not look cool and app-like within roughly 8-10 focused worker-hours, pivot to **Vite + React Router SPA + shadcn/ui**, not Next full-stack.

### Strongest Case Against This Recommendation

The operator explicitly values **cool and sleek** as a first-class requirement and delegates heavily to AI workers. That is where React wins. shadcn/ui, Vaul, Motion, Radix, Magic UI, Aceternity, Vercel templates, and abundant examples give AI agents a larger paved road. Because PM has only two storefront components and zero completed planned primitives, the switching cost is low enough that the operator could rationally choose React now to maximize aesthetic and AI velocity.

My answer to that dissent: choose React if visual velocity is the top risk. But choose **Vite + React Router SPA under Flask**, not a backend/database rewrite and not necessarily Next.js. That captures most of React's UI advantage while preserving PM's home-server architecture.

## 6. Aesthetic Reference Links

Svelte path:

- [shadcn-svelte docs](https://www.shadcn-svelte.com/docs) - closest match to shadcn aesthetics in Svelte.
- [shadcn-svelte about](https://www.shadcn-svelte.com/docs/about) - confirms Bits UI, Formsnap, Paneforge, and Vaul Svelte foundation.
- [Bits UI](https://bits-ui.com/docs) - headless Svelte primitives for accessible custom UI.
- [Skeleton UI](https://www.skeleton.dev/) - Svelte-first design system to compare against shadcn-svelte.

React path:

- [shadcn/ui](https://ui.shadcn.com/) - aesthetic baseline.
- [shadcn/ui Playground example](https://ui.shadcn.com/examples/playground) - dense app UI reference.
- [shadcn CLI](https://ui.shadcn.com/docs/cli) - component scaffolding and docs flow.
- [Vercel Next.js + shadcn dashboard template](https://vercel.com/templates/next.js/next-js-and-shadcn-ui-admin-dashboard) - realistic dashboard/app-shell example.
- [Magic UI](https://magicui.design/) - animation accents and shadcn-compatible visual inspiration.
- [Aceternity UI](https://ui.aceternity.com/) - high-polish React component inspiration.
- [Vaul](https://vaul.emilkowal.ski/) - drawer/bottom-sheet interaction reference.
- [Motion](https://motion.dev/) - animation library reference for React and vanilla patterns.

Mobile interaction references:

- [Apple Human Interface Guidelines - Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets) - target behavior for bottom sheets and swipe-to-dismiss.
- [MDN View Transition API](https://developer.mozilla.org/docs/Web/API/View_Transition_API) - route/view transition primitive.
- [MDN touch-action](https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action) - required CSS for sane touch gesture handling.

## 7. Sources

Local repo sources read:

- `data/batch_reports/pm_pwa_audit_2026-04-19.md`
- `data/batch_reports/pm_mobile_strategy_research_2026-04-20.md`
- `pm/CLAUDE.md`
- `pm/app.py`
- `pm/storefront/package.json`
- `pm/storefront/svelte.config.js`
- `pm/storefront/vite.config.ts`
- `pm/storefront/components.json`
- `pm/storefront/src/lib/components/BottomNav.svelte`
- `pm/storefront/src/lib/components/PageShell.svelte`
- `pm/storefront/src/app.css`
- `pm/routes/api.py`
- `pm/routes/pages.py`
- `pm/templates/miru_nav_shell.html`
- `pm/db.py`

Web and practitioner sources:

- https://svelte.dev/docs/kit/adapter-static
- https://svelte.dev/docs/svelte/what-are-runes
- https://nextjs.org/docs/app/building-your-application/deploying/static-exports
- https://nextjs.org/docs/15/app/guides/self-hosting
- https://remix.run/blog/react-router-v7
- https://reactrouter.com/start/framework/installation
- https://tanstack.com/start/latest/docs/framework/react/overview
- https://krausest.github.io/js-framework-benchmark/
- https://2024.stateofjs.com/en-US/libraries/front-end-frameworks/
- https://survey.stackoverflow.co/2025/technology/
- https://ui.shadcn.com/
- https://ui.shadcn.com/docs/cli
- https://ui.shadcn.com/docs/registry
- https://www.shadcn-svelte.com/docs
- https://www.shadcn-svelte.com/docs/about
- https://bits-ui.com/docs
- https://www.skeleton.dev/
- https://www.radix-ui.com/primitives
- https://magicui.design/
- https://ui.aceternity.com/
- https://vaul.emilkowal.ski/
- https://motion.dev/
- https://use-gesture.netlify.app/docs/
- https://gsap.com/docs/v3/Plugins/Draggable
- https://developer.apple.com/design/human-interface-guidelines/sheets
- https://developer.mozilla.org/docs/Web/API/View_Transition_API
- https://developer.mozilla.org/en-US/docs/Web/API/ViewTransition
- https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action
- https://flask.palletsprojects.com/en/stable/deploying/waitress/
- https://fastapi.tiangolo.com/
- https://www.sqlite.org/whentouse.html
- https://www.sqlite.org/
- https://litestream.io/
- https://litestream.io/how-it-works/

Completion status: **CONFIRMED WORKING** - report written to expected path, all 7 research areas covered, shortlist ranked, recommendation with dissent stated, and aesthetic references included.
