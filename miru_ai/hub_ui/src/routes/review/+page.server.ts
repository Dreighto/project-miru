import { fetchFlask } from '$lib/server/flask';
import type { PageServerLoad } from './$types';

export interface QueueItem {
	canonical_code: string;
	print_id: string;
	contributing_model: string;
	promotion_status: string;
	confidence_score: number;
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
