<script lang="ts">
	import PageShell from '$lib/components/PageShell.svelte';
	import { swipe } from '$lib/actions/swipe';
	import {
		getCardsList,
		getSets,
		createDeck,
		validateDeck,
		type CardSummary,
		type SetSummary,
		type DeckValidation
	} from '$lib/api/client';

	type Tab = 'pool' | 'deck';

	const COLOR_CHIPS: { key: string; label: string; full: string }[] = [
		{ key: 'R', label: 'R', full: 'Red' },
		{ key: 'G', label: 'G', full: 'Green' },
		{ key: 'B', label: 'B', full: 'Blue' },
		{ key: 'P', label: 'P', full: 'Purple' },
		{ key: 'K', label: 'K', full: 'Black' },
		{ key: 'Y', label: 'Y', full: 'Yellow' }
	];

	function splitColors(color: string): string[] {
		return (color || '')
			.split('/')
			.map((c) => c.trim())
			.filter(Boolean);
	}

	function colorTokenFor(color: string): string {
		const first = splitColors(color)[0]?.toLowerCase();
		switch (first) {
			case 'red':
				return 'var(--color-leader-red)';
			case 'green':
				return 'var(--color-leader-green)';
			case 'blue':
				return 'var(--color-leader-blue)';
			case 'purple':
				return 'var(--color-leader-purple)';
			case 'yellow':
				return 'var(--color-leader-yellow)';
			case 'black':
				return 'var(--color-leader-black)';
			default:
				return 'rgba(255,255,255,0.08)';
		}
	}

	// ── Leader picker ────────────────────────────────────────────────
	let leader: CardSummary | null = $state(null);
	let leaderSearch = $state('');
	let leaders: CardSummary[] = $state([]);
	let leadersLoading = $state(true);
	let leadersError = $state<string | null>(null);
	let sets: SetSummary[] = $state([]);

	async function loadLeaders() {
		leadersLoading = true;
		leadersError = null;
		try {
			const setsRes = await getSets();
			sets = setsRes.sets;
			// All leaders in one shot — the API now allows type-only queries.
			const acc: CardSummary[] = [];
			let page = 1;
			while (true) {
				const res = await getCardsList({ type: 'Leader', limit: 100, page });
				acc.push(...res.cards);
				if (page >= res.pages || res.pages === 0) break;
				page += 1;
			}
			leaders = acc;
		} catch (e) {
			leadersError = e instanceof Error ? e.message : String(e);
		} finally {
			leadersLoading = false;
		}
	}

	const filteredLeaders = $derived.by(() => {
		const q = leaderSearch.trim().toLowerCase();
		if (!q) return leaders;
		return leaders.filter(
			(l) =>
				l.name.toLowerCase().includes(q) || l.code.toLowerCase().includes(q)
		);
	});

	function pickLeader(l: CardSummary) {
		leader = l;
		tab = 'pool';
		deckCards = new Map();
		activePoolColor = splitColors(l.color)[0] ?? '';
		loadPool();
	}

	function resetLeader() {
		leader = null;
		deckCards = new Map();
		pool = [];
		saveMessage = null;
		validation = null;
	}

	// ── Pool ─────────────────────────────────────────────────────────
	let tab: Tab = $state('pool');
	let poolSearch = $state('');
	let activePoolColor = $state('');
	let pool: CardSummary[] = $state([]);
	let poolLoading = $state(false);
	let poolError = $state<string | null>(null);

	const leaderColors = $derived(leader ? splitColors(leader.color) : []);

	async function loadPool() {
		if (!leader) return;
		poolLoading = true;
		poolError = null;
		try {
			// Pull all non-leader cards from every set that matches any of the
			// leader's colors. The API enforces exact color match, so we make
			// one request per (set, color) combo.
			const acc: CardSummary[] = [];
			const seen = new Set<string>();
			for (const s of sets) {
				for (const c of leaderColors) {
					let page = 1;
					while (true) {
						const res = await getCardsList({
							set: s.set_id,
							color: c,
							limit: 100,
							page
						});
						for (const card of res.cards) {
							if (card.type === 'Leader') continue;
							if (seen.has(card.code)) continue;
							seen.add(card.code);
							acc.push(card);
						}
						if (page >= res.pages) break;
						page += 1;
					}
				}
			}
			pool = acc;
		} catch (e) {
			poolError = e instanceof Error ? e.message : String(e);
		} finally {
			poolLoading = false;
		}
	}

	const filteredPool = $derived.by(() => {
		const q = poolSearch.trim().toLowerCase();
		return pool.filter((c) => {
			if (activePoolColor) {
				const cardColors = splitColors(c.color);
				if (!cardColors.some((cc) => cc === activePoolColor)) return false;
			}
			if (q && !(c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q))) {
				return false;
			}
			return true;
		});
	});

	// ── Deck state (Map<code, {count, card}>) ───────────────────────
	type DeckEntry = { count: number; card: CardSummary };
	let deckCards: Map<string, DeckEntry> = $state(new Map());

	const deckTotal = $derived.by(() => {
		let n = 0;
		for (const e of deckCards.values()) n += e.count;
		return n;
	});

	function getCount(code: string): number {
		return deckCards.get(code)?.count ?? 0;
	}

	// Haptic feedback per mobile-deckbuilder-ux skill. Add/remove get a light
	// tick, the 4-copy or 50-card ceiling fires a heavier "you can't" pattern.
	// All paths are no-ops on devices without the Vibration API (desktop, iOS
	// Safari without user interaction) so this is safe everywhere.
	function haptic(pattern: number | number[]): void {
		if (typeof navigator === 'undefined') return;
		const nav = navigator as Navigator & { vibrate?: (p: number | number[]) => boolean };
		try {
			nav.vibrate?.(pattern);
		} catch {
			/* iOS Safari can throw if the page isn't visible */
		}
	}

	// Card code most-recently added — drives the brief scale-pulse animation on
	// the matching pool tile so the operator gets visual confirmation alongside
	// the haptic. Cleared on a short timer so the same card can pulse again.
	let lastAddedCode = $state<string | null>(null);
	let lastAddedTimer: ReturnType<typeof setTimeout> | null = null;
	function flashAdded(code: string): void {
		lastAddedCode = code;
		if (lastAddedTimer) clearTimeout(lastAddedTimer);
		lastAddedTimer = setTimeout(() => (lastAddedCode = null), 220);
	}

	function addCard(c: CardSummary) {
		const existing = deckCards.get(c.code);
		const current = existing?.count ?? 0;
		if (current >= 4) {
			haptic([18, 40, 18]); // limit hit — "denied" pattern
			return;
		}
		if (deckTotal >= 50) {
			haptic([24, 40, 24]); // deck full — slightly heavier
			return;
		}
		const next = new Map(deckCards);
		next.set(c.code, { count: current + 1, card: c });
		deckCards = next;
		// Celebrate the exact moment the deck hits the legal 50.
		haptic(deckTotal === 50 ? [12, 30, 12, 30, 24] : 14);
		flashAdded(c.code);
	}

	function incCard(code: string) {
		const existing = deckCards.get(code);
		if (!existing) return;
		if (existing.count >= 4) {
			haptic([18, 40, 18]);
			return;
		}
		if (deckTotal >= 50) {
			haptic([24, 40, 24]);
			return;
		}
		const next = new Map(deckCards);
		next.set(code, { count: existing.count + 1, card: existing.card });
		deckCards = next;
		haptic(deckTotal === 50 ? [12, 30, 12, 30, 24] : 14);
	}

	function decCard(code: string) {
		const existing = deckCards.get(code);
		if (!existing) return;
		const next = new Map(deckCards);
		if (existing.count <= 1) next.delete(code);
		else next.set(code, { count: existing.count - 1, card: existing.card });
		deckCards = next;
		haptic(8); // lighter than add — removal is subtractive
	}

	// ── Cost curve ──────────────────────────────────────────────────
	const costCurve = $derived.by(() => {
		const buckets: Record<string, number> = {
			'0': 0,
			'1': 0,
			'2': 0,
			'3': 0,
			'4': 0,
			'5': 0,
			'6': 0,
			'7+': 0
		};
		for (const e of deckCards.values()) {
			const cost = e.card.cost;
			if (cost === null || cost === undefined) continue;
			const key = cost >= 7 ? '7+' : String(cost);
			buckets[key] = (buckets[key] ?? 0) + e.count;
		}
		return buckets;
	});

	const costCurveMax = $derived.by(() => {
		let m = 1;
		for (const v of Object.values(costCurve)) if (v > m) m = v;
		return m;
	});

	const avgCost = $derived.by(() => {
		let totalCost = 0;
		let totalCount = 0;
		for (const e of deckCards.values()) {
			if (e.card.cost === null || e.card.cost === undefined) continue;
			totalCost += e.card.cost * e.count;
			totalCount += e.count;
		}
		if (totalCount === 0) return '0.0';
		return (totalCost / totalCount).toFixed(1);
	});

	// ── Grouped deck list ───────────────────────────────────────────
	const groupedDeck = $derived.by(() => {
		const groups: Record<string, DeckEntry[]> = {};
		for (const e of deckCards.values()) {
			const t = e.card.type || 'Other';
			if (!groups[t]) groups[t] = [];
			groups[t].push(e);
		}
		for (const t of Object.keys(groups)) {
			groups[t].sort((a, b) => {
				const ac = a.card.cost ?? 99;
				const bc = b.card.cost ?? 99;
				if (ac !== bc) return ac - bc;
				return a.card.name.localeCompare(b.card.name);
			});
		}
		return groups;
	});

	const deckGroupOrder = ['Character', 'Event', 'Stage', 'Other'];

	// ── Save + Validate ─────────────────────────────────────────────
	let deckName = $state('');
	let savedDeckId: string | null = $state(null);
	let saveMessage: { kind: 'ok' | 'err'; text: string } | null = $state(null);
	let validation: DeckValidation | null = $state(null);
	let saving = $state(false);
	let validating = $state(false);

	async function saveDeck() {
		if (!leader) return;
		const name = deckName.trim() || `${leader.name} Deck`;
		saving = true;
		saveMessage = null;
		try {
			const cards = [...deckCards.values()].map((e) => ({
				code: e.card.code,
				count: e.count
			}));
			const body: { name: string; leader_code: string; cards: { code: string; count: number }[]; id?: string } = {
				name,
				leader_code: leader.code,
				cards
			};
			if (savedDeckId) body.id = savedDeckId;
			const deck = await createDeck(body);
			savedDeckId = deck.id;
			saveMessage = { kind: 'ok', text: `Saved as “${deck.name}”` };
		} catch (e: unknown) {
			const msg = e instanceof Error ? e.message : String(e);
			const payload = (e as { payload?: { error?: string; details?: string[] } })?.payload;
			const detail = payload?.details?.join('; ') ?? payload?.error ?? msg;
			saveMessage = { kind: 'err', text: detail };
		} finally {
			saving = false;
		}
	}

	async function runValidate() {
		if (!savedDeckId) {
			// Save first, then validate
			await saveDeck();
			if (!savedDeckId) return;
		}
		validating = true;
		validation = null;
		try {
			validation = await validateDeck(savedDeckId);
		} catch {
			validation = null;
		} finally {
			validating = false;
		}
	}

	$effect(() => {
		loadLeaders();
	});
</script>

<PageShell title="Deck Builder">
	<section class="px-1 pt-1 pb-[120px]">
		{#if !leader}
			<div class="mb-3">
				<h2
					class="m-0 mb-2 text-[20px] font-bold tracking-[-0.02em] text-white"
					style="font-family: var(--font-display);"
				>
					Pick a Leader
				</h2>
				<input
					type="search"
					bind:value={leaderSearch}
					placeholder="Search leaders…"
					class="w-full rounded-[12px] px-3 py-[10px] text-[16px] outline-none"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-text); font-family: var(--font-ui);"
				/>
			</div>

			{#if leadersLoading}
				<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">Loading leaders…</p>
			{:else if leadersError}
				<p class="m-0 text-[12px]" style="color: var(--color-leader-red);">
					Failed to load leaders: {leadersError}
				</p>
			{:else if filteredLeaders.length === 0}
				<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">No leaders match.</p>
			{:else}
				<!-- 3-col on every phone width (operator directive 2026-05-24, updating
				     the 2026-05-23 rule). The 390px breakpoint left iPhone SE / mini and
				     iPhone-with-Display-Zoom users on 2-col, which the operator rejected.
				     At 375px the tile is ~110px wide — still 2.5× the WCAG 2.5.5 floor.
				     Whole-tile is the touch target so 3-col stays accessible. -->
				<div class="grid grid-cols-3 gap-2">
					{#each filteredLeaders as l (l.code)}
						<button
							type="button"
							class="flex flex-col overflow-hidden rounded-[14px] text-left transition-colors"
							style="background: var(--color-miru-surface); border: 1px solid var(--color-miru-stroke);"
							onclick={() => pickLeader(l)}
						>
							<div
								class="relative aspect-[5/7] w-full overflow-hidden"
								style="background: {colorTokenFor(l.color)};"
							>
								{#if l.image_path}
									<img
										src={l.image_path}
										alt={l.name}
										loading="lazy"
										class="h-full w-full object-cover"
									/>
								{:else}
									<div class="flex h-full w-full items-center justify-center">
										<span
											class="text-[11px]"
											style="color: rgba(255,255,255,0.66); font-family: 'JetBrains Mono', ui-monospace, monospace;"
										>
											{l.code}
										</span>
									</div>
								{/if}
							</div>
							<div class="p-2">
								<div
									class="mb-[2px] text-[9px]"
									style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
								>
									{l.code}
								</div>
								<div
									class="truncate text-[12px] font-bold"
									style="font-family: var(--font-display); color: var(--color-miru-text);"
								>
									{l.name}
								</div>
								<div
									class="text-[10px]"
									style="color: var(--color-miru-muted); font-family: var(--font-ui);"
								>
									{l.color || '—'}
								</div>
							</div>
						</button>
					{/each}
				</div>
			{/if}
		{:else}
			<!-- Active-deck header -->
			<div class="mb-3 flex items-center gap-2">
				<button
					type="button"
					class="min-h-11 rounded-[10px] px-3 py-2 text-[13px] transition-colors"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-muted); font-family: var(--font-ui);"
					onclick={resetLeader}
				>
					Change leader
				</button>
				<div class="min-w-0 flex-1">
					<div
						class="truncate text-[14px] font-bold tracking-[-0.02em] text-white"
						style="font-family: var(--font-display);"
					>
						{leader.name}
					</div>
					<div
						class="text-[10px]"
						style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
					>
						{leader.code} · {leader.color}
					</div>
				</div>
			</div>

			<!-- Tabs -->
			<div
				class="mb-3 flex gap-1 rounded-[10px] p-[3px]"
				style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke);"
			>
				{#each ['pool', 'deck'] as t (t)}
					{@const on = tab === t}
					<button
						type="button"
						class="min-h-11 flex-1 rounded-[8px] px-3 py-2 text-[14px] capitalize transition-colors"
						style="font-family: var(--font-ui); background: {on
							? 'rgba(200,162,97,0.12)'
							: 'transparent'}; color: {on
							? 'var(--color-miru-gold)'
							: 'var(--color-miru-muted)'};"
						onclick={() => (tab = t as Tab)}
					>
						{t}
					</button>
				{/each}
			</div>

			{#if tab === 'pool'}
				<div class="mb-3 -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
					{#each COLOR_CHIPS as chip (chip.key)}
						{@const allowed = leaderColors.includes(chip.full)}
						{@const on = activePoolColor === chip.full}
						<button
							type="button"
							disabled={!allowed}
							class="shrink-0 rounded-[12px] px-3 py-[6px] text-[11px] transition-colors"
							style="font-family: var(--font-ui); opacity: {allowed
								? '1'
								: '0.35'}; border: 1px solid {on
								? 'rgba(200,162,97,0.35)'
								: 'rgba(255,255,255,0.08)'}; background: {on
								? 'rgba(200,162,97,0.12)'
								: 'rgba(255,255,255,0.02)'}; color: {on
								? 'var(--color-miru-gold)'
								: 'var(--color-miru-muted)'};"
							onclick={() => allowed && (activePoolColor = chip.full)}
						>
							{chip.label}
						</button>
					{/each}
				</div>

				<input
					type="search"
					bind:value={poolSearch}
					placeholder="Search pool…"
					class="mb-3 w-full rounded-[12px] px-3 py-[10px] text-[16px] outline-none"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-text); font-family: var(--font-ui);"
				/>

				{#if poolLoading}
					<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">Building pool…</p>
				{:else if poolError}
					<p class="m-0 text-[12px]" style="color: var(--color-leader-red);">
						Failed to load pool: {poolError}
					</p>
				{:else}
					<!-- Same 3-col rule as the leader picker (plain, no breakpoint). -->
					<div class="grid grid-cols-3 gap-2">
						{#each filteredPool as c (c.code)}
							{@const n = getCount(c.code)}
							{@const justAdded = lastAddedCode === c.code}
							<!-- Swipe wrapper: holds the reveal indicators (rose + / gold −)
							     that show behind the tile as it translates with the finger.
							     The button itself is the swipe target + receives the
							     translateX(var(--swipe-dx)) transform. -->
							<div class="deck-card-swipe-wrap relative overflow-hidden rounded-[14px]">
								<span
									class="deck-card-swipe-reveal deck-card-swipe-reveal--right pointer-events-none absolute inset-y-0 left-0 flex items-center justify-start pl-3 font-bold"
									aria-hidden="true">+</span
								>
								<span
									class="deck-card-swipe-reveal deck-card-swipe-reveal--left pointer-events-none absolute inset-y-0 right-0 flex items-center justify-end pr-3 font-bold"
									aria-hidden="true">−</span
								>
								<button
									type="button"
									use:swipe={{
										onSwipeRight: () => addCard(c),
										onSwipeLeft: () => (getCount(c.code) > 0 ? decCard(c.code) : undefined),
										onArmed: () => haptic(8)
									}}
									data-swipe-armed=""
									class="deck-card-tile relative flex w-full flex-col overflow-hidden rounded-[14px] text-left transition-colors {justAdded
										? 'deck-card-tile--pulse'
										: ''}"
									style="background: var(--color-pm-bg-surface); border: 1px solid {n > 0
										? 'rgba(200,162,97,0.35)'
										: 'var(--color-pm-stroke)'};"
									onclick={() => addCard(c)}
								>
								<div
									class="relative aspect-[5/7] w-full overflow-hidden"
									style="background: {colorTokenFor(c.color)};"
								>
									{#if c.image_path}
										<img
											src={c.image_path}
											alt={c.name}
											loading="lazy"
											class="h-full w-full object-cover"
										/>
									{/if}
									{#if n > 0}
										<span
											class="absolute top-1 left-1 rounded-[6px] px-[6px] py-[2px] text-[10px] font-bold"
											style="background: rgba(8,6,15,0.85); color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
										>
											×{n}
										</span>
									{/if}
								</div>
								<div class="p-2">
									<div
										class="mb-[2px] text-[9px]"
										style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
									>
										{c.code}
									</div>
									<div
										class="mb-[2px] truncate text-[12px] font-bold"
										style="font-family: var(--font-display); color: var(--color-miru-text);"
									>
										{c.name}
									</div>
									<div
										class="text-[10px]"
										style="color: var(--color-miru-muted); font-family: 'JetBrains Mono', ui-monospace, monospace;"
									>
										{c.cost ?? '—'} · {c.power || '—'}
									</div>
								</div>
							</button>
							</div>
						{/each}
					</div>
				{/if}
			{:else if tab === 'deck'}
				<!-- Cost curve -->
				<div
					class="mb-3 rounded-[12px] p-3"
					style="background: var(--color-miru-surface); border: 1px solid var(--color-miru-stroke);"
				>
					<div
						class="mb-2 text-[11px]"
						style="color: var(--color-miru-muted); font-family: var(--font-ui);"
					>
						Cost curve
					</div>
					<div class="flex h-[80px] items-end gap-2">
						{#each Object.entries(costCurve) as [k, v] (k)}
							<div class="flex flex-1 flex-col items-center gap-1">
								<div
									class="relative w-full rounded-[4px]"
									style="height: {(v / costCurveMax) * 68}px; background: linear-gradient(180deg, rgba(200,162,97,0.56), rgba(200,162,97,0.18)); border: 1px solid rgba(200,162,97,0.28); min-height: {v > 0
										? '4px'
										: '1px'};"
								>
									{#if v > 0}
										<span
											class="absolute -top-[14px] left-1/2 -translate-x-1/2 text-[9px]"
											style="color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
										>
											{v}
										</span>
									{/if}
								</div>
								<span
									class="text-[9px]"
									style="color: var(--color-miru-muted); font-family: 'JetBrains Mono', ui-monospace, monospace;"
								>
									{k}
								</span>
							</div>
						{/each}
					</div>
				</div>

				<!-- Stats -->
				<div
					class="mb-3 flex items-center justify-between rounded-[12px] px-3 py-[10px]"
					style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke);"
				>
					<div>
						<span
							class="text-[16px] font-bold"
							style="color: {deckTotal === 50
								? 'var(--color-miru-gold)'
								: 'var(--color-miru-text)'}; font-family: 'JetBrains Mono', ui-monospace, monospace;"
						>
							{deckTotal}/50
						</span>
						<span
							class="ml-2 text-[11px]"
							style="color: var(--color-miru-muted); font-family: var(--font-ui);"
						>
							cards
						</span>
					</div>
					<div>
						<span
							class="text-[16px] font-bold"
							style="color: var(--color-miru-text); font-family: 'JetBrains Mono', ui-monospace, monospace;"
						>
							{avgCost}
						</span>
						<span
							class="ml-2 text-[11px]"
							style="color: var(--color-miru-muted); font-family: var(--font-ui);"
						>
							avg cost
						</span>
					</div>
				</div>

				{#if deckCards.size === 0}
					<div
						class="rounded-[12px] p-4 text-center"
						style="background: rgba(255,255,255,0.02); border: 1px dashed var(--color-miru-stroke);"
					>
						<p class="m-0 mb-2 text-[12px]" style="color: var(--color-miru-muted); font-family: var(--font-ui);">
							Your deck is empty.
						</p>
						<button
							type="button"
							class="rounded-[10px] px-3 py-[6px] text-[11px] font-semibold"
							style="background: rgba(200,162,97,0.12); border: 1px solid rgba(200,162,97,0.35); color: var(--color-miru-gold); font-family: var(--font-ui);"
							onclick={() => (tab = 'pool')}
						>
							Add cards from Pool →
						</button>
					</div>
				{:else}
					{#each deckGroupOrder as groupName (groupName)}
						{@const entries = groupedDeck[groupName] ?? []}
						{#if entries.length}
							<div class="mb-3">
								<div
									class="mb-1 text-[11px] uppercase tracking-[0.08em]"
									style="color: var(--color-miru-muted); font-family: var(--font-ui);"
								>
									{groupName} · {entries.reduce((s, e) => s + e.count, 0)}
								</div>
								<div class="flex flex-col gap-1">
									{#each entries as e (e.card.code)}
										<div
											class="flex items-center gap-2 rounded-[10px] px-2 py-[6px]"
											style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke);"
										>
											<div
												class="h-6 w-6 shrink-0 rounded-full"
												style="background: {colorTokenFor(e.card.color)};"
												aria-hidden="true"
											></div>
											<div class="min-w-0 flex-1">
												<div
													class="truncate text-[12px] font-semibold"
													style="color: var(--color-miru-text); font-family: var(--font-ui);"
												>
													{e.card.name}
												</div>
												<div
													class="text-[10px]"
													style="color: var(--color-miru-muted); font-family: 'JetBrains Mono', ui-monospace, monospace;"
												>
													{e.card.code} · cost {e.card.cost ?? '—'}
												</div>
											</div>
											<div class="flex shrink-0 items-center gap-1">
												<button
													type="button"
													class="h-11 w-11 rounded-[8px] text-[16px]"
													style="background: rgba(255,255,255,0.05); color: var(--color-miru-text);"
													aria-label="Remove one"
													onclick={() => decCard(e.card.code)}
												>
													−
												</button>
												<span
													class="w-6 text-center text-[13px] font-bold"
													style="color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
												>
													{e.count}
												</span>
												<button
													type="button"
													class="h-11 w-11 rounded-[8px] text-[16px]"
													style="background: rgba(200,162,97,0.15); color: var(--color-miru-gold);"
													disabled={e.count >= 4 || deckTotal >= 50}
													aria-label="Add one"
													onclick={() => incCard(e.card.code)}
												>
													+
												</button>
											</div>
										</div>
									{/each}
								</div>
							</div>
						{/if}
					{/each}
				{/if}

				{#if saveMessage}
					<div
						class="mb-2 rounded-[10px] px-3 py-[8px] text-[12px]"
						style="background: {saveMessage.kind === 'ok'
							? 'rgba(45,157,95,0.12)'
							: 'rgba(201,58,58,0.14)'}; border: 1px solid {saveMessage.kind === 'ok'
							? 'rgba(45,157,95,0.4)'
							: 'rgba(201,58,58,0.4)'}; color: {saveMessage.kind === 'ok'
							? 'var(--color-leader-green)'
							: 'var(--color-leader-red)'}; font-family: var(--font-ui);"
					>
						{saveMessage.text}
					</div>
				{/if}

				{#if validation}
					<div
						class="mb-2 rounded-[10px] p-3 text-[12px]"
						style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-text); font-family: var(--font-ui);"
					>
						<div class="mb-1">
							<span
								class="text-[11px] font-semibold"
								style="color: {validation.valid ? 'var(--color-leader-green)' : 'var(--color-miru-gold)'};"
							>
								{validation.valid ? 'Legal' : 'Not yet legal'}
							</span>
							<span class="ml-2" style="color: var(--color-miru-muted);">
								{validation.summary.total_cards}/50 cards
							</span>
						</div>
						{#if validation.errors.length}
							<ul class="m-0 mt-2 list-disc pl-5">
								{#each validation.errors as err (err)}
									<li style="color: var(--color-leader-red);">{err}</li>
								{/each}
							</ul>
						{/if}
						{#if validation.warnings.length}
							<ul class="m-0 mt-2 list-disc pl-5">
								{#each validation.warnings as w (w)}
									<li style="color: var(--color-miru-gold);">{w}</li>
								{/each}
							</ul>
						{/if}
					</div>
				{/if}
			{/if}
		{/if}
	</section>
</PageShell>

{#if leader}
	<!-- Sticky footer: name + save + validate -->
	<div
		class="fixed right-0 bottom-[var(--bottom-nav-height)] left-0 z-[70] border-t px-3 py-[10px]"
		style="background: rgba(12,10,20,0.97); border-top-color: var(--color-miru-stroke); backdrop-filter: blur(8px);"
	>
		{#if tab === 'pool'}
			<!-- Primary CTA → rose per PM 06 § 1 (Miru asking the user to act).
			     The deckTotal stays gold because it represents the user's agency. -->
			<button
				type="button"
				class="flex w-full items-center justify-center gap-2 rounded-[12px] px-4 py-[10px] text-[14px] font-semibold"
				style="background: var(--color-pm-accent-dim); border: 1px solid var(--color-pm-accent); color: var(--color-pm-fg-primary); font-family: var(--font-ui);"
				onclick={() => (tab = 'deck')}
			>
				<span style="font-family: var(--font-mono); color: var(--color-pm-gold);">{deckTotal}/50</span>
				<span style="color: var(--color-pm-fg-secondary);">·</span>
				<span>View Deck →</span>
			</button>
		{:else}
			<div class="mb-2">
				<input
					type="text"
					bind:value={deckName}
					placeholder="Deck name"
					class="w-full rounded-[8px] px-3 py-[10px] text-[16px] outline-none"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-pm-stroke); color: var(--color-pm-fg-primary); font-family: var(--font-ui);"
				/>
			</div>
			<div class="flex gap-2">
				<!-- Save Deck = primary CTA → rose. Disabled state visibly distinct
				     (PM audit W4 fix). Validate is secondary → neutral. -->
				<button
					type="button"
					class="flex-1 rounded-[12px] px-3 py-[10px] text-[14px] font-semibold transition-opacity"
					style="background: var(--color-pm-accent); border: 1px solid var(--color-pm-accent); color: var(--color-pm-fg-primary); font-family: var(--font-ui); opacity: {saving || deckCards.size === 0 ? '0.45' : '1'}; cursor: {saving || deckCards.size === 0 ? 'not-allowed' : 'pointer'};"
					disabled={saving || deckCards.size === 0}
					onclick={saveDeck}
				>
					{saving ? 'Saving…' : savedDeckId ? 'Update' : 'Save Deck'}
				</button>
				<button
					type="button"
					class="flex-1 rounded-[12px] px-3 py-[10px] text-[14px] font-semibold transition-opacity"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-pm-stroke-strong); color: var(--color-pm-fg-primary); font-family: var(--font-ui); opacity: {validating || deckCards.size === 0 ? '0.45' : '1'}; cursor: {validating || deckCards.size === 0 ? 'not-allowed' : 'pointer'};"
					disabled={validating || deckCards.size === 0}
					onclick={runValidate}
				>
					{validating ? 'Checking…' : 'Validate'}
				</button>
			</div>
		{/if}
	</div>
{/if}

<style>
	/* ── Tap confirmation: brief scale pulse + accent ring on the tile that was
	     just added. Pairs with the haptic so the operator gets visual feedback
	     alongside the vibration. Falls back under prefers-reduced-motion. ── */
	.deck-card-tile {
		transform-origin: center center;
		/* Default state transitions handle tap-confirm + snap-back. During an
		   active swipe the action sets --swipe-dx and we apply translateX via
		   the rule below; on release --swipe-dx is removed and this transition
		   animates the tile back to centre. */
		transition: transform 180ms cubic-bezier(0.2, 0.8, 0.3, 1.2);
		transform: translateX(var(--swipe-dx, 0px));
		/* While a swipe is in progress, the action also sets a touch-action
		   inline style so the browser knows we own horizontal. */

		/* iOS Safari suppression — without these, the operator's long-press
		   on a card thumb triggers Safari's native image-context menu (Save
		   to Photos, Copy) BEFORE my pointer-event listeners can react. The
		   gesture then "only selects the card thumb" instead of swiping.
		   webkit-touch-callout silences the context menu; user-select stops
		   the drag from initiating a text/image selection; tap-highlight kills
		   the blue flash that conflicts with the rose/gold reveal indicators. */
		-webkit-touch-callout: none;
		-webkit-user-select: none;
		user-select: none;
		-webkit-tap-highlight-color: transparent;
	}
	/* Same suppression on any descendant <img> — image elements have their own
	   iOS callout defaults that override the parent's. */
	.deck-card-tile img {
		-webkit-touch-callout: none;
		-webkit-user-select: none;
		user-select: none;
		-webkit-user-drag: none;
		pointer-events: none; /* let the swipe action see the gesture on the tile, not on the img */
	}
	.deck-card-tile:active {
		/* Only apply the press-shrink when there's no active swipe (no --swipe-dx). */
		transform: translateX(var(--swipe-dx, 0px)) scale(0.97);
	}
	.deck-card-tile--pulse {
		animation: deck-card-pulse 220ms cubic-bezier(0.2, 0.8, 0.3, 1.2);
	}
	@keyframes deck-card-pulse {
		0%   { transform: scale(0.97); box-shadow: 0 0 0 0 rgba(200, 162, 97, 0.55); }
		60%  { transform: scale(1.04); box-shadow: 0 0 0 6px rgba(200, 162, 97, 0); }
		100% { transform: scale(1);    box-shadow: 0 0 0 0 rgba(200, 162, 97, 0); }
	}

	/* ── Swipe reveal indicators ──
	   The wrap div is a relative-positioned overflow-hidden container.
	   Two reveal layers sit absolutely behind the tile:
	     - right-side ROSE "+" reveals as the user swipes right (= add)
	     - left-side GOLD "−" reveals as the user swipes left (= remove)
	   Opacity is driven by --swipe-dx via clamp(): the further the finger
	   travels, the more solid the indicator. data-swipe-armed flips to a
	   stronger fill when the commit threshold is crossed, providing the
	   visual companion to the "armed" haptic. */
	.deck-card-swipe-wrap {
		/* Live with the tile's own border-radius so the reveals don't bleed. */
		isolation: isolate;
	}
	.deck-card-swipe-reveal {
		font-size: 28px;
		line-height: 1;
		opacity: 0;
		transition: opacity 120ms ease-out, background-color 120ms ease-out;
		z-index: 0;
		width: 64px;
	}
	.deck-card-swipe-reveal--right {
		color: var(--color-pm-accent);
		background: var(--color-pm-accent-dim);
	}
	.deck-card-swipe-reveal--left {
		color: var(--color-pm-gold);
		background: var(--color-pm-gold-dim);
	}
	/* Show the right reveal (+) when the finger is dragging right. */
	.deck-card-swipe-wrap:has([data-swipe-armed='right']) .deck-card-swipe-reveal--right,
	.deck-card-swipe-wrap:has([style*='--swipe-dx']) .deck-card-swipe-reveal--right {
		opacity: 0.75;
	}
	.deck-card-swipe-wrap:has([data-swipe-armed='right']) .deck-card-swipe-reveal--right {
		opacity: 1;
		background: var(--color-pm-accent);
		color: var(--color-pm-fg-primary);
	}
	/* Show the left reveal (−) when the finger is dragging left. */
	.deck-card-swipe-wrap:has([data-swipe-armed='left']) .deck-card-swipe-reveal--left,
	.deck-card-swipe-wrap:has([style*='--swipe-dx']) .deck-card-swipe-reveal--left {
		opacity: 0.75;
	}
	.deck-card-swipe-wrap:has([data-swipe-armed='left']) .deck-card-swipe-reveal--left {
		opacity: 1;
		background: var(--color-pm-gold);
		color: var(--color-pm-bg-canvas);
	}
	/* The tile itself sits on top so its content keeps full opacity even when
	   the reveal layers are visible behind. */
	.deck-card-tile {
		position: relative;
		z-index: 1;
	}

	@media (prefers-reduced-motion: reduce) {
		.deck-card-tile,
		.deck-card-tile--pulse,
		.deck-card-swipe-reveal {
			transition: none;
			animation: none;
		}
	}
</style>
