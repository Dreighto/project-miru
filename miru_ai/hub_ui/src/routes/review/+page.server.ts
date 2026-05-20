import { fetchFlask } from '$lib/server/flask';
import type { PageServerLoad } from './$types';

// Shape of a row from Flask's GET /api/shadow-review/queue
// (shadow_review.fetch_queue). PRO-928 replaced the single `promotion_status`
// column with the three-axis state model — readiness / approval / promotion —
// so each queue row now carries all three states.
export interface QueueItem {
	canonical_code: string;
	print_id: string;
	contributing_model: string;
	readiness_state: string;
	approval_state: string;
	promotion_state: string;
	confidence_score: number;
	inconclusive_field_count: number;
}

export interface QueueResponse {
	items: QueueItem[];
}

export const load: PageServerLoad = async () => {
	try {
		const queue = await fetchFlask<QueueResponse>('/api/shadow-review/queue');
		return { items: queue.items, flaskDown: false };
	} catch {
		return { items: null as QueueItem[] | null, flaskDown: true };
	}
};
