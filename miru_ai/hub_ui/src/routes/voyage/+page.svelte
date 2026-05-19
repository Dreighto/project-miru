<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
</script>

<h1>Voyage</h1>
<p>Island: {currentIsland.value}</p>
