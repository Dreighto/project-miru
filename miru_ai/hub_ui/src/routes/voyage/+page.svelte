<script lang="ts">
	import { fade, slide } from 'svelte/transition';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Island = NonNullable<PageData['voyage']>['islands'][number];
	type IslandState = Island['state'];

	const islands = $derived(data.voyage?.islands ?? []);
	const voyageLog = $derived(data.voyage?.voyage_log ?? []);
	const progress = $derived(data.voyage?.progress ?? null);
	const currentIsland = $derived(islands.find((i) => i.state === 'current') ?? null);
	const islandByKey = $derived(new Map(islands.map((i) => [i.key, i])));

	// ── The Atlas — canon regions, each a chart page ─────────────────────────
	const CHAPTERS = [
		{
			id: 'east-blue',
			label: 'East Blue',
			sub: 'The home sea',
			keys: ['foosha_village', 'shells_town', 'orange_town', 'syrup_village', 'baratie', 'cocoyasi_village', 'loguetown']
		},
		{
			id: 'paradise-1',
			label: 'Paradise I',
			sub: 'Into the Grand Line',
			keys: ['reverse_mountain', 'whisky_peak', 'little_garden', 'drum_island', 'alabasta', 'jaya', 'skypiea']
		},
		{
			id: 'paradise-2',
			label: 'Paradise II',
			sub: 'To the Red Line',
			keys: ['long_ring_long_land', 'water_seven', 'enies_lobby', 'thriller_bark', 'sabaody_archipelago']
		},
		{
			id: 'new-world',
			label: 'New World',
			sub: 'Toward Laugh Tale',
			keys: ['fish_man_island', 'punk_hazard', 'dressrosa', 'zou', 'whole_cake_island', 'wano_country', 'egghead', 'elbaf']
		}
	];

	// Island positions on the square chart (% coords) — hand-placed so the route
	// winds with canon twists and turns, never a uniform swirl.
	const LAYOUT: Record<string, { x: number; y: number }> = {
		foosha_village: { x: 24, y: 86 }, shells_town: { x: 55, y: 80 }, orange_town: { x: 33, y: 67 },
		syrup_village: { x: 63, y: 57 }, baratie: { x: 38, y: 45 }, cocoyasi_village: { x: 67, y: 33 },
		loguetown: { x: 46, y: 17 },
		reverse_mountain: { x: 25, y: 87 }, whisky_peak: { x: 58, y: 82 }, little_garden: { x: 35, y: 69 },
		drum_island: { x: 67, y: 60 }, alabasta: { x: 37, y: 47 }, jaya: { x: 64, y: 37 },
		skypiea: { x: 44, y: 13 },
		long_ring_long_land: { x: 27, y: 83 }, water_seven: { x: 57, y: 70 }, enies_lobby: { x: 77, y: 54 },
		thriller_bark: { x: 42, y: 45 }, sabaody_archipelago: { x: 56, y: 23 },
		fish_man_island: { x: 30, y: 86 }, punk_hazard: { x: 61, y: 79 }, dressrosa: { x: 35, y: 66 },
		zou: { x: 67, y: 57 }, whole_cake_island: { x: 32, y: 46 }, wano_country: { x: 60, y: 38 },
		egghead: { x: 40, y: 25 }, elbaf: { x: 64, y: 15 }
	};

	// Art aspect ratios (width / height) — keeps visual weight even across the set.
	const ASPECT: Record<string, number> = {
		alabasta: 1.0, baratie: 1.026, cocoyasi_village: 1.0, dressrosa: 2.216, drum_island: 1.24,
		egghead: 1.045, elbaf: 1.125, enies_lobby: 1.153, fish_man_island: 0.836, foosha_village: 2.056,
		jaya: 1.354, little_garden: 1.0, loguetown: 1.0, long_ring_long_land: 1.096, orange_town: 1.407,
		punk_hazard: 1.193, reverse_mountain: 1.0, sabaody_archipelago: 1.135, shells_town: 1.842,
		skypiea: 0.879, syrup_village: 1.323, thriller_bark: 1.0, wano_country: 1.533, water_seven: 1.32,
		whisky_peak: 1.0, whole_cake_island: 2.179, zou: 0.959
	};

	// Canon flavour — one line per milestone.
	const CAPTIONS: Record<string, string> = {
		foosha_village: 'Where every voyage begins.',
		shells_town: 'The first Marine base, left in the wake.',
		orange_town: 'A town reclaimed from a clown.',
		syrup_village: 'A quiet slope and a brave lie.',
		baratie: 'The sea-going kitchen.',
		cocoyasi_village: 'Tangerine groves, hard-won peace.',
		loguetown: 'The town of the beginning and the end.',
		reverse_mountain: 'The gate that climbs to the sky.',
		whisky_peak: 'A welcome with a hidden edge.',
		little_garden: 'An island the ages forgot.',
		drum_island: 'A winter kingdom; a doctor found.',
		alabasta: 'Sand, kings, and revolution.',
		jaya: 'Half a town, half a dream.',
		skypiea: 'Above the clouds, a forgotten sky.',
		long_ring_long_land: 'An island stretched thin by the tide.',
		water_seven: 'The city of water and shipwrights.',
		enies_lobby: 'The judicial island — a declaration of war.',
		thriller_bark: 'The island of stolen shadows.',
		sabaody_archipelago: 'The archipelago at the edge of Paradise.',
		fish_man_island: 'Ten thousand metres beneath the Red Line.',
		punk_hazard: 'Fire on one shore, ice on the other.',
		dressrosa: 'A kingdom of toys and strings.',
		zou: 'A country on the back of a wandering giant.',
		whole_cake_island: 'A tea party with an Emperor.',
		wano_country: 'The closed country, opening.',
		egghead: 'The island of the future.',
		elbaf: 'The land of giants.'
	};

	// ── Active chapter — opens on the leg the ship is sailing ─────────────────
	function chapterOf(key: string | undefined): number {
		if (!key) return 0;
		const i = CHAPTERS.findIndex((c) => c.keys.includes(key));
		return i >= 0 ? i : 0;
	}
	function startChapter(): number {
		const key = data.voyage?.islands?.find((i) => i.state === 'current')?.key;
		return chapterOf(key);
	}
	let activeChapter = $state(startChapter());
	const chapter = $derived(CHAPTERS[activeChapter]);

	type Placed = { x: number; y: number; isl: Island };
	const pageIslands = $derived(
		chapter.keys.map((k) => islandByKey.get(k)).filter((x): x is Island => !!x)
	);
	const pagePts = $derived<Placed[]>(
		pageIslands.map((isl) => ({ x: LAYOUT[isl.key].x, y: LAYOUT[isl.key].y, isl }))
	);

	// route split — wake (sailed) vs ahead (uncharted)
	const splitIdx = $derived.by(() => {
		let last = -1;
		pageIslands.forEach((isl, i) => {
			if (isl.state !== 'fog') last = i;
		});
		return last;
	});

	function pathThrough(pts: { x: number; y: number }[]): string {
		if (pts.length === 0) return '';
		const f = (n: number) => n.toFixed(2);
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
	const wakePath = $derived(pathThrough(pagePts.slice(0, splitIdx + 1)));
	const aheadPath = $derived(pathThrough(pagePts.slice(Math.max(0, splitIdx))));

	const shipOnPage = $derived(pageIslands.some((i) => i.state === 'current'));
	const shipPt = $derived(currentIsland ? LAYOUT[currentIsland.key] : null);

	// island sizing — long side held roughly constant whatever the art's aspect
	function box(key: string, state: IslandState): { w: number; h: number } {
		const a = ASPECT[key] ?? 1;
		const long = state === 'current' ? 23 : state === 'charted' ? 17.5 : 15.5;
		return a >= 1 ? { w: long, h: long / a } : { w: long * a, h: long };
	}
	const fileOf = (key: string) => key.replace(/_/g, '-');

	function stateLabel(s: IslandState): string {
		return s === 'charted' ? 'Charted' : s === 'current' ? 'Log Pose locked' : 'Uncharted';
	}
	function milestoneLine(s: IslandState): string {
		return s === 'charted'
			? 'A milestone charted — this accomplishment is behind the ship.'
			: s === 'current'
				? 'The Log Pose is locked here — the voyage stands at this milestone now.'
				: 'Uncharted — a milestone still ahead, waiting in the mist.';
	}

	// ── Selection + the Voyage Log ───────────────────────────────────────────
	let selectedKey = $state<string | null>(null);
	const selected = $derived(selectedKey ? (islandByKey.get(selectedKey) ?? null) : null);
	function tapIsland(key: string) {
		selectedKey = selectedKey === key ? null : key;
	}

	// ── Chapter navigation ───────────────────────────────────────────────────
	function go(dir: number) {
		const n = activeChapter + dir;
		if (n >= 0 && n < CHAPTERS.length) {
			activeChapter = n;
			selectedKey = null;
		}
	}
	function setChapter(i: number) {
		activeChapter = i;
		selectedKey = null;
	}

	let touchX = 0;
	function onTouchStart(e: TouchEvent) {
		touchX = e.changedTouches[0].clientX;
	}
	function onTouchEnd(e: TouchEvent) {
		const dx = e.changedTouches[0].clientX - touchX;
		if (Math.abs(dx) > 55) go(dx < 0 ? 1 : -1);
	}
</script>

<main class="voyage">
	<div class="col">
		<header class="vhead">
			<p class="eyebrow">The Grand Line</p>
			<h1>Voyage</h1>
			{#if currentIsland}
				<p class="sub">The Log Pose points to <span>{currentIsland.name}</span></p>
			{/if}
		</header>

		{#if data.flaskDown}
			<div role="alert" data-testid="flask-down-banner" class="flask-down">
				Flask service unreachable. Start <code>miru_ai.server</code> on port 18765 and reload.
			</div>
		{:else if data.voyage}
			<!-- ── Chapter tabs ─────────────────────────────────────────────── -->
			<nav class="tabs" aria-label="Voyage chapters">
				{#each CHAPTERS as c, i (c.id)}
					<button
						type="button"
						class="tab"
						class:active={i === activeChapter}
						aria-current={i === activeChapter}
						onclick={() => setChapter(i)}
					>
						{c.label}
					</button>
				{/each}
			</nav>

			<!-- ── The chart page ───────────────────────────────────────────── -->
			{#key activeChapter}
				<div class="page" in:fade={{ duration: 220 }}>
					<div
						class="chart"
						data-testid="route-map"
						role="list"
						aria-label="Voyage chart — {chapter.label}"
						ontouchstart={onTouchStart}
						ontouchend={onTouchEnd}
					>
						<!-- the Red Line — the wall at the end of Paradise -->
						{#if chapter.id === 'paradise-2'}
							<div class="redline" aria-hidden="true"><span>Red Line</span></div>
						{/if}

						<!-- the route -->
						<svg class="route" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
							<defs>
								<linearGradient id="wake" x1="0" y1="1" x2="0" y2="0">
									<stop offset="0" stop-color="var(--color-positive)" stop-opacity="0.85" />
									<stop offset="1" stop-color="var(--color-accent)" stop-opacity="0.95" />
								</linearGradient>
								<linearGradient id="ahead" x1="0" y1="1" x2="0" y2="0">
									<stop offset="0" stop-color="var(--color-sea)" stop-opacity="0.7" />
									<stop offset="1" stop-color="var(--color-sea)" stop-opacity="0.12" />
								</linearGradient>
							</defs>
							{#if aheadPath}
								<path class="r-ahead" d={aheadPath} stroke="url(#ahead)" />
							{/if}
							{#if wakePath && splitIdx > 0}
								<path class="r-wake" d={wakePath} stroke="url(#wake)" />
							{/if}
						</svg>

						<!-- islands -->
						{#each pagePts as p (p.isl.key)}
							{@const b = box(p.isl.key, p.isl.state)}
							<button
								type="button"
								class="island {p.isl.state}"
								class:sel={selectedKey === p.isl.key}
								style="left:{p.x}%;top:{p.y}%;width:{b.w}%;height:{b.h}%"
								data-testid="island-node-{p.isl.key}"
								aria-pressed={selectedKey === p.isl.key}
								aria-label="{p.isl.name} — {stateLabel(p.isl.state)}"
								onclick={() => tapIsland(p.isl.key)}
							>
								{#if p.isl.state === 'current'}
									<span class="glow" aria-hidden="true"></span>
								{/if}
								<span
									class="art"
									style="--art:url(/voyage/islands/{fileOf(p.isl.key)}.png)"
									aria-hidden="true"
								></span>
								<span class="iname">{p.isl.name}</span>
							</button>
						{/each}

						<!-- the ship — the Log Pose is here -->
						{#if shipOnPage && shipPt}
							<div class="ship" style="left:{shipPt.x}%;top:{shipPt.y}%" aria-hidden="true">
								<svg viewBox="0 0 36 40">
									<path class="hull" d="M18 5 Q30 21 24 34 L12 34 Q6 21 18 5 Z" />
									<path class="sail" d="M18 9 Q26 19 18 27 Q14 19 18 9 Z" />
									<line class="mast" x1="18" y1="7" x2="18" y2="31" />
								</svg>
							</div>
						{/if}

						<!-- Laugh Tale — the horizon that is never reached -->
						{#if chapter.id === 'new-world'}
							<div class="laugh" aria-hidden="true">
								<span class="laugh-glow"></span>
								<span class="laugh-label">Laugh Tale</span>
							</div>
						{/if}
					</div>

					<!-- chapter caption + arrows -->
					<div class="chapnav">
						<button
							type="button"
							class="arrow"
							onclick={() => go(-1)}
							disabled={activeChapter === 0}
							aria-label="Previous chapter">&lsaquo;</button
						>
						<div class="capt">
							<span class="cap-name">{chapter.label}</span>
							<span class="cap-sub">{chapter.sub}</span>
						</div>
						<button
							type="button"
							class="arrow"
							onclick={() => go(1)}
							disabled={activeChapter === CHAPTERS.length - 1}
							aria-label="Next chapter">&rsaquo;</button
						>
					</div>
				</div>
			{/key}

			<!-- ── Voyage Log — slides in on island tap ─────────────────────── -->
			{#if selected}
				<section
					class="logpanel"
					data-testid="voyage-log-panel"
					aria-label="Voyage Log — {selected.name}"
					transition:slide={{ duration: 220 }}
				>
					<div class="crest">
						<span class="medal {selected.state}" aria-hidden="true"></span>
						<div class="crest-tx">
							<p class="crest-eye">Voyage Log</p>
							<h2>{selected.name}</h2>
							<span class="pill {selected.state}">{stateLabel(selected.state)}</span>
						</div>
						<button
							type="button"
							class="close"
							aria-label="Close Voyage Log"
							onclick={() => (selectedKey = null)}>&times;</button
						>
					</div>

					{#if CAPTIONS[selected.key]}
						<p class="canon">&ldquo;{CAPTIONS[selected.key]}&rdquo;</p>
					{/if}
					<p class="mline">{milestoneLine(selected.state)}</p>

					{#if selected.state === 'current'}
						<div class="logsec">
							<h3>Ship's Log</h3>
							{#if voyageLog.length > 0}
								<ul class="entries" data-testid="voyage-log-entries">
									{#each voyageLog as e, i (e.issue_type + '-' + e.kind + '-' + i)}
										<li class="entry">
											<span class="edot" class:alert={e.kind === 'alert'} aria-hidden="true"></span>
											<span class="etext" class:alert={e.kind === 'alert'}>{e.message}</span>
											{#if e.count > 1}<span class="ecount">&times;{e.count}</span>{/if}
										</li>
									{/each}
								</ul>
							{:else}
								<p class="empty" data-testid="voyage-log-empty">
									No patterns recorded — the log is clear.
								</p>
							{/if}
						</div>
					{/if}
				</section>
			{/if}

			<!-- ── Progress ─────────────────────────────────────────────────── -->
			{#if progress}
				<section class="progress" data-testid="progress-summary" aria-label="Voyage progress">
					<div class="pstats">
						<div class="pstat">
							<span class="pnum">{progress.islands_charted}</span>
							<span class="plabel">charted</span>
						</div>
						<div class="pstat">
							<span class="pnum">{islands.length}</span>
							<span class="plabel">milestones</span>
						</div>
						<div class="pstat">
							<span class="pnum">{progress.islands_fog}</span>
							<span class="plabel">ahead</span>
						</div>
					</div>
					<p class="prog-line">
						The Log Pose is locked on <span>{currentIsland?.name ?? 'the horizon'}</span> — the
						voyage goes on.
					</p>
				</section>
			{/if}
		{/if}
	</div>
</main>

<style>
	.voyage {
		--isl-charted: var(--color-accent);
		--isl-current: #f2d9ab;
		--isl-fog: #66747f;
		padding: 1rem 0 2.5rem;
	}
	.col {
		max-width: 460px;
		margin: 0 auto;
		padding: 0 1rem;
	}

	/* ── Header ─────────────────────────────────────────────────────────── */
	.vhead {
		margin-bottom: 0.85rem;
	}
	.eyebrow {
		font-family: var(--font-mono, monospace);
		font-size: 0.62rem;
		letter-spacing: 0.24em;
		text-transform: uppercase;
		color: var(--color-sea);
	}
	.vhead h1 {
		font-family: var(--font-display, serif);
		font-size: 2.4rem;
		font-weight: 600;
		line-height: 1;
		color: var(--color-text);
		margin-top: 0.1rem;
	}
	.sub {
		margin-top: 0.35rem;
		font-size: 0.85rem;
		color: var(--color-text-dim);
	}
	.sub span {
		color: var(--color-accent);
		font-weight: 500;
	}

	.flask-down {
		border: 1px solid var(--color-negative);
		background: var(--color-surface);
		color: var(--color-negative);
		border-radius: 0.5rem;
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
	}
	.flask-down code {
		font-family: var(--font-mono, monospace);
		font-size: 0.8rem;
	}

	/* ── Chapter tabs ───────────────────────────────────────────────────── */
	.tabs {
		display: flex;
		gap: 3px;
		margin-bottom: 0.6rem;
	}
	.tab {
		flex: 1;
		padding: 0.42rem 0.15rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		color: var(--color-text-faint);
		font-family: var(--font-mono, monospace);
		font-size: 0.57rem;
		letter-spacing: 0.04em;
		cursor: pointer;
		transition: color 0.12s ease, border-color 0.12s ease;
	}
	.tab.active {
		color: var(--color-accent);
		border-color: var(--color-accent-dim);
		background: var(--color-surface2);
	}

	/* ── The chart ──────────────────────────────────────────────────────── */
	.chart {
		position: relative;
		width: 100%;
		aspect-ratio: 1 / 1;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: #0c1420 url('/voyage/chart-voyage.png') center / cover no-repeat;
		box-shadow: 0 20px 50px -26px rgba(0, 0, 0, 0.9);
		touch-action: pan-y;
	}

	.route {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		pointer-events: none;
	}
	.r-wake {
		fill: none;
		stroke-width: 2.2;
		stroke-linecap: round;
	}
	.r-ahead {
		fill: none;
		stroke-width: 2;
		stroke-linecap: round;
		stroke-dasharray: 2.8 3.8;
	}

	/* the Red Line band */
	.redline {
		position: absolute;
		top: 9%;
		left: 0;
		right: 0;
		height: 6.5%;
		background: linear-gradient(
			180deg,
			rgba(120, 44, 52, 0),
			rgba(124, 46, 54, 0.8) 45%,
			rgba(120, 44, 52, 0)
		);
		display: flex;
		align-items: center;
		pointer-events: none;
	}
	.redline span {
		margin-left: 0.7rem;
		font-family: var(--font-mono, monospace);
		font-size: 0.5rem;
		letter-spacing: 0.2em;
		text-transform: uppercase;
		color: rgba(231, 173, 178, 0.92);
	}

	/* ── Islands ────────────────────────────────────────────────────────── */
	.island {
		position: absolute;
		transform: translate(-50%, -50%);
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		overflow: visible;
	}
	.glow {
		position: absolute;
		inset: -34%;
		border-radius: 50%;
		background: radial-gradient(circle, rgba(242, 217, 171, 0.42), transparent 66%);
		pointer-events: none;
	}
	.art {
		position: absolute;
		inset: 0;
		-webkit-mask: var(--art) center / contain no-repeat;
		mask: var(--art) center / contain no-repeat;
	}
	.island.fog .art {
		background: var(--isl-fog);
	}
	.island.charted .art {
		background: var(--isl-charted);
	}
	.island.current .art {
		background: var(--isl-current);
	}
	.island.sel .art {
		filter: drop-shadow(0 0 5px rgba(242, 217, 171, 0.8));
	}
	.island:focus-visible {
		outline: 2px solid var(--color-accent);
		outline-offset: 3px;
		border-radius: 0.4rem;
	}

	.iname {
		position: absolute;
		top: 100%;
		left: 50%;
		transform: translateX(-50%);
		margin-top: 3px;
		padding: 1px 5px;
		border-radius: 3px;
		background: rgba(8, 12, 18, 0.62);
		font-family: var(--font-display, serif);
		font-size: 0.62rem;
		line-height: 1.3;
		white-space: nowrap;
		color: var(--color-text-dim);
	}
	.island.charted .iname {
		color: #dcc095;
	}
	.island.current .iname {
		color: var(--color-text);
		font-weight: 600;
	}
	.island.fog .iname {
		color: #97a2ad;
	}

	/* ── The ship ───────────────────────────────────────────────────────── */
	.ship {
		position: absolute;
		width: 11%;
		transform: translate(-50%, -132%);
		pointer-events: none;
		z-index: 3;
	}
	.ship svg {
		display: block;
		width: 100%;
		height: auto;
		overflow: visible;
		animation: bob 4.2s ease-in-out infinite;
		filter: drop-shadow(0 3px 5px rgba(0, 0, 0, 0.7));
	}
	.hull {
		fill: #d8b483;
		stroke: var(--color-accent);
		stroke-width: 1.3;
		stroke-linejoin: round;
	}
	.sail {
		fill: #f2e9d8;
	}
	.mast {
		stroke: #6b5436;
		stroke-width: 1.5;
		stroke-linecap: round;
	}
	@keyframes bob {
		0%,
		100% {
			transform: translateY(0);
		}
		50% {
			transform: translateY(-2.5px);
		}
	}

	/* ── Laugh Tale ─────────────────────────────────────────────────────── */
	.laugh {
		position: absolute;
		top: 3.5%;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 3px;
		pointer-events: none;
	}
	.laugh-glow {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		background: var(--color-accent);
		box-shadow:
			0 0 12px 3px rgba(200, 152, 96, 0.6),
			0 0 28px 9px rgba(200, 152, 96, 0.22);
	}
	.laugh-label {
		font-family: var(--font-display, serif);
		font-style: italic;
		font-size: 0.64rem;
		letter-spacing: 0.13em;
		color: rgba(200, 152, 96, 0.72);
	}

	/* ── Chapter nav ────────────────────────────────────────────────────── */
	.chapnav {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-top: 0.55rem;
	}
	.arrow {
		background: none;
		border: none;
		color: var(--color-text-dim);
		font-size: 1.5rem;
		line-height: 1;
		cursor: pointer;
		padding: 0.1rem 0.7rem;
	}
	.arrow:disabled {
		opacity: 0.22;
		cursor: default;
	}
	.capt {
		text-align: center;
	}
	.cap-name {
		display: block;
		font-family: var(--font-display, serif);
		font-size: 1.05rem;
		color: var(--color-text);
	}
	.cap-sub {
		display: block;
		font-size: 0.66rem;
		color: var(--color-text-dim);
	}

	/* ── Voyage Log panel ───────────────────────────────────────────────── */
	.logpanel {
		margin-top: 0.9rem;
		border: 1px solid var(--color-border);
		border-top: 2px solid color-mix(in srgb, var(--color-accent) 45%, transparent);
		border-radius: 0.75rem;
		background: var(--color-surface);
		padding: 1rem;
	}
	.crest {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		padding-bottom: 0.8rem;
		border-bottom: 1px solid var(--color-border);
	}
	.medal {
		width: 38px;
		height: 38px;
		border-radius: 50%;
		flex-shrink: 0;
	}
	.medal.charted {
		background: radial-gradient(circle at 38% 32%, #e8c79a, #b3823f);
		border: 2px solid var(--color-accent);
	}
	.medal.current {
		background: radial-gradient(circle at 38% 32%, #f6e3bf, #c89860);
		border: 2px solid var(--color-accent);
		box-shadow: 0 0 14px rgba(200, 152, 96, 0.45);
	}
	.medal.fog {
		background: rgba(56, 66, 76, 0.6);
		border: 1.5px dashed var(--color-text-faint);
	}
	.crest-tx {
		flex: 1;
		min-width: 0;
	}
	.crest-eye {
		font-family: var(--font-mono, monospace);
		font-size: 0.55rem;
		letter-spacing: 0.18em;
		text-transform: uppercase;
		color: var(--color-text-faint);
	}
	.crest-tx h2 {
		font-family: var(--font-display, serif);
		font-size: 1.4rem;
		font-weight: 600;
		line-height: 1.1;
		color: var(--color-text);
		margin: 0.1rem 0 0.3rem;
	}
	.pill {
		display: inline-block;
		font-family: var(--font-mono, monospace);
		font-size: 0.58rem;
		letter-spacing: 0.05em;
		padding: 0.12rem 0.45rem;
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
	.close {
		flex-shrink: 0;
		background: none;
		border: none;
		color: var(--color-text-faint);
		font-size: 1.3rem;
		line-height: 1;
		cursor: pointer;
		padding: 0.15rem 0.35rem;
	}
	.close:hover {
		color: var(--color-text);
	}
	.canon {
		margin-top: 0.8rem;
		font-family: var(--font-display, serif);
		font-size: 1rem;
		font-style: italic;
		line-height: 1.5;
		color: var(--color-accent);
	}
	.mline {
		margin-top: 0.5rem;
		font-size: 0.84rem;
		line-height: 1.5;
		color: var(--color-text-dim);
	}
	.logsec {
		margin-top: 1rem;
	}
	.logsec h3 {
		font-family: var(--font-mono, monospace);
		font-size: 0.62rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--color-text-faint);
		margin-bottom: 0.5rem;
	}
	.empty {
		font-size: 0.85rem;
		color: var(--color-text-faint);
		border: 1px dashed var(--color-border);
		border-radius: 0.5rem;
		padding: 0.7rem 0.85rem;
	}
	.entries {
		display: flex;
		flex-direction: column;
	}
	.entry {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--color-border);
		font-size: 0.85rem;
	}
	.entry:last-child {
		border-bottom: none;
	}
	.edot {
		flex-shrink: 0;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--color-text-faint);
		align-self: center;
	}
	.edot.alert {
		background: var(--color-warning);
	}
	.etext {
		flex: 1;
		color: var(--color-text-dim);
	}
	.etext.alert {
		color: var(--color-warning);
	}
	.ecount {
		flex-shrink: 0;
		font-family: var(--font-mono, monospace);
		font-size: 0.7rem;
		color: var(--color-text-faint);
	}

	/* ── Progress ───────────────────────────────────────────────────────── */
	.progress {
		margin-top: 0.9rem;
	}
	.pstats {
		display: flex;
		gap: 0.6rem;
	}
	.pstat {
		flex: 1;
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: var(--color-surface2);
		padding: 0.65rem 0.5rem;
		text-align: center;
	}
	.pnum {
		display: block;
		font-family: var(--font-mono, monospace);
		font-size: 1.5rem;
		font-weight: 600;
		color: var(--color-accent);
	}
	.plabel {
		display: block;
		margin-top: 0.15rem;
		font-size: 0.64rem;
		letter-spacing: 0.04em;
		color: var(--color-text-dim);
	}
	.prog-line {
		margin-top: 0.6rem;
		font-size: 0.8rem;
		line-height: 1.5;
		color: var(--color-text-dim);
		text-align: center;
	}
	.prog-line span {
		color: var(--color-accent);
		font-weight: 500;
	}

	@media (prefers-reduced-motion: reduce) {
		.ship svg {
			animation: none;
		}
	}
</style>
