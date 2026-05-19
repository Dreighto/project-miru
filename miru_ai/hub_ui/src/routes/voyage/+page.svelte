<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<h1>Voyage</h1>
<p>Island: {currentIsland.value}</p>

{#if data.flaskDown}
	<div class="flask-error" role="alert" aria-label="Flask service unreachable">
		<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
			<circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" stroke-width="1.5" />
			<line
				x1="10"
				y1="6"
				x2="10"
				y2="11"
				stroke="currentColor"
				stroke-width="1.5"
				stroke-linecap="round"
			/>
			<circle cx="10" cy="14" r="1" fill="currentColor" />
		</svg>
		Flask service unreachable
	</div>
{:else if data.throughput}
	<section class="throughput-grid" aria-label="OP01 Throughput">
		<div class="stat-tile" data-testid="stat-total-reviews">
			<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true">
				<path
					d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linecap="round"
					stroke-linejoin="round"
				/>
			</svg>
			<span class="stat-value">{data.throughput.total_reviews}</span>
			<span class="stat-label">Total Reviews</span>
		</div>
		<div class="stat-tile" data-testid="stat-today-reviews">
			<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true">
				<circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5" />
				<polyline
					points="12 6 12 12 16 14"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linecap="round"
				/>
			</svg>
			<span class="stat-value">{data.throughput.today_reviews}</span>
			<span class="stat-label">Today's Reviews</span>
		</div>
		<div class="stat-tile" data-testid="stat-distinct-cards">
			<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true">
				<rect
					x="2"
					y="5"
					width="14"
					height="18"
					rx="2"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
				/>
				<rect
					x="6"
					y="2"
					width="14"
					height="18"
					rx="2"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
				/>
			</svg>
			<span class="stat-value">{data.throughput.distinct_cards_reviewed}</span>
			<span class="stat-label">Cards Reviewed</span>
		</div>
		<div class="stat-tile" data-testid="stat-op01-total">
			<svg class="stat-icon" viewBox="0 0 24 24" aria-hidden="true">
				<polygon
					points="12 2 22 20 2 20"
					fill="none"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linejoin="round"
				/>
			</svg>
			<span class="stat-value">{data.throughput.op01_total_cards}</span>
			<span class="stat-label">OP01 Catalog</span>
		</div>
	</section>
{/if}

<style>
	.flask-error {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 1.5rem;
		padding: 0.75rem 1rem;
		border: 1px solid #f87171;
		border-radius: 0.5rem;
		color: #dc2626;
		background: #fef2f2;
		font-size: 0.875rem;
	}

	.throughput-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
		gap: 1rem;
		margin-top: 1.5rem;
	}

	.stat-tile {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.375rem;
		padding: 1.25rem 1rem;
		border: 1px solid #e5e7eb;
		border-radius: 0.75rem;
		background: #f9fafb;
	}

	.stat-icon {
		width: 1.5rem;
		height: 1.5rem;
		color: #6b7280;
	}

	.stat-value {
		font-size: 1.75rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		line-height: 1;
		color: #111827;
	}

	.stat-label {
		font-size: 0.75rem;
		color: #6b7280;
		text-align: center;
	}
</style>
