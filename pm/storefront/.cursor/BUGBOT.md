# PM Storefront — Bugbot Rules

## Framework — locked, no exceptions
- SvelteKit 2 + Svelte 5 runes only; flag any Svelte 4 `writable`/`derived` store patterns in new code
- `lucide-svelte` for icons — never `lucide-react`
- Plus Jakarta Sans is the only font family; flag any new `@font-face` or `font-family` imports

## Design tokens — Pattern B Rosinante (locked)
- Canvas: `#101217` (`--pm-bg-canvas`); Rose/Miru: `#C54668` (`--pm-accent-rose`); Gold: `#C8A261` (`--pm-gold-base`); Text: `#F4F1EB`
- Use `--pm-*` CSS variables, not raw hex values in component styles

## Retired palette — flag on sight
- `#f4d078` (old forge gold), `#c9b0ff` (old Miru purple), `#08060f` (old canvas) — none of these should appear in any file

## Card images — Bandai CDN only
- Never reference TCGPlayer image URLs; JPEG compression causes visible color distortion

## VirtualCardGrid — don't bypass virtualization
- `content-visibility: auto` is load-bearing for large card sets; don't add `display: contents` or remove the sentinel rows

## Deck line ID format
- Deck entries are keyed as `baseId#variantId` (e.g. `OP03-121#v2`); never simplify to `baseId` alone
- TCGPlayer export collapses variants by card name (correct); OPTCGSim export preserves the full ID (required)

## Leader swap behavior
- Changing the active leader must NOT clear `deckCards`; it filters the pool view only
- Any code that calls `deckCards.clear()` or reassigns the map on leader change is a regression

## Deck constraints — enforced at add time
- 50-card main deck cap + 4-of playset limit via `maxQtyForDeckLine()`
- Never defer these to a validate-later step; enforce on each `addCard`/`incCard` call

## Persistence — both layers required
- localStorage debounced auto-save = interrupt-resume (never remove)
- Backend `saveDeck()` POST = durable named deck with validation (orthogonal, not a replacement)

## Workstation mode — BottomNav hide
- BottomNav must not render on `/deck-builder/build`; conditional is in `+layout.svelte`
- Do not reintroduce BottomNav render on workstation routes as a "fix"

## Miru voice — no chatbot patterns
- Miru surfaces as ambient Insight glow (`data-insight` attribute + `--pm-insight-*` tokens) only
- No Miru avatar, mascot illustration, or chat bubble in PM surfaces
- Insight glow categories: meta > price > synergy > lore (Phase 1 placeholders; colors are reserved)
