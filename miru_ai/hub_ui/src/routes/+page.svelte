<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tone = 'positive' | 'warning' | 'negative' | 'neutral';

	function learnerTone(state: string | undefined): Tone {
		if (!state) return 'negative';
		const s = state.toLowerCase();
		if (s === 'running' || s === 'idle') return 'positive';
		if (s === 'starting' || s === 'stale') return 'warning';
		return 'negative';
	}

	function serviceTone(status: string | undefined, reachable?: boolean): Tone {
		if (reachable === false) return 'negative';
		if (!status) return 'neutral';
		const s = status.toLowerCase();
		if (s === 'running' || s === 'online' || s === 'ok') return 'positive';
		if (s === 'starting' || s === 'degraded') return 'warning';
		if (s === 'offline') return 'negative';
		return 'neutral';
	}

	const dotClass: Record<Tone, string> = {
		positive: 'bg-positive',
		warning: 'bg-warning',
		negative: 'bg-negative',
		neutral: 'bg-text-faint'
	};

	const textClass: Record<Tone, string> = {
		positive: 'text-positive',
		warning: 'text-warning',
		negative: 'text-negative',
		neutral: 'text-text-dim'
	};

	const miru18765Tone = $derived(
		data.devStatus ? serviceTone(data.devStatus.surface_status?.miru_ai?.status) : 'negative'
	);
	const miru18080Tone = $derived(
		data.devStatus
			? serviceTone(
					data.devStatus.surface_status?.worktree_dashboard?.status,
					data.devStatus.project_miru?.reachable
				)
			: 'negative'
	);
	const learnerToneValue = $derived(learnerTone(data.devStatus?.learning_engine?.learner_state));

	const topIssue = $derived((): string => {
		if (!data.devStatus) return 'Flask service is unreachable.';
		const miruItems = data.devStatus.issues?.miru_ai?.items ?? [];
		const pmItems = data.devStatus.issues?.project_miru?.items ?? [];
		return miruItems[0] ?? pmItems[0] ?? 'Nothing needs you.';
	});

	const hasIssue = $derived((): boolean => {
		if (!data.devStatus) return true;
		const miruItems = data.devStatus.issues?.miru_ai?.items ?? [];
		const pmItems = data.devStatus.issues?.project_miru?.items ?? [];
		return miruItems.length > 0 || pmItems.length > 0;
	});

	const pendingTotal = $derived(
		(data.devStatus?.pending_approvals_count ?? 0) +
			(data.devStatus?.publication_review_count ?? 0)
	);

	const statusSentence = $derived(
		data.devStatus?.intelligence_status?.status_sentence ??
			data.devStatus?.intelligence_status?.activity_hint ??
			'Status unavailable.'
	);
</script>

<main class="mx-auto max-w-4xl space-y-8 p-6">
	{#if data.flaskDown}
		<div
			role="alert"
			data-testid="flask-down-banner"
			class="rounded border border-negative bg-surface px-4 py-3 text-negative"
		>
			Flask service unreachable. Start <code class="font-mono text-sm">miru_ai.server</code> on port
			18765 and reload.
		</div>
	{:else}
		<!-- Four-question status view -->
		<section aria-label="Glance status" data-testid="glance-status-section">
			<h1 class="mb-6 text-xl font-sans font-semibold text-text">Glance</h1>

			<!-- Q1: Is everything up? -->
			<div
				class="mb-4 rounded border border-border bg-surface p-4"
				data-testid="q1-services-section"
			>
				<h2 class="mb-3 text-xs font-mono uppercase tracking-widest text-text-faint">
					Is everything up?
				</h2>
				<ul class="space-y-2 text-sm">
					<li class="flex items-center gap-3">
						<span
							class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[miru18765Tone]}"
							aria-hidden="true"
						></span>
						<span class="text-text-dim w-16 shrink-0 font-mono text-xs">18765</span>
						<span class="{textClass[miru18765Tone]}">
							{data.devStatus?.surface_status?.miru_ai?.status ?? 'Running'}
						</span>
						<span class="text-text-faint text-xs">Miru AI</span>
					</li>
					<li class="flex items-center gap-3">
						<span
							class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[miru18080Tone]}"
							aria-hidden="true"
						></span>
						<span class="text-text-dim w-16 shrink-0 font-mono text-xs">18080</span>
						<span class="{textClass[miru18080Tone]}">
							{data.devStatus?.project_miru?.reachable ? 'Online' : 'Offline'}
						</span>
						<span class="text-text-faint text-xs">Project Miru</span>
					</li>
					<li class="flex items-center gap-3">
						<span
							class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[learnerToneValue]}"
							aria-hidden="true"
						></span>
						<span class="text-text-dim w-16 shrink-0 font-mono text-xs">learner</span>
						<span class="{textClass[learnerToneValue]}">
							{data.devStatus?.learning_engine?.learner_state ?? 'Unknown'}
						</span>
						<span class="text-text-faint text-xs">Learning worker</span>
					</li>
				</ul>
			</div>

			<!-- Q2: What is Miru doing right now? -->
			<div
				class="mb-4 rounded border border-border bg-surface p-4"
				data-testid="q2-activity-section"
			>
				<h2 class="mb-2 text-xs font-mono uppercase tracking-widest text-text-faint">
					What is Miru doing right now?
				</h2>
				<p class="text-sm text-text" data-testid="status-sentence">{statusSentence}</p>
			</div>

			<!-- Q3: Is anything wrong? -->
			<div
				class="mb-4 rounded border border-border bg-surface p-4"
				data-testid="q3-issues-section"
			>
				<h2 class="mb-2 text-xs font-mono uppercase tracking-widest text-text-faint">
					Is anything wrong?
				</h2>
				<p
					class="text-sm {hasIssue() ? 'text-warning' : 'text-positive'}"
					data-testid="top-issue"
				>
					{topIssue()}
				</p>
			</div>

			<!-- Q4: What's waiting for me? -->
			<div class="rounded border border-border bg-surface p-4" data-testid="q4-waiting-section">
				<h2 class="mb-2 text-xs font-mono uppercase tracking-widest text-text-faint">
					What's waiting for me?
				</h2>
				{#if pendingTotal > 0}
					<p class="text-sm">
						<span class="text-warning font-semibold">{pendingTotal}</span>
						<span class="text-text-dim">
							{pendingTotal === 1 ? 'card' : 'cards'} pending review —
						</span>
						<a href="/review" class="text-accent underline underline-offset-2">open Review</a>
					</p>
				{:else}
					<p class="text-sm text-positive">Queue is clear. Nothing held for review.</p>
				{/if}
			</div>
		</section>

		<!-- Supporting detail: Recent Activity -->
		<section aria-label="Activity feed" data-testid="activity-feed-section">
			<h2 class="mb-3 text-sm font-semibold text-text-dim">Recent Activity</h2>
			{#if data.activity && data.activity.length > 0}
				<ul class="space-y-1 text-sm">
					{#each data.activity as item (item.timestamp + item.card_code + item.kind)}
						<li class="flex gap-3">
							<span class="shrink-0 font-mono text-xs text-text-faint"
								>{item.timestamp.slice(0, 16)}</span
							>
							<span
								class={item.tone === 'good'
									? 'text-positive'
									: item.tone === 'bad'
										? 'text-negative'
										: 'text-text-dim'}
							>
								<span class="font-semibold">{item.card_code}</span>
								{item.detail}
							</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-text-faint">No recent activity.</p>
			{/if}
		</section>

		<!-- Supporting detail: Resource Metrics -->
		<section aria-label="Resource metrics" data-testid="resource-metrics-section">
			<h2 class="mb-3 text-sm font-semibold text-text-dim">Resource Metrics</h2>
			{#if data.resourceMetrics && data.resourceMetrics.length > 0}
				<ul class="space-y-2 text-sm">
					{#each data.resourceMetrics as metric (metric.key)}
						<li class="flex items-center gap-4">
							<span class="w-20 shrink-0 font-mono text-xs text-text-dim">{metric.label}</span>
							{#if metric.available}
								<div class="h-2 w-36 overflow-hidden rounded-full bg-surface2">
									<div
										class="h-full rounded-full bg-accent"
										style="width: {metric.percent}%"
									></div>
								</div>
								<span class="text-text-dim">{metric.value}</span>
							{:else}
								<span class="text-text-faint">Unavailable</span>
							{/if}
						</li>
					{/each}
				</ul>
				<p class="mt-2 font-mono text-xs text-text-faint">Updated {data.metricsUpdatedAt}</p>
			{:else}
				<p class="text-sm text-text-faint">No metrics available.</p>
			{/if}
		</section>
	{/if}
</main>
