// E2E test — Review page (/review): Ink re-skin + evidence panel (PRO-935).
//
// The queue loads server-side (+page.server.ts). The evidence panel BFF
// (/api/review/item/...) is intercepted here to control item-detail data.
// Verdict submission goes through /review/verdict (also intercepted).
import { test, expect } from '@playwright/test';

test.describe('Review (/review)', () => {
	test('renders the review page with Ink styling', async ({ page }) => {
		await page.goto('/review');
		await expect(page.getByRole('heading', { name: /review/i })).toBeVisible();
		await expect(page.getByTestId('review-queue')).toBeVisible();
	});

	test('shows empty queue message when queue is empty', async ({ page }) => {
		await page.goto('/review');
		// If the live queue is empty the placeholder should show
		const queueEmpty = page.getByTestId('queue-empty');
		const queueRow = page.locator('[data-testid^="queue-row-"]').first();
		// One of these should be visible — either rows or empty state
		const hasRows = (await queueRow.count()) > 0;
		if (!hasRows) {
			await expect(queueEmpty).toBeVisible();
		}
	});

	test('selecting a row fetches evidence and shows verdict actions', async ({ page }) => {
		// Intercept the item-detail BFF
		await page.route('**/api/review/item/**', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					canonical_code: 'OP01-001',
					print_id: 'OP01-001',
					contributing_model: 'qwen2.5:7b',
					readiness_state: 'blocked_by_guardrail',
					approval_state: 'pending_review',
					promotion_state: '',
					confidence_score: 0.5,
					field_outcomes: [
						{
							field: 'name',
							tier: 'primary',
							outcome: 'verified',
							reason: 'Sources agree',
							primary_value: 'Monkey D. Luffy',
							validator_value: 'Monkey D. Luffy',
							catalog_value: 'Monkey D. Luffy',
							bandai_value: null
						}
					],
					bandai_url: 'https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-001',
					tcgplayer_url:
						'https://www.tcgplayer.com/search/one-piece-card-game/product?q=OP01-001'
				})
			});
		});

		await page.goto('/review');

		const firstRow = page.locator('[data-testid^="queue-row-"]').first();
		test.skip((await firstRow.count()) === 0, 'live queue is empty — no rows to select');

		await firstRow.click();
		await expect(page.getByTestId('evidence-panel')).toBeVisible();
		await expect(page.getByTestId('verdict-actions')).toBeVisible();
	});

	test('verdict buttons are wired to the review backend (not inert)', async ({ page }) => {
		let verdictPayload: { verdict?: string } | null = null;

		await page.route('**/api/review/item/**', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					canonical_code: 'OP01-001',
					print_id: 'OP01-001',
					contributing_model: 'qwen2.5:7b',
					readiness_state: 'blocked_by_guardrail',
					approval_state: 'pending_review',
					promotion_state: '',
					confidence_score: 0.5,
					field_outcomes: [],
					bandai_url: null,
					tcgplayer_url: null
				})
			});
		});

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

		const firstRow = page.locator('[data-testid^="queue-row-"]').first();
		test.skip((await firstRow.count()) === 0, 'live queue is empty — no rows to exercise');

		await firstRow.click();
		await expect(page.getByTestId('verdict-actions')).toBeVisible({ timeout: 5_000 });

		await expect(async () => {
			await page.getByTestId('verdict-approve').click();
			expect(verdictPayload?.verdict).toBe('correct');
		}).toPass({ timeout: 15_000 });
	});
});
