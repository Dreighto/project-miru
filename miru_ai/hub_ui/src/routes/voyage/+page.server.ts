import { fetchFlask } from '$lib/server/flask';
import type { PageServerLoad } from './$types';

export interface Island {
	key: string;
	name: string;
	state: 'charted' | 'current' | 'fog';
}

export interface SetEntry {
	set_code: string;
	set_name: string;
	state: 'charted' | 'current' | 'fog';
	verified_count: number;
	total_count: number;
}

export interface VoyageLogEntry {
	kind: 'pattern' | 'alert';
	issue_type: string;
	count: number;
	message: string;
}

export interface VoyageProgress {
	sets_charted: number;
	sets_current: number;
	sets_fog: number;
	sets_total: number;
	islands_charted: number;
	islands_fog: number;
}

export interface VoyageData {
	islands: Island[];
	current_island: Island | null;
	sets: SetEntry[];
	voyage_log: VoyageLogEntry[];
	progress: VoyageProgress;
}

export const load: PageServerLoad = async () => {
	try {
		const voyage = await fetchFlask<VoyageData>('/api/dev/voyage');
		return { voyage, flaskDown: false as const };
	} catch {
		return { voyage: null, flaskDown: true as const };
	}
};
