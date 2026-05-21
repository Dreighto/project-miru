# Dev Page Mockups — Design Reference

Saved HTML mockups for the Project Miru dev page (SvelteKit `hub_ui`, port 18768). These are the operator-approved design vision for the Voyage surface. Build against them.

## Files

- `miru-voyage-map.html` — the Voyage map vision: a winding nautical route, charted / current / fog island nodes, a living beacon, atmospheric depth, the Red Line, a "New World" fog bank.
- `miru-voyage-log-v3.html` — the Voyage Log panel vision (v3, latest).
- `miru-18765-mockup.html` — an earlier mockup of the status / overview surface.

## Important — these are VISION, not pixel-spec

- **Colors are placeholder.** The mockups use pink / purple / gold. The locked design system is **Ink** (dark, brass accent) — see `data/peer_reviews/miru-dev-2.0-debrief.md` §8. Translate: current-island beacon -> brass `accent`, charted -> `positive` green, fog -> `text-faint` / dashed `border`.
- **Fonts are placeholder.** The mockups use Cinzel / IBM Plex Mono. The locked system is **Geist + Geist Mono**.
- **Positions are illustrative.** The mockup hard-codes island pixel positions; the real build generates the route and places nodes from live `/api/dev/voyage` data.

Read alongside `data/research/2026-05-20_voyage_map_research.md` (lore accuracy + design-technique research) and `data/peer_reviews/2026-05-20_dev_page_ui_audit_agy_findings.md` (the UI audit).
