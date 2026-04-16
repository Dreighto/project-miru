import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		// Static build output — served by Flask/Waitress after cutover.
		// fallback enables SPA client-side routing (all unknown paths return index.html).
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: 'index.html',
			precompress: false,
			strict: true
		}),
		// Phase 2: served by Flask under /storefront/ (Jinja UI keeps /).
		paths: {
			base: '/storefront'
		}
	}
};

export default config;
