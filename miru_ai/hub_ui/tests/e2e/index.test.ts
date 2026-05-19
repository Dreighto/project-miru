// E2E smoke test — proves the Playwright pipeline works and the scaffold heading loads.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('home page', () => {
	test('loads and shows the scaffold heading', async ({ page }) => {
		await page.goto('/');
		await expect(page).toHaveURL(/\//);
		const heading = page.getByRole('heading', { name: /Miru AI Dev/i });
		await expect(heading).toBeVisible();
	});

	test('passes axe-core accessibility scan (no serious/critical violations)', async ({ page }) => {
		await page.goto('/');
		const results = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
			.analyze();

		const blocking = results.violations.filter(
			(v) => v.impact === 'serious' || v.impact === 'critical'
		);
		const non_blocking = results.violations.filter(
			(v) => v.impact !== 'serious' && v.impact !== 'critical'
		);

		if (non_blocking.length > 0) {
			console.warn(
				`[a11y] non-blocking violations (minor/moderate): ${non_blocking
					.map((v) => v.id)
					.join(', ')}`
			);
		}

		expect(
			blocking,
			`Blocking a11y violations: ${blocking.map((v) => `${v.id} (${v.impact})`).join(', ')}`
		).toEqual([]);
	});
});
