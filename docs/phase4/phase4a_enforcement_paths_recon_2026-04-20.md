# Phase 4A — Enforcement Paths Recon — 2026-04-20

Recon pass for the four pilot PM craft guides. Read-only. No edits to
guides, no frontmatter changes, no commits. The purpose is to propose
`enforcement_paths` (and optional `exemption_paths`) so Phase 4A proper
can land them with operator approval.

## Format finding

**All four pilot guides use inline markdown scope headers, not YAML
frontmatter.** The pattern, consistent across all four files and
mirrored by `docs/pm/README.md`:

```
# NN — Title

**Applies to:** …
**Read this when:** …
**Skip this when:** …
**Length:** …
**Related docs:** …
```

Implication for Phase 4A proper: either (a) inject a YAML frontmatter
block above the H1 on each guide, or (b) add a new bolded inline field
like `**Enforcement paths:**` adjacent to the existing scope block.
Option (b) preserves the current house style; option (a) is friendlier
to tooling. Operator decision needed — flagged in open questions.

## Pattern format

**Chose: glob.** The PM tree uses clean directory boundaries
(`pm/templates/`, `pm/storefront/src/lib/components/`,
`pm/storefront/src/routes/`) and SvelteKit conventions that map well to
glob patterns. Globs also read naturally for human reviewers and are
directly supported by most enforcement tooling (ripgrep, git pathspecs,
eslint overrides, etc.). Where a guide's scope is inherently semantic
(not path-based), this recon flags that and defaults to a broad path
with a LOW confidence mark rather than inventing a precise pattern.

Anchor convention: all patterns are repo-root-relative, forward-slash,
with `**` for recursive globs.

---

## Per-guide proposals

### docs/pm/06_DESIGN_LANGUAGE.md

Existing scope headers:

> **Applies to:** every visual decision in PM — color, type, spacing, shadow, radius, motion, iconography.
> **Read this when:** you're picking a color, specifying a type size, deciding a radius, tweaking a shadow, or adding a new visual pattern.
> **Skip this when:** you're implementing against existing tokens without modifying them.

Proposed enforcement_paths:
- `pm/storefront/src/app.css` — the theme / token source of truth explicitly cited by the guide ("All colors are defined in `pm/storefront/src/app.css` under the `@theme` block").
- `pm/storefront/src/**/*.css`
- `pm/storefront/src/**/*.svelte` — Svelte single-file components contain inline `<style>` blocks where visual decisions commonly land.
- `pm/templates/**/*.html` — the Jinja template layer still exists (`home.html`, `deck_builder.html`, etc.) and can introduce visual decisions via inline CSS or classnames.
- `pm/static/**/*.css`

Proposed exemption_paths: none cleanly expressible as paths. The guide's
skip clause ("implementing against existing tokens without modifying
them") is a semantic distinction about *what kind of change* is being
made, not *which file* it lives in. A CSS/Svelte edit that only consumes
tokens is in scope for skipping; the same file with a new hex literal is
in scope for reading. This must be a content-level check (grep for
hardcoded hex / `px` in text / non-token values) layered on top of the
path match. Flagged below.

Confidence: **MEDIUM** — paths confidently capture "every file that can
carry a visual decision," but the skip clause can't be encoded as a
path exemption.

Ambiguities:
- Skip clause is semantic, not path-based. Phase 4A proper may want a
  paired content rule ("fires only if the diff introduces a hex
  literal, `px` in text, or a new CSS variable").
- `pm/templates/**/*.html` may be mostly dead if the storefront has
  superseded it — operator should confirm whether template HTML is
  still a surface that can carry visual decisions, or whether it's
  frozen. If frozen, drop it from enforcement_paths.

---

### docs/pm/02_PM_PRIMITIVES.md

Existing scope headers:

> **Applies to:** PM-domain components — card tile, leader chip, cost gauge, watchlist star, Miru gem, price badge, meter variants.
> **Read this when:** you need a PM-specific component; you're updating the card tile; you're adding a new PM visual.
> **Skip this when:** you're using generic primitives (button, input, sheet, modal) — those are in [docs/ui_ux/04_PRIMITIVES.md](../ui_ux/04_PRIMITIVES.md).

Proposed enforcement_paths:
- `pm/storefront/src/lib/components/**/*.svelte`
- `pm/storefront/src/lib/components/**/*.ts`
- `pm/storefront/src/lib/icons/**/*` — the guide mentions PM-domain icons (cost hex, Miru gem, leader symbols) live here.
- Named-pattern specifics for the primitives the guide enumerates:
  - `pm/storefront/src/lib/components/**/CardTile*`
  - `pm/storefront/src/lib/components/**/CardImage*`
  - `pm/storefront/src/lib/components/**/CostGauge*`
  - `pm/storefront/src/lib/components/**/PowerBadge*`
  - `pm/storefront/src/lib/components/**/LeaderChip*`
  - `pm/storefront/src/lib/components/**/ColorChip*`
  - `pm/storefront/src/lib/components/**/WatchlistStar*`
  - `pm/storefront/src/lib/components/**/VariantDots*`
  - `pm/storefront/src/lib/components/**/CountBadge*`
  - `pm/storefront/src/lib/components/**/PriceBadge*`
  - `pm/storefront/src/lib/components/**/MiruGem*`
  - `pm/storefront/src/lib/components/**/MeterBar*`
  - `pm/storefront/src/lib/components/**/SetBadge*`
  - `pm/storefront/src/lib/components/**/RarityChip*`
  - `pm/storefront/src/lib/components/**/MatchupBar*`

Proposed exemption_paths:
- Generic primitives per the skip clause — best-effort path patterns
  assuming the component names follow the guide's language:
  - `pm/storefront/src/lib/components/**/Button*`
  - `pm/storefront/src/lib/components/**/Input*`
  - `pm/storefront/src/lib/components/**/Sheet*`
  - `pm/storefront/src/lib/components/**/Modal*`

Confidence: **MEDIUM** — the primitive directory is correct, and the
named patterns should catch the 15 primitives the guide enumerates. The
unknown is whether generic primitives (Button, Input, Sheet, Modal)
live in the same directory as PM primitives or in a separate subdir;
recon did not open the components dir to enumerate.

Ambiguities:
- Recon did not list the actual contents of
  `pm/storefront/src/lib/components/`. If the dir separates
  PM-specific from generic primitives (e.g., `components/primitives/`
  vs `components/pm/`), the enforcement_paths should use those
  subdirs and drop the broad match. Operator or Phase 4A proper
  should `ls` this dir first.
- If any of the enumerated primitives are actually named differently
  in code (e.g., `Tile.svelte` instead of `CardTile.svelte`), the
  named patterns will miss. The broad match
  (`pm/storefront/src/lib/components/**/*.svelte`) is the safety net.

---

### docs/pm/03_MIRU_LAYER.md

Existing scope headers:

> **Applies to:** every place Miru (the AI / assistance layer) speaks, suggests, filters, or intervenes.
> **Read this when:** you're adding a Miru feature; you're adding any ambient intelligence, suggestion, or auto-fill to a surface; you're writing copy for Miru output.
> **Skip this when:** the surface has no AI involvement whatsoever.

Proposed enforcement_paths:
- `pm/storefront/src/**/Miru*.svelte` — component-name heuristic
  (MiruGem, MiruSays, etc.).
- `pm/storefront/src/lib/api/**/miru*` — any PM-side API client that
  calls the Miru AI service.
- `pm/storefront/src/**/miru*.ts` / `pm/storefront/src/**/miru*.css`
  — catch stores, utilities, and style overrides.
- Broad fallback: any change to `pm/storefront/src/routes/**` that
  touches Miru output (cannot be expressed as a path alone — see
  ambiguity below).

Proposed exemption_paths: none. The skip clause is the inverse of the
whole file tree — "surface with no AI involvement whatsoever" is 90%+
of the app. The guide is an opt-IN trigger, not opt-out.

Confidence: **LOW** — Miru-layer scope is a design posture ("every
place Miru speaks, suggests, filters, or intervenes"), not a file-path
attribute. A path match catches the obvious named files but misses
route-level and ambient uses where Miru output is introduced inside an
otherwise generic file.

Ambiguities:
- Guide covers copy conventions for Miru output. Copy could live in
  route files, component files, or content config — no path tells us
  where "Miru copy" lives.
- Guide covers ambient filtering (e.g., "Deck Builder pre-filters the
  card pool to the leader's colors"). That's a route-level feature
  implemented in `pm/storefront/src/routes/deck-builder/`, but most
  edits to that route have nothing to do with the Miru layer.
- Phase 4A proper may want to pair this guide with a content-level
  trigger (imports of a `miru*` module, mentions of `Miru` as a JSX
  identifier, or changes under a tagged comment block) rather than
  rely on paths alone.

---

### docs/pm/05_GESTURES_PM.md

Existing scope headers:

> **Applies to:** gestures specific to PM surfaces, particularly the swipe-for-variants pattern. Extends the universal gesture vocabulary.
> **Read this when:** you're wiring a gesture inside a PM surface; you want to know why swipe-for-variants is locked in; you're proposing a new PM-specific gesture.
> **Skip this when:** the gesture is universal (swipe-to-dismiss sheet, pull-to-refresh). Those live in [docs/ui_ux/02_GESTURES.md](../ui_ux/02_GESTURES.md).

Proposed enforcement_paths:
- `pm/storefront/src/lib/components/**/CardTile*` — the canonical
  surface (swipe-for-variants, long-press for context sheet).
- `pm/storefront/src/routes/deck-builder/**` — drag-from-pool-to-deck,
  long-press to start drag, drag-to-gutter to remove.
- `pm/storefront/src/routes/**/*.svelte` — catch-all for route-level
  gesture wiring (watchlist multi-select, saved-decks swipe-up,
  card-detail pinch-zoom).
- `pm/storefront/src/lib/**/gesture*` / `**/swipe*` / `**/drag*` —
  any gesture utility modules if they exist.

Proposed exemption_paths: none expressible as paths. The skip clause
("gesture is universal — lives in `docs/ui_ux/02_GESTURES.md`") is a
content distinction (which gesture is being wired), not a path one.

Confidence: **LOW** — gestures are a behavior, not a location. A
gesture handler can appear in almost any component or route file, and
the guide's surface list (CardTile, Deck Builder pool, Watchlist page,
Saved decks list, Card detail sheet) spans both components and routes.
Path-based enforcement is a proxy at best; it will over-match (firing
on route edits that don't touch gestures) and potentially under-match
(missing a new gesture added to a surface we didn't enumerate).

Ambiguities:
- Best signal for this guide is probably content-level: any file that
  adds or modifies `on:touch*`, `on:pointer*`, drag event handlers, or
  imports from a gesture utility. Paths alone are a weak trigger.
- Guide surfaces a mix of CardTile-bound gestures (universal across
  the app) and route-bound gestures (Deck Builder, Watchlist). The
  enforcement_paths list reflects both, but fires on many
  unrelated-to-gesture edits to the same files.

---

## Open questions for operator

1. **Format decision** — add YAML frontmatter blocks above each guide's
   H1, or append a new inline `**Enforcement paths:**` field alongside
   the existing scope block? Preference affects Phase 4A proper's edit
   pattern across all guides, not just the four pilots.

2. **`pm/templates/**/*.html` status** — are the Jinja templates in
   `pm/templates/` still an active visual surface, or has the Svelte
   storefront fully superseded them? Answer decides whether they
   belong in DESIGN_LANGUAGE's enforcement_paths or should be
   dropped.

3. **Component directory structure** — is
   `pm/storefront/src/lib/components/` a flat list mixing PM-specific
   and generic primitives, or does it split them into subdirectories?
   If split, PM_PRIMITIVES enforcement_paths should point at the
   PM-specific subdir only and drop the broad match. (Recon did not
   enumerate this directory to avoid scope creep.)

4. **Content-level triggers** — for the LOW-confidence guides
   (MIRU_LAYER, GESTURES_PM) and the semantic exemption in
   DESIGN_LANGUAGE, is Phase 4A willing to support a paired
   content-match rule (grep pattern, import check) alongside the
   path match? If path-only is the only supported trigger, those
   two guides will always over- or under-match.

5. **Universal-vs-PM gesture resolution** — when a gesture fires on
   a PM surface but the gesture itself is universal (swipe-to-dismiss
   on a PM sheet), should GESTURES_PM still read, or only the
   universal `docs/ui_ux/02_GESTURES.md`? The current skip clause
   suggests only universal; confirming for the enforcement layer.

---

## Summary

- 4/4 guides use inline markdown scope headers (no YAML frontmatter).
- Pattern format: glob, repo-root-anchored, forward-slash, `**` for
  recursive.
- Confidence distribution: 2 MEDIUM (DESIGN_LANGUAGE,
  PM_PRIMITIVES), 2 LOW (MIRU_LAYER, GESTURES_PM).
- Two LOW-confidence guides have scopes that are semantic or
  behavioral, not path-based — flagged for operator to decide how
  Phase 4A handles them.
- No guide files edited. No frontmatter changes. No commits. No
  service-directory writes.

`STATUS: CONFIRMED WORKING`

Report path: `docs/phase4/phase4a_enforcement_paths_recon_2026-04-20.md`
Format finding: **inline markdown scope headers (not frontmatter)** across all four pilots.
