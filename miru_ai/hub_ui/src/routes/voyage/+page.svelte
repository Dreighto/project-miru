<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Voyage = NonNullable<PageData['voyage']>;
	type IslandState = Voyage['islands'][number]['state'];

	const CAPTIONS: Record<string, string> = {
		east_blue: 'The beginning of all things.',
		reverse_mountain: 'Enter the Grand Line.',
		whisky_peak: "The bounty hunters' paradise.",
		alabasta: 'Sand, kings, and revolution.',
		skypiea: 'Above the clouds, a forgotten sky.',
		water_7: 'Shipwrights and goodbyes.',
		thriller_bark: 'The island of shadows.',
		sabaody: "The archipelago at the world's edge.",
		fishman_island: 'Below the sea, beneath it all.',
		punk_hazard: 'Fire and ice in equal measure.',
		dressrosa: 'The kingdom under strings.',
		whole_cake: 'Tea parties with emperors.',
		wano: 'The closed country opens.',
		egghead: 'The future island.',
		elbaf: 'The land of giants.',
	};

	let selectedKey = $state<string | null>(null);

	const islands = $derived(data.voyage?.islands ?? []);
	const progress = $derived(data.voyage?.progress ?? null);
	const voyageLog = $derived(data.voyage?.voyage_log ?? []);
	const allSets = $derived(data.voyage?.sets ?? []);
	const selectedIsland = $derived(islands.find((i) => i.key === selectedKey) ?? null);
	const selectedSets = $derived(
		selectedIsland ? allSets.filter((s) => s.state === selectedIsland.state) : []
	);

	function toggle(key: string) {
		selectedKey = selectedKey === key ? null : key;
	}

	function dotClass(state: IslandState): string {
		if (state === 'charted') return 'h-3 w-3 rounded-full bg-positive';
		if (state === 'current') return 'h-4 w-4 rounded-full bg-accent';
		return 'h-3 w-3 rounded-full border border-border bg-surface2';
	}

	function nameClass(state: IslandState): string {
		if (state === 'charted') return 'text-positive';
		if (state === 'current') return 'text-accent';
		return 'text-text-faint';
	}

	function badgeClass(state: IslandState): string {
		if (state === 'charted') return 'bg-surface2 text-positive';
		if (state === 'current') return 'bg-surface2 text-accent';
		return 'bg-surface2 text-text-faint';
	}
</script>

<main class="mx-auto max-w-4xl space-y-6 p-6">
	<h1 class="font-sans text-xl font-semibold text-text">Voyage</h1>

	{#if data.flaskDown}
		<div
			role="alert"
			data-testid="flask-down-banner"
			class="rounded border border-negative bg-surface px-4 py-3 text-negative"
		>
			Flask service unreachable. Start <code class="font-mono text-sm">miru_ai.server</code> on port
			18765 and reload.
		</div>
	{:else if data.voyage}
		<!-- Route map -->
		<section aria-label="Island route map" data-testid="route-map">
			<h2 class="mb-3 font-mono text-xs uppercase tracking-widest text-text-faint">Route</h2>

			<div class="flex items-start gap-0 overflow-x-auto pb-3" role="list">
				{#each islands as island, i (island.key)}
					<!-- Connector (except before first island) -->
					{#if i > 0}
						<div
							class="mt-3 h-px w-4 shrink-0 {island.state !== 'fog' ? 'bg-border' : 'bg-surface2'}"
							aria-hidden="true"
						></div>
					{/if}

					<!-- Island node -->
					<div role="listitem">
						<button
							class="flex flex-col items-center gap-1.5 rounded px-2 py-1.5 transition-colors hover:bg-surface2 {selectedKey === island.key ? 'bg-surface2' : ''} {island.state === 'fog' ? 'opacity-50 hover:opacity-75' : ''}"
							onclick={() => toggle(island.key)}
							aria-pressed={selectedKey === island.key}
							data-testid="island-node-{island.key}"
							aria-label="{island.name} ({island.state})"
						>
							<!-- Log Pose label for current island -->
							{#if island.state === 'current'}
								<span class="font-mono text-[9px] leading-none text-accent">Log Pose</span>
							{:else}
								<span class="h-[13px]" aria-hidden="true"></span>
							{/if}

							<!-- State dot -->
							<span class={dotClass(island.state)} aria-hidden="true"></span>

							<!-- Island name -->
							<span
								class="max-w-[72px] text-center font-mono text-[10px] leading-tight {nameClass(island.state)}"
								>{island.name}</span
							>
						</button>
					</div>
				{/each}

				<!-- Open-ended horizon hint -->
				<div class="mt-3 flex shrink-0 items-center gap-0.5 pl-1" aria-hidden="true">
					<div class="h-px w-3 bg-surface2"></div>
					<div class="h-px w-2 bg-surface2 opacity-60"></div>
					<div class="h-px w-1 bg-surface2 opacity-30"></div>
				</div>
			</div>
		</section>

		<!-- Voyage Log panel (shown when an island is selected) -->
		{#if selectedIsland}
			<section
				class="space-y-4 rounded border border-border bg-surface p-4"
				aria-label="Voyage Log — {selectedIsland.name}"
				data-testid="voyage-log-panel"
			>
				<!-- Island header -->
				<div>
					<div class="mb-1 flex items-center gap-2">
						<h2 class="font-sans text-base font-semibold {nameClass(selectedIsland.state)}">
							{selectedIsland.name}
						</h2>
						<span
							class="rounded px-1.5 py-0.5 font-mono text-[10px] {badgeClass(selectedIsland.state)}"
						>
							{selectedIsland.state}
						</span>
					</div>
					{#if CAPTIONS[selectedIsland.key]}
						<p class="font-mono text-xs italic text-text-faint">{CAPTIONS[selectedIsland.key]}</p>
					{/if}
				</div>

				<!-- Set progress -->
				{#if selectedSets.length > 0}
					<div data-testid="set-progress">
						<h3 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">Sets</h3>
						<ul class="space-y-1">
							{#each selectedSets as set (set.set_code)}
								<li class="flex items-center gap-3 text-sm">
									<span class="w-10 shrink-0 font-mono text-xs text-text-faint">{set.set_code}</span>
									<span class="flex-1 text-text-dim">{set.set_name}</span>
									{#if set.total_count > 0}
										<span class="shrink-0 font-mono text-xs text-text-faint">
											{set.verified_count}/{set.total_count}
										</span>
									{/if}
								</li>
							{/each}
						</ul>
					</div>
				{:else if selectedIsland.state === 'fog'}
					<p class="text-sm text-text-faint">
						Sets ahead are uncharted — the Log Pose hasn't locked on yet.
					</p>
				{/if}

				<!-- Voyage Log entries -->
				{#if voyageLog.length > 0}
					<div data-testid="voyage-log-entries">
						<h3 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">
							Voyage Log
						</h3>
						<ul class="space-y-1.5">
							{#each voyageLog as entry (entry.issue_type + entry.kind)}
								<li class="flex items-start gap-2 text-sm">
									<span
										class="mt-1 h-1.5 w-1.5 shrink-0 rounded-full {entry.kind === 'alert'
											? 'bg-warning'
											: 'bg-text-faint'}"
										aria-hidden="true"
									></span>
									<span class={entry.kind === 'alert' ? 'text-warning' : 'text-text-dim'}
										>{entry.message}</span
									>
								</li>
							{/each}
						</ul>
					</div>
				{:else}
					<p class="text-sm text-text-faint" data-testid="voyage-log-empty">
						No patterns recorded yet — the log is clear.
					</p>
				{/if}
			</section>
		{/if}

		<!-- Progress summary -->
		{#if progress}
			<section aria-label="Voyage progress" data-testid="progress-summary">
				<h2 class="mb-3 font-mono text-xs uppercase tracking-widest text-text-faint">Progress</h2>
				<div class="flex flex-wrap gap-4 text-sm">
					<div class="flex items-center gap-2">
						<span class="h-2 w-2 rounded-full bg-positive" aria-hidden="true"></span>
						<span class="text-text-dim">
							<span class="font-semibold text-text">{progress.sets_charted}</span> sets charted
						</span>
					</div>
					{#if progress.sets_current > 0}
						<div class="flex items-center gap-2">
							<span class="h-2 w-2 rounded-full bg-accent" aria-hidden="true"></span>
							<span class="text-text-dim">
								<span class="font-semibold text-text">{progress.sets_current}</span> in progress
							</span>
						</div>
					{/if}
					<div class="flex items-center gap-2">
						<span
							class="h-2 w-2 rounded-full border border-border bg-surface2"
							aria-hidden="true"
						></span>
						<span class="text-text-dim">
							<span class="font-semibold text-text">{progress.sets_fog}</span> ahead
						</span>
					</div>
				</div>
			</section>
		{/if}
	{/if}
</main>
