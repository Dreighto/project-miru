<script lang="ts">
	import { tick } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { resolve } from '$app/paths';
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
	}

	// Per-row verdict state
	let submitting = $state<Record<string, boolean>>({});
	let errors = $state<Record<string, string>>({});

	// Selected row + evidence panel state
	let selectedKey = $state<string | null>(null);
	let itemDetail = $state<ItemDetail | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);

	// Ref for mobile scroll-into-view
	let evidencePanelEl = $state<HTMLElement | null>(null);

	function rowKey(item: QueueItem): string {
		return `${item.canonical_code}|${item.print_id}|${item.contributing_model}`;
	}

	const selectedItem = $derived(
		data.items?.find((i) => rowKey(i) === selectedKey) ?? null
	);

	async function selectRow(item: QueueItem) {
		const key = rowKey(item);
		if (selectedKey === key) {
			selectedKey = null;
			itemDetail = null;
			return;
		}
		selectedKey = key;
		itemDetail = null;
		detailError = null;
		detailLoading = true;

		// Mobile: scroll evidence panel into view after DOM updates
		await tick();
		if (typeof window !== 'undefined' && window.innerWidth < 768) {
			evidencePanelEl?.scrollIntoView({ behavior: 'smooth' });
		}

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
		return String(val);
	}
</script>

<main
	class="mx-auto max-w-5xl p-6 md:flex md:h-[calc(100dvh-49px)] md:flex-col md:overflow-hidden"
>
	<h1 class="mb-6 shrink-0 font-sans text-xl font-semibold text-text">Review</h1>

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
			class="grid min-h-0 flex-1 grid-cols-1 gap-4 md:auto-rows-fr md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
		>
			<!-- Queue list -->
			<section
				aria-label="Review queue"
				data-testid="review-queue"
				class="md:flex md:min-h-0 md:flex-col"
			>
				<h2 class="mb-3 shrink-0 font-mono text-xs uppercase tracking-widest text-text-faint">
					Queue {data.items ? `(${data.items.length})` : ''}
				</h2>
				{#if !data.items || data.items.length === 0}
					<div class="rounded border border-dashed border-border p-6 text-center">
						<p class="text-sm text-text-faint" data-testid="queue-empty">Queue is empty.</p>
					</div>
				{:else}
					<ul class="flex flex-col gap-1 md:flex-1 md:overflow-y-auto" role="list">
						{#each data.items as item (rowKey(item))}
							{@const key = rowKey(item)}
							{@const selected = selectedKey === key}
							<li role="listitem">
								<button
									class="w-full rounded border text-left transition-colors {selected
										? 'border-accent bg-surface2'
										: 'border-border bg-surface hover:bg-surface2'}"
									onclick={() => selectRow(item)}
									aria-pressed={selected}
									data-testid="queue-row-{item.canonical_code}"
								>
									<div class="p-2">
										<div class="mb-1.5 flex items-center justify-between gap-2">
											<span class="font-mono text-sm font-medium text-text"
												>{item.canonical_code}</span
											>
											<span class="font-mono text-xs text-text-faint"
												>{item.confidence_score.toFixed(2)}</span
											>
										</div>
										<div class="mb-2 font-mono text-xs text-text-dim">
											{item.print_id} · {item.contributing_model}
										</div>
										<div class="flex flex-wrap gap-1.5">
											<span
												class="rounded border px-1.5 py-0.5 font-mono text-[10px] {readinessTextClass(item.readiness_state)} {readinessBorderClass(item.readiness_state)}"
											>
												{item.readiness_state}
											</span>
											<span
												class="rounded border px-1.5 py-0.5 font-mono text-[10px] {approvalTextClass(item.approval_state)} {approvalBorderClass(item.approval_state)}"
											>
												{item.approval_state}
											</span>
											{#if item.inconclusive_field_count > 0}
												<span
													class="rounded border border-warning px-1.5 py-0.5 font-mono text-[10px] text-warning"
												>
													{item.inconclusive_field_count} inconclusive
												</span>
											{/if}
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

			<!-- Evidence panel -->
			<section
				aria-label="Evidence panel"
				data-testid="evidence-panel"
				class="md:h-full md:overflow-y-auto"
				bind:this={evidencePanelEl}
			>
				{#if !selectedItem}
					<div
						class="flex h-48 items-center justify-center rounded border border-border bg-surface"
					>
						<p class="text-sm text-text-faint">Select a queue row to inspect its evidence.</p>
					</div>
				{:else}
					<div class="rounded border border-border bg-surface">
						<!-- Scrollable evidence content -->
						<div class="p-4">
							<!-- Card header -->
							<div class="mb-4 flex items-start justify-between gap-4">
								<div>
									<h2 class="font-mono text-base font-medium text-text">
										{selectedItem.canonical_code}
									</h2>
									<p class="font-mono text-xs text-text-dim">
										{selectedItem.print_id} · {selectedItem.contributing_model}
									</p>
								</div>
								{#if itemDetail}
									<div class="flex shrink-0 gap-2">
										{#if itemDetail.bandai_url}
											<a
												href={itemDetail.bandai_url}
												target="_blank"
												rel="noopener noreferrer"
												data-testid="bandai-link"
												class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:border-accent hover:text-accent"
											>Bandai</a>
										{/if}
										{#if itemDetail.tcgplayer_url}
											<a
												href={itemDetail.tcgplayer_url}
												target="_blank"
												rel="noopener noreferrer"
												data-testid="tcgplayer-link"
												class="rounded border border-border px-2 py-0.5 font-mono text-xs text-text-dim hover:border-accent hover:text-accent"
											>TCGPlayer</a>
										{/if}
									</div>
								{/if}
							</div>

							<!-- Three-axis state -->
							<div class="mb-4 flex flex-wrap gap-2">
								<div class="rounded bg-surface2 px-2 py-1">
									<span
										class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
										>Readiness</span
									>
									<span
										class="font-mono text-xs {readinessTextClass(selectedItem.readiness_state)}"
										>{selectedItem.readiness_state}</span
									>
								</div>
								<div class="rounded bg-surface2 px-2 py-1">
									<span
										class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
										>Approval</span
									>
									<span
										class="font-mono text-xs {approvalTextClass(selectedItem.approval_state)}"
										>{selectedItem.approval_state}</span
									>
								</div>
								<div class="rounded bg-surface2 px-2 py-1">
									<span
										class="mb-0.5 block font-mono text-[10px] uppercase tracking-wider text-text-faint"
										>Promotion</span
									>
									<span class="font-mono text-xs text-text-dim"
										>{selectedItem.promotion_state || '(none)'}</span
									>
								</div>
							</div>

							<!-- Field evidence -->
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
										Field Evidence
									</h3>
									<div class="overflow-x-auto">
										<table class="w-full text-xs">
											<thead>
												<tr class="border-b border-border text-left">
													<th class="pb-1.5 pr-3 font-mono font-medium text-text-faint">Field</th>
													<th class="pb-1.5 pr-3 font-mono font-medium text-text-faint">Trainer</th>
													<th class="pb-1.5 pr-3 font-mono font-medium text-text-faint"
														>Verifier</th
													>
													<th class="pb-1.5 pr-3 font-mono font-medium text-text-faint">Catalog</th>
													<th class="pb-1.5 pr-3 font-mono font-medium text-text-faint">Bandai</th>
													<th class="pb-1.5 font-mono font-medium text-text-faint">Result</th>
												</tr>
											</thead>
											<tbody>
												{#each itemDetail.field_outcomes as outcome (outcome.field)}
													<tr
														class="border-b border-surface2 even:bg-surface2/30 hover:bg-surface2/50"
													>
														<td class="py-1.5 pr-3 font-mono text-text-dim">{outcome.field}</td>
														<td class="py-1.5 pr-3 font-mono text-text"
															>{formatVal(outcome.primary_value)}</td
														>
														<td class="py-1.5 pr-3 font-mono text-text"
															>{formatVal(outcome.validator_value)}</td
														>
														<td class="py-1.5 pr-3 font-mono text-text-dim"
															>{formatVal(outcome.catalog_value)}</td
														>
														<td class="py-1.5 pr-3 font-mono text-text-dim"
															>{formatVal(outcome.bandai_value)}</td
														>
														<td class="py-1.5">
															<span class="font-mono {outcomeTextClass(outcome.outcome)}"
																>{outcome.outcome}</span
															>
														</td>
													</tr>
													{#if outcome.reason}
														<tr class="border-b border-surface2 bg-surface2">
															<td class="py-1 pr-3 font-mono italic text-text-faint" colspan={6}>
																{outcome.reason}
															</td>
														</tr>
													{/if}
												{/each}
											</tbody>
										</table>
									</div>
								</div>
							{:else if itemDetail}
								<p class="text-sm text-text-faint" data-testid="no-field-evidence">
									No field evidence recorded for this row.
								</p>
							{/if}
						</div>

						<!-- Verdict bar: sticky to bottom of the scrollable evidence section -->
						{#if selectedItem}
							{@const key = rowKey(selectedItem)}
							{@const busy = submitting[key] === true}
							<div
								class="sticky bottom-0 border-t border-border bg-surface px-4 pb-4 pt-4"
								data-testid="verdict-actions"
							>
								<h3 class="mb-2 font-mono text-xs uppercase tracking-widest text-text-faint">
									Verdict
								</h3>
								<div class="flex items-center gap-2">
									<button
										type="button"
										disabled={busy}
										data-testid="verdict-approve"
										class="rounded border border-positive px-3 py-1.5 font-mono text-xs text-positive hover:bg-positive hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
										onclick={() => submitVerdict(selectedItem, 'correct')}
									>Approve</button>
									<button
										type="button"
										disabled={busy}
										data-testid="verdict-reject"
										class="rounded border border-negative px-3 py-1.5 font-mono text-xs text-negative hover:bg-negative hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
										onclick={() => submitVerdict(selectedItem, 'wrong')}
									>Reject</button>
									{#if busy}
										<span class="font-mono text-xs text-text-faint">Submitting...</span>
									{/if}
								</div>
							</div>
						{/if}
					</div>
				{/if}
			</section>
		</div>
	{/if}
</main>
