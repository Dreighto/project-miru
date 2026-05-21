---
name: hub-ui-deploy
description: Use this skill to build, serve, and redeploy the Project Miru dev page — the SvelteKit hub_ui app on port 18768 (the Glance / Voyage / Review surfaces). Triggers include build the dev page, deploy hub_ui, redeploy 18768, restart the dev page, ship the dev page change, rebuild the hub UI, the dev page is stale, bring up the dev page. Do NOT use for the PM storefront, the LogueOS Console, or other services.
---

# hub-ui-deploy

This skill is self-contained. It is the build → serve → verify loop for the dev page.

## The app

- **Location:** `miru_ai/hub_ui/` in the project-miru repo.
- **Stack:** SvelteKit 2 · Svelte 5 (runes) · Tailwind CSS v4 (`@theme` in `src/app.css`) · `@sveltejs/adapter-node`.
- **Surfaces:** `/` (Glance), `/voyage`, `/review`. Design system: "Ink" — see `src/app.css` and the `immersive-ui-craft` skill.
- **Backend:** the surface loaders fetch Flask on **port 18765** (`MIRU_FLASK_BASE_URL`, default `http://127.0.0.1:18765`). If Flask is down the surfaces render a flask-down state — start `python -m miru_ai.server` first if needed.

## Gates (run in `miru_ai/hub_ui/`)

- `npm run check` — Svelte/TS check; must be 0 errors.
- `npm run build` — adapter-node build into `build/`; must be clean.
- `npm run test:unit` — Vitest; must be green.

## Serve / redeploy

The dev page runs as a plain Node process (no scheduled task):

```
PORT=18768 HOST=0.0.0.0 node build/index.js
```

Run it from `miru_ai/hub_ui/`, in the background. `HOST=0.0.0.0` is required for Tailscale reach.

**Redeploy in the right order:** kill the old process FIRST (`Get-NetTCPConnection -LocalPort 18768` → `Stop-Process`), confirm the port is free, THEN start the new one. Otherwise the new process loses the port-bind race and silently serves nothing.

## URLs

- Loopback: `http://127.0.0.1:18768/`
- Tailscale (the operator's phone): `http://100.81.19.49:18768/` — always confirm this one too; it is how the operator actually views the page.

## Commit gotcha

The pre-commit hook runs prettier; it reformats `.svelte` / `.ts` files and aborts the first commit. Re-stage the reformatted files and commit again.

## After deploy

Hand off to `ui-visual-verify` — build + serve is not done until the surface is verified at the operator's viewport.

## When NOT to use

- The PM storefront, the LogueOS Console, or any non-hub_ui service.
