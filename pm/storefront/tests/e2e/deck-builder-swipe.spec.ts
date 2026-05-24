/**
 * E2E — swipe gestures on the deck builder.
 *
 * STATUS — these tests are written against @playwright/test but the
 * `pm/storefront` project does not yet have it installed. To run:
 *
 *     npm i -D @playwright/test
 *     npx playwright install chromium
 *     npx playwright test tests/e2e/deck-builder-swipe.spec.ts
 *
 * Until the install lands, the same behaviors were verified manually
 * via the Playwright MCP on 2026-05-24 by dispatching synthetic
 * PointerEvents against the live :18080 build — results in
 * .agent/research/SWIPE_IMPLEMENTATION_2026-05-24.md.
 *
 * The tests assume the PM Storefront is running at http://127.0.0.1:18080
 * with Hannyabal (or any Purple leader) able to load a pool.
 */

import { test, expect, type Page, type Locator } from '@playwright/test';

const BASE = process.env.PM_STOREFRONT_URL ?? 'http://127.0.0.1:18080';
const VIEWPORT = { width: 390, height: 844 }; // iPhone 14 baseline

async function dispatchSwipe(
	page: Page,
	target: Locator,
	deltaX: number,
	deltaY = 0,
	steps = 10
) {
	// locator.evaluate passes the matched element as the FIRST arg and the
	// serialized payload as the SECOND. Avoid evaluateHandle round-trips.
	await target.evaluate(
		async (el: HTMLElement, args: { dx: number; dy: number; n: number }) => {
			const { dx, dy, n } = args;
			const r = el.getBoundingClientRect();
			const sx = r.x + 50; // start 50px in from tile's left edge — clear of 20px viewport guard
			const sy = r.y + r.height / 2;
			function pe(type: string, x: number, y: number, id = 7) {
				return new PointerEvent(type, {
					pointerId: id,
					pointerType: 'touch',
					isPrimary: true,
					clientX: x,
					clientY: y,
					bubbles: true,
					cancelable: true,
				});
			}
			el.dispatchEvent(pe('pointerdown', sx, sy));
			for (let i = 1; i <= n; i++) {
				el.dispatchEvent(pe('pointermove', sx + (dx * i) / n, sy + (dy * i) / n));
				await new Promise((r) => setTimeout(r, 8));
			}
			el.dispatchEvent(pe('pointerup', sx + dx, sy + dy));
		},
		{ dx: deltaX, dy: deltaY, n: steps }
	);
	// Give Svelte a tick to react
	await page.waitForTimeout(60);
}

test.beforeEach(async ({ page, viewport }) => {
	test.skip(viewport === null, 'requires viewport');
	await page.setViewportSize(VIEWPORT);
	await page.goto(`${BASE}/deck-builder`);
	// Pick any visible leader to enter the workstation
	const anyLeader = page.getByRole('button', { name: /Hannyabal|Kouzuki Oden|Kyros|Roronoa Zoro/i }).first();
	await anyLeader.waitFor({ state: 'visible' });
	await anyLeader.click();
	// Wait for pool to be populated
	await page.locator('.deck-card-tile').first().waitFor({ state: 'visible' });
});

test('swipe-right on a pool tile adds the card to the deck', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	const before = (await cta.innerText()).match(/(\d+)\/50/)?.[1];
	await dispatchSwipe(page, tile, 60);
	const after = (await cta.innerText()).match(/(\d+)\/50/)?.[1];
	expect(Number(after)).toBe(Number(before) + 1);
});

test('swipe-left on an already-in-deck tile removes one copy', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	// Add first
	await dispatchSwipe(page, tile, 60);
	const after1 = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	// Now swipe left
	await dispatchSwipe(page, tile, -60);
	const after2 = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	expect(after2).toBe(after1 - 1);
});

test('sub-threshold swipe does NOT add', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	const before = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	await dispatchSwipe(page, tile, 30);
	const after = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	expect(after).toBe(before);
});

test('vertical-dominant motion releases the gesture (page scroll allowed)', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	const before = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	await dispatchSwipe(page, tile, 5, 80); // vertical-dominant
	const after = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	expect(after).toBe(before);
});

test('tap still adds a card (canonical fallback path unaffected)', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	const before = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	await tile.click(); // single tap
	const after = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	expect(after).toBe(before + 1);
});

test('keyboard Enter on focused tile adds (accessibility fallback)', async ({ page }) => {
	const tile = page.locator('.deck-card-tile').first();
	const cta = page.locator('button.flex.w-full.items-center');
	const before = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	await tile.focus();
	await page.keyboard.press('Enter');
	const after = Number((await cta.innerText()).match(/(\d+)\/50/)?.[1]);
	expect(after).toBe(before + 1);
});
