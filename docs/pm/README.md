# docs/pm — Project Miru craft library

**Applies to:** every PM-surface UI decision. PM is the Project Miru app served at port 18080, a One Piece TCG companion PWA.
**Read this when:** you're building or changing anything inside `pm/storefront/` or `pm/templates/`.
**Skip this when:** the task is backend-only (routes, data, scrapers); cross-surface work that isn't PM; or the task lives in `miru_ai/` or `dispatcher/`.
**Length:** ~60 pages across 9 files.
**Related docs:** [docs/ui_ux/](../ui_ux/README.md) for universal craft. Every PM doc assumes you've skimmed [docs/ui_ux/00_PRINCIPLES.md](../ui_ux/00_PRINCIPLES.md).

---

## What PM is

**Project Miru for One Piece TCG** — a mobile-first PWA. Think of it as a sidecar at a locals table: a phone the player picks up in 15–60 second bursts to check a price, cycle a variant, see if a card is in their watchlist, or jot a deck idea.

PM is not a tournament client. It's not a coach. It's not a store. It's a companion that knows the player's decks, watchlist, and leader rotation, and surfaces just-enough intelligence to respect the player's attention.

The user is an adult who plays One Piece. They own their time. They open PM when they need it and close it when they don't.

---

## What PM is not

- **Not Duolingo.** No streaks, no guilt, no "you haven't checked prices in 3 days" notifications.
- **Not TikTok.** No algorithmic feed, no engagement bait, no infinite scroll without purpose.
- **Not a casino.** No fake urgency, no slot-machine pulls for variants, no "investment grade" hype.
- **Not a social network.** Sharing decks is a feature; sharing everything everywhere is not.
- **Not a shop.** We display prices from third parties. We link out to buy. We don't handle payments.

When a feature smells like one of the above, stop. There's a version of it that respects the user — find that one.

---

## How to use this library

**This is a library, not a gate.** Every doc opens with a scope header. Load one or two at a time.

PM docs sit on top of `docs/ui_ux/`. If you're in a PM doc and see "see §02_GESTURES.md," that's the universal doc — both libraries interleave.

## The index

| # | File | When to read |
|---|------|--------------|
| 00 | [PRINCIPLES.md](00_PRINCIPLES.md) | First PM visit. Sidecar-at-locals mental model, 15–60s burst design. |
| 01 | [TAB_LANDINGS.md](01_TAB_LANDINGS.md) | You're designing or changing a tab root (Home/Cards/Deck Builder/Leaders/Profile). |
| 02 | [PM_PRIMITIVES.md](02_PM_PRIMITIVES.md) | You need a PM-domain component (card tile, hex gauge, watchlist star, leader chip). |
| 03 | [MIRU_LAYER.md](03_MIRU_LAYER.md) | You're adding an AI / suggestion / ambient-intelligence feature. Read this before code. |
| 04 | [WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md) | You're touching watchlist, target prices, or the Miru Meter retention loop. |
| 05 | [GESTURES_PM.md](05_GESTURES_PM.md) | You're wiring the swipe-for-variants gesture or any PM-specific gesture. Pairs with `docs/ui_ux/02_GESTURES.md`. |
| 06 | [DESIGN_LANGUAGE.md](06_DESIGN_LANGUAGE.md) | You're making a color / type / spacing decision. Forge aesthetic — gold on dark, purple as Miru. |
| 07 | [OPTCG_STUDY.md](07_OPTCG_STUDY.md) | You're considering a pattern common in Collectr / Manabox / Egman / OPTCGSim and want to know if it fits PM. |
| 08 | [PM_ANTI_PATTERNS.md](08_PM_ANTI_PATTERNS.md) | Before shipping a PM feature. PM-specific failure modes we've seen in the domain. |

## The voice

Calm, concrete, TCG-aware. Same voice as `docs/ui_ux/`. Every rule traces to evidence: a 1-star App Store review, a Reddit thread, a tournament format spec, an OPTCG convention.

## How PM docs interact with `docs/ui_ux/`

Every universal rule in `docs/ui_ux/` is the baseline. PM applies, specializes, or (rarely) overrides.

- **Applies:** the primitive Button from `04_PRIMITIVES.md` is the base; PM doesn't redefine it.
- **Specializes:** the primitive card-surface becomes PM's card tile in `02_PM_PRIMITIVES.md` — domain-specific (image, code, cost, power, variant dots).
- **Overrides:** rare. If PM needs to break a universal rule, `08_PM_ANTI_PATTERNS.md` or the relevant doc names the override and cites why.

## Where this library lives vs where code lives

Docs live at repo root `docs/pm/`. Code lives at `pm/storefront/`. Neither imports from the other. When a code file deserves a docs update, the PR description should link the doc.

## Updating this library

- **Add a rule** when we hit a failure mode unique to PM (a gesture that conflicted, a pattern that confused card players, a price-data edge case).
- **Remove a rule** when it no longer holds. Card-game meta changes; PM does too.
- **Never copy from `docs/ui_ux/`.** Link there. The PM doc explains the PM-specific application, not the universal rule.
- **Cite.** OPTCG-specific claims link to card data, tournament reports, or Reddit / Discord URLs.
