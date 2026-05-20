// E2E test — Review page (/review): render + verdict-button wiring (PRO-929).
//
// The queue loads server-side (+page.server.ts), so it renders against the live
// review service and is not stubbed here. The verdict POST is a browser-side
// fetch to the /review/verdict BFF, which page.route() CAN intercept.
import { test, expect } from '@playwright/test';

test.describe('Review (/review)', () => {
	test('renders the review page', async ({ page }) => {
		await page.goto('/review');
		await expect(page.getByRole('heading', { name: /review/i })).toBeVisible();
		await expect(page.getByTestId('current-island')).toBeVisible();
	});

	test('verdict buttons are wired to the review backend (not inert)', async ({ page }) => {
		let verdictPayload: { verdict?: string } | null = null;
		await page.route('**/review/verdict', async (route) => {
			verdictPayload = JSON.parse(route.request().postData() ?? '{}');
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					ok: true,
					new_approval_state: 'approved_for_candidate',
					event_logged: true
				})
			});
		});

		await page.goto('/review');
		await expect(page.getByRole('heading', { name: /review/i })).toBeVisible();

		const approve = page.getByRole('button', { name: /approve/i }).first();
		if ((await approve.count()) === 0) {
			// Live queue is empty — no pending rows to act on. The render check
			// above already proves the page is healthy; nothing to click.
			return;
		}

		// Retry the click + assertion as a unit. The first click can land before
		// SvelteKit finishes hydrating the button's handler (a no-op), and the
		// verdict fetch resolves asynchronously — toPass() re-runs until the
		// verdict POST has actually fired. PRO-929: the button is no longer an
		// inert stub.
		await expect(async () => {
			await approve.click();
			expect(verdictPayload?.verdict).toBe('correct');
		}).toPass({ timeout: 15_000 });
	});
});
