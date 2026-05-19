# Miru AI Hub UI

SvelteKit dev-page for the Miru AI two-AI learning pipeline. Thin BFF over the
existing Flask service at port 18765; Flask remains sole owner of the data layer.

This is the scaffold (Ticket A of 5). The three surface routes (`/voyage`, `/review`),
the `currentIsland` runes store, and BFF server files land in follow-up tickets
(PRO-919 / PRO-920 / PRO-921 / PRO-922).

---

## Dev server

```bash
cd miru_ai/hub_ui
npm install
npm run dev
```

Starts at `http://localhost:18768`.

## Build (adapter-node)

```bash
npm run build
```

Output at `build/index.js`. Launch with:

```bash
# POSIX
PORT=18768 node build/index.js

# PowerShell
$env:PORT = '18768'; node build/index.js
```

## Tests

```bash
# Unit tests (Vitest + jsdom)
npm run test:unit

# E2E tests (Playwright, Chromium)
npm run test:e2e

# Both
npm test
```

Playwright starts the dev server automatically on port 18768 before running tests.

## Lint

```bash
npm run lint
```

## Environment variables (upcoming -- not used yet)

| Variable              | Purpose                                                                    |
| --------------------- | -------------------------------------------------------------------------- |
| `MIRU_FLASK_BASE_URL` | Base URL for the Miru AI Flask service (default: `http://localhost:18765`) |

These will be wired in BFF files during Tickets C/D/E.
