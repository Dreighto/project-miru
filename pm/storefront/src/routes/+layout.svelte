<script lang="ts">
	import '../app.css';
	import BottomNav from '$lib/components/BottomNav.svelte';

	let { children } = $props();
</script>

<svelte:head>
	<meta name="theme-color" content="#08060f" />
	<link rel="manifest" href="/manifest.json" />
</svelte:head>

{@render children()}

<BottomNav />
