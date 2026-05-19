<script lang="ts">
	import { currentIsland } from '$lib/stores/currentIsland.svelte';
</script>

<h1>Review</h1>
<p>Island: {currentIsland.value}</p>
