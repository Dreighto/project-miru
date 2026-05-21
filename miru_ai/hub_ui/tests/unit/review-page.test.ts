// Component tests — Review page (PRO-935). Covers:
//   - queue list rendering + row selection
//   - evidence panel loading on row select
//   - verdict submission (Approve / Reject) via /review/verdict BFF
//   - per-row error surfacing
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';

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

const itemDetail = {
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
			reason: 'Both sources agree',
			primary_value: 'Monkey D. Luffy',
			validator_value: 'Monkey D. Luffy',
			catalog_value: 'Monkey D. Luffy',
			bandai_value: null
		}
	],
	bandai_url: 'https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-001',
	tcgplayer_url: 'https://www.tcgplayer.com/search/one-piece-card-game/product?q=OP01-001'
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

describe('Review page — queue list', () => {
	it('renders the heading and queue section', () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(itemDetail)));
		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		expect(screen.getByRole('heading', { name: /review/i })).toBeTruthy();
		expect(screen.getByTestId('review-queue')).toBeTruthy();
	});

	it('shows empty message when queue has no items', () => {
		vi.stubGlobal('fetch', vi.fn());
		render(ReviewPage, { props: { data: { items: [], flaskDown: false } } });
		expect(screen.getByTestId('queue-empty')).toBeTruthy();
	});

	it('shows flask-down banner when service is unreachable', () => {
		vi.stubGlobal('fetch', vi.fn());
		render(ReviewPage, { props: { data: { items: null, flaskDown: true } } });
		expect(screen.getByTestId('flask-down-banner')).toBeTruthy();
	});

	it('renders a queue row for each item', () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(itemDetail)));
		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		expect(screen.getByTestId('queue-row-OP01-001')).toBeTruthy();
	});
});

describe('Review page — row selection + evidence panel', () => {
	it('clicking a queue row fetches item detail and shows the evidence panel', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(itemDetail));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });

		const row = screen.getByTestId('queue-row-OP01-001');
		await fireEvent.click(row);

		// The BFF call for item detail should have fired (URL includes query string)
		expect(fetchMock).toHaveBeenCalledWith(
			expect.stringContaining('/api/review/item/OP01-001/OP01-001')
		);

		// Verdict actions should now be visible
		await waitFor(() => {
			expect(screen.getByTestId('verdict-actions')).toBeTruthy();
		});
	});

	it('shows field outcomes table when item detail loads', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(itemDetail));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('queue-row-OP01-001'));

		await waitFor(() => {
			expect(screen.getByTestId('field-outcomes')).toBeTruthy();
		});
	});

	it('shows evidence error when BFF fetch fails', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: 'not found' }, 404));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('queue-row-OP01-001'));

		await waitFor(() => {
			expect(screen.getByTestId('evidence-error')).toBeTruthy();
		});
	});

	it('deselects row on second click', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(itemDetail));
		vi.stubGlobal('fetch', fetchMock);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		const row = screen.getByTestId('queue-row-OP01-001');
		await fireEvent.click(row);
		await fireEvent.click(row);

		// Evidence panel placeholder should be back
		await waitFor(() => {
			expect(screen.queryByTestId('verdict-actions')).toBeNull();
		});
	});
});

describe('Review page — verdict buttons', () => {
	async function renderWithSelectedRow() {
		const detailFetch = vi.fn().mockResolvedValue(jsonResponse(itemDetail));
		vi.stubGlobal('fetch', detailFetch);

		render(ReviewPage, { props: { data: { items: [item], flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('queue-row-OP01-001'));
		await waitFor(() => expect(screen.getByTestId('verdict-actions')).toBeTruthy());

		return detailFetch;
	}

	it('Approve POSTs verdict "correct" to the /review/verdict BFF', async () => {
		const detailFetch = await renderWithSelectedRow();
		detailFetch.mockResolvedValue(
			jsonResponse({ ok: true, new_approval_state: 'approved_for_candidate' })
		);

		await fireEvent.click(screen.getByTestId('verdict-approve'));

		const verdictCall = detailFetch.mock.calls.find(
			([url]) => typeof url === 'string' && url.includes('/review/verdict')
		);
		expect(verdictCall).toBeTruthy();
		expect(JSON.parse(verdictCall![1].body as string).verdict).toBe('correct');
	});

	it('Reject POSTs verdict "wrong"', async () => {
		const detailFetch = await renderWithSelectedRow();
		detailFetch.mockResolvedValue(jsonResponse({ ok: true }));

		await fireEvent.click(screen.getByTestId('verdict-reject'));

		const verdictCall = detailFetch.mock.calls.find(
			([url]) => typeof url === 'string' && url.includes('/review/verdict')
		);
		expect(verdictCall).toBeTruthy();
		expect(JSON.parse(verdictCall![1].body as string).verdict).toBe('wrong');
	});

	it('surfaces a per-row error when the verdict request fails', async () => {
		const detailFetch = await renderWithSelectedRow();
		detailFetch.mockResolvedValue(
			jsonResponse({ error: 'The review service is unreachable.' }, 503)
		);

		await fireEvent.click(screen.getByTestId('verdict-approve'));

		const err = await screen.findByTestId('verdict-error');
		expect(err).toHaveTextContent('unreachable');
	});
});
