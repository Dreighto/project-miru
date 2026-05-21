<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { onNavigate } from '$app/navigation';
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();

	onNavigate((navigation) => {
		if (!document.startViewTransition) return;
		return new Promise((resolve) => {
			document.startViewTransition(async () => {
				resolve();
				await navigation.complete;
			});
		});
	});
</script>

<div class="min-h-screen bg-bg font-sans text-text">
	<header class="sticky top-0 z-10 border-b border-border bg-surface">
		<nav class="mx-auto flex max-w-5xl items-center gap-3 px-4 py-2 sm:gap-6 sm:px-6">
			<span class="font-mono text-sm text-text-faint">miru//dev</span>
			<ul class="flex gap-1" role="list">
				<li>
					<a
						href="/"
						class="nav-link"
						aria-current={page.url.pathname === '/' ? 'page' : undefined}
					>Glance</a>
				</li>
				<li>
					<a
						href="/voyage"
						class="nav-link"
						aria-current={page.url.pathname === '/voyage' ? 'page' : undefined}
					>Voyage</a>
				</li>
				<li>
					<a
						href="/review"
						class="nav-link"
						aria-current={page.url.pathname === '/review' ? 'page' : undefined}
					>Review</a>
				</li>
			</ul>
		</nav>
	</header>

	{@render children()}
</div>

<style>
	.nav-link {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		padding: 0.5rem 0.85rem;
		border-radius: 0.5rem;
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-text-dim);
		text-decoration: none;
		-webkit-tap-highlight-color: transparent;
		transition:
			color 120ms ease,
			background-color 120ms ease;
	}

	.nav-link:hover,
	.nav-link:active {
		color: var(--color-text);
		background-color: var(--color-surface2);
	}

	.nav-link[aria-current='page'] {
		color: var(--color-accent);
		background-color: color-mix(in srgb, var(--color-accent) 14%, transparent);
	}
</style>
