<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Tone = 'positive' | 'warning' | 'negative' | 'neutral';
	type ServiceId = 'miru-ai' | 'pm-storefront' | 'learner';
	type Action = 'start' | 'stop' | 'restart';

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

	// ── Service controls ──────────────────────────────────────────────────────

	type ControlState = {
		inflight: Action | null;
		error: string | null;
		confirmPending: Action | null;
	};

	let controls = $state<Record<ServiceId, ControlState>>({
		'miru-ai': { inflight: null, error: null, confirmPending: null },
		'pm-storefront': { inflight: null, error: null, confirmPending: null },
		learner: { inflight: null, error: null, confirmPending: null }
	});

	function requiresConfirm(action: Action): boolean {
		return action === 'stop' || action === 'restart';
	}

	async function triggerControl(service: ServiceId, action: Action): Promise<void> {
		const state = controls[service];

		if (requiresConfirm(action) && state.confirmPending !== action) {
			state.confirmPending = action;
			state.error = null;
			return;
		}

		state.confirmPending = null;
		state.inflight = action;
		state.error = null;

		try {
			const resp = await fetch('/api/runtime', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ service, action })
			});
			const body = (await resp.json()) as { ok?: boolean; error?: string; message?: string };
			if (!resp.ok || body.ok === false) {
				state.error = body.error ?? body.message ?? `Action failed (HTTP ${resp.status}).`;
			}
		} catch {
			state.error = 'Network error — could not reach the control endpoint.';
		} finally {
			state.inflight = null;
		}
	}

	function cancelConfirm(service: ServiceId): void {
		controls[service].confirmPending = null;
	}

	function dismissError(service: ServiceId): void {
		controls[service].error = null;
	}
</script>

<main class="mx-auto max-w-5xl space-y-8 p-6">
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

			<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<!-- Q1: Is everything up? -->
			<div
				class="rounded border border-border bg-surface p-4 md:col-span-2"
				data-testid="q1-services-section"
			>
				<h2 class="mb-3 text-xs font-mono uppercase tracking-widest text-text-faint">
					Is everything up?
				</h2>
				<ul class="space-y-3 text-sm">
					<!-- Miru AI (18765) -->
					<li data-testid="service-row-miru-ai">
						<div class="flex items-center gap-3">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[miru18765Tone]}"
								aria-hidden="true"
							></span>
							<span class="text-text-dim w-16 shrink-0 font-mono text-xs">18765</span>
							<span class="{textClass[miru18765Tone]} w-20">
								{data.devStatus?.surface_status?.miru_ai?.status ?? 'Running'}
							</span>
							<span class="text-text-faint text-xs">Miru AI</span>
							<span class="ml-auto flex items-center gap-1">
								{#if controls['miru-ai'].inflight}
									<span class="font-mono text-xs text-text-faint"
										>{controls['miru-ai'].inflight}&hellip;</span
									>
								{:else if controls['miru-ai'].confirmPending}
									<span class="mr-1 text-xs text-warning"
										>{controls['miru-ai'].confirmPending}?</span
									>
									<button
										class="rounded border border-negative px-2 py-0.5 font-mono text-xs text-negative hover:bg-negative hover:text-bg"
										onclick={() => triggerControl('miru-ai', controls['miru-ai'].confirmPending!)}
									>confirm</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2"
										onclick={() => cancelConfirm('miru-ai')}
									>cancel</button>
								{:else}
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['miru-ai'].inflight}
										onclick={() => triggerControl('miru-ai', 'start')}
										data-testid="ctrl-miru-ai-start"
									>start</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['miru-ai'].inflight}
										onclick={() => triggerControl('miru-ai', 'stop')}
										data-testid="ctrl-miru-ai-stop"
									>stop</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-accent hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['miru-ai'].inflight}
										onclick={() => triggerControl('miru-ai', 'restart')}
										data-testid="ctrl-miru-ai-restart"
									>restart</button>
								{/if}
							</span>
						</div>
						{#if controls['miru-ai'].error}
							<div class="mt-1 flex items-center gap-2 pl-5">
								<span class="text-xs text-negative">{controls['miru-ai'].error}</span>
								<button
									class="font-mono text-xs text-text-faint hover:text-text"
									onclick={() => dismissError('miru-ai')}
									aria-label="Dismiss error"
								>&times;</button>
							</div>
						{/if}
					</li>

					<!-- PM Storefront (18080) -->
					<li data-testid="service-row-pm-storefront">
						<div class="flex items-center gap-3">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[miru18080Tone]}"
								aria-hidden="true"
							></span>
							<span class="text-text-dim w-16 shrink-0 font-mono text-xs">18080</span>
							<span class="{textClass[miru18080Tone]} w-20">
								{data.devStatus?.project_miru?.reachable ? 'Online' : 'Offline'}
							</span>
							<span class="text-text-faint text-xs">Project Miru</span>
							<span class="ml-auto flex items-center gap-1">
								{#if controls['pm-storefront'].inflight}
									<span class="font-mono text-xs text-text-faint"
										>{controls['pm-storefront'].inflight}&hellip;</span
									>
								{:else if controls['pm-storefront'].confirmPending}
									<span class="mr-1 text-xs text-warning"
										>{controls['pm-storefront'].confirmPending}?</span
									>
									<button
										class="rounded border border-negative px-2 py-0.5 font-mono text-xs text-negative hover:bg-negative hover:text-bg"
										onclick={() =>
											triggerControl('pm-storefront', controls['pm-storefront'].confirmPending!)}
									>confirm</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2"
										onclick={() => cancelConfirm('pm-storefront')}
									>cancel</button>
								{:else}
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['pm-storefront'].inflight}
										onclick={() => triggerControl('pm-storefront', 'start')}
										data-testid="ctrl-pm-storefront-start"
									>start</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['pm-storefront'].inflight}
										onclick={() => triggerControl('pm-storefront', 'stop')}
										data-testid="ctrl-pm-storefront-stop"
									>stop</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-accent hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['pm-storefront'].inflight}
										onclick={() => triggerControl('pm-storefront', 'restart')}
										data-testid="ctrl-pm-storefront-restart"
									>restart</button>
								{/if}
							</span>
						</div>
						{#if controls['pm-storefront'].error}
							<div class="mt-1 flex items-center gap-2 pl-5">
								<span class="text-xs text-negative">{controls['pm-storefront'].error}</span>
								<button
									class="font-mono text-xs text-text-faint hover:text-text"
									onclick={() => dismissError('pm-storefront')}
									aria-label="Dismiss error"
								>&times;</button>
							</div>
						{/if}
					</li>

					<!-- Learner / shadow-loop -->
					<li data-testid="service-row-learner">
						<div class="flex items-center gap-3">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[learnerToneValue]}"
								aria-hidden="true"
							></span>
							<span class="text-text-dim w-16 shrink-0 font-mono text-xs">learner</span>
							<span class="{textClass[learnerToneValue]} w-20">
								{data.devStatus?.learning_engine?.learner_state ?? 'Unknown'}
							</span>
							<span class="text-text-faint text-xs">Learning worker</span>
							<span class="ml-auto flex items-center gap-1">
								{#if controls['learner'].inflight}
									<span class="font-mono text-xs text-text-faint"
										>{controls['learner'].inflight}&hellip;</span
									>
								{:else if controls['learner'].confirmPending}
									<span class="mr-1 text-xs text-warning"
										>{controls['learner'].confirmPending}?</span
									>
									<button
										class="rounded border border-negative px-2 py-0.5 font-mono text-xs text-negative hover:bg-negative hover:text-bg"
										onclick={() => triggerControl('learner', controls['learner'].confirmPending!)}
									>confirm</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2"
										onclick={() => cancelConfirm('learner')}
									>cancel</button>
								{:else}
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['learner'].inflight}
										onclick={() => triggerControl('learner', 'start')}
										data-testid="ctrl-learner-start"
									>start</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['learner'].inflight}
										onclick={() => triggerControl('learner', 'stop')}
										data-testid="ctrl-learner-stop"
									>stop</button>
									<button
										class="rounded border border-border px-2 py-0.5 font-mono text-xs text-accent hover:bg-surface2 disabled:opacity-40 disabled:cursor-not-allowed"
										disabled={!!controls['learner'].inflight}
										onclick={() => triggerControl('learner', 'restart')}
										data-testid="ctrl-learner-restart"
									>restart</button>
								{/if}
							</span>
						</div>
						{#if controls['learner'].error}
							<div class="mt-1 flex items-center gap-2 pl-5">
								<span class="text-xs text-negative">{controls['learner'].error}</span>
								<button
									class="font-mono text-xs text-text-faint hover:text-text"
									onclick={() => dismissError('learner')}
									aria-label="Dismiss error"
								>&times;</button>
							</div>
						{/if}
					</li>
				</ul>
			</div>

			<!-- Q2: What is Miru doing right now? -->
			<div
				class="rounded border border-border bg-surface p-4"
				data-testid="q2-activity-section"
			>
				<h2 class="mb-2 text-xs font-mono uppercase tracking-widest text-text-faint">
					What is Miru doing right now?
				</h2>
				<p class="text-sm text-text" data-testid="status-sentence">{statusSentence}</p>
			</div>

			<!-- Q3: Is anything wrong? -->
			<div
				class="rounded border border-border bg-surface p-4"
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
			<div class="rounded border border-border bg-surface p-4 md:col-span-2" data-testid="q4-waiting-section">
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
				<div class="rounded border border-dashed border-border px-4 py-3 text-sm text-text-faint">No recent activity.</div>
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
										class="h-full rounded-full bg-text-dim"
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
				<div class="rounded border border-dashed border-border px-4 py-3 text-sm text-text-faint">No metrics available.</div>
			{/if}
		</section>
	{/if}
</main>
