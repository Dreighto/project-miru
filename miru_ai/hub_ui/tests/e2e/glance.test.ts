// E2E test — Glance page BFF (PRO-920): three data sections render from Flask.
import { test, expect } from '@playwright/test';

test.describe('Glance page', () => {
	test('renders all three data sections when Flask is up', async ({ page }) => {
		await page.goto('/');

		// Flask-down banner must NOT be visible when Flask is reachable
		await expect(page.getByTestId('flask-down-banner')).not.toBeVisible();

		// Dev status section
		const devStatus = page.getByTestId('dev-status-section');
		await expect(devStatus).toBeVisible();

		// Activity feed section
		const activityFeed = page.getByTestId('activity-feed-section');
		await expect(activityFeed).toBeVisible();

		// Resource metrics section
		const resourceMetrics = page.getByTestId('resource-metrics-section');
		await expect(resourceMetrics).toBeVisible();
	});
});
