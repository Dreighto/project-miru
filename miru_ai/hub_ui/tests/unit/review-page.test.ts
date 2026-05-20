// Component test — Review page verdict buttons (PRO-929). Deterministic: mock
// data + mocked fetch, no live Flask. The e2e (tests/e2e/review.test.ts) covers
// the same wiring against the real backend when the live queue has rows.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

// $app/* are SvelteKit virtual modules with no router context under vitest —
// mock them to no-ops so the page component can be unit-tested in isolation.
vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn().mockResolvedValue(undefined) }));
vi.mock('$app/paths', () => ({ resolve: (path: string) => path }));

import ReviewPage from '../../src/routes/review/+page.svelte';

const item = {
	canonical_code: 'OP01-001',
	print_id: 'OP01-001',
	contributing_model: 'qwen2.5:7b',
	readiness_state: 'blocked_by_guardrail',
	approval_state: 'pending_review',
	promotion_state: '',
	confidence_score: 0.5,
	inconclusive_field_count: 1
};

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

describe('Review page verdict buttons', () => {
	it('Approve POSTs verdict "correct" to the /review/verdict BFF', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(jsonResponse({ ok: true, new_approval_state: 'approved_for_candidate' }));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByRole('button', { name: /approve/i }));

		expect(fetchMock).toHaveBeenCalledTimes(1);
		const [url, init] = fetchMock.mock.calls[0];
		expect(String(url)).toContain('/review/verdict');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string).verdict).toBe('correct');
	});

	it('Reject POSTs verdict "wrong"', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByRole('button', { name: /reject/i }));

		expect(JSON.parse(fetchMock.mock.calls[0][1].body as string).verdict).toBe('wrong');
	});

	it('surfaces a per-row error when the verdict request fails', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(jsonResponse({ error: 'The review service is unreachable.' }, 503));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByRole('button', { name: /approve/i }));

		const err = await screen.findByTestId('verdict-error');
		expect(err).toHaveTextContent('unreachable');
	});
});
