// E2E test — Glance page: four-question status view + supporting detail (PRO-931).
import { test, expect } from '@playwright/test';

test.describe('Glance page', () => {
	test('renders the four-question status view when Flask is up', async ({ page }) => {
		await page.goto('/');

		// Flask-down banner must NOT be visible when Flask is reachable
		await expect(page.getByTestId('flask-down-banner')).not.toBeVisible();

		// The four-question status sections
		await expect(page.getByTestId('q1-services-section')).toBeVisible();
		await expect(page.getByTestId('q2-activity-section')).toBeVisible();
		await expect(page.getByTestId('q3-issues-section')).toBeVisible();
		await expect(page.getByTestId('q4-waiting-section')).toBeVisible();

		// Supporting detail sections
		await expect(page.getByTestId('activity-feed-section')).toBeVisible();
		await expect(page.getByTestId('resource-metrics-section')).toBeVisible();
	});
});
