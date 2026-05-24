<script lang="ts">
	import { tick } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { fly, fade } from 'svelte/transition';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type QueueItem = NonNullable<PageData['items']>[number];

	interface FieldOutcome {
		field: string;
		tier: string;
		outcome: string;
		reason: string;
		primary_value: unknown;
		validator_value: unknown;
		catalog_value: unknown;
		bandai_value: unknown;
	}

	interface ItemDetail {
		canonical_code: string;
		print_id: string;
		contributing_model: string;
		readiness_state: string;
		approval_state: string;
		promotion_state: string;
		confidence_score: number;
		field_outcomes: FieldOutcome[];
		bandai_url: string | null;
		tcgplayer_url: string | null;
		image_url: string | null;
		full_image_url: string | null;
		image_source: 'variant_exact' | 'base_fallback' | 'missing';
	}

	interface LocalImage {
		filename: string;
		rel_path: string;
		url: string;
		size_bytes: number;
	}

	// Per-row verdict state
	let submitting = $state<Record<string, boolean>>({});
	let errors = $state<Record<string, string>>({});

	// Track which image URLs failed to load so we can fall back to the no-image
	// placeholder. The catalog can hold a stale image_path pointing at a file
	// that's been moved or renamed — the server-side existence check is a guard,
	// but this is the client-side safety net.
	let imageFailed = $state<Record<string, true>>({});
	function markImageFailed(url: string) {
		imageFailed[url] = true;
	}

	// Selected row + evidence panel state
	let selectedKey = $state<string | null>(null);
	let itemDetail = $state<ItemDetail | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);

	// Mobile sheet open/close. Desktop ignores this — the right pane is always visible.
	let mobileSheetOpen = $state(false);

	// Attach modal state
	let attachOpen = $state(false);
	let attachTab = $state<'local' | 'upload' | 'url'>('local');
	let attachBusy = $state(false);
	let attachError = $state<string | null>(null);
	let localImages = $state<LocalImage[] | null>(null);
	let localImagesLoading = $state(false);
	let urlInput = $state('');
	let fileInputEl = $state<HTMLInputElement | null>(null);

	function rowKey(item: QueueItem): string {
		return `${item.canonical_code}|${item.print_id}|${item.contributing_model}`;
	}

	const selectedItem = $derived(data.items?.find((i) => rowKey(i) === selectedKey) ?? null);

	// Prefer the freshly-fetched detail's image_url (it reflects a just-attached
	// image immediately) and fall back to the queue row's pre-resolved URL so the
	// hero thumb shows even before the detail GET finishes.
	const heroImageUrl = $derived(itemDetail?.image_url ?? selectedItem?.image_url ?? null);
	// Full-resolution URL used by the lightbox so the operator can actually read
	// the card text. Falls back to the thumb if no full art is available, but the
	// "showing base art" notice in the lightbox covers that case.
	const heroFullImageUrl = $derived(
		itemDetail?.full_image_url ??
			itemDetail?.image_url ??
			selectedItem?.full_image_url ??
			selectedItem?.image_url ??
			null
	);
	// The resolver's verdict on whether the shown art is the actual variant or a
	// base-art fallback. Surface this whenever we ended up displaying base.
	const heroImageSource = $derived(
		itemDetail?.image_source ?? selectedItem?.image_source ?? 'missing'
	);

	async function selectRow(item: QueueItem) {
		const key = rowKey(item);
		if (selectedKey === key && !isMobile()) {
			selectedKey = null;
			itemDetail = null;
			return;
		}
		selectedKey = key;
		itemDetail = null;
		detailError = null;
		detailLoading = true;
		mobileSheetOpen = isMobile();

		await tick();

		try {
			const params = new URLSearchParams({ contributing_model: item.contributing_model });
			const resp = await fetch(
				`/api/review/item/${encodeURIComponent(item.canonical_code)}/${encodeURIComponent(item.print_id)}?${params}`
			);
			if (!resp.ok) {
				const body = (await resp.json().catch(() => ({}))) as { error?: string };
				detailError = body.error ?? `Could not load evidence (HTTP ${resp.status}).`;
				return;
			}
			itemDetail = (await resp.json()) as ItemDetail;
		} catch {
			detailError = 'Could not reach the review service.';
		} finally {
			detailLoading = false;
		}
	}

	function isMobile(): boolean {
		return typeof window !== 'undefined' && window.innerWidth < 768;
	}

	function closeMobileSheet() {
		mobileSheetOpen = false;
		// On mobile the row deselects so the queue scroll position is restored;
		// on desktop the selection should persist because the right pane is visible.
		if (isMobile()) {
			selectedKey = null;
			itemDetail = null;
		}
	}

	async function submitVerdict(item: QueueItem, verdict: 'correct' | 'wrong') {
		const key = rowKey(item);
		submitting[key] = true;
		delete errors[key];
		try {
			const resp = await fetch(resolve('/review/verdict'), {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					canonical_code: item.canonical_code,
					print_id: item.print_id,
					contributing_model: item.contributing_model,
					verdict
				})
			});
			if (!resp.ok) {
				const body = (await resp.json().catch(() => ({}))) as { error?: string };
				errors[key] = body.error ?? `Verdict submission failed (HTTP ${resp.status}).`;
				return;
			}
			selectedKey = null;
			itemDetail = null;
			mobileSheetOpen = false;
			await invalidateAll();
		} catch {
			errors[key] = 'Could not reach the review service. Try again in a moment.';
		} finally {
			delete submitting[key];
		}
	}

	function readinessTextClass(state: string): string {
		if (state === 'blocked_by_guardrail') return 'text-negative';
		if (state === 'ready_for_review') return 'text-positive';
		return 'text-text-faint';
	}

	function approvalTextClass(state: string): string {
		if (state === 'approved_for_candidate') return 'text-positive';
		if (state === 'rejected') return 'text-negative';
		if (state === 'pending_review') return 'text-warning';
		return 'text-text-faint';
	}

	function readinessBorderClass(state: string): string {
		if (state === 'blocked_by_guardrail') return 'border-negative';
		if (state === 'ready_for_review') return 'border-positive';
		return 'border-border';
	}

	function approvalBorderClass(state: string): string {
		if (state === 'approved_for_candidate') return 'border-positive';
		if (state === 'rejected') return 'border-negative';
		if (state === 'pending_review') return 'border-warning';
		return 'border-border';
	}

	function outcomeTextClass(outcome: string): string {
		if (outcome === 'verified' || outcome === 'match') return 'text-positive';
		if (outcome === 'conflict') return 'text-negative';
		return 'text-warning';
	}

	function formatVal(val: unknown): string {
		if (val === null || val === undefined) return '—';
		const text = String(val).trim();
		return text === '' ? '—' : text;
	}

	// Translate a verifier's verdict shorthand into one of three plain-English buckets.
	// Catalog evidence collectors emit phrases like "verified-correct"/"verified-wrong",
	// while the matcher tier emits "match"/"conflict"/"inconclusive". Treat them all
	// as the same three categories from the operator's perspective.
	type AiCall = 'correct' | 'wrong' | 'unknown';
	function aiOutcome(outcome: string): AiCall {
		const o = (outcome || '').toLowerCase();
		if (o === 'match' || o === 'verified' || o === 'verified-correct' || o === 'agree')
			return 'correct';
		if (o === 'conflict' || o === 'verified-wrong' || o === 'override') return 'wrong';
		return 'unknown';
	}

	// Map raw schema field names to short, human-readable labels the operator
	// recognizes from the printed card face. Unknown fields fall back to a
	// title-cased version of the key (e.g. `set_release_block` → "Set release block").
	const FIELD_LABELS: Record<string, string> = {
		card_name: 'Name',
		name: 'Name',
		card_type: 'Type',
		type: 'Type',
		color: 'Color',
		colors: 'Color',
		rarity: 'Rarity',
		cost: 'Cost',
		power: 'Power',
		counter: 'Counter',
		attribute: 'Attribute',
		life: 'Life',
		effect_text: 'Effect',
		effect: 'Effect',
		trigger_text: 'Trigger',
		trigger: 'Trigger',
		feature: 'Feature',
		features: 'Feature',
		card_set_id: 'Set',
		set_id: 'Set',
		block_icon: 'Block',
		illustrator: 'Illustrator'
	};
	function humanFieldLabel(field: string): string {
		const key = (field || '').toLowerCase();
		if (FIELD_LABELS[key]) return FIELD_LABELS[key];
		return (field || '')
			.replace(/_/g, ' ')
			.replace(/^./, (c) => c.toUpperCase());
	}

	// "Truth" is whatever non-AI source we have most authority for. Catalog
	// (Bandai-sourced, hand-curated) wins; raw Bandai scrape is next; a generic
	// "validator" verdict is the last fallback. If none of them have a value the
	// AI's claim wasn't verifiable — that becomes an `unknown` row.
	function truthValue(outcome: FieldOutcome): unknown {
		const candidates: unknown[] = [outcome.catalog_value, outcome.bandai_value, outcome.validator_value];
		for (const c of candidates) {
			if (c === null || c === undefined) continue;
			const text = String(c).trim();
			if (text !== '') return c;
		}
		return null;
	}

	// Sort: AI was wrong first (these are what the operator most needs to see),
	// then AI was right, then anything we couldn't verify. Preserves original
	// order within each bucket so the trainer's emit order is stable.
	function sortedOutcomes(outcomes: FieldOutcome[]): FieldOutcome[] {
		const rank: Record<AiCall, number> = { wrong: 0, correct: 1, unknown: 2 };
		return [...outcomes].sort((a, b) => rank[aiOutcome(a.outcome)] - rank[aiOutcome(b.outcome)]);
	}

	// Counts feed the plain-English banner at the top of the evidence sheet.
	const aiCounts = $derived.by(() => {
		const out = itemDetail?.field_outcomes ?? [];
		let correct = 0;
		let wrong = 0;
		let unknown = 0;
		for (const o of out) {
			const call = aiOutcome(o.outcome);
			if (call === 'correct') correct++;
			else if (call === 'wrong') wrong++;
			else unknown++;
		}
		return { correct, wrong, unknown, total: out.length };
	});

	// The instructional banner copy. One sentence, no jargon. The Approve/Reject
	// terminology stays (operator preference) but is anchored to what each
	// button means in the AI-was-right vs AI-was-wrong framing.
	const verdictGuidance = $derived.by((): { headline: string; instruction: string; tone: 'good' | 'bad' | 'mixed' | 'unknown' } => {
		const { correct, wrong, unknown, total } = aiCounts;
		if (total === 0) {
			return {
				headline: 'No automated checks ran on this row.',
				instruction:
					'Compare to the card art on the left, then Approve if the AI got it right or Reject if it got it wrong.',
				tone: 'unknown'
			};
		}
		if (wrong === 0 && unknown === 0) {
			return {
				headline: `The AI matched the catalog on all ${total} field${total === 1 ? '' : 's'}.`,
				instruction:
					'Looks correct end-to-end. Tap Approve to promote the AI’s answer, or Reject if the card art tells you otherwise.',
				tone: 'good'
			};
		}
		if (correct === 0 && unknown === 0) {
			return {
				headline: `The AI disagreed with the catalog on all ${total} field${total === 1 ? '' : 's'}.`,
				instruction: 'Tap Reject — the AI got this card wrong.',
				tone: 'bad'
			};
		}
		const parts: string[] = [];
		if (wrong > 0) parts.push(`${wrong} wrong`);
		if (correct > 0) parts.push(`${correct} right`);
		if (unknown > 0) parts.push(`${unknown} couldn’t check`);
		return {
			headline: `Out of ${total} field${total === 1 ? '' : 's'}: ${parts.join(', ')}.`,
			instruction:
				'Tap Approve if the AI is right overall. Tap Reject if the disagreements below are real errors.',
			tone: 'mixed'
		};
	});

	// Fullscreen lightbox state — used so the operator can tap the hero thumb
	// and read the actual card values before deciding.
	let lightboxOpen = $state(false);
	function openLightbox() {
		if ((heroFullImageUrl || heroImageUrl) && !imageFailed[heroFullImageUrl ?? heroImageUrl ?? ''])
			lightboxOpen = true;
	}
	function closeLightbox() {
		lightboxOpen = false;
	}

	function formatBytes(b: number): string {
		if (b < 1024) return `${b} B`;
		if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
		return `${(b / (1024 * 1024)).toFixed(1)} MB`;
	}

	// ── Attach modal ──────────────────────────────────────────────────────────

	async function openAttach() {
		if (!selectedItem) return;
		attachOpen = true;
		attachTab = 'local';
		attachError = null;
		urlInput = '';
		await loadLocalImages();
	}

	function closeAttach() {
		attachOpen = false;
		attachBusy = false;
		attachError = null;
	}

	async function loadLocalImages() {
		if (!selectedItem) return;
		localImages = null;
		localImagesLoading = true;
		try {
			const resp = await fetch(
				`/api/review/local-images/${encodeURIComponent(selectedItem.canonical_code)}`
			);
			if (!resp.ok) {
				const body = (await resp.json().catch(() => ({}))) as { error?: string };
				attachError = body.error ?? `Could not list local images (HTTP ${resp.status}).`;
				localImages = [];
				return;
			}
			const body = (await resp.json()) as { items: LocalImage[] };
			localImages = body.items;
		} catch {
			attachError = 'Could not reach the review service.';
			localImages = [];
		} finally {
			localImagesLoading = false;
		}
	}

	async function attachLocal(img: LocalImage) {
		if (!selectedItem) return;
		await runAttach({ source: 'local', rel_path: img.rel_path });
	}

	async function attachUpload(event: Event) {
		event.preventDefault();
		if (!selectedItem) return;
		const file = fileInputEl?.files?.[0];
		if (!file) {
			attachError = 'Choose a file first.';
			return;
		}
		attachBusy = true;
		attachError = null;
		const form = new FormData();
		form.append('canonical_code', selectedItem.canonical_code);
		form.append('print_id', selectedItem.print_id);
		form.append('file', file);
		try {
			const resp = await fetch('/api/review/attach/upload', { method: 'POST', body: form });
			if (!resp.ok) {
				const body = (await resp.json().catch(() => ({}))) as { error?: string };
				attachError = body.error ?? `Upload failed (HTTP ${resp.status}).`;
				return;
			}
			await onAttachSuccess();
		} catch {
			attachError = 'Could not reach the review service.';
		} finally {
			attachBusy = false;
		}
	}

	async function attachFromUrl(event: Event) {
		event.preventDefault();
		if (!selectedItem) return;
		const url = urlInput.trim();
		if (!url) {
			attachError = 'Paste an image URL first.';
			return;
		}
		await runAttach({ source: 'url', url });
	}

	async function runAttach(body: Record<string, unknown>) {
		if (!selectedItem) return;
		attachBusy = true;
		attachError = null;
		try {
			const resp = await fetch('/api/review/attach', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					canonical_code: selectedItem.canonical_code,
					print_id: selectedItem.print_id,
					...body
				})
			});
			if (!resp.ok) {
				const errBody = (await resp.json().catch(() => ({}))) as { error?: string };
				attachError = errBody.error ?? `Attach failed (HTTP ${resp.status}).`;
				return;
			}
			await onAttachSuccess();
		} catch {
			attachError = 'Could not reach the review service.';
		} finally {
			attachBusy = false;
		}
	}

	async function onAttachSuccess() {
		// Refresh both the queue (so the row's thumb reflects the new image) and
		// the open detail (so the hero image updates without a second tap).
		closeAttach();
		const item = selectedItem;
		await invalidateAll();
		if (item) {
			// Re-fetch detail manually because invalidateAll only re-runs the load fn.
			try {
				const params = new URLSearchParams({ contributing_model: item.contributing_model });
				const resp = await fetch(
					`/api/review/item/${encodeURIComponent(item.canonical_code)}/${encodeURIComponent(item.print_id)}?${params}`
				);
				if (resp.ok) {
					itemDetail = (await resp.json()) as ItemDetail;
				}
			} catch {
				// Non-fatal — queue invalidation already updated the row thumb.
			}
		}
	}
</script>

<main class="mx-auto max-w-6xl p-3 sm:p-5 md:flex md:h-[calc(100dvh-61px)] md:flex-col md:overflow-hidden">
	<h1 class="mb-4 shrink-0 font-sans text-lg font-semibold text-text sm:text-xl">Review</h1>

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
		<div
			class="grid min-h-0 flex-1 grid-cols-1 gap-4 md:auto-rows-fr md:grid-cols-[minmax(0,3fr)_minmax(0,4fr)]"
		>
			<!-- ───── Queue (left on desktop, full-width on mobile) ───── -->
			<section
				aria-label="Review queue"
				data-testid="review-queue"
				class="md:flex md:min-h-0 md:flex-col"
			>
				<h2 class="mb-2 shrink-0 font-mono text-xs uppercase tracking-widest text-text-faint">
					Queue {data.items ? `(${data.items.length})` : ''}
				</h2>
				{#if !data.items || data.items.length === 0}
					<div class="rounded border border-dashed border-border p-6 text-center">
						<p class="text-sm text-text-faint" data-testid="queue-empty">Queue is empty.</p>
					</div>
				{:else}
					<ul class="flex flex-col gap-2 md:flex-1 md:overflow-y-auto md:pr-1" role="list">
						{#each data.items as item (rowKey(item))}
							{@const key = rowKey(item)}
							{@const selected = selectedKey === key}
							<li role="listitem">
								<button
									class="group block w-full rounded border text-left transition-colors {selected
										? 'border-accent bg-surface2'
										: 'border-border bg-surface hover:bg-surface2'}"
									onclick={() => selectRow(item)}
									aria-pressed={selected}
									data-testid="queue-row-{item.canonical_code}"
								>
									<div class="flex gap-3 p-2.5">
										<!-- Thumb / no-image badge -->
										<div
											class="relative flex h-[88px] w-[64px] shrink-0 items-center justify-center overflow-hidden rounded border border-border bg-bg sm:h-[100px] sm:w-[72px]"
											data-testid="queue-thumb-{item.canonical_code}"
										>
											{#if item.image_url && !imageFailed[item.image_url]}
												<img
													src={item.image_url}
													alt=""
													loading="lazy"
													onerror={() => markImageFailed(item.image_url!)}
													class="h-full w-full object-cover"
												/>
												{#if item.image_source === 'base_fallback'}
													<span
														class="pointer-events-none absolute inset-x-0 bottom-0 bg-warning/90 px-1 py-[1px] text-center font-mono text-[8px] uppercase tracking-wide text-bg"
														title="Variant image missing — showing base art"
														data-testid="queue-base-fallback-{item.canonical_code}"
													>base art</span>
												{/if}
											{:else}
												<div
													class="flex h-full w-full flex-col items-center justify-center px-1 text-center"
													data-testid="queue-no-image-{item.canonical_code}"
												>
													<span
														class="font-mono text-[10px] uppercase tracking-wide text-warning"
														>no img</span
													>
													<span class="mt-0.5 break-all font-mono text-[9px] text-text-faint"
														>{item.canonical_code}</span
													>
												</div>
											{/if}
										</div>

										<!-- Right column: code + states + confidence -->
										<div class="flex min-w-0 flex-1 flex-col justify-between gap-1.5">
											<div class="flex items-start justify-between gap-2">
												<span
													class="truncate font-mono text-sm font-medium text-text sm:text-base"
													>{item.canonical_code}</span
												>
												<span
													class="shrink-0 font-mono text-[11px] text-text-faint"
													title="confidence score"
												>
													{item.confidence_score.toFixed(2)}
												</span>
											</div>
											<div
												class="truncate font-mono text-[11px] text-text-dim"
												title="{item.print_id} · {item.contributing_model}"
											>
												{item.print_id} · {item.contributing_model}
											</div>
											<div class="flex flex-wrap gap-1">
												<span
													class="rounded border px-1.5 py-0.5 font-mono text-[10px] {readinessTextClass(
														item.readiness_state
													)} {readinessBorderClass(item.readiness_state)}"
												>
													{item.readiness_state.replace(/_/g, ' ')}
												</span>
												<span
													class="rounded border px-1.5 py-0.5 font-mono text-[10px] {approvalTextClass(
														item.approval_state
													)} {approvalBorderClass(item.approval_state)}"
												>
													{item.approval_state.replace(/_/g, ' ')}
												</span>
												{#if item.inconclusive_field_count > 0}
													<span
														class="rounded border border-warning px-1.5 py-0.5 font-mono text-[10px] text-warning"
													>
														{item.inconclusive_field_count} inconclusive
													</span>
												{/if}
												{#if !item.image_url || imageFailed[item.image_url]}
													<span
														class="rounded border border-warning bg-warning/10 px-1.5 py-0.5 font-mono text-[10px] text-warning"
														title="no card image on file"
													>
														no image
													</span>
												{/if}
											</div>
										</div>
									</div>
								</button>
								{#if errors[key]}
									<p
										role="alert"
										data-testid="verdict-error"
										class="mt-1 px-3 text-xs text-negative"
									>
										{errors[key]}
									</p>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<!-- ───── Evidence panel (right on desktop, hidden on mobile until sheet opens) ───── -->
			<section
				aria-label="Evidence panel"
				data-testid="evidence-panel"
				class="hidden md:block md:h-full md:overflow-y-auto"
			>
				{#if !selectedItem}
					<div class="flex h-48 items-center justify-center rounded border border-border bg-surface">
						<p class="text-sm text-text-faint">Select a queue row to inspect its evidence.</p>
					</div>
				{:else}
					{@render evidenceBody(false)}
				{/if}
			</section>
		</div>
	{/if}
</main>

<!-- ───── Mobile evidence sheet ───── -->
{#if mobileSheetOpen && selectedItem}
	<div
		class="fixed inset-0 z-30 md:hidden"
		role="dialog"
		aria-modal="true"
		aria-label="Evidence for {selectedItem.canonical_code}"
		data-testid="mobile-evidence-sheet"
	>
		<div
			class="absolute inset-0 bg-black/60"
			transition:fade={{ duration: 150 }}
			onclick={closeMobileSheet}
			onkeydown={(e) => e.key === 'Escape' && closeMobileSheet()}
			role="button"
			tabindex="-1"
			aria-label="Dismiss"
		></div>
		<div
			class="absolute inset-x-0 bottom-0 top-12 flex flex-col rounded-t-xl border-t border-border bg-bg shadow-2xl"
			transition:fly={{ y: 400, duration: 220 }}
		>
			<div class="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
				<span class="font-mono text-sm text-text-dim">{selectedItem.canonical_code}</span>
				<button
					type="button"
					class="rounded border border-border px-2 py-1 font-mono text-xs text-text-dim hover:bg-surface2"
					onclick={closeMobileSheet}
					aria-label="Close evidence"
				>Close</button>
			</div>
			{@render evidenceBody(true)}
		</div>
	</div>
{/if}

<!-- ───── Lightbox: tap the hero thumb to read the card art ───── -->
{#if lightboxOpen && (heroFullImageUrl || heroImageUrl)}
	{@const lightboxSrc = heroFullImageUrl ?? heroImageUrl}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6"
		role="dialog"
		aria-modal="true"
		aria-label="Card art lightbox"
		data-testid="lightbox"
	>
		<div
			class="absolute inset-0 bg-black/85"
			transition:fade={{ duration: 120 }}
			onclick={closeLightbox}
			onkeydown={(e) => e.key === 'Escape' && closeLightbox()}
			role="button"
			tabindex="-1"
			aria-label="Close"
		></div>
		<button
			type="button"
			onclick={closeLightbox}
			class="absolute right-3 top-3 z-10 rounded border border-border bg-surface px-3 py-1 font-mono text-xs text-text-dim hover:bg-surface2"
			data-testid="lightbox-close"
			aria-label="Close lightbox"
		>Close</button>
		{#if heroImageSource === 'base_fallback'}
			<div
				class="absolute left-3 top-3 z-10 max-w-[80vw] rounded border border-warning bg-surface px-3 py-1.5 text-xs text-warning"
				data-testid="lightbox-base-fallback-notice"
			>
				Showing base card art — variant image not on file.
			</div>
		{/if}
		<img
			src={lightboxSrc}
			alt="{selectedItem?.canonical_code ?? ''} full card art"
			class="relative max-h-[92dvh] max-w-[92vw] rounded border border-border object-contain shadow-2xl"
			transition:fade={{ duration: 120 }}
		/>
	</div>
{/if}

<!-- ───── Attach-image modal ───── -->
{#if attachOpen && selectedItem}
	<div
		class="fixed inset-0 z-40 flex items-end justify-center p-0 sm:items-center sm:p-4"
		role="dialog"
		aria-modal="true"
		aria-label="Attach image to {selectedItem.canonical_code}"
		data-testid="attach-modal"
	>
		<div
			class="absolute inset-0 bg-black/60"
			transition:fade={{ duration: 150 }}
			onclick={closeAttach}
			onkeydown={(e) => e.key === 'Escape' && closeAttach()}
			role="button"
			tabindex="-1"
			aria-label="Dismiss"
		></div>
		<div
			class="relative flex max-h-[85dvh] w-full flex-col rounded-t-xl border-t border-border bg-bg shadow-2xl sm:max-w-2xl sm:rounded-xl sm:border"
			transition:fly={{ y: 200, duration: 200 }}
		>
			<div class="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
				<span class="font-mono text-sm text-text">
					Attach image —
					<span class="text-text-dim">{selectedItem.canonical_code}</span>
				</span>
				<button
					type="button"
					class="rounded border border-border px-2 py-1 font-mono text-xs text-text-dim hover:bg-surface2"
					onclick={closeAttach}
					aria-label="Close"
				>Close</button>
			</div>

			<!-- Tabs -->
			<div class="flex shrink-0 border-b border-border" role="tablist">
				{#each ['local', 'upload', 'url'] as const as tab (tab)}
					<button
						type="button"
						role="tab"
						aria-selected={attachTab === tab}
						class="flex-1 border-b-2 px-3 py-2 font-mono text-xs uppercase tracking-widest transition-colors {attachTab ===
						tab
							? 'border-accent text-accent'
							: 'border-transparent text-text-dim hover:text-text'}"
						onclick={() => (attachTab = tab)}
						data-testid="attach-tab-{tab}"
					>
						{tab === 'local' ? 'Local files' : tab === 'upload' ? 'Upload' : 'URL'}
					</button>
				{/each}
			</div>

			<!-- Tab body -->
			<div class="min-h-0 flex-1 overflow-y-auto p-3">
				{#if attachTab === 'local'}
					{#if localImagesLoading}
						<p class="text-sm text-text-faint" data-testid="local-images-loading">
							Scanning OPTCG_Images…
						</p>
					{:else if localImages && localImages.length === 0}
						<p class="text-sm text-text-faint" data-testid="local-images-empty">
							No files in <code class="font-mono">D:/OPTCG_Images</code> match this code. Try Upload or
							URL.
						</p>
					{:else if localImages}
						<ul class="grid grid-cols-2 gap-2 sm:grid-cols-3" data-testid="local-images-list">
							{#each localImages as img (img.rel_path)}
								<li>
									<button
										type="button"
										disabled={attachBusy}
										onclick={() => attachLocal(img)}
										class="group block w-full overflow-hidden rounded border border-border bg-surface text-left hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
										data-testid="local-image-{img.filename}"
									>
										<div class="aspect-[2/3] bg-bg">
											<img
												src={img.url}
												alt={img.filename}
												loading="lazy"
												class="h-full w-full object-cover"
											/>
										</div>
										<div class="px-2 py-1.5">
											<p class="truncate font-mono text-[11px] text-text" title={img.filename}>
												{img.filename}
											</p>
											<p class="font-mono text-[10px] text-text-faint">{formatBytes(img.size_bytes)}</p>
										</div>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				{:else if attachTab === 'upload'}
					<form onsubmit={attachUpload} class="space-y-3" data-testid="attach-upload-form">
						<label class="block">
							<span class="mb-1 block font-mono text-xs uppercase tracking-widest text-text-faint">
								Image file
							</span>
							<input
								type="file"
								accept="image/jpeg,image/png,image/webp"
								bind:this={fileInputEl}
								class="block w-full text-sm text-text file:mr-3 file:rounded file:border file:border-border file:bg-surface file:px-3 file:py-1.5 file:font-mono file:text-xs file:text-text-dim file:hover:bg-surface2"
							/>
						</label>
						<p class="font-mono text-[11px] text-text-faint">
							Max 25 MB. JPG/PNG/WebP. Lands in
							<code class="font-mono"
								>OPTCG_Images/{selectedItem.canonical_code.split('-', 1)[0]}/uploads/</code
							>.
						</p>
						<button
							type="submit"
							disabled={attachBusy}
							class="rounded border border-accent px-3 py-1.5 font-mono text-xs text-accent hover:bg-accent hover:text-bg disabled:cursor-not-allowed disabled:opacity-50"
							data-testid="attach-upload-submit"
						>
							{attachBusy ? 'Uploading…' : 'Upload + attach'}
						</button>
					</form>
				{:else}
					<form onsubmit={attachFromUrl} class="space-y-3" data-testid="attach-url-form">
						<label class="block">
							<span class="mb-1 block font-mono text-xs uppercase tracking-widest text-text-faint">
								Image URL
							</span>
							<input
								type="url"
								bind:value={urlInput}
								placeholder="https://en.onepiece-cardgame.com/images/cardlist/card/OP01-001.png"
								class="block w-full rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs text-text focus:border-accent focus:outline-none"
							/>
						</label>
						<button
							type="submit"
							disabled={attachBusy}
							class="rounded border border-accent px-3 py-1.5 font-mono text-xs text-accent hover:bg-accent hover:text-bg disabled:cursor-not-allowed disabled:opacity-50"
							data-testid="attach-url-submit"
						>
							{attachBusy ? 'Downloading…' : 'Download + attach'}
						</button>
					</form>
				{/if}

				{#if attachError}
					<p class="mt-3 text-xs text-negative" role="alert" data-testid="attach-error">
						{attachError}
					</p>
				{/if}
			</div>
		</div>
	</div>
{/if}

<!-- ───── Shared evidence-body snippet (used by both desktop pane and mobile sheet) ───── -->
{#snippet evidenceBody(onSheet: boolean)}
	{#if selectedItem}
		{@const verdictKey = rowKey(selectedItem)}
		{@const verdictBusy = submitting[verdictKey] === true}
		<div
			class="flex h-full flex-col {onSheet
				? ''
				: 'rounded border border-border bg-surface'} min-h-0"
		>
			<div class="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
				<!-- Hero: thumb + code + bandai/tcg buttons -->
				<div class="mb-4 flex gap-3">
					<div
						class="relative flex h-[140px] w-[100px] shrink-0 items-center justify-center overflow-hidden rounded border border-border bg-bg sm:h-[168px] sm:w-[120px]"
						data-testid="evidence-thumb"
					>
						{#if heroImageUrl && !imageFailed[heroImageUrl]}
							<button
								type="button"
								onclick={openLightbox}
								aria-label="Open full-size card art for {selectedItem.canonical_code}"
								class="block h-full w-full cursor-zoom-in border-0 bg-transparent p-0"
								data-testid="evidence-thumb-button"
							>
								<img
									src={heroImageUrl}
									alt=""
									onerror={() => markImageFailed(heroImageUrl!)}
									class="h-full w-full object-cover"
								/>
								{#if heroImageSource === 'base_fallback'}
									<span
										class="pointer-events-none absolute inset-x-0 bottom-0 bg-warning/90 px-1 py-[1px] text-center font-mono text-[9px] uppercase tracking-wide text-bg"
										data-testid="evidence-base-fallback"
									>base art</span>
								{/if}
							</button>
						{:else}
							<div
								class="flex h-full w-full flex-col items-center justify-center px-2 text-center"
								data-testid="evidence-no-image"
							>
								<span class="font-mono text-xs uppercase tracking-wide text-warning">no image</span>
								<span class="mt-1 break-all font-mono text-[10px] text-text-faint"
									>{selectedItem.canonical_code}</span
								>
							</div>
						{/if}
					</div>
					<div class="flex min-w-0 flex-1 flex-col justify-between gap-2">
						<div>
							<h2 class="truncate font-mono text-base font-medium text-text">
								{selectedItem.canonical_code}
							</h2>
							<p class="mt-0.5 truncate font-mono text-xs text-text-dim">
								{selectedItem.print_id} · {selectedItem.contributing_model}
							</p>
						</div>
						<div class="flex flex-wrap gap-1.5">
							<button
								type="button"
								onclick={openAttach}
								class="rounded border border-accent px-2 py-0.5 font-mono text-xs text-accent hover:bg-accent hover:text-bg"
								data-testid="open-attach"
							>
								{heroImageUrl && !imageFailed[heroImageUrl] ? 'Replace image' : 'Attach image'}
							</button>
							{#if itemDetail?.bandai_url}
								<a
									href={itemDetail.bandai_url}
									target="_blank"
									rel="noopener noreferrer"
									data-testid="bandai-link"
									class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:border-accent hover:text-accent"
								>Bandai</a>
							{/if}
							{#if itemDetail?.tcgplayer_url}
								<a
									href={itemDetail.tcgplayer_url}
									target="_blank"
									rel="noopener noreferrer"
									data-testid="tcgplayer-link"
									class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:border-accent hover:text-accent"
								>TCGPlayer</a>
							{/if}
						</div>
					</div>
				</div>

				<!-- Plain-English verdict banner: tells the operator what they're being
				     asked, in their language, before any state chips or schema rows. -->
				{#if itemDetail && !detailLoading && !detailError}
					<div
						class="mb-3 rounded border p-3 {verdictGuidance.tone === 'good'
							? 'border-positive bg-positive/5'
							: verdictGuidance.tone === 'bad'
								? 'border-negative bg-negative/5'
								: verdictGuidance.tone === 'mixed'
									? 'border-warning bg-warning/5'
									: 'border-border bg-surface2'}"
						data-testid="verdict-banner"
					>
						<p
							class="text-sm font-semibold {verdictGuidance.tone === 'good'
								? 'text-positive'
								: verdictGuidance.tone === 'bad'
									? 'text-negative'
									: verdictGuidance.tone === 'mixed'
										? 'text-warning'
										: 'text-text'}"
							data-testid="verdict-headline"
						>
							{verdictGuidance.headline}
						</p>
						<p class="mt-1 text-xs text-text-dim" data-testid="verdict-instruction">
							{verdictGuidance.instruction}
						</p>
					</div>
				{/if}

				<!-- Compact pipeline-state chips. The operator can ignore these most
				     of the time — they're for diagnosing why a row landed in the
				     queue, not for the approve/reject decision itself. -->
				<details
					class="mb-4 rounded border border-border bg-surface2"
					data-testid="pipeline-state-details"
				>
					<summary
						class="cursor-pointer px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-text-faint hover:text-text"
					>
						Pipeline state
					</summary>
					<div class="grid grid-cols-3 gap-2 px-3 pb-2 pt-1">
						<div>
							<span
								class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
								>Readiness</span
							>
							<span
								class="block truncate font-mono text-xs {readinessTextClass(selectedItem.readiness_state)}"
								>{selectedItem.readiness_state.replace(/_/g, ' ')}</span
							>
						</div>
						<div>
							<span
								class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
								>Approval</span
							>
							<span
								class="block truncate font-mono text-xs {approvalTextClass(selectedItem.approval_state)}"
								>{selectedItem.approval_state.replace(/_/g, ' ')}</span
							>
						</div>
						<div>
							<span
								class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
								>Promotion</span
							>
							<span class="block truncate font-mono text-xs text-text-dim"
								>{(selectedItem.promotion_state || '—').replace(/_/g, ' ')}</span
							>
						</div>
					</div>
				</details>

				<!-- AI Said / Truth comparison — replaces the raw schema table. One
				     row per field, sorted with disagreements first so the operator
				     looks at what likely matters before scrolling. -->
				{#if detailLoading}
					<div class="animate-pulse space-y-3" data-testid="evidence-loading">
						<div class="h-4 rounded bg-surface2"></div>
						<div class="h-4 w-3/4 rounded bg-surface2"></div>
						<div class="h-4 rounded bg-surface2"></div>
						<div class="h-4 w-1/2 rounded bg-surface2"></div>
						<div class="h-16 rounded bg-surface2"></div>
					</div>
				{:else if detailError}
					<p class="text-sm text-negative" data-testid="evidence-error">{detailError}</p>
				{:else if itemDetail && itemDetail.field_outcomes.length > 0}
					<div data-testid="field-outcomes">
						<h3 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">
							What the AI said vs. the catalog
						</h3>
						<ul class="space-y-1.5" role="list">
							{#each sortedOutcomes(itemDetail.field_outcomes) as outcome (outcome.field)}
								{@const call = aiOutcome(outcome.outcome)}
								<li
									class="rounded border p-2.5 {call === 'wrong'
										? 'border-negative/40 bg-negative/5'
										: call === 'correct'
											? 'border-positive/30 bg-positive/5'
											: 'border-border bg-surface2'}"
									data-testid="field-row-{outcome.field}"
									data-call={call}
								>
									<div class="mb-1 flex items-start justify-between gap-2">
										<span
											class="font-mono text-xs font-medium text-text"
											data-testid="field-label-{outcome.field}"
											>{humanFieldLabel(outcome.field)}</span
										>
										<span
											class="shrink-0 font-mono text-[10px] uppercase tracking-wider {call ===
											'wrong'
												? 'text-negative'
												: call === 'correct'
													? 'text-positive'
													: 'text-warning'}"
										>
											{call === 'wrong'
												? '✗ AI wrong'
												: call === 'correct'
													? '✓ AI right'
													: '? unverified'}
										</span>
									</div>
									<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-xs">
										<dt class="font-mono text-[10px] uppercase tracking-wider text-text-faint">
											AI said
										</dt>
										<dd
											class="break-words font-mono {call === 'wrong'
												? 'text-negative'
												: 'text-text'}"
										>
											{formatVal(outcome.primary_value)}
										</dd>
										<dt class="font-mono text-[10px] uppercase tracking-wider text-text-faint">
											Truth
										</dt>
										<dd
											class="break-words font-mono {call === 'unknown'
												? 'text-text-faint'
												: 'text-text-dim'}"
										>
											{formatVal(truthValue(outcome))}
										</dd>
									</dl>
									{#if outcome.reason}
										<p
											class="mt-1.5 border-t border-border/40 pt-1.5 font-mono text-[10px] italic text-text-faint"
										>
											{outcome.reason}
										</p>
									{/if}
								</li>
							{/each}
						</ul>
					</div>
				{:else if itemDetail}
					<p class="text-sm text-text-faint" data-testid="no-field-evidence">
						No field evidence recorded for this row.
					</p>
				{/if}
			</div>

			<!-- Verdict bar — pinned to bottom -->
			<div
				class="shrink-0 border-t border-border bg-surface px-3 py-2 sm:px-4 sm:py-3"
				data-testid="verdict-actions"
			>
				<div class="flex flex-wrap items-center gap-2">
					<button
						type="button"
						disabled={verdictBusy}
						data-testid="verdict-approve"
						class="flex-1 rounded border border-positive px-3 py-2 font-mono text-xs font-semibold text-positive hover:bg-positive hover:text-bg disabled:cursor-not-allowed disabled:opacity-40 sm:flex-none"
						onclick={() => submitVerdict(selectedItem, 'correct')}
					>Approve</button>
					<button
						type="button"
						disabled={verdictBusy}
						data-testid="verdict-reject"
						class="flex-1 rounded border border-negative px-3 py-2 font-mono text-xs font-semibold text-negative hover:bg-negative hover:text-bg disabled:cursor-not-allowed disabled:opacity-40 sm:flex-none"
						onclick={() => submitVerdict(selectedItem, 'wrong')}
					>Reject</button>
					{#if verdictBusy}
						<span class="font-mono text-xs text-text-faint">Submitting…</span>
					{/if}
				</div>
			</div>
		</div>
	{/if}
{/snippet}
