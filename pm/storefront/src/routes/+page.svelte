<script lang="ts">
	import PageShell from '$lib/components/PageShell.svelte';
</script>

<PageShell title="Project Miru">
	<section class="px-1 pt-1">
		<div
			class="relative overflow-hidden rounded-[18px] border border-[rgba(160,120,255,0.10)] px-6 py-7 text-center"
			style="background: radial-gradient(ellipse 120% 100% at 50% 0%, rgba(120, 70, 200, 0.28) 0%, transparent 65%), radial-gradient(ellipse 80% 80% at 30% 90%, rgba(80, 40, 160, 0.18) 0%, transparent 60%), linear-gradient(165deg, rgba(28, 16, 52, 0.95) 0%, rgba(14, 8, 30, 0.98) 100%); box-shadow: 0 2px 24px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(200, 160, 255, 0.06);"
		>
			<div
				class="mb-[6px] inline-block text-[10px] font-semibold uppercase tracking-[0.14em]"
				style="color: #c9a84c; font-family: var(--font-ui);"
			>
				Storefront · Phase 1
			</div>
			<h1
				class="m-0 mb-[6px] text-[clamp(22px,5.5vw,30px)] font-bold leading-[1.2] tracking-[-0.02em] text-white"
				style="font-family: var(--font-display);"
			>
				Project <span style="color: #c9a84c;">Miru</span>
			</h1>
			<p class="m-0 text-[13px] leading-[1.4]" style="color: var(--color-miru-muted);">
				SvelteKit shell wired up. Flask API proxy active on <code>/api/*</code>.
			</p>
		</div>
	</section>
</PageShell>
