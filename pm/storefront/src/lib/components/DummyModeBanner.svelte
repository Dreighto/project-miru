<script lang="ts">
	import { storefrontMode, setStorefrontMode } from '$lib/api/mode';

	let mode = $state<'real' | 'dummy'>('real');
	storefrontMode.subscribe((m) => (mode = m));
</script>

{#if mode === 'dummy'}
	<div
		class="fixed inset-x-0 top-0 z-[100] flex items-center justify-between gap-3 px-3 py-[6px] text-[11px] font-semibold"
		style="background: linear-gradient(180deg, rgba(244,140,40,0.95) 0%, rgba(220,110,30,0.9) 100%); color: #1a0f00; font-family: var(--font-ui); border-bottom: 1px solid rgba(0,0,0,0.25); box-shadow: 0 1px 4px rgba(0,0,0,0.3); padding-top: max(6px, env(safe-area-inset-top, 0px));"
		role="status"
		aria-label="Dummy data mode active"
	>
		<span class="flex items-center gap-2">
			<span style="font-family: 'JetBrains Mono', ui-monospace, monospace;">[DUMMY]</span>
			<span>Storefront is on fake data — nothing here is a real card.</span>
		</span>
		<button
			type="button"
			class="rounded-[6px] px-2 py-[2px] text-[10px] font-bold"
			style="background: rgba(0,0,0,0.35); color: #ffd9a8; border: 1px solid rgba(0,0,0,0.45);"
			onclick={() => setStorefrontMode('real')}
		>
			Switch to real data
		</button>
	</div>
{/if}
