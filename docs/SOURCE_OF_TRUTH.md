# Live Page Source of Truth

> Legacy scope note for this worktree: this document is historical and centered on the `8080`/`8765` runtime model.
> For current runtime/process authority (including worktree `18080`/`18765` and canonical launchers), use:
> - `docs/RUNTIME_AUTHORITY_MATRIX.md`
> - `windows/README.worktree.md`
> - `windows/start_op_miru_worktree.ps1`

This document identifies the exact files that drive the live Project Miru and Miru AI pages, and how to ensure the running services use the current code.

## Summary (why pages looked unchanged)

- **Library (8080):** The live page is served by a Docker container whose image was built from an **older** copy of `dashboard/app.py`. The image bakes in `app.py` at build time; no volume mounts the repo. So the container was still serving code without "View Card", `cardDetailModal`, or `titleBlock`/`subtitle`. **Fix:** Rebuild the image from this repo and use that image for the container on 8080 (e.g. stop the current 8080 container and run `docker compose up -d tcg-dashboard` from this repo).
- **Dev (8765):** The live HTML and static assets **do** include the newer markup (System Health, devHealthPanel, Pushover, etc.). The server is using `tools/templates` and `tools/static` from the same checkout as `miru_ai_server.py`, and cache-busting (`?v=...`) is in use. If the Dev page still looks old in the browser, do a hard refresh (Ctrl+Shift+R / Cmd+Shift+R) or clear cache for the site; the server is already serving the current files.

## A. Project Miru Library page (port 8080)

### Runtime
- **Served by**: Docker container (dashboard service).
- **Not** direct Python: the app runs inside a container built from `dashboard/`.

### Source-of-truth files (this repo)
| Asset | Path | Notes |
|-------|------|--------|
| Route + HTML | `dashboard/app.py` | Single file: route `GET /`, all HTML and inline CSS/JS. No separate template dir. |
| Deps | `dashboard/requirements.txt` | Used at image build. |
| Image build | `dashboard/Dockerfile` | Copies only `requirements.txt` and `app.py`. |

### How the container gets its code
- **Dockerfile** (`dashboard/Dockerfile`): `COPY app.py .` — the image bakes in whatever `app.py` is in the build context at build time.
- **No volumes** mount the dashboard source into the container; the running process uses only the copied `app.py` from the image.
- So the **live Library page is whatever `app.py` was when the image was last built**. Edits to `dashboard/app.py` on disk have no effect until the image is rebuilt and the container is recreated.

### Intended UI markers (current app.py)
- "View Card" button (class `viewbtn`)
- Modal: `id="cardDetailModal"`
- Card title block: `class="titleBlock"`, `.title` (card name), `.subtitle` (set name)

### If the live 8080 page looks old
**Root cause:** The container serving 8080 was built from an older copy of `dashboard/app.py`. The Docker image bakes in `app.py` at build time; it does not mount the repo.

**Fix (from this repo):**
1. Rebuild the image:  
   `docker compose build tcg-dashboard --no-cache`
2. Stop whatever is currently using port 8080 (e.g. container `Miru` or another dashboard container):  
   `docker stop Miru`  (or the actual container name)
3. Start the dashboard from this repo so 8080 uses the new image:  
   `docker compose up -d tcg-dashboard`

This repo’s dashboard service builds image `tcg-watcher-tcg-dashboard` and container name `tcg-dashboard`. If you normally use a different compose (e.g. service name `dashboard`, container `Miru`), rebuild and start that compose from **this** worktree so the image includes the updated `dashboard/app.py`.

### Duplicate / legacy
- Only one `app.py` for the library in this repo: `dashboard/app.py`. No duplicate dashboard templates.

---

## B. Miru AI Dev page (port 8765)

### Runtime
- **Served by**: Direct Python process: `python tools/miru_ai_server.py --host 0.0.0.0 --port 8765`.
- **Working directory** when started must be the repo root (so `tools/` is relative to it). Startup scripts (e.g. `run_miru_dev.ps1`, `windows/start_op_miru.ps1`) set `WorkingDirectory` to the repo root.

### Source-of-truth files (this repo)
| Asset | Path | Notes |
|-------|------|--------|
| App + routes | `tools/miru_ai_server.py` | Flask app, `template_folder` and `static_folder` set from `TOOL_ROOT` (directory of this file). |
| Dev page template | `tools/templates/miru_ai.html` | Used by `render_template("miru_ai.html", ...)`. |
| Dev CSS | `tools/static/miru_ai.css` | Linked as `url_for('static', filename='miru_ai.css')?v={{ asset_version }}`. |
| Dev JS | `tools/static/miru_ai.js` | Linked as `url_for('static', filename='miru_ai.js')?v={{ asset_version }}`. |

### How paths are resolved
- `TOOL_ROOT = Path(__file__).resolve().parent` → directory containing `miru_ai_server.py` (i.e. `tools/`).
- `TEMPLATE_DIR = TOOL_ROOT / "templates"` → `tools/templates/`.
- `STATIC_DIR = TOOL_ROOT / "static"` → `tools/static/`.
- So the **live Dev page is the `tools/templates` and `tools/static` from the same checkout as the running `miru_ai_server.py`**. If the process was started from a different worktree or repo, it will serve that other copy.

### Cache-busting
- `asset_version=compute_asset_version()` is passed into the template; it is derived from file mtimes of the server script, templates, and static files.
- Template uses `?v={{ asset_version }}` on CSS and JS. When you change those files, the query string changes and browsers load the updated assets.

### Intended UI markers (current template/JS)
- "System Health" section, `devHealthPanel`, Pushover status card
- `id="devUpdatedAtLocal"` and "Local time updates in your browser."
- Validation audit panels

### If the live 8765 page looks old
1. Confirm the process was started from **this** repo (this worktree): check the command line or startup script and that `tools/miru_ai_server.py` is from this repo.
2. Restart the Miru AI server from this repo root so it loads `tools/templates` and `tools/static` from here.
3. Hard-refresh or clear cache for the Dev page; the `?v=` query string should update after file changes once the server is restarted.

### Duplicate / legacy
- Single Dev template and assets in this repo: `tools/templates/miru_ai.html`, `tools/static/miru_ai.css`, `tools/static/miru_ai.js`. No duplicate Dev monitor templates or static files in this repo.

---

## Verification

**Library (8080):** After switching to the image built from this repo, fetch `http://127.0.0.1:8080/` and confirm the response contains: `View Card`, `cardDetailModal`, `titleBlock`, `viewbtn`.

**Dev (8765):** Fetch `http://127.0.0.1:8765/dev` and confirm the response contains: `System Health`, `devHealthPanel`, `devUpdatedAtLocal`, `Pushover`. Fetch the linked `miru_ai.js?v=...` and confirm it contains `renderPushoverStatus` and `formatBrowserLocalTimestamp`.
