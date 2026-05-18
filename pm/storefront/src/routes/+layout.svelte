<script lang="ts">
	import '../app.css';
	import { browser } from '$app/environment';
	import BottomNav from '$lib/components/BottomNav.svelte';
	import DummyModeBanner from '$lib/components/DummyModeBanner.svelte';
	import { storefrontMode } from '$lib/api/mode';

	let { children } = $props();

	// Track current mode reactively for the document-level CSS variable.
	let mode = $state<'real' | 'dummy'>('real');
	storefrontMode.subscribe((m) => (mode = m));

	// Offset the fixed PageShell header + spacer below it when the banner is
	// up so the title bar isn't occluded.
	$effect(() => {
		if (!browser) return;
		document.documentElement.style.setProperty(
			'--dummy-banner-h',
			mode === 'dummy' ? '30px' : '0px'
		);
	});
</script>

<svelte:head>
	<meta name="theme-color" content="#08060f" />
	<link rel="manifest" href="/manifest.json" />
</svelte:head>

<DummyModeBanner />
{@render children()}

<BottomNav />
