import { fetchFlask } from '$lib/server/flask';
import type { PageServerLoad } from './$types';

export interface ThroughputData {
	total_reviews: number;
	today_reviews: number;
	distinct_cards_reviewed: number;
	op01_total_cards: number;
}

export const load: PageServerLoad = async () => {
	try {
		const throughput = await fetchFlask<ThroughputData>('/api/dev/op01/throughput');
		return { throughput, flaskDown: false };
	} catch {
		return { throughput: null as ThroughputData | null, flaskDown: true };
	}
};
