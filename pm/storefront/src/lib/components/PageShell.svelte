<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title?: string;
		children: Snippet;
	}

	let { title = 'Project Miru', children }: Props = $props();
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<header
	class="fixed inset-x-0 top-0 z-50 grid min-h-[64px] w-full grid-cols-[1fr_auto_1fr] items-center border-b border-[rgba(196,173,255,0.06)] bg-[#0c0a14] px-3 py-[6px]"
	style="padding-top: max(6px, env(safe-area-inset-top, 0px));"
>
	<div class="flex min-w-0 items-center justify-self-start">
		<span class="text-[13px] font-medium tracking-[0.02em] text-white/[0.58]">{title}</span>
	</div>
	<div class="flex h-16 w-16 items-center justify-center justify-self-center">
		<!-- logo slot — compass + fruit will be ported in a later phase -->
	</div>
	<div class="min-w-0 justify-self-end"></div>
</header>

<div style="height: calc(76px + max(0px, env(safe-area-inset-top, 0px) - 6px));"></div>

<main class="mx-auto w-full max-w-[640px] pb-6">
	{@render children()}
</main>
