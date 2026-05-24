<script lang="ts">
	import PageShell from '$lib/components/PageShell.svelte';
	import {
		getSets,
		getCardsList,
		getCardDetail,
		type SetSummary,
		type CardSummary,
		type CardDetail
	} from '$lib/api/client';

	type View = 'sets' | 'cards';
	type SetKind = 'all' | 'booster' | 'starter' | 'extra' | 'premium';

	const COLOR_CHIPS: { key: string; label: string; full: string }[] = [
		{ key: 'all', label: 'All', full: '' },
		{ key: 'R', label: 'R', full: 'Red' },
		{ key: 'G', label: 'G', full: 'Green' },
		{ key: 'B', label: 'B', full: 'Blue' },
		{ key: 'P', label: 'P', full: 'Purple' },
		{ key: 'K', label: 'K', full: 'Black' },
		{ key: 'Y', label: 'Y', full: 'Yellow' }
	];

	const SET_KINDS: { key: SetKind; label: string }[] = [
		{ key: 'all', label: 'All' },
		{ key: 'booster', label: 'Booster' },
		{ key: 'starter', label: 'Starter' },
		{ key: 'extra', label: 'Extra' },
		{ key: 'premium', label: 'Premium' }
	];

	function classifySet(set_id: string): SetKind {
		const id = (set_id || '').toUpperCase();
		if (id.startsWith('ST')) return 'starter';
		if (id.startsWith('EB')) return 'extra';
		if (id.startsWith('PRB') || id.startsWith('PR')) return 'premium';
		if (id.startsWith('OP')) return 'booster';
		return 'premium';
	}

	function colorTokenFor(color: string): string {
		const first = (color || '').split('/')[0]?.trim().toLowerCase();
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

	let view: View = $state('sets');
	let setKind: SetKind = $state('all');
	let sets: SetSummary[] = $state([]);
	let setsLoading = $state(true);
	let setsError = $state<string | null>(null);

	let activeSet: SetSummary | null = $state(null);
	let activeColor = $state('all');
	let cards: CardSummary[] = $state([]);
	let cardsTotal = $state(0);
	let cardsPage = $state(1);
	let cardsPages = $state(0);
	let cardsLoading = $state(false);
	let cardsError = $state<string | null>(null);

	let detailCode: string | null = $state(null);
	let detail: CardDetail | null = $state(null);
	let detailLoading = $state(false);
	let detailTab: 'details' | 'variants' | 'prices' = $state('details');
	let watchlist: Set<string> = $state(new Set());

	const filteredSets = $derived(
		setKind === 'all' ? sets : sets.filter((s) => classifySet(s.set_id) === setKind)
	);

	async function loadSets() {
		setsLoading = true;
		setsError = null;
		try {
			const res = await getSets();
			sets = res.sets;
		} catch (e) {
			setsError = e instanceof Error ? e.message : String(e);
		} finally {
			setsLoading = false;
		}
	}

	async function loadCards(reset = true) {
		if (!activeSet) return;
		cardsLoading = true;
		cardsError = null;
		const page = reset ? 1 : cardsPage + 1;
		try {
			const color = activeColor === 'all'
				? undefined
				: COLOR_CHIPS.find((c) => c.key === activeColor)?.full;
			const res = await getCardsList({
				set: activeSet.set_id,
				page,
				limit: 40,
				color
			});
			cardsTotal = res.total;
			cardsPage = res.page;
			cardsPages = res.pages;
			cards = reset ? res.cards : [...cards, ...res.cards];
		} catch (e) {
			cardsError = e instanceof Error ? e.message : String(e);
		} finally {
			cardsLoading = false;
		}
	}

	function openSet(s: SetSummary) {
		activeSet = s;
		activeColor = 'all';
		cards = [];
		cardsPage = 1;
		cardsPages = 0;
		cardsTotal = 0;
		view = 'cards';
		loadCards(true);
	}

	function backToSets() {
		view = 'sets';
		activeSet = null;
		cards = [];
	}

	function setColor(key: string) {
		if (activeColor === key) return;
		activeColor = key;
		loadCards(true);
	}

	async function openDetail(code: string) {
		detailCode = code;
		detail = null;
		detailTab = 'details';
		detailLoading = true;
		try {
			detail = await getCardDetail(code);
		} catch {
			detail = null;
		} finally {
			detailLoading = false;
		}
	}

	function closeDetail() {
		detailCode = null;
		detail = null;
	}

	function loadWatchlist() {
		try {
			const raw = localStorage.getItem('miru:watchlist');
			if (raw) {
				const arr = JSON.parse(raw);
				if (Array.isArray(arr)) watchlist = new Set(arr.map(String));
			}
		} catch {
			/* ignore */
		}
	}

	function toggleWatchlist(code: string) {
		const next = new Set(watchlist);
		if (next.has(code)) next.delete(code);
		else next.add(code);
		watchlist = next;
		try {
			localStorage.setItem('miru:watchlist', JSON.stringify([...next]));
		} catch {
			/* ignore */
		}
	}

	$effect(() => {
		loadSets();
		loadWatchlist();
	});
</script>

<PageShell title="Cards">
	<section class="px-1 pt-1 pb-8">
		{#if view === 'sets'}
			<div class="mb-3 flex items-baseline justify-between">
				<h2
					class="m-0 text-[20px] font-bold tracking-[-0.02em] text-white"
					style="font-family: var(--font-display);"
				>
					Cards
				</h2>
				<span class="text-[11px]" style="color: var(--color-miru-muted); font-family: var(--font-ui);">
					Newest ↓
				</span>
			</div>

			<div class="mb-3 -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
				{#each SET_KINDS as kind (kind.key)}
					{@const on = setKind === kind.key}
					<button
						type="button"
						class="shrink-0 rounded-[12px] px-3 py-[6px] text-[11px] transition-colors"
						style="font-family: var(--font-ui); border: 1px solid {on
							? 'rgba(200,162,97,0.35)'
							: 'rgba(255,255,255,0.08)'}; background: {on
							? 'rgba(200,162,97,0.12)'
							: 'rgba(255,255,255,0.02)'}; color: {on
							? 'var(--color-miru-gold)'
							: 'var(--color-miru-muted)'};"
						onclick={() => (setKind = kind.key)}
					>
						{kind.label}
					</button>
				{/each}
			</div>

			{#if setsLoading}
				<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">Loading sets…</p>
			{:else if setsError}
				<p class="m-0 text-[12px]" style="color: var(--color-leader-red);">
					Failed to load sets: {setsError}
				</p>
			{:else if filteredSets.length === 0}
				<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">No sets in this category.</p>
			{:else}
				<div class="grid grid-cols-2 gap-2">
					{#each filteredSets as s (s.set_id)}
						<button
							type="button"
							class="rounded-[14px] p-3 text-left transition-colors"
							style="background: var(--color-miru-surface); border: 1px solid var(--color-miru-stroke);"
							onclick={() => openSet(s)}
						>
							<div
								class="mb-1 text-[20px] font-extrabold tracking-[-0.02em] text-white"
								style="font-family: var(--font-display);"
							>
								{s.set_id}
							</div>
							<!-- Audit W8: bump 11/10px → 12px (PM 06 § 3 floor for micro-labels). -->
							<div
								class="mb-2 line-clamp-2 text-[12px]"
								style="color: var(--color-pm-fg-secondary); font-family: var(--font-ui);"
							>
								{s.set_name}
							</div>
							<div
								class="text-[12px]"
								style="color: var(--color-pm-fg-tertiary); font-family: var(--font-mono);"
							>
								{s.card_count} cards
							</div>
						</button>
					{/each}
				</div>
			{/if}
		{:else if view === 'cards' && activeSet}
			<div class="mb-3 flex items-center gap-2">
				<button
					type="button"
					class="rounded-[10px] px-[10px] py-[6px] text-[13px] transition-colors"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-text); font-family: var(--font-ui);"
					onclick={backToSets}
					aria-label="Back to sets"
				>
					← Back
				</button>
				<div class="min-w-0 flex-1">
					<div
						class="truncate text-[15px] font-bold tracking-[-0.02em] text-white"
						style="font-family: var(--font-display);"
					>
						{activeSet.set_id} · {activeSet.set_name}
					</div>
					<div
						class="text-[10px]"
						style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
					>
						{cardsTotal} cards
					</div>
				</div>
			</div>

			<div class="mb-3 -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
				{#each COLOR_CHIPS as chip (chip.key)}
					{@const on = activeColor === chip.key}
					<button
						type="button"
						class="shrink-0 rounded-[12px] px-3 py-[6px] text-[11px] transition-colors"
						style="font-family: var(--font-ui); border: 1px solid {on
							? 'rgba(200,162,97,0.35)'
							: 'rgba(255,255,255,0.08)'}; background: {on
							? 'rgba(200,162,97,0.12)'
							: 'rgba(255,255,255,0.02)'}; color: {on
							? 'var(--color-miru-gold)'
							: 'var(--color-miru-muted)'};"
						onclick={() => setColor(chip.key)}
					>
						{chip.label}
					</button>
				{/each}
			</div>

			{#if cardsError}
				<p class="m-0 text-[12px]" style="color: var(--color-leader-red);">
					Failed to load cards: {cardsError}
				</p>
			{/if}

			<!-- 3-col card grid on all phone widths per operator directive 2026-05-24
			     (updated from 2026-05-23 which had a 390px fallback that left
			     iPhone SE / Display Zoom users on 2-col). -->
			<div class="grid grid-cols-3 gap-2">
				{#each cards as c (c.code)}
					<button
						type="button"
						class="flex flex-col overflow-hidden rounded-[14px] text-left transition-colors"
						style="background: var(--color-miru-surface); border: 1px solid var(--color-miru-stroke);"
						onclick={() => openDetail(c.code)}
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
							{:else}
								<div class="flex h-full w-full items-center justify-center">
									<span
										class="text-[11px]"
										style="color: rgba(255,255,255,0.66); font-family: 'JetBrains Mono', ui-monospace, monospace;"
									>
										{c.code}
									</span>
								</div>
							{/if}
							<span
								class="absolute top-1 right-1 rounded-[6px] px-[6px] py-[1px] text-[9px] font-semibold"
								style="background: rgba(8,6,15,0.75); color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
							>
								{c.rarity || '—'}
							</span>
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
								{c.cost ?? '—'} · {c.power || '—'} · {c.counter && c.counter !== '-' ? c.counter : '—'}
							</div>
						</div>
					</button>
				{/each}
			</div>

			{#if cardsLoading}
				<p class="mt-3 text-center text-[12px]" style="color: var(--color-miru-muted);">Loading…</p>
			{/if}

			{#if !cardsLoading && cardsPage < cardsPages}
				<div class="mt-3 flex justify-center">
					<button
						type="button"
						class="rounded-[12px] px-4 py-2 text-[12px] font-semibold transition-colors"
						style="background: rgba(200,162,97,0.12); border: 1px solid rgba(200,162,97,0.35); color: var(--color-miru-gold); font-family: var(--font-ui);"
						onclick={() => loadCards(false)}
					>
						Load more · {cardsPage}/{cardsPages}
					</button>
				</div>
			{/if}
		{/if}
	</section>
</PageShell>

{#if detailCode}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-[80] bg-black/60"
		onclick={closeDetail}
		role="presentation"
	></div>
	<div
		class="fixed inset-x-0 bottom-0 z-[90] flex h-[92vh] flex-col overflow-hidden rounded-t-[18px]"
		style="background: #0c0a14; border-top: 1px solid var(--color-miru-stroke-brand);"
		role="dialog"
		aria-modal="true"
		aria-label="Card detail"
	>
		<div class="flex items-center justify-between px-4 pt-2 pb-1">
			<div class="mx-auto h-[4px] w-[40px] rounded-full" style="background: rgba(255,255,255,0.18);"></div>
		</div>
		<div class="flex items-start justify-between px-4 pb-2">
			<div class="min-w-0 flex-1">
				<div
					class="text-[10px]"
					style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
				>
					{detailCode}
				</div>
				<div
					class="truncate text-[16px] font-bold tracking-[-0.02em]"
					style="font-family: var(--font-display); color: var(--color-miru-text);"
				>
					{detail?.name ?? (detailLoading ? 'Loading…' : detailCode)}
				</div>
			</div>
			<button
				type="button"
				class="ml-2 h-8 w-8 rounded-full text-[16px]"
				style="background: rgba(255,255,255,0.06); color: var(--color-miru-muted);"
				onclick={closeDetail}
				aria-label="Close"
			>
				×
			</button>
		</div>

		<div class="flex-1 overflow-y-auto px-4 pb-[100px]">
			{#if detailLoading}
				<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">Loading card…</p>
			{:else if detail}
				<div
					class="relative mb-3 aspect-[5/7] w-full max-w-[320px] overflow-hidden rounded-[14px]"
					style="background: {colorTokenFor(detail.color)};"
				>
					{#if detail.image_path}
						<img src={detail.image_path} alt={detail.name} class="h-full w-full object-cover" />
					{/if}
				</div>
				<p
					class="mb-3 text-[10px]"
					style="color: var(--color-miru-muted-2); font-family: var(--font-ui);"
				>
					Pinch to zoom image (coming soon)
				</p>
				<div class="mb-3 flex items-center gap-2">
					<span
						class="rounded-[8px] px-2 py-[2px] text-[10px] font-semibold"
						style="background: rgba(200,162,97,0.12); color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
					>
						{detail.rarity || '—'}
					</span>
					<span class="text-[11px]" style="color: var(--color-miru-muted); font-family: var(--font-ui);">
						{detail.set_id}
					</span>
				</div>

				<div
					class="mb-3 flex gap-1 rounded-[10px] p-[3px]"
					style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke);"
				>
					{#each ['details', 'variants', 'prices'] as tab (tab)}
						{@const on = detailTab === tab}
						<button
							type="button"
							class="flex-1 rounded-[8px] px-2 py-[6px] text-[11px] capitalize transition-colors"
							style="font-family: var(--font-ui); background: {on
								? 'rgba(200,162,97,0.12)'
								: 'transparent'}; color: {on
								? 'var(--color-miru-gold)'
								: 'var(--color-miru-muted)'};"
							onclick={() => (detailTab = tab as typeof detailTab)}
						>
							{tab}
						</button>
					{/each}
				</div>

				{#if detailTab === 'details'}
					<dl class="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-[6px] text-[12px]">
						{#each [
							['Color', detail.color || '—'],
							['Type', detail.type || '—'],
							['Cost', detail.cost !== null ? String(detail.cost) : '—'],
							['Power', detail.power || '—'],
							['Counter', detail.counter && detail.counter !== '-' ? detail.counter : '—'],
							['Attribute', detail.attribute || '—'],
							['Archetype', detail.archetype.length ? detail.archetype.join(' · ') : '—']
						] as [k, v] (k)}
							<dt style="color: var(--color-miru-muted); font-family: var(--font-ui);">{k}</dt>
							<dd class="m-0" style="color: var(--color-miru-text); font-family: var(--font-ui);">{v}</dd>
						{/each}
					</dl>
					{#if detail.effect_text}
						<div
							class="mt-3 rounded-[10px] p-3 text-[12px] leading-[1.55]"
							style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke); color: var(--color-miru-text); font-family: var(--font-ui);"
						>
							{detail.effect_text}
						</div>
					{/if}
				{:else if detailTab === 'variants'}
					{#if detail.variants.length === 0}
						<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">No variants.</p>
					{:else}
						<div class="-mx-4 flex gap-2 overflow-x-auto px-4 pb-2">
							{#each detail.variants as v (v.variant_key + v.label)}
								<div
									class="flex w-[140px] shrink-0 flex-col overflow-hidden rounded-[10px]"
									style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke);"
								>
									<div class="aspect-[5/7]" style="background: {colorTokenFor(detail.color)};">
										{#if v.image_path}
											<img src={v.image_path} alt={v.label} class="h-full w-full object-cover" />
										{/if}
									</div>
									<div class="p-2">
										<div
											class="truncate text-[11px] font-semibold"
											style="color: var(--color-miru-text); font-family: var(--font-ui);"
										>
											{v.label}
										</div>
										<div
											class="text-[10px]"
											style="color: var(--color-miru-muted); font-family: 'JetBrains Mono', ui-monospace, monospace;"
										>
											{v.market_price !== null ? `$${v.market_price.toFixed(2)}` : '—'}
										</div>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				{:else}
					{@const basePrice = detail.variants.find((v) => v.variant_key === 'base')?.market_price ?? null}
					{#if basePrice !== null}
						<div
							class="rounded-[10px] p-3 text-[12px]"
							style="background: rgba(255,255,255,0.03); border: 1px solid var(--color-miru-stroke);"
						>
							<div style="color: var(--color-miru-muted); font-family: var(--font-ui);">Market price</div>
							<div
								class="text-[20px] font-bold"
								style="color: var(--color-miru-gold); font-family: 'JetBrains Mono', ui-monospace, monospace;"
							>
								${basePrice.toFixed(2)}
							</div>
						</div>
					{:else}
						<p class="m-0 text-[12px]" style="color: var(--color-miru-muted);">No price data</p>
					{/if}
				{/if}
			{:else}
				<p class="m-0 text-[12px]" style="color: var(--color-leader-red);">Card not found.</p>
			{/if}
		</div>

		<div
			class="absolute right-0 bottom-0 left-0 flex gap-2 px-4 pt-2 pb-4"
			style="background: rgba(12,10,20,0.96); border-top: 1px solid var(--color-miru-stroke);"
		>
			{#if detailCode}
				{@const inList = watchlist.has(detailCode)}
				<button
					type="button"
					class="flex-1 rounded-[12px] px-3 py-[10px] text-[12px] font-semibold transition-colors"
					style="background: {inList
						? 'rgba(200,162,97,0.12)'
						: 'rgba(255,255,255,0.04)'}; border: 1px solid {inList
						? 'rgba(200,162,97,0.35)'
						: 'var(--color-miru-stroke)'}; color: {inList
						? 'var(--color-miru-gold)'
						: 'var(--color-miru-text)'}; font-family: var(--font-ui);"
					onclick={() => toggleWatchlist(detailCode)}
				>
					{inList ? '♥ Watching' : '♥ Watchlist'}
				</button>
			{/if}
			<button
				type="button"
				class="flex-1 rounded-[12px] px-3 py-[10px] text-[12px] font-semibold"
				style="background: rgba(200,162,97,0.15); border: 1px solid rgba(200,162,97,0.4); color: var(--color-miru-gold); opacity: 0.5; font-family: var(--font-ui);"
				disabled
			>
				Add to Deck
			</button>
		</div>
	</div>
{/if}
