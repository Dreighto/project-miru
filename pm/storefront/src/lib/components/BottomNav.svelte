<script lang="ts">
	import { page } from '$app/state';

	interface NavItem {
		href: string;
		label: string;
		match: (pathname: string) => boolean;
	}

	const items: NavItem[] = [
		{ href: '/', label: 'Home', match: (p) => p === '/' },
		{ href: '/cards', label: 'Cards', match: (p) => p.startsWith('/cards') },
		{ href: '/deck-builder', label: 'Deck', match: (p) => p.startsWith('/deck-builder') },
		{ href: '/leaders', label: 'Leaders', match: (p) => p.startsWith('/leaders') },
		{ href: '/profile', label: 'Profile', match: (p) => p.startsWith('/profile') }
	];
</script>

<nav
	class="fixed inset-x-0 bottom-0 z-[60] w-full border-t border-white/[0.06] bg-[rgba(10,9,18,0.97)] pb-[env(safe-area-inset-bottom,0)] shadow-[0_-1px_0_rgba(0,0,0,0.2)] backdrop-blur-sm"
	style="min-height: var(--bottom-nav-height);"
	aria-label="Primary navigation"
>
	<div class="flex min-h-[var(--bottom-nav-height)] w-full items-stretch justify-around">
		{#each items as item (item.href)}
			{@const active = item.match(page.url.pathname)}
			<a
				href={item.href}
				class="m-[3px_2px] flex min-h-[44px] min-w-0 flex-1 touch-manipulation flex-col items-center justify-center gap-[3px] rounded-[10px] px-[2px] py-[7px] pb-[9px] text-center transition-colors"
				class:text-miru-gold={active}
				class:bg-miru-gold={false}
				style={active
					? 'color: var(--color-miru-gold); background: rgba(244, 208, 120, 0.11); box-shadow: inset 0 0 0 1px rgba(244, 208, 120, 0.16);'
					: 'color: var(--color-miru-muted);'}
				aria-current={active ? 'page' : undefined}
			>
				<span
					class="text-[10px] font-medium tracking-[0.02em]"
					style="font-family: var(--font-ui); font-weight: {active ? 600 : 500};"
				>
					{item.label}
				</span>
			</a>
		{/each}
	</div>
</nav>
