import { fetchFlask } from '$lib/server/flask';

export interface ServiceStatus {
	port: string;
	status: string;
}

export interface IssueCard {
	label: string;
	status: string;
	tone: 'good' | 'warn';
	detail: string;
	items: string[];
}

export interface DevStatus {
	updated_at_display: string;
	pending_approvals_count: number;
	publication_review_count: number;
	catalog_status: { cards: number; sets: number; usable: boolean };
	dev_environment: { label: string; current_port: number };
	surface_status: {
		miru_ai: ServiceStatus;
		worktree_dashboard: ServiceStatus;
	};
	project_miru: {
		reachable: boolean;
		status_code: number;
		detail: string;
	};
	learning_engine: {
		learner_state: string;
	};
	issues: {
		miru_ai: IssueCard;
		project_miru: IssueCard;
	};
	intelligence_status: {
		status_sentence: string;
		worker: { label: string; tone: string; detail: string };
		activity_hint: string;
	};
}

export interface ActivityItem {
	card_code: string;
	detail: string;
	kind: string;
	timestamp: string;
	title: string;
	tone: string;
}

export interface ResourceMetric {
	available: boolean;
	detail: string;
	key: string;
	label: string;
	percent: number;
	value: string;
}

// Top-of-Glance "Needs you" tile data. Mirrors the shape Flask returns on
// /api/shadow-review/queue rows (only the fields the landing tile renders, to
// keep the load payload tight).
export interface NeedsYouTile {
	canonical_code: string;
	print_id: string;
	contributing_model: string;
	readiness_state: string;
	approval_state: string;
	inconclusive_field_count: number;
	image_url: string | null;
}

export async function load() {
	try {
		// Pull the top 5 review-queue entries alongside status — same data the
		// Review page uses, but capped for the Glance tile strip. If the queue
		// call fails (older Flask, transient hiccup) we still want Glance to
		// render — just without the tile strip.
		const [devStatus, activityRaw, metricsRaw, queueRaw] = await Promise.all([
			fetchFlask<DevStatus>('/api/dev-status'),
			fetchFlask<{ activity: ActivityItem[] }>('/api/dev/activity-feed'),
			fetchFlask<{ resource_metrics: ResourceMetric[]; updated_at: string }>(
				'/api/dev/resource-metrics'
			),
			fetchFlask<{ items: NeedsYouTile[]; total: number }>(
				'/api/shadow-review/queue?limit=6'
			).catch(() => ({ items: [], total: 0 }))
		]);

		return {
			flaskDown: false as const,
			devStatus,
			activity: activityRaw.activity.slice(0, 8),
			resourceMetrics: metricsRaw.resource_metrics,
			metricsUpdatedAt: metricsRaw.updated_at,
			needsYou: queueRaw.items,
			needsYouTotal: queueRaw.total
		};
	} catch {
		return {
			flaskDown: true as const,
			devStatus: null,
			activity: null,
			resourceMetrics: null,
			metricsUpdatedAt: null,
			needsYou: [] as NeedsYouTile[],
			needsYouTotal: 0
		};
	}
}
