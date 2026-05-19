// Voyage BFF — asserts throughput data renders (or error banner if Flask is down).
import { test, expect } from '@playwright/test';

test.describe('Voyage (/voyage) BFF', () => {
	test('shows Voyage heading', async ({ page }) => {
		await page.goto('/voyage');
		await expect(page.getByRole('heading', { name: 'Voyage' })).toBeVisible();
	});

	test('displays currentIsland value', async ({ page }) => {
		await page.goto('/voyage');
		await expect(page.getByTestId('current-island')).toContainText('OP01');
	});

	test('renders throughput data or Flask-down banner', async ({ page }) => {
		await page.goto('/voyage');

		const hasGrid = await page.locator('[aria-label="OP01 Throughput"]').isVisible();
		const hasBanner = await page
			.getByRole('alert', { name: 'Flask service unreachable' })
			.isVisible();

		expect(hasGrid || hasBanner, 'Expected either throughput grid or Flask-down banner').toBe(true);
	});

	test('throughput stat tiles render when Flask is reachable', async ({ page }) => {
		await page.goto('/voyage');

		const flaskDown = await page
			.getByRole('alert', { name: 'Flask service unreachable' })
			.isVisible();
		if (flaskDown) {
			test.skip(true, 'Flask not available in this environment — skipping data-render assertions');
		}

		await expect(page.locator('[aria-label="OP01 Throughput"]')).toBeVisible();
		await expect(page.getByTestId('stat-total-reviews')).toBeVisible();
		await expect(page.getByTestId('stat-today-reviews')).toBeVisible();
		await expect(page.getByTestId('stat-distinct-cards')).toBeVisible();
		await expect(page.getByTestId('stat-op01-total')).toBeVisible();
	});
});
