<script lang="ts">
	import { invalidateAll } from '$app/navigation';
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

	const needsYouTotal = $derived(data.needsYouTotal ?? 0);
	const needsYouTiles = $derived(data.needsYou ?? []);

	// Some catalog rows hold an image_path that no longer exists on disk. Track
	// per-URL load failure so the tile can fall back to the no-image badge
	// instead of showing the browser's broken-image glyph.
	let imageFailed = $state<Record<string, true>>({});
	function markImageFailed(url: string) {
		imageFailed[url] = true;
	}

	// ── Image fetcher (singleton background job on Flask) ─────────────────────

	interface FetchJobState {
		status: 'idle' | 'running' | 'done' | 'error';
		started_at?: string | null;
		finished_at?: string | null;
		fetched: number;
		skipped: number;
		failed: string[];
		log_tail: string[];
		error?: string | null;
		trigger?: string | null;
	}

	let fetchJob = $state<FetchJobState | null>(null);
	let fetchPollTimer: ReturnType<typeof setTimeout> | null = null;
	let fetchKickoffError = $state<string | null>(null);

	async function startImageFetch(): Promise<void> {
		if (fetchJob?.status === 'running') return;
		fetchKickoffError = null;
		try {
			const resp = await fetch('/api/dev/fetch-missing-images', { method: 'POST' });
			const body = (await resp.json().catch(() => ({}))) as {
				status?: string;
				message?: string;
				error?: string;
			};
			if (!resp.ok) {
				fetchKickoffError = body.error ?? `Could not start fetch (HTTP ${resp.status}).`;
				return;
			}
			// The Flask route is singleton: it returns {status: "started", message}
			// whether it kicked off a new job or attached to an already-running one.
			// Either way, start polling so the panel reflects current progress.
			fetchJob = {
				status: 'running',
				fetched: 0,
				skipped: 0,
				failed: [],
				log_tail: [],
				error: null
			};
			pollFetchJob();
		} catch {
			fetchKickoffError = 'Could not reach the dev-status service.';
		}
	}

	async function pollFetchJob(): Promise<void> {
		try {
			const resp = await fetch('/api/dev/fetch-missing-images/status');
			if (resp.ok) {
				fetchJob = (await resp.json()) as FetchJobState;
			}
		} catch {
			// Transient — keep polling.
		}
		if (fetchJob && fetchJob.status === 'running') {
			fetchPollTimer = setTimeout(pollFetchJob, 2500);
		} else if (fetchJob && fetchJob.status === 'done') {
			// Refresh the page data so the Needs-you tile thumbs reflect newly-fetched art.
			invalidateAll();
		}
	}

	function dismissFetchJob(): void {
		if (fetchPollTimer) {
			clearTimeout(fetchPollTimer);
			fetchPollTimer = null;
		}
		fetchJob = null;
		fetchKickoffError = null;
	}

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

	// Service rows reused by Q1 — kept as a data array so the markup stays uniform.
	type ServiceRow = {
		id: ServiceId;
		port: string;
		label: string;
		tone: Tone;
		statusText: string;
		testid: string;
	};

	const serviceRows = $derived<ServiceRow[]>([
		{
			id: 'miru-ai',
			port: '18765',
			label: 'Miru AI',
			tone: miru18765Tone,
			statusText: data.devStatus?.surface_status?.miru_ai?.status ?? 'Running',
			testid: 'service-row-miru-ai'
		},
		{
			id: 'pm-storefront',
			port: '18080',
			label: 'Project Miru',
			tone: miru18080Tone,
			statusText: data.devStatus?.project_miru?.reachable ? 'Online' : 'Offline',
			testid: 'service-row-pm-storefront'
		},
		{
			id: 'learner',
			port: 'learner',
			label: 'Learning worker',
			tone: learnerToneValue,
			statusText: data.devStatus?.learning_engine?.learner_state ?? 'Unknown',
			testid: 'service-row-learner'
		}
	]);
</script>

<main class="mx-auto max-w-5xl space-y-4 p-3 sm:space-y-5 sm:p-5">
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
		<section aria-label="Glance status" data-testid="glance-status-section" class="space-y-4">
			<div class="flex items-baseline justify-between">
				<h1 class="font-sans text-lg font-semibold text-text sm:text-xl">Glance</h1>
				<span class="font-mono text-[11px] text-text-faint"
					>{data.devStatus?.updated_at_display ?? ''}</span
				>
			</div>

			<!-- ───── Needs-you tile strip ───── -->
			<div
				class="rounded border border-border bg-surface p-3 sm:p-4"
				data-testid="needs-you-section"
				aria-label="Cards waiting for review"
			>
				<div class="mb-2 flex items-baseline justify-between gap-2">
					<h2 class="font-mono text-xs uppercase tracking-widest text-text-faint">
						{needsYouTotal > 0 ? `Needs you (${needsYouTotal})` : "What's waiting for me?"}
					</h2>
					{#if needsYouTotal > 0}
						<a
							href="/review"
							class="font-mono text-[11px] text-accent underline underline-offset-2"
							data-testid="needs-you-open-review"
						>open Review →</a
						>
					{/if}
				</div>

				{#if needsYouTiles.length === 0}
					<p class="text-sm text-positive" data-testid="needs-you-empty">
						Queue is clear. Nothing held for review.
					</p>
				{:else}
					<ul
						class="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6"
						data-testid="needs-you-tiles"
						role="list"
					>
						{#each needsYouTiles.slice(0, 6) as item (item.canonical_code + '|' + item.print_id + '|' + item.contributing_model)}
							<li>
								<a
									href="/review"
									class="group block overflow-hidden rounded border border-border bg-bg hover:border-accent"
									data-testid="needs-you-tile-{item.canonical_code}"
								>
									<div class="relative aspect-[2/3] w-full bg-surface">
										{#if item.image_url && !imageFailed[item.image_url]}
											<img
												src={item.image_url}
												alt=""
												loading="lazy"
												onerror={() => markImageFailed(item.image_url!)}
												class="h-full w-full object-cover"
											/>
										{:else}
											<div
												class="flex h-full w-full flex-col items-center justify-center p-1 text-center"
											>
												<span
													class="font-mono text-[9px] uppercase tracking-wide text-warning"
													>no img</span
												>
											</div>
										{/if}
										{#if item.readiness_state === 'blocked_by_guardrail'}
											<span
												class="absolute left-1 top-1 rounded bg-negative/85 px-1 py-0 font-mono text-[8px] uppercase text-bg"
												title="blocked by guardrail">blocked</span
											>
										{:else if item.inconclusive_field_count > 0}
											<span
												class="absolute left-1 top-1 rounded bg-warning/85 px-1 py-0 font-mono text-[8px] uppercase text-bg"
												title="{item.inconclusive_field_count} inconclusive field(s)"
												>{item.inconclusive_field_count} inc</span
											>
										{/if}
									</div>
									<div class="px-1.5 py-1">
										<p class="truncate font-mono text-[10px] text-text" title={item.canonical_code}>
											{item.canonical_code}
										</p>
									</div>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<!-- ───── Image fetcher (manual maintenance) ───── -->
			<div
				class="rounded border border-border bg-surface p-3 sm:p-4"
				data-testid="fetch-missing-images-section"
				aria-label="Fetch missing card art"
			>
				<div class="flex flex-wrap items-start justify-between gap-3">
					<div>
						<h2 class="font-mono text-xs uppercase tracking-widest text-text-faint">
							Missing card art
						</h2>
						<p class="mt-1 text-xs text-text-dim">
							Pull any card images the catalog knows about but doesn't have on disk yet (from
							Bandai's official card list). Safe to re-run — already-downloaded files are skipped.
						</p>
						<p class="mt-1 text-[11px] italic text-text-faint">
							Only fetches rows where the catalog's <code class="font-mono">image_path</code> is empty.
							If the catalog points to a file that's actually missing on disk (stale path), this
							won't catch it — that's a separate clean-up I'll wire next.
						</p>
					</div>
					{#if !fetchJob || fetchJob.status !== 'running'}
						<button
							type="button"
							onclick={startImageFetch}
							class="shrink-0 rounded border border-accent px-3 py-1.5 font-mono text-xs text-accent hover:bg-accent hover:text-bg"
							data-testid="fetch-missing-images-start"
						>
							{fetchJob ? 'Run again' : 'Fetch missing card art'}
						</button>
					{/if}
				</div>

				{#if fetchKickoffError}
					<p class="mt-2 text-xs text-negative" role="alert" data-testid="fetch-kickoff-error">
						{fetchKickoffError}
					</p>
				{/if}

				{#if fetchJob}
					<div class="mt-3 rounded border border-border bg-bg p-2.5" data-testid="fetch-job-panel">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<span
								class="font-mono text-[11px] uppercase tracking-widest {fetchJob.status === 'running'
									? 'text-warning'
									: fetchJob.status === 'done'
										? 'text-positive'
										: 'text-negative'}"
								data-testid="fetch-job-status"
							>
								{fetchJob.status === 'running'
									? 'Fetching…'
									: fetchJob.status === 'done'
										? 'Done'
										: 'Error'}
							</span>
							{#if fetchJob.status !== 'running'}
								<button
									type="button"
									onclick={dismissFetchJob}
									class="font-mono text-[11px] text-text-faint hover:text-text"
									data-testid="fetch-job-dismiss"
									aria-label="Dismiss fetch status"
								>&times;</button>
							{/if}
						</div>
						<dl
							class="mt-1.5 grid grid-cols-3 gap-2 font-mono text-[11px]"
							data-testid="fetch-job-counts"
						>
							<div>
								<dt class="text-text-faint">Fetched</dt>
								<dd class="text-positive">{fetchJob.fetched}</dd>
							</div>
							<div>
								<dt class="text-text-faint">Skipped</dt>
								<dd class="text-text-dim">{fetchJob.skipped}</dd>
							</div>
							<div>
								<dt class="text-text-faint">Failed</dt>
								<dd class={fetchJob.failed.length > 0 ? 'text-negative' : 'text-text-dim'}>
									{fetchJob.failed.length}
								</dd>
							</div>
						</dl>
						{#if fetchJob.error}
							<p class="mt-2 text-xs text-negative" data-testid="fetch-job-error">
								{fetchJob.error}
							</p>
						{/if}
						{#if fetchJob.log_tail.length > 0}
							<details class="mt-2">
								<summary
									class="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-text-faint hover:text-text"
								>
									Show log ({fetchJob.log_tail.length})
								</summary>
								<pre
									class="mt-1 max-h-40 overflow-y-auto rounded bg-surface2 p-2 font-mono text-[10px] leading-tight text-text-dim"
									data-testid="fetch-job-log">{fetchJob.log_tail.join('\n')}</pre>
							</details>
						{/if}
						{#if fetchJob.status === 'done' && fetchJob.failed.length > 0}
							<details class="mt-2">
								<summary
									class="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-negative"
								>
									Failed ({fetchJob.failed.length})
								</summary>
								<ul
									class="mt-1 max-h-32 overflow-y-auto rounded bg-surface2 p-2 font-mono text-[10px] text-negative"
								>
									{#each fetchJob.failed as f, i (i)}
										<li>{f}</li>
									{/each}
								</ul>
							</details>
						{/if}
					</div>
				{/if}
			</div>

			<!-- ───── Status grid (Q1 services + Q2/Q3) ───── -->
			<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
				<!-- Q1: services -->
				<div
					class="rounded border border-border bg-surface p-3 sm:p-4 md:col-span-2"
					data-testid="q1-services-section"
				>
					<h2 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">
						Is everything up?
					</h2>
					<ul class="space-y-2 text-sm">
						{#each serviceRows as row (row.id)}
							<li data-testid={row.testid}>
								<div class="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
									<span
										class="inline-block h-2 w-2 shrink-0 rounded-full {dotClass[row.tone]}"
										aria-hidden="true"
									></span>
									<span class="w-14 shrink-0 font-mono text-[11px] text-text-dim sm:w-16">
										{row.port}
									</span>
									<span class="{textClass[row.tone]} w-20 truncate font-mono text-xs">
										{row.statusText}
									</span>
									<span class="hidden text-xs text-text-faint sm:inline">{row.label}</span>
									<span class="ml-auto flex items-center gap-1">
										{#if controls[row.id].inflight}
											<span class="font-mono text-[11px] text-text-faint"
												>{controls[row.id].inflight}…</span
											>
										{:else if controls[row.id].confirmPending}
											<span class="mr-1 text-[11px] text-warning"
												>{controls[row.id].confirmPending}?</span
											>
											<button
												class="rounded border border-negative px-1.5 py-0.5 font-mono text-[11px] text-negative hover:bg-negative hover:text-bg"
												onclick={() => triggerControl(row.id, controls[row.id].confirmPending!)}
											>confirm</button>
											<button
												class="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-text-dim hover:bg-surface2"
												onclick={() => cancelConfirm(row.id)}
											>cancel</button>
										{:else}
											<button
												class="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-text-dim hover:bg-surface2 disabled:cursor-not-allowed disabled:opacity-40"
												disabled={!!controls[row.id].inflight}
												onclick={() => triggerControl(row.id, 'start')}
												data-testid="ctrl-{row.id}-start"
											>start</button>
											<button
												class="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-text-dim hover:bg-surface2 disabled:cursor-not-allowed disabled:opacity-40"
												disabled={!!controls[row.id].inflight}
												onclick={() => triggerControl(row.id, 'stop')}
												data-testid="ctrl-{row.id}-stop"
											>stop</button>
											<button
												class="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-accent hover:bg-surface2 disabled:cursor-not-allowed disabled:opacity-40"
												disabled={!!controls[row.id].inflight}
												onclick={() => triggerControl(row.id, 'restart')}
												data-testid="ctrl-{row.id}-restart"
											>restart</button>
										{/if}
									</span>
								</div>
								{#if controls[row.id].error}
									<div class="mt-1 flex items-center gap-2 pl-5">
										<span class="text-[11px] text-negative">{controls[row.id].error}</span>
										<button
											class="font-mono text-[11px] text-text-faint hover:text-text"
											onclick={() => dismissError(row.id)}
											aria-label="Dismiss error"
										>&times;</button>
									</div>
								{/if}
							</li>
						{/each}
					</ul>
				</div>

				<!-- Q2: Activity sentence -->
				<div
					class="rounded border border-border bg-surface p-3 sm:p-4"
					data-testid="q2-activity-section"
				>
					<h2 class="mb-1.5 font-mono text-xs uppercase tracking-widest text-text-faint">
						What is Miru doing right now?
					</h2>
					<p class="text-sm text-text" data-testid="status-sentence">{statusSentence}</p>
				</div>

				<!-- Q3: Issues -->
				<div
					class="rounded border border-border bg-surface p-3 sm:p-4"
					data-testid="q3-issues-section"
				>
					<h2 class="mb-1.5 font-mono text-xs uppercase tracking-widest text-text-faint">
						Is anything wrong?
					</h2>
					<p
						class="text-sm {hasIssue() ? 'text-warning' : 'text-positive'}"
						data-testid="top-issue"
					>
						{topIssue()}
					</p>
				</div>
			</div>
		</section>

		<!-- ───── Recent activity ───── -->
		<section aria-label="Activity feed" data-testid="activity-feed-section">
			<h2 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">
				Recent activity
			</h2>
			{#if data.activity && data.activity.length > 0}
				<ul
					class="divide-y divide-border overflow-hidden rounded border border-border bg-surface text-sm"
				>
					{#each data.activity as item (item.timestamp + item.card_code + item.kind)}
						<li class="flex items-center gap-2 px-3 py-1.5">
							<span class="shrink-0 font-mono text-[11px] text-text-faint">
								{item.timestamp.slice(11, 16) || item.timestamp.slice(0, 16)}
							</span>
							<span
								class="min-w-0 flex-1 truncate text-xs sm:text-sm {item.tone === 'good'
									? 'text-positive'
									: item.tone === 'bad'
										? 'text-negative'
										: 'text-text-dim'}"
							>
								<span class="font-mono font-medium">{item.card_code}</span>
								{item.detail}
							</span>
						</li>
					{/each}
				</ul>
			{:else}
				<div
					class="rounded border border-dashed border-border px-3 py-2.5 text-sm text-text-faint"
				>
					No recent activity.
				</div>
			{/if}
		</section>

		<!-- ───── Resource metrics ───── -->
		<section aria-label="Resource metrics" data-testid="resource-metrics-section">
			<div class="mb-2 flex items-baseline justify-between">
				<h2 class="font-mono text-xs uppercase tracking-widest text-text-faint">
					Resource metrics
				</h2>
				{#if data.metricsUpdatedAt}
					<span class="font-mono text-[10px] text-text-faint">{data.metricsUpdatedAt}</span>
				{/if}
			</div>
			{#if data.resourceMetrics && data.resourceMetrics.length > 0}
				<ul
					class="divide-y divide-border overflow-hidden rounded border border-border bg-surface"
				>
					{#each data.resourceMetrics as metric (metric.key)}
						<li class="flex items-center gap-3 px-3 py-1.5 text-sm">
							<span class="w-16 shrink-0 font-mono text-[11px] text-text-dim sm:w-20"
								>{metric.label}</span
							>
							{#if metric.available}
								<div
									class="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface2 sm:max-w-[200px]"
								>
									<div
										class="h-full rounded-full bg-text-dim"
										style="width: {metric.percent}%"
									></div>
								</div>
								<span class="shrink-0 font-mono text-[11px] text-text-dim">{metric.value}</span>
							{:else}
								<span class="font-mono text-[11px] text-text-faint">Unavailable</span>
							{/if}
						</li>
					{/each}
				</ul>
			{:else}
				<div
					class="rounded border border-dashed border-border px-3 py-2.5 text-sm text-text-faint"
				>
					No metrics available.
				</div>
			{/if}
			{#if pendingTotal > 0 && needsYouTotal === 0}
				<p class="mt-2 text-[11px] text-text-faint" data-testid="extra-pending-hint">
					<span class="text-warning">{pendingTotal}</span> approvals tracked separately —
					<a href="/review" class="text-accent underline underline-offset-2">open Review</a>.
				</p>
			{/if}
		</section>
	{/if}
</main>
