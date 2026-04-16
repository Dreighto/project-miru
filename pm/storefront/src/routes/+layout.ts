// SPA mode — Flask owns the API, SvelteKit owns the shell.
// All routes render client-side so adapter-static's fallback (index.html) can
// serve any unknown path. This matches the single-port 18080 deployment plan.
export const ssr = false;
export const prerender = false;
export const trailingSlash = 'never';
