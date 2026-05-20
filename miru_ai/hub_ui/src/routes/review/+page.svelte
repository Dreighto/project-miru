<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type QueueItem = NonNullable<PageData['items']>[number];

	// Per-row state, keyed by rowKey(item). Keeping this per-row (rather than a
	// single shared key) means concurrent submissions on different rows don't
	// clobber each other's in-flight flag or error message.
	let submitting = $state<Record<string, boolean>>({});
	let errors = $state<Record<string, string>>({});

	function rowKey(item: QueueItem): string {
		return `${item.canonical_code}|${item.print_id}|${item.contributing_model}`;
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
			// Verdict committed — the row is no longer pending_review, so a
			// re-fetch of the queue drops it.
			await invalidateAll();
		} catch {
			errors[key] = 'Could not reach the review service. Try again in a moment.';
		} finally {
			delete submitting[key];
		}
	}
</script>

<main class="mx-auto max-w-5xl space-y-6 p-6">
	<h1 class="text-2xl font-bold">Review</h1>
	<p data-testid="current-island" class="text-sm text-gray-500">Island: {currentIsland.value}</p>

	{#if data.flaskDown}
		<div
			role="alert"
			data-testid="flask-down-banner"
			class="rounded border border-red-400 bg-red-50 px-4 py-3 text-red-800"
		>
			Flask service unreachable. Start <code>miru_ai.server</code> on port 18765 and reload.
		</div>
	{:else if data.items && data.items.length > 0}
		<ul class="space-y-3">
			{#each data.items as item (rowKey(item))}
				{@const key = rowKey(item)}
				{@const busy = submitting[key] === true}
				{@const rowError = errors[key]}
				<li class="rounded border border-gray-200 bg-white p-4">
					<div class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
						<div>
							<span class="text-gray-500">Card</span>
							<p class="font-medium">{item.canonical_code}</p>
						</div>
						<div>
							<span class="text-gray-500">Print</span>
							<p class="font-medium">{item.print_id}</p>
						</div>
						<div>
							<span class="text-gray-500">Model</span>
							<p class="font-medium">{item.contributing_model}</p>
						</div>
						<div>
							<span class="text-gray-500">Confidence</span>
							<p class="font-medium">{item.confidence_score.toFixed(2)}</p>
						</div>
					</div>
					<div class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
						<div>
							<span class="text-gray-500">Readiness</span>
							<p class="font-medium">{item.readiness_state}</p>
						</div>
						<div>
							<span class="text-gray-500">Approval</span>
							<p class="font-medium">{item.approval_state}</p>
						</div>
						<div>
							<span class="text-gray-500">Promotion</span>
							<p class="font-medium">{item.promotion_state || '(none)'}</p>
						</div>
					</div>
					<div class="mt-3 flex items-center gap-2">
						<button
							type="button"
							disabled={busy}
							class="rounded bg-green-100 px-3 py-1 text-xs font-medium text-green-800 hover:bg-green-200 disabled:cursor-not-allowed disabled:opacity-50"
							onclick={() => submitVerdict(item, 'correct')}>Approve</button
						>
						<button
							type="button"
							disabled={busy}
							class="rounded bg-red-100 px-3 py-1 text-xs font-medium text-red-800 hover:bg-red-200 disabled:cursor-not-allowed disabled:opacity-50"
							onclick={() => submitVerdict(item, 'wrong')}>Reject</button
						>
						{#if busy}
							<span class="text-xs text-gray-500">Submitting...</span>
						{/if}
					</div>
					{#if rowError}
						<p role="alert" data-testid="verdict-error" class="mt-2 text-xs text-red-700">
							{rowError}
						</p>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="text-sm text-gray-500">Queue is empty.</p>
	{/if}
</main>
