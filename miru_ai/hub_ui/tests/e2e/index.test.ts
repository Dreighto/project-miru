// E2E smoke tests — surface route headings + currentIsland store display.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('Glance (/)', () => {
	test('shows Glance heading', async ({ page }) => {
		await page.goto('/');
		await expect(page.getByRole('heading', { name: 'Glance' })).toBeVisible();
	});

	test('displays currentIsland value', async ({ page }) => {
		await page.goto('/');
		await expect(page.getByTestId('current-island')).toContainText('OP01');
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

test.describe('Voyage (/voyage)', () => {
	test('shows Voyage heading', async ({ page }) => {
		await page.goto('/voyage');
		await expect(page.getByRole('heading', { name: 'Voyage' })).toBeVisible();
	});

	test('displays currentIsland value', async ({ page }) => {
		await page.goto('/voyage');
		await expect(page.getByTestId('current-island')).toContainText('OP01');
	});
});

test.describe('Review (/review)', () => {
	test('shows Review heading', async ({ page }) => {
		await page.goto('/review');
		await expect(page.getByRole('heading', { name: 'Review' })).toBeVisible();
	});

	test('displays currentIsland value', async ({ page }) => {
		await page.goto('/review');
		await expect(page.getByTestId('current-island')).toContainText('OP01');
	});
});
