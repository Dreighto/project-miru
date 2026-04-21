# Phase 4A Step 1 — Components Directory Layout — 2026-04-20

Read-only recon of `pm/storefront/src/lib/components/` to refine the
PM_PRIMITIVES enforcement_paths proposal from the Phase 4A recon.

## Directory tree

```
pm/storefront/src/lib/components/
├── BottomNav.svelte
└── PageShell.svelte
```

No subdirectories. Two files total.

For context, the full Svelte component/route inventory across the
entire storefront source tree:

```
pm/storefront/src/
├── lib/
│   └── components/
│       ├── BottomNav.svelte
│       └── PageShell.svelte
└── routes/
    ├── +layout.svelte
    ├── +page.svelte
    ├── cards/+page.svelte
    ├── deck-builder/+page.svelte
    ├── leaders/+page.svelte
    └── profile/+page.svelte
```

## Q1 — Flat vs organized

**Flat — and essentially empty.** The components directory has no
subdirectories and contains only two files: `BottomNav.svelte` and
`PageShell.svelte`. There is no organizational hierarchy because there
are not enough components to need one.

## Q2 — PM-vs-generic split

**No split exists — because no PM primitives exist yet.** Neither of the
two files in the directory is a PM-domain primitive as enumerated by
`docs/pm/02_PM_PRIMITIVES.md`; both are page-scaffolding primitives
(bottom navigation bar, page shell wrapper). There is also no generic
primitive subdirectory (no `Button.svelte`, `Input.svelte`, `Sheet.svelte`,
or `Modal.svelte` anywhere in the storefront tree).

The PM_PRIMITIVES guide describes a 15-component set that, as of this
recon, is **aspirational** — the guide exists but the Svelte
implementations do not. PM UI is presumably still rendered primarily
via the Jinja templates in `pm/templates/*.html` (confirmed present in
earlier Phase 4A recon), with the Svelte storefront only partially
stood up.

## Q3 — Named primitive matches

- CardTile: **NOT FOUND**
- CardImage: **NOT FOUND**
- CostGauge: **NOT FOUND**
- PowerBadge: **NOT FOUND**
- LeaderChip: **NOT FOUND**
- ColorChip: **NOT FOUND**
- WatchlistStar: **NOT FOUND**
- VariantDots: **NOT FOUND**
- CountBadge: **NOT FOUND**
- PriceBadge: **NOT FOUND**
- MiruGem: **NOT FOUND**
- MeterBar: **NOT FOUND**
- SetBadge: **NOT FOUND**
- RarityChip: **NOT FOUND**
- MatchupBar: **NOT FOUND**

Zero of 15 primitives have corresponding files. Searched
case-insensitively across the entire `pm/storefront/src/` tree, not
just the components directory — no match anywhere.

## Proposed updated enforcement_paths for PM_PRIMITIVES.md

Given that the primitives don't exist yet, enforcement_paths should be
forward-looking: fire when a new file is created in the place they
*will* live, so the guide is read before the first implementation.

- `pm/storefront/src/lib/components/**/*.svelte`
- `pm/storefront/src/lib/components/**/*.ts`
- `pm/storefront/src/lib/icons/**/*`

Broad match on the components dir is correct right now because the dir
is so sparse that over-matching is not a real problem — any new file
there is likely a PM primitive by default. The named-primitive patterns
from the Phase 4A recon can be dropped as redundant (every PM primitive
file that ever lands will match the broad glob).

When the generic primitive set eventually arrives (Button, Input,
Sheet, Modal), the operator should either (a) split them into
`pm/storefront/src/lib/components/primitives/` and add that to
`exemption_paths`, or (b) revisit this enforcement_paths proposal. Both
decisions belong to whoever builds the first generic primitives, not to
Phase 4A.

## Proposed updated exemption_paths for PM_PRIMITIVES.md (optional)

**None.** No generic-primitive subdir exists. The Phase 4A recon's
proposed exemption patterns (`Button*`, `Input*`, `Sheet*`, `Modal*`)
would currently exempt zero files and can be dropped until a generic
primitive is written. The two existing files (`BottomNav.svelte`,
`PageShell.svelte`) are page-scaffolding, not generic primitives — they
cover navigation + layout, which arguably belongs to
`docs/ui_ux/` rather than to either PM_PRIMITIVES or a generic
primitive doc. Operator may want to decide whether `BottomNav` and
`PageShell` are in scope for PM_PRIMITIVES, a ui_ux doc, or neither;
flagged below.

## Notes

- **Finding worth escalating:** the Svelte storefront is essentially a
  skeleton. The PM_PRIMITIVES guide documents a primitive set that
  has not yet been built in Svelte. This does not invalidate Phase 4A
  — the enforcement layer can still fire on new component files as
  they're created — but it does mean PM_PRIMITIVES is currently a
  *design brief* more than a *code-audit reference*.
- The active PM UI surface is likely still `pm/templates/*.html`
  (Jinja). If operator wants PM_PRIMITIVES to fire on template work
  too (e.g., a `card_tile.html` partial if one exists), the
  enforcement_paths should add `pm/templates/**/*.html` — but that
  overlaps with DESIGN_LANGUAGE's enforcement_paths and may not be
  the intended trigger. Flagging for operator, not assuming.
- `BottomNav.svelte` and `PageShell.svelte` classification — are
  these in-scope for PM_PRIMITIVES (as PM-domain components), or
  should they be considered generic primitives (and covered by
  `docs/ui_ux/04_PRIMITIVES.md` instead)? Not a Phase 4A Step 1
  decision, but a Phase 4A proper question.

---

`STATUS: CONFIRMED WORKING`

Report path: `docs/phase4/phase4a_step1_components_layout_2026-04-20.md`
One-line summary: **flat — directory contains 2 files, zero subdirectories, and zero of the 15 PM primitives enumerated in the guide (the primitive set is currently aspirational).**
