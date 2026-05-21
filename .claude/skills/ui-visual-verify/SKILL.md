---
name: ui-visual-verify
description: Use this skill whenever verifying that a UI surface actually looks and works correctly — drive a real browser, interact with it, and measure, never trust a screenshot alone. Triggers include verify the UI, check the page, does it look right, visual QA, test the surface, verify on mobile, check it at 430px, the page looks off, did the layout work, confirm the redesign, screenshot the page. Use it after any UI change before declaring it done. Do NOT use for backend/service deploy verification (that is verify-deploy) or for running unit tests.
---

# ui-visual-verify

This skill is self-contained. It exists because "I screenshotted it and it looked fine" is how broken UI ships.

## The discipline

A screenshot is the LAST step, not the verification. It captures one static frame at one size and cannot reveal dead tap targets, broken navigation, element overflow, console errors, or interaction bugs. Real verification is **navigate → interact → measure → then screenshot.**

This skill encodes a failure that already shipped on the dev page: a nav whose tap targets were 29px (below the 44px touch minimum) and service rows overflowing their card by ~50px — both invisible in a screenshot, both caught instantly by measuring.

## Procedure

1. **Serve over HTTP.** Playwright blocks `file://`. Serve the running app, or `python -m http.server` for a standalone file.
2. **Set the real viewport.** `browser_resize` to the operator's device — iPhone Plus/Pro Max class, **430–440px** wide — and also check **393px** (the narrow iPhone). The dev page is used on a phone; verify at phone width, not desktop.
3. **Navigate every route.** `browser_navigate` to each surface; click the nav between them — confirm navigation works, both directions.
4. **Interact.** Click buttons, tap rows / islands, open panels, submit a form. A surface that renders is not a surface that works.
5. **Measure with `browser_evaluate`** — do not eyeball:
   - Tap targets: every interactive element's `getBoundingClientRect().height` >= 44.
   - Element overflow: `el.scrollWidth > el.clientWidth` on rows / cards that should fit.
   - Horizontal page scroll: `document.body.scrollWidth` vs `documentElement.clientWidth` — should be equal.
   - "Towering" panels: measure suspect panel heights.
6. **Console.** `browser_console_messages` at error level — a hydration error or `each_key_duplicate` silently breaks interactivity while the page still looks rendered.
7. **Screenshot last** — full-page + viewport — as the evidence, after the pass above.

## Report honestly

State what you measured, with numbers ("nav tap targets: 44px; service rows: 0 overflow"). Never declare a surface done from a screenshot. If something is off, name which surface and what — measured, not guessed.

## When NOT to use

- Service / deploy verification that a restart picked up new code — that is `verify-deploy`.
- Unit / integration test runs — that is the test suite.
