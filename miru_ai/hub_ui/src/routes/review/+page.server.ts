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
	// Resolved /images/cards/... URL when art exists on disk, else null.
	// Added by the Flask route after fetch_queue() returns — the queue tile
	// uses it to render the thumb or fall back to the no-image badge.
	image_url: string | null;
	// Full-resolution URL for the lightbox (so the operator can read printed
	// card text). May equal image_url if only one resolution is on disk.
	full_image_url: string | null;
	// Resolver's verdict: "variant_exact" = showing the right art for the
	// variant; "base_fallback" = variant image missing, showing base art
	// instead (UI badges this); "missing" = nothing on disk.
	image_source: 'variant_exact' | 'base_fallback' | 'missing';
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
