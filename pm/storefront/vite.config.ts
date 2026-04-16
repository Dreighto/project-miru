import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Dev server proxies /api/* and /img/* to the running Flask PM instance on 18080.
// Production build is served by Flask directly, so no proxy is needed there.
export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		port: 5173,
		strictPort: false,
		proxy: {
			'/api': {
				target: 'http://127.0.0.1:18080',
				changeOrigin: true
			},
			'/img': {
				target: 'http://127.0.0.1:18080',
				changeOrigin: true
			},
			'/static/assets': {
				target: 'http://127.0.0.1:18080',
				changeOrigin: true
			}
		}
	}
});
