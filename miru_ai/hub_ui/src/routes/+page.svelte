<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<main class="mx-auto max-w-5xl space-y-8 p-6">
	<h1 class="text-2xl font-bold">Miru AI Dev — Glance</h1>
	<p data-testid="current-island" class="text-sm text-gray-500">Island: {currentIsland.value}</p>

	{#if data.flaskDown}
		<div
			role="alert"
			data-testid="flask-down-banner"
			class="rounded border border-red-400 bg-red-50 px-4 py-3 text-red-800"
		>
			Flask service unreachable. Start <code>miru_ai.server</code> on port 18765 and reload.
		</div>
	{:else}
		<!-- Dev Status -->
		<section aria-label="Dev status" data-testid="dev-status-section">
			<h2 class="mb-3 text-lg font-semibold">Dev Status</h2>
			<dl class="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
				<div>
					<dt class="text-gray-500">Updated</dt>
					<dd class="font-medium">{data.devStatus?.updated_at_display ?? '—'}</dd>
				</div>
				<div>
					<dt class="text-gray-500">Pending approvals</dt>
					<dd class="font-medium">{data.devStatus?.pending_approvals_count ?? '—'}</dd>
				</div>
				<div>
					<dt class="text-gray-500">Publication review</dt>
					<dd class="font-medium">{data.devStatus?.publication_review_count ?? '—'}</dd>
				</div>
				<div>
					<dt class="text-gray-500">Cards in catalog</dt>
					<dd class="font-medium">{data.devStatus?.catalog_status?.cards ?? '—'}</dd>
				</div>
				<div>
					<dt class="text-gray-500">Sets</dt>
					<dd class="font-medium">{data.devStatus?.catalog_status?.sets ?? '—'}</dd>
				</div>
				<div>
					<dt class="text-gray-500">Environment</dt>
					<dd class="font-medium">{data.devStatus?.dev_environment?.label ?? '—'}</dd>
				</div>
			</dl>
		</section>

		<!-- Activity Feed -->
		<section aria-label="Activity feed" data-testid="activity-feed-section">
			<h2 class="mb-3 text-lg font-semibold">Recent Activity</h2>
			{#if data.activity && data.activity.length > 0}
				<ul class="space-y-1 text-sm">
					{#each data.activity as item (item.timestamp + item.card_code + item.kind)}
						<li class="flex gap-3">
							<span class="shrink-0 text-gray-500">{item.timestamp.slice(0, 16)}</span>
							<span
								class={item.tone === 'good'
									? 'text-green-700'
									: item.tone === 'bad'
										? 'text-red-700'
										: 'text-gray-700'}
							>
								<span class="font-medium">{item.card_code}</span>
								{item.detail}
							</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-gray-500">No recent activity.</p>
			{/if}
		</section>

		<!-- Resource Metrics -->
		<section aria-label="Resource metrics" data-testid="resource-metrics-section">
			<h2 class="mb-3 text-lg font-semibold">Resource Metrics</h2>
			{#if data.resourceMetrics && data.resourceMetrics.length > 0}
				<ul class="space-y-2 text-sm">
					{#each data.resourceMetrics as metric (metric.key)}
						<li class="flex items-center gap-4">
							<span class="w-16 font-medium">{metric.label}</span>
							{#if metric.available}
								<div class="h-3 w-40 overflow-hidden rounded-full bg-gray-200">
									<div
										class="h-full rounded-full bg-blue-500"
										style="width: {metric.percent}%"
									></div>
								</div>
								<span class="text-gray-700">{metric.value}</span>
							{:else}
								<span class="text-gray-500">Unavailable</span>
							{/if}
						</li>
					{/each}
				</ul>
				<p class="mt-2 text-xs text-gray-600">Updated {data.metricsUpdatedAt}</p>
			{:else}
				<p class="text-sm text-gray-500">No metrics available.</p>
			{/if}
		</section>
	{/if}
</main>
