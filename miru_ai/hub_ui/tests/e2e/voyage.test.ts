// Voyage surface E2E — islands route map + Voyage Log panel.
import { test, expect } from '@playwright/test';

test.describe('Voyage (/voyage)', () => {
	test('shows Voyage heading', async ({ page }) => {
		await page.goto('/voyage');
		await expect(page.getByRole('heading', { name: 'Voyage' })).toBeVisible();
	});

	test('renders route map or Flask-down banner', async ({ page }) => {
		await page.goto('/voyage');
		const hasMap = await page.getByTestId('route-map').isVisible();
		const hasBanner = await page.getByTestId('flask-down-banner').isVisible();
		expect(hasMap || hasBanner, 'Expected either route map or Flask-down banner').toBe(true);
	});

	test('route map renders island nodes when Flask is reachable', async ({ page }) => {
		await page.goto('/voyage');
		const flaskDown = await page.getByTestId('flask-down-banner').isVisible();
		if (flaskDown) {
			test.skip(true, 'Flask not available in this environment — skipping route map assertions');
		}
		await expect(page.getByTestId('route-map')).toBeVisible();
		const islands = page.locator('[data-testid^="island-node-"]');
		await expect(islands.first()).toBeVisible();
	});

	test('clicking an island opens the Voyage Log panel', async ({ page }) => {
		await page.goto('/voyage');
		const flaskDown = await page.getByTestId('flask-down-banner').isVisible();
		if (flaskDown) {
			test.skip(true, 'Flask not available — skipping interaction test');
		}
		const firstIsland = page.locator('[data-testid^="island-node-"]').first();
		if (!(await firstIsland.isVisible())) return;
		await firstIsland.click();
		await expect(page.getByTestId('voyage-log-panel')).toBeVisible();
	});

	test('clicking same island again closes the Voyage Log panel', async ({ page }) => {
		await page.goto('/voyage');
		const flaskDown = await page.getByTestId('flask-down-banner').isVisible();
		if (flaskDown) {
			test.skip(true, 'Flask not available — skipping toggle test');
		}
		const firstIsland = page.locator('[data-testid^="island-node-"]').first();
		if (!(await firstIsland.isVisible())) return;
		await firstIsland.click();
		await expect(page.getByTestId('voyage-log-panel')).toBeVisible();
		await firstIsland.click();
		await expect(page.getByTestId('voyage-log-panel')).not.toBeVisible();
	});
});
