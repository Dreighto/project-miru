<script lang="ts">
	import { onMount } from 'svelte';
	import { slide } from 'svelte/transition';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Voyage = NonNullable<PageData['voyage']>;
	type Island = Voyage['islands'][number];
	type IslandState = Island['state'];

	// Canon flavour — one line per island, the voyage's story.
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
		elbaf: 'The land of giants.'
	};

	const islands = $derived(data.voyage?.islands ?? []);
	const progress = $derived(data.voyage?.progress ?? null);
	const voyageLog = $derived(data.voyage?.voyage_log ?? []);
	const allSets = $derived(data.voyage?.sets ?? []);

	let selectedKey = $state<string | null>(null);
	const selectedIsland = $derived(islands.find((i) => i.key === selectedKey) ?? null);
	const selectedSets = $derived(
		selectedIsland ? allSets.filter((s) => s.state === selectedIsland.state) : []
	);
	const SET_CAP = 12;
	const cappedSets = $derived(selectedSets.slice(0, SET_CAP));

	const currentIsland = $derived(islands.find((i) => i.state === 'current') ?? null);

	// ── Map geometry ──────────────────────────────────────────────────────────
	// The route is generated, not hard-coded: island nodes are placed along a
	// serpentine sine wave, the path is a Catmull-Rom spline through them.
	const VB_W = 360;
	const GAP = 76; // vertical spacing between islands
	const TOP_PAD = 92;
	const BOT_PAD = 78;
	const AMP = 104; // serpentine amplitude
	const CX = VB_W / 2;

	const vbH = $derived(TOP_PAD + Math.max(0, islands.length - 1) * GAP + BOT_PAD);

	type Pt = { x: number; y: number; island: Island; idx: number };

	// index 0 = first island = bottom of the chart; the voyage climbs upward.
	const points = $derived.by<Pt[]>(() =>
		islands.map((island, i) => ({
			x: CX + AMP * Math.sin(i * 0.8 + 0.6),
			y: vbH - BOT_PAD - i * GAP,
			island,
			idx: i
		}))
	);

	const currentIdx = $derived.by(() => {
		const i = islands.findIndex((is) => is.state === 'current');
		if (i >= 0) return i;
		let last = 0;
		islands.forEach((is, idx) => {
			if (is.state === 'charted') last = idx;
		});
		return last;
	});

	// Catmull-Rom spline → cubic Bézier path, so the route curves smoothly
	// through every island node.
	function pathThrough(pts: Pt[]): string {
		if (pts.length === 0) return '';
		const f = (n: number) => n.toFixed(1);
		if (pts.length === 1) return `M ${f(pts[0].x)} ${f(pts[0].y)}`;
		let d = `M ${f(pts[0].x)} ${f(pts[0].y)}`;
		for (let i = 0; i < pts.length - 1; i++) {
			const p0 = pts[i - 1] ?? pts[i];
			const p1 = pts[i];
			const p2 = pts[i + 1];
			const p3 = pts[i + 2] ?? p2;
			const c1x = p1.x + (p2.x - p0.x) / 6;
			const c1y = p1.y + (p2.y - p0.y) / 6;
			const c2x = p2.x - (p3.x - p1.x) / 6;
			const c2y = p2.y - (p3.y - p1.y) / 6;
			d += ` C ${f(c1x)} ${f(c1y)}, ${f(c2x)} ${f(c2y)}, ${f(p2.x)} ${f(p2.y)}`;
		}
		return d;
	}

	const chartedPath = $derived(pathThrough(points.slice(0, currentIdx + 1)));
	const aheadPath = $derived(pathThrough(points.slice(currentIdx)));

	// The Red Line is crossed at Fish-Man Island — the Paradise / New World divide.
	const redlineY = $derived.by(() => {
		const fm = points.find((p) => p.island.key === 'fishman_island');
		return fm ? fm.y : vbH * 0.5;
	});

	const pctX = (x: number) => (x / VB_W) * 100;
	const pctY = (y: number) => (y / vbH) * 100;

	function toggle(key: string) {
		selectedKey = selectedKey === key ? null : key;
	}

	function stateLabel(s: IslandState): string {
		if (s === 'charted') return 'Charted';
		if (s === 'current') return 'Log Pose locked';
		return 'Uncharted';
	}

	onMount(() => {
		// Open the chart on the operator's current position.
		const node = document.getElementById('voyage-current-node');
		try {
			node?.scrollIntoView({ block: 'center', behavior: 'auto' });
		} catch {
			// scrollIntoView is unavailable in some environments (e.g. jsdom).
		}
	});
</script>

<main class="voyage mx-auto max-w-5xl p-4 sm:p-6">
	<div class="vcol">
	<header class="vhead">
		<h1>Voyage</h1>
		{#if data.voyage && currentIsland}
			<p class="vsub">
				The Grand Line · Log Pose locked on <span>{currentIsland.name}</span>
			</p>
		{:else}
			<p class="vsub">The Grand Line</p>
		{/if}
	</header>

	{#if data.flaskDown}
		<div role="alert" data-testid="flask-down-banner" class="flask-down">
			Flask service unreachable. Start <code>miru_ai.server</code> on port 18765 and reload.
		</div>
	{:else if data.voyage}
		<!-- ── The chart ──────────────────────────────────────────────────── -->
		<div class="chart-frame">
			<div
				class="map"
				data-testid="route-map"
				role="list"
				aria-label="Island route map"
				style="aspect-ratio: {VB_W} / {vbH};"
			>
				<div class="grain" aria-hidden="true"></div>
				<div class="seacurrent c1" aria-hidden="true"></div>
				<div class="seacurrent c2" aria-hidden="true"></div>
				<div class="seacurrent c3" aria-hidden="true"></div>
				<div class="fogbank" aria-hidden="true"><span>NEW WORLD</span></div>

				<svg
					class="route"
					viewBox="0 0 {VB_W} {vbH}"
					preserveAspectRatio="none"
					aria-hidden="true"
				>
					<defs>
						<linearGradient id="charted-grad" x1="0" y1="1" x2="0" y2="0">
							<stop offset="0" stop-color="var(--color-positive)" stop-opacity="0.85" />
							<stop offset="1" stop-color="var(--color-accent)" stop-opacity="0.9" />
						</linearGradient>
					</defs>
					<!-- Red Line — the Paradise / New World divide -->
					<line
						class="redline"
						x1="0"
						y1={redlineY}
						x2={VB_W}
						y2={redlineY}
					/>
					<!-- route ahead (faint, dashed, into the fog) -->
					<path class="seg-ahead" d={aheadPath} />
					<!-- charted route (the wake behind you) -->
					<path class="seg-charted" d={chartedPath} stroke="url(#charted-grad)" />
				</svg>

				<div class="redline-tag" style="top: {pctY(redlineY)}%;">RED LINE</div>

				<svg class="rose" viewBox="0 0 100 100" aria-hidden="true">
					<circle cx="50" cy="50" r="34" />
					<circle cx="50" cy="50" r="21" />
					<path d="M50 8 L56 50 L50 92 L44 50 Z" class="rose-ns" />
					<path d="M8 50 L50 56 L92 50 L50 44 Z" class="rose-ew" />
				</svg>

				<!-- ── Island nodes ──────────────────────────────────────────── -->
				{#each points as p (p.island.key)}
					{@const sel = selectedKey === p.island.key}
					<button
						type="button"
						class="island {p.island.state}"
						class:selected={sel}
						style="left: {pctX(p.x)}%; top: {pctY(p.y)}%;"
						id={p.idx === currentIdx ? 'voyage-current-node' : undefined}
						data-testid="island-node-{p.island.key}"
						aria-pressed={sel}
						aria-label="{p.island.name} — {stateLabel(p.island.state)}"
						onclick={() => toggle(p.island.key)}
					>
						<span class="dot" aria-hidden="true">
							{#if p.island.state === 'charted'}
								<svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7" /></svg>
							{:else if p.island.state === 'current'}
								<svg viewBox="0 0 24 24" class="ic-current">
									<circle cx="12" cy="12" r="3.4" />
									<path d="M12 2.5v3.5M12 18v3.5M2.5 12h3.5M18 12h3.5" />
								</svg>
							{/if}
						</span>
						{#if p.island.state === 'current'}
							<span class="eyebrow">Log Pose</span>
						{/if}
						<span class="iname">{p.island.name}</span>
					</button>
				{/each}
			</div>
		</div>

		<!-- ── Voyage Log panel (slides in on island tap) ──────────────────── -->
		{#if selectedIsland}
			<section
				class="logpanel"
				data-testid="voyage-log-panel"
				aria-label="Voyage Log — {selectedIsland.name}"
				transition:slide={{ duration: 220 }}
			>
				<div class="crest">
					<span class="medal {selectedIsland.state}" aria-hidden="true">
						{#if selectedIsland.state === 'charted'}
							<svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7" /></svg>
						{:else if selectedIsland.state === 'current'}
							<svg viewBox="0 0 24 24">
								<circle cx="12" cy="12" r="3.4" />
								<path d="M12 2.5v3.5M12 18v3.5M2.5 12h3.5M18 12h3.5" />
							</svg>
						{/if}
					</span>
					<div class="crest-text">
						<h2>{selectedIsland.name}</h2>
						<span class="pill {selectedIsland.state}">{stateLabel(selectedIsland.state)}</span>
					</div>
					<button
						type="button"
						class="crest-close"
						aria-label="Close Voyage Log"
						onclick={() => (selectedKey = null)}>&times;</button
					>
				</div>

				{#if CAPTIONS[selectedIsland.key]}
					<div class="canon">
						<p>{CAPTIONS[selectedIsland.key]}</p>
					</div>
				{/if}

				<div class="logsec" data-testid="set-progress">
					<h3>Sets</h3>
					{#if selectedIsland.state === 'fog'}
						<p class="empty">Sets ahead are uncharted — the Log Pose hasn't locked on yet.</p>
					{:else if selectedSets.length > 0}
						<ul class="setlist">
							{#each cappedSets as set, i (set.set_code + '-' + i)}
								{@const ratio =
									set.total_count > 0 ? set.verified_count / set.total_count : 0}
								<li class="setrow">
									<span class="setcode">{set.set_code}</span>
									<span class="setname">{set.set_name}</span>
									{#if set.total_count > 0}
										<span class="setbar" aria-hidden="true">
											<i style="width: {Math.round(ratio * 100)}%;"></i>
										</span>
										<span class="setcount">{set.verified_count}/{set.total_count}</span>
									{/if}
								</li>
							{/each}
						</ul>
						{#if selectedSets.length > SET_CAP}
							<p class="more">+{selectedSets.length - SET_CAP} more sets in this state</p>
						{/if}
					{:else}
						<p class="empty">No sets recorded for this island.</p>
					{/if}
				</div>

				<div class="logsec">
					<h3>Voyage Log</h3>
					{#if voyageLog.length > 0}
						<ul class="entries" data-testid="voyage-log-entries">
							{#each voyageLog as entry, i (entry.issue_type + '-' + entry.kind + '-' + i)}
								<li class="entry">
									<span
										class="etrack"
										class:alert={entry.kind === 'alert'}
										aria-hidden="true"
									></span>
									<span class="etext">
										<span class="emsg" class:alert={entry.kind === 'alert'}
											>{entry.message}</span
										>
										{#if entry.count > 1}
											<span class="ecount">&times;{entry.count}</span>
										{/if}
									</span>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="empty" data-testid="voyage-log-empty">
							No patterns recorded yet — the log is clear.
						</p>
					{/if}
				</div>
			</section>
		{/if}

		<!-- ── Progress summary ────────────────────────────────────────────── -->
		{#if progress}
			<section class="progress" data-testid="progress-summary" aria-label="Voyage progress">
				<h2>Progress</h2>
				<div class="pstats">
					<div class="pstat">
						<span class="pdot charted" aria-hidden="true"></span>
						<span class="pnum">{progress.sets_charted}</span>
						<span class="plabel">sets charted</span>
					</div>
					{#if progress.sets_current > 0}
						<div class="pstat">
							<span class="pdot current" aria-hidden="true"></span>
							<span class="pnum">{progress.sets_current}</span>
							<span class="plabel">in progress</span>
						</div>
					{/if}
					<div class="pstat">
						<span class="pdot fog" aria-hidden="true"></span>
						<span class="pnum">{progress.sets_fog}</span>
						<span class="plabel">ahead</span>
					</div>
					<div class="pstat">
						<span class="pdot charted" aria-hidden="true"></span>
						<span class="pnum">{progress.islands_charted}</span>
						<span class="plabel">islands charted</span>
					</div>
				</div>
			</section>
		{/if}
	{/if}
	</div>
</main>

<style>
	.voyage {
		--sea-deep: #0c141f;
		--sea-mid: #101d2e;
		--sea-glow: rgba(200, 152, 96, 0.07);
		--redline: #6e2530;
	}
	.vcol {
		max-width: 480px;
		margin: 0 auto;
	}

	.vhead {
		margin-bottom: 1rem;
	}
	.vhead h1 {
		font-size: 1.25rem;
		font-weight: 600;
		color: var(--color-text);
	}
	.vsub {
		margin-top: 0.15rem;
		font-size: 0.8rem;
		color: var(--color-text-dim);
	}
	.vsub span {
		color: var(--color-accent);
		font-weight: 500;
	}

	.flask-down {
		border: 1px solid var(--color-negative);
		background: var(--color-surface);
		color: var(--color-negative);
		border-radius: 0.375rem;
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
	}
	.flask-down code {
		font-family: var(--font-mono, monospace);
		font-size: 0.8rem;
	}

	/* ── The chart ─────────────────────────────────────────────────────── */
	.chart-frame {
		display: flex;
		justify-content: center;
	}
	.map {
		position: relative;
		width: 100%;
		max-width: 460px;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: 0.75rem;
		background:
			radial-gradient(ellipse at 78% 14%, var(--sea-glow), transparent 55%),
			radial-gradient(ellipse at 20% 88%, rgba(143, 190, 122, 0.05), transparent 55%),
			linear-gradient(180deg, var(--sea-mid), var(--sea-deep) 62%, #090c10);
	}
	.grain {
		position: absolute;
		inset: 0;
		background-image:
			linear-gradient(rgba(200, 152, 96, 0.035) 1px, transparent 1px),
			linear-gradient(90deg, rgba(200, 152, 96, 0.035) 1px, transparent 1px);
		background-size: 34px 34px;
		pointer-events: none;
	}

	/* drifting sea-current lines */
	.seacurrent {
		position: absolute;
		left: -20%;
		width: 140%;
		height: 1px;
		background: linear-gradient(
			90deg,
			transparent,
			rgba(120, 160, 200, 0.18),
			transparent
		);
		animation: drift 11s linear infinite alternate;
		pointer-events: none;
	}
	.seacurrent.c1 {
		top: 22%;
		animation-duration: 12s;
	}
	.seacurrent.c2 {
		top: 51%;
		animation-duration: 15s;
		animation-delay: -4s;
	}
	.seacurrent.c3 {
		top: 77%;
		animation-duration: 13s;
		animation-delay: -7s;
	}
	@keyframes drift {
		from {
			transform: translateX(-26px);
		}
		to {
			transform: translateX(26px);
		}
	}

	/* fog bank over the New World */
	.fogbank {
		position: absolute;
		top: 0;
		left: 0;
		right: 0;
		height: 30%;
		background: linear-gradient(180deg, rgba(14, 19, 28, 0.96), transparent);
		pointer-events: none;
	}
	.fogbank span {
		position: absolute;
		top: 14px;
		right: 16px;
		font-family: var(--font-mono, monospace);
		font-size: 0.5rem;
		letter-spacing: 0.22em;
		color: rgba(150, 165, 190, 0.42);
	}

	.route {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
	}
	.redline {
		stroke: var(--redline);
		stroke-width: 3;
		opacity: 0.7;
	}
	.seg-charted {
		fill: none;
		stroke-width: 2.6;
		stroke-linecap: round;
		stroke-dasharray: 0.1 7;
	}
	.seg-ahead {
		fill: none;
		stroke: var(--color-text-faint);
		stroke-width: 1.6;
		stroke-linecap: round;
		stroke-dasharray: 2 8;
		opacity: 0.55;
	}

	.redline-tag {
		position: absolute;
		left: 12px;
		transform: translateY(-130%);
		font-family: var(--font-mono, monospace);
		font-size: 0.46rem;
		letter-spacing: 0.18em;
		color: rgba(200, 130, 138, 0.78);
		pointer-events: none;
	}

	.rose {
		position: absolute;
		bottom: 5%;
		left: 14px;
		width: 58px;
		height: 58px;
		opacity: 0.22;
		pointer-events: none;
		fill: none;
		stroke: var(--color-accent);
		stroke-width: 1.4;
	}
	.rose-ns {
		fill: var(--color-accent);
		stroke: none;
		opacity: 0.7;
	}
	.rose-ew {
		fill: var(--color-accent);
		stroke: none;
		opacity: 0.35;
	}

	/* ── Island nodes ──────────────────────────────────────────────────── */
	.island {
		position: absolute;
		transform: translate(-50%, -50%);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.2rem;
		padding: 0.55rem;
		background: none;
		border: none;
		cursor: pointer;
		min-width: 44px;
	}
	.dot {
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
		position: relative;
		transition: transform 0.14s ease;
	}
	.island:active .dot {
		transform: scale(0.9);
	}
	.dot svg {
		width: 13px;
		height: 13px;
		fill: none;
		stroke: var(--color-bg);
		stroke-width: 3;
		stroke-linecap: round;
		stroke-linejoin: round;
	}

	/* charted */
	.island.charted .dot {
		width: 27px;
		height: 27px;
		background: radial-gradient(circle at 38% 32%, #9fc98a, #5f8a4e);
		border: 2px solid var(--color-positive);
		box-shadow: 0 0 13px rgba(143, 190, 122, 0.35);
	}

	/* current — the living beacon */
	.island.current .dot {
		width: 40px;
		height: 40px;
		background: radial-gradient(circle at 38% 32%, #e8c79a, #b3823f);
		border: 2.5px solid var(--color-accent);
		box-shadow: 0 0 22px rgba(200, 152, 96, 0.55);
	}
	.island.current .dot svg {
		width: 17px;
		height: 17px;
		stroke-width: 2.4;
	}
	.island.current .dot::after {
		content: '';
		position: absolute;
		inset: -7px;
		border-radius: 50%;
		border: 1.5px solid rgba(200, 152, 96, 0.5);
		animation: ripple 2.8s ease-out infinite;
	}
	@keyframes ripple {
		0% {
			transform: scale(0.82);
			opacity: 0.85;
		}
		100% {
			transform: scale(1.5);
			opacity: 0;
		}
	}

	/* fog */
	.island.fog .dot {
		width: 21px;
		height: 21px;
		background: rgba(40, 48, 56, 0.55);
		border: 1.5px dashed var(--color-text-faint);
	}

	.eyebrow {
		font-family: var(--font-mono, monospace);
		font-size: 0.46rem;
		letter-spacing: 0.1em;
		color: var(--color-accent);
		text-transform: uppercase;
		line-height: 1;
	}
	.iname {
		font-size: 0.62rem;
		font-weight: 500;
		color: var(--color-text-dim);
		white-space: nowrap;
		line-height: 1.1;
	}
	.island.charted .iname {
		color: var(--color-positive);
	}
	.island.current .iname {
		color: var(--color-text);
		font-weight: 600;
	}
	.island.fog .iname {
		color: var(--color-text-faint);
	}
	.island.selected .dot {
		outline: 2px solid var(--color-accent);
		outline-offset: 3px;
	}
	.island:focus-visible {
		outline: 2px solid var(--color-accent);
		outline-offset: 2px;
		border-radius: 0.5rem;
	}

	/* ── Voyage Log panel ──────────────────────────────────────────────── */
	.logpanel {
		margin-top: 1rem;
		border: 1px solid var(--color-border);
		border-top: 2px solid color-mix(in srgb, var(--color-accent) 40%, transparent);
		border-radius: 0.75rem;
		background: var(--color-surface);
		padding: 1rem;
	}
	.crest {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding-bottom: 0.85rem;
		border-bottom: 1px solid var(--color-border);
	}
	.medal {
		width: 42px;
		height: 42px;
		border-radius: 50%;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.medal svg {
		width: 18px;
		height: 18px;
		fill: none;
		stroke: var(--color-bg);
		stroke-width: 2.6;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.medal.charted {
		background: radial-gradient(circle at 38% 32%, #9fc98a, #5f8a4e);
		border: 2px solid var(--color-positive);
	}
	.medal.current {
		background: radial-gradient(circle at 38% 32%, #e8c79a, #b3823f);
		border: 2px solid var(--color-accent);
		box-shadow: 0 0 16px rgba(200, 152, 96, 0.35);
	}
	.medal.fog {
		background: rgba(40, 48, 56, 0.55);
		border: 1.5px dashed var(--color-text-faint);
	}
	.crest-text {
		flex: 1;
		min-width: 0;
	}
	.crest-text h2 {
		font-size: 1.05rem;
		font-weight: 600;
		color: var(--color-text);
		line-height: 1.2;
	}
	.pill {
		display: inline-block;
		margin-top: 0.25rem;
		font-family: var(--font-mono, monospace);
		font-size: 0.6rem;
		letter-spacing: 0.04em;
		padding: 0.1rem 0.4rem;
		border-radius: 0.25rem;
		border: 1px solid var(--color-border);
		color: var(--color-text-dim);
	}
	.pill.charted {
		color: var(--color-positive);
		border-color: color-mix(in srgb, var(--color-positive) 40%, transparent);
	}
	.pill.current {
		color: var(--color-accent);
		border-color: color-mix(in srgb, var(--color-accent) 40%, transparent);
	}
	.crest-close {
		flex-shrink: 0;
		align-self: flex-start;
		background: none;
		border: none;
		color: var(--color-text-faint);
		font-size: 1.25rem;
		line-height: 1;
		cursor: pointer;
		padding: 0.15rem 0.35rem;
	}
	.crest-close:hover {
		color: var(--color-text);
	}

	.canon {
		margin-top: 0.85rem;
		padding: 0.7rem 0.85rem;
		border-radius: 0.5rem;
		border: 1px solid color-mix(in srgb, var(--color-accent) 18%, transparent);
		background: color-mix(in srgb, var(--color-accent) 6%, transparent);
	}
	.canon p {
		font-size: 0.85rem;
		font-style: italic;
		color: var(--color-text);
		line-height: 1.5;
	}

	.logsec {
		margin-top: 1rem;
	}
	.logsec h3 {
		font-family: var(--font-mono, monospace);
		font-size: 0.65rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--color-text-faint);
		margin-bottom: 0.55rem;
	}
	.empty {
		font-size: 0.85rem;
		color: var(--color-text-faint);
		border: 1px dashed var(--color-border);
		border-radius: 0.5rem;
		padding: 0.7rem 0.85rem;
	}
	.more {
		margin-top: 0.45rem;
		font-family: var(--font-mono, monospace);
		font-size: 0.68rem;
		color: var(--color-text-faint);
	}

	.setlist {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.setrow {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		font-size: 0.8rem;
	}
	.setcode {
		font-family: var(--font-mono, monospace);
		font-size: 0.7rem;
		color: var(--color-text-faint);
		width: 3rem;
		flex-shrink: 0;
	}
	.setname {
		color: var(--color-text-dim);
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.setbar {
		width: 4.5rem;
		height: 5px;
		border-radius: 3px;
		background: var(--color-surface2);
		overflow: hidden;
		flex-shrink: 0;
	}
	.setbar i {
		display: block;
		height: 100%;
		background: var(--color-positive);
		border-radius: 3px;
	}
	.setcount {
		font-family: var(--font-mono, monospace);
		font-size: 0.68rem;
		color: var(--color-text-faint);
		width: 3.4rem;
		text-align: right;
		flex-shrink: 0;
	}

	.entries {
		display: flex;
		flex-direction: column;
	}
	.entry {
		display: flex;
		gap: 0.6rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.85rem;
	}
	.entry:last-child {
		border-bottom: none;
	}
	.etrack {
		flex-shrink: 0;
		width: 7px;
		height: 7px;
		margin-top: 0.32rem;
		border-radius: 50%;
		background: var(--color-text-faint);
	}
	.etrack.alert {
		background: var(--color-warning);
	}
	.etext {
		flex: 1;
		display: flex;
		gap: 0.5rem;
		align-items: baseline;
		justify-content: space-between;
	}
	.emsg {
		color: var(--color-text-dim);
	}
	.emsg.alert {
		color: var(--color-warning);
	}
	.ecount {
		flex-shrink: 0;
		font-family: var(--font-mono, monospace);
		font-size: 0.7rem;
		color: var(--color-text-faint);
	}

	/* ── Progress summary ──────────────────────────────────────────────── */
	.progress {
		margin-top: 1rem;
	}
	.progress h2 {
		font-family: var(--font-mono, monospace);
		font-size: 0.65rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--color-text-faint);
		margin-bottom: 0.6rem;
	}
	.pstats {
		display: flex;
		flex-wrap: wrap;
		gap: 0.65rem;
	}
	.pstat {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		padding: 0.5rem 0.7rem;
		background: var(--color-surface);
	}
	.pdot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex-shrink: 0;
	}
	.pdot.charted {
		background: var(--color-positive);
	}
	.pdot.current {
		background: var(--color-accent);
	}
	.pdot.fog {
		background: var(--color-surface2);
		border: 1px solid var(--color-border);
	}
	.pnum {
		font-weight: 600;
		color: var(--color-text);
	}
	.plabel {
		font-size: 0.8rem;
		color: var(--color-text-dim);
	}

	/* ── Motion off ────────────────────────────────────────────────────── */
	@media (prefers-reduced-motion: reduce) {
		.seacurrent,
		.island.current .dot::after {
			animation: none;
		}
	}
</style>
