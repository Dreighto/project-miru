import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  compilerOptions: {
    // Force runes mode for the project, except for libraries. Can be removed in svelte 6.
    runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true),
  },
  kit: {
    // adapter-node produces a standalone Node.js server (build/index.js).
    // Launch with:
    //   POSIX:      PORT=18768 node build/index.js
    //   PowerShell: $env:PORT='18768'; node build/index.js
    adapter: adapter(),
  },
};

export default config;
