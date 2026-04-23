# n8n deploy scripts

PowerShell helpers for deploying versioned workflow JSON from
`docker/n8n/workflows/` into the running n8n instance.

## Prerequisites

- n8n running locally on port **15678** (see `../README.md`)
- Credentials `notion-n8n`, `linear-n8n`, `pushover-n8n` already created in the
  n8n UI (PRO-21 through PRO-23). `github-n8n` is optional — only substituted
  if present and a workflow uses it.
- `N8N_API_KEY` set in `D:\dev\miru\.env`. Generate one under
  **Settings → n8n API** in the n8n UI and paste it into `.env`.

## Files

| File | Purpose |
|---|---|
| `deploy-workflow.ps1` | Load a workflow JSON template, substitute credential-ID placeholders, create-or-update via the n8n public REST API |

## Usage

All commands from `D:\dev\miru\docker\n8n\` (any cwd works — the script resolves
paths relative to itself).

```powershell
# Deploy (or re-deploy) the main W1 workflow
.\scripts\deploy-workflow.ps1 w1-planning-intake.json

# Deploy (or re-deploy) the W1 error handler
.\scripts\deploy-workflow.ps1 w1-error-handler.json
```

On success the script prints the workflow name, ID, and editor URL. The
workflow is left **inactive** — open the URL, review, then click **Activate**.

### Re-deploy / update

Run the same command again. The script looks up the workflow by name and
PUTs the new content to the existing workflow ID (n8n bumps the version). No
duplicate workflows are created. The operator-set active/inactive state is
preserved by n8n across updates (the script never toggles activation). On
success the status line reports the actual state (`active` or `inactive`)
read from the server, not a fixed string.

### Pre-flight validation (PRO-27)

Before any POST/PUT, the script runs two blocker checks against the parsed
workflow JSON. If either fails the deploy aborts non-zero with a clear
message and no API write happens.

1. **Connections integrity.** Every top-level key in `connections` and
   every `.node` value in every connection edge must match a node name in
   `nodes[]`. Catches the W1-era silent-breakage class where a node is
   renamed in `nodes[]` without a matching rewrite of every reference —
   n8n would run cleanly with zero items flowing downstream.
2. **Credential references.** Every credential ID referenced by a node
   (`credentials.<type>.id`) must be present in the n8n vault (looked up
   via the same `/credentials` call already used for placeholder
   substitution). Catches credential-rotation UUID drift: if a credential
   is deleted + recreated, its UUID changes and any workflow JSON still
   holding the old UUID would break silently after deploy.

Re-running the deploy is itself the fix for most credential-rotation
drifts — the `{{..._CRED_ID}}` placeholders resubstitute against the
current vault on every run.

### Settings merge (PRO-27)

On update (not create), the script first fetches the existing workflow's
`settings` block, then merges the incoming JSON settings on top (incoming
values win on conflict). This preserves operator-configured keys that the
workflow JSON does not ship with — most importantly the one-time manual
`errorWorkflow` wire-up described below, which now survives subsequent
redeploys automatically. Prior behavior replaced the settings block
wholesale, which silently dropped that key (and anything else the
operator had set in the UI).

### Rollback

- In the n8n UI: open the workflow → **…** menu → **History** → restore a
  prior version. Fastest path.
- Or delete the workflow entirely: UI → **…** → **Delete**, then redeploy
  an earlier JSON from git (`git checkout <ref> -- docker/n8n/workflows/`).

## Secret / ID handling

- The script reads `N8N_API_KEY` from `.env` once and passes it only in the
  `X-N8N-API-KEY` header. It is never echoed, logged, or written to disk.
- n8n credential UUIDs (`{{NOTION_CRED_ID}}` etc.) are fetched fresh on every
  run. They are substituted directly into the outgoing payload and never
  printed to stdout.
- Workflow JSON in git contains **only placeholders** — never real UUIDs,
  never real API keys.

## One-time manual step: Error Workflow wire-up

n8n's public REST API does not expose the per-workflow "Error Workflow"
setting. After deploying both `w1-planning-intake.json` and
`w1-error-handler.json`:

1. Open the main W1 workflow in the UI.
2. Click **Settings** (gear icon, top-right) → **Error Workflow**.
3. Select **W1 — Error Handler** from the dropdown.
4. Save.

Then activate both workflows. This is a one-time manual step per environment;
automating it is tracked as a future Phase 2 improvement.
