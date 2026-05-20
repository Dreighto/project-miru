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
		<nav class="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
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
		display: inline-block;
		padding: 0.25rem 0.75rem;
		border-radius: 0.375rem;
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-text-dim);
		text-decoration: none;
		transition:
			color 150ms ease,
			background-color 150ms ease;
	}

	.nav-link:hover {
		color: var(--color-text);
		background-color: var(--color-surface2);
	}

	.nav-link[aria-current='page'] {
		color: var(--color-accent);
		background-color: color-mix(in srgb, var(--color-accent) 12%, transparent);
	}
</style>
