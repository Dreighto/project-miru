<script lang="ts">
	import PageShell from '$lib/components/PageShell.svelte';
	import { storefrontMode, setStorefrontMode } from '$lib/api/mode';

	let mode = $state<'real' | 'dummy'>('real');
	storefrontMode.subscribe((m) => (mode = m));
</script>

<PageShell title="Profile">
	<section class="px-1 pt-1">
		<h2
			class="m-0 mb-2 text-[20px] font-bold tracking-[-0.02em] text-white"
			style="font-family: var(--font-display);"
		>
			Profile
		</h2>
		<p class="m-0 mb-4 text-[13px]" style="color: var(--color-miru-muted);">
			Auth + user settings land in Phase 2.
		</p>

		<div
			class="mb-3 rounded-[14px] p-4"
			style="background: var(--color-miru-surface); border: 1px solid var(--color-miru-stroke);"
		>
			<h3
				class="m-0 mb-1 text-[13px] font-bold tracking-[-0.01em]"
				style="font-family: var(--font-display); color: var(--color-miru-text);"
			>
				Data source
			</h3>
			<p class="m-0 mb-3 text-[11px]" style="color: var(--color-miru-muted); font-family: var(--font-ui);">
				Dummy mode runs the storefront on a fake card set so the UI can be exercised
				while the real catalog is being reworked. Nothing in dummy mode is a real
				Bandai card.
			</p>
			<div
				class="flex gap-1 rounded-[10px] p-[3px]"
				style="background: rgba(255,255,255,0.04); border: 1px solid var(--color-miru-stroke);"
			>
				{#each ['real', 'dummy'] as opt (opt)}
					{@const on = mode === opt}
					<button
						type="button"
						class="flex-1 rounded-[8px] px-2 py-[7px] text-[12px] capitalize transition-colors"
						style="font-family: var(--font-ui); background: {on
							? 'rgba(244,208,120,0.12)'
							: 'transparent'}; color: {on
							? 'var(--color-miru-gold)'
							: 'var(--color-miru-muted)'};"
						onclick={() => setStorefrontMode(opt as 'real' | 'dummy')}
					>
						{opt === 'real' ? 'Real data' : 'Dummy data'}
					</button>
				{/each}
			</div>
			<p
				class="m-0 mt-2 text-[10px]"
				style="color: var(--color-miru-muted-2); font-family: 'JetBrains Mono', ui-monospace, monospace;"
			>
				Tip: append <code>?dummy=1</code> to any URL to enter dummy mode quickly.
			</p>
		</div>
	</section>
</PageShell>
