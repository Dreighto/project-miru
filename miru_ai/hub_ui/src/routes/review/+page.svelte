<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<main class="mx-auto max-w-5xl space-y-6 p-6">
	<h1 class="text-2xl font-bold">Review</h1>
	<p class="text-sm text-gray-500">Island: {currentIsland.value}</p>

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
			{#each data.items as item (item.canonical_code + item.print_id)}
				<li class="rounded border border-gray-200 bg-white p-4">
					<div class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
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
							<span class="text-gray-500">Status</span>
							<p class="font-medium">{item.promotion_status}</p>
						</div>
						<div>
							<span class="text-gray-500">Confidence</span>
							<p class="font-medium">{item.confidence_score.toFixed(2)}</p>
						</div>
					</div>
					<div class="mt-3 flex gap-2">
						<!-- TODO: verification gate — write path pending CH spec -->
						<button
							type="button"
							class="rounded bg-green-100 px-3 py-1 text-xs font-medium text-green-800 hover:bg-green-200"
							onclick={() => {}}
						>Approve</button>
						<!-- TODO: verification gate — write path pending CH spec -->
						<button
							type="button"
							class="rounded bg-red-100 px-3 py-1 text-xs font-medium text-red-800 hover:bg-red-200"
							onclick={() => {}}
						>Reject</button>
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="text-sm text-gray-500">Queue is empty.</p>
	{/if}
</main>
