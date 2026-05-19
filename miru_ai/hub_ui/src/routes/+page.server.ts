import { fetchFlask } from '$lib/server/flask';

export interface DevStatus {
	updated_at_display: string;
	pending_approvals_count: number;
	publication_review_count: number;
	catalog_status: { cards: number; sets: number; usable: boolean };
	dev_environment: { label: string; current_port: number };
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

export async function load() {
	try {
		const [devStatus, activityRaw, metricsRaw] = await Promise.all([
			fetchFlask<DevStatus>('/api/dev-status'),
			fetchFlask<{ activity: ActivityItem[] }>('/api/dev/activity-feed'),
			fetchFlask<{ resource_metrics: ResourceMetric[]; updated_at: string }>(
				'/api/dev/resource-metrics'
			)
		]);

		return {
			flaskDown: false as const,
			devStatus,
			activity: activityRaw.activity.slice(0, 10),
			resourceMetrics: metricsRaw.resource_metrics,
			metricsUpdatedAt: metricsRaw.updated_at
		};
	} catch {
		return {
			flaskDown: true as const,
			devStatus: null,
			activity: null,
			resourceMetrics: null,
			metricsUpdatedAt: null
		};
	}
}
