# n8n — minimal install (PRO-20)

Self-hosted n8n, running in Docker on ROOM, reachable via Tailscale.

## What this is

- Minimal n8n install — container only, no workflows, no credentials yet
- Persistent volume `n8n_data` holds all state
- Basic auth protects the UI
- Auto-starts with Docker Desktop (`restart: unless-stopped`)

## Ports

- Host: **15678** → container: 5678
- Nothing else is exposed

## Access

- LAN / localhost: http://localhost:15678
- Tailscale (from phone, laptop, etc.): http://room.taila28611.ts.net:15678
- Login: n8n's built-in user management (email + password). The owner account is stored inside the `n8n_data` volume, not in `.env`. Reset it with `docker exec miru-n8n n8n user-management:reset` (wipes users, re-triggers the setup wizard).

## Daily operations

All commands run from `D:\dev\miru\docker\n8n\`.

| Action | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| Status | `docker compose ps` |
| Logs (follow) | `docker compose logs -f` |
| Logs (last 100 lines) | `docker compose logs --tail=100` |
| Update image | `docker compose pull && docker compose up -d` |

## Backup

State lives in the Docker named volume `n8n_data`.

- Volume metadata: `docker volume inspect n8n_data`
- On Docker Desktop Windows, the volume's real data path is inside the WSL2 `docker-desktop-data` distribution (not a normal Windows path). Use `docker cp` or a dedicated backup container to pull files out.

Quick backup (tar the whole volume to a Windows file):

```powershell
docker run --rm -v n8n_data:/data -v ${PWD}:/backup alpine tar czf /backup/n8n_data_backup.tar.gz -C /data .
```

Restore from that tarball:

```powershell
docker run --rm -v n8n_data:/data -v ${PWD}:/backup alpine sh -c "cd /data && tar xzf /backup/n8n_data_backup.tar.gz"
```

## First-time setup (already done on ROOM)

1. `cp .env.example .env` (optional — `.env` is only needed for extra env vars like SMTP)
2. `docker compose up -d`
3. Visit http://localhost:15678 (or the Tailscale URL) and complete the owner-account prompt — n8n 2.x asks for email, name, and password. That account becomes the instance owner.

## What is NOT set up yet (separate tickets)

- Credentials for Notion, Linear, GitHub, Pushover
- Any workflows
- Any wiring to Miru services (PM 18080, Miru AI 18765, Dispatcher 19000)
- Version pinning — currently tracks `n8nio/n8n:latest`; consider pinning to a specific tag after first successful run

## Config notes

- Image: `n8nio/n8n:latest`
- Container: `miru-n8n`
- Volume: `n8n_data` mounted at `/home/node/.n8n`
- Timezone: America/Los_Angeles
- `WEBHOOK_URL` is set to the Tailscale hostname so webhooks generated inside n8n are reachable from the public-ish Tailnet
- `N8N_SECURE_COOKIE=false` — n8n defaults to requiring HTTPS for session cookies and only whitelists `localhost` as the plain-HTTP exception. Since we access this over Tailscale (WireGuard-encrypted end-to-end inside the tailnet), we disable n8n's cookie-secure guard. If this ever becomes reachable outside the tailnet, put TLS in front (e.g. `tailscale serve https / http://localhost:15678`) and re-enable the secure cookie.
