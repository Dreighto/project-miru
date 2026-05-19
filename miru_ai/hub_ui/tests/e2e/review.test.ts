import { test, expect } from '@playwright/test';

test.describe('Review (/review)', () => {
	test('shows review heading and queue items', async ({ page }) => {
		// Stub /api/shadow-review/queue with page.route() — no separate mock server
		await page.route('**/api/shadow-review/queue', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						{
							canonical_code: 'OP01-001',
							print_id: 'OP01-001',
							contributing_model: 'qwen2.5:7b',
							promotion_status: 'experimental',
							confidence_score: 0.5
						}
					]
				})
			});
		});
		await page.goto('/review');
		await expect(page.getByRole('heading', { name: /review/i })).toBeVisible();
		await expect(page.getByText('OP01-001').first()).toBeVisible();
	});

	test('approve/reject buttons are inert (no network call)', async ({ page }) => {
		await page.route('**/api/shadow-review/queue', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						{
							canonical_code: 'OP01-001',
							print_id: 'OP01-001',
							contributing_model: 'qwen2.5:7b',
							promotion_status: 'experimental',
							confidence_score: 0.5
						}
					]
				})
			});
		});
		// Fail the test if ANY request fires to verdict/approve/reject endpoints
		let writeFired = false;
		await page.route('**/api/shadow-review/verdict', () => {
			writeFired = true;
		});
		await page.route('**/api/dev/publish-review/**', () => {
			writeFired = true;
		});
		await page.route('**/api/dev/training-review/**', () => {
			writeFired = true;
		});
		await page.goto('/review');
		await page.getByRole('button', { name: /approve/i }).first().click();
		await page.getByRole('button', { name: /reject/i }).first().click();
		expect(writeFired).toBe(false);
	});
});
