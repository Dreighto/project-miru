# Live Page Source of Truth

> Legacy scope note for this worktree: this document is historical and centered on the older `dashboard/` + `tools/` ownership model.
> The current canonical worktree runtime is `pm/app.py` on `18080` and `python -m miru_ai.server` on `18765`.
> For current runtime/process authority, use:
> - `docs/RUNTIME_AUTHORITY_MATRIX.md`
> - `windows/README.worktree.md`
> - `windows/start_op_miru_worktree.ps1`

This document identifies the exact files that drive the live Project Miru and Miru AI pages, and how to ensure the running services use the current code.

## Summary (why pages looked unchanged)

- **Library (8080):** The live page is served by a Docker container whose image was built from an **older** copy of `pm/app.py`. The image bakes in `app.py` at build time; no volume mounts the repo. So the container was still serving code without "View Card", `cardDetailModal`, or `titleBlock`/`subtitle`. **Fix:** Rebuild the image from this repo and use that image for the container on 8080 (e.g. stop the current 8080 container and run `docker compose up -d tcg-dashboard` from this repo).
- **Dev (18765):** The live HTML and static assets are served by the canonical Miru AI package from `miru_ai/templates` and `miru_ai/static`, launched via `python -m miru_ai.server`. If the Dev page still looks old in the browser, do a hard refresh (Ctrl+Shift+R / Cmd+Shift+R) or clear cache for the site; the server is already serving the current files.

## A. Project Miru Library page (port 8080)

### Runtime
- **Served by**: Docker container (dashboard service).
- **Not** direct Python: the app runs inside a container built from `dashboard/`.

### Source-of-truth files (this repo)
| Asset | Path | Notes |
|-------|------|--------|
| Route + HTML | `pm/app.py` | Single file: route `GET /`, all HTML and inline CSS/JS. No separate template dir. |
| Deps | `dashboard/requirements.txt` | Used at image build. |
| Image build | `dashboard/Dockerfile` | Copies only `requirements.txt` and `app.py`. |

### How the container gets its code
- **Dockerfile** (`dashboard/Dockerfile`): `COPY app.py .` — the image bakes in whatever `app.py` is in the build context at build time.
- **No volumes** mount the dashboard source into the container; the running process uses only the copied `app.py` from the image.
- So the **live Library page is whatever `app.py` was when the image was last built**. Edits to `pm/app.py` on disk have no effect until the image is rebuilt and the container is recreated.

### Intended UI markers (current app.py)
- "View Card" button (class `viewbtn`)
- Modal: `id="cardDetailModal"`
- Card title block: `class="titleBlock"`, `.title` (card name), `.subtitle` (set name)

### If the live 8080 page looks old
**Root cause:** The container serving 8080 was built from an older copy of `pm/app.py`. The Docker image bakes in `app.py` at build time; it does not mount the repo.

**Fix (from this repo):**
1. Rebuild the image:  
   `docker compose build tcg-dashboard --no-cache`
2. Stop whatever is currently using port 8080 (e.g. container `Miru` or another dashboard container):  
   `docker stop Miru`  (or the actual container name)
3. Start the dashboard from this repo so 8080 uses the new image:  
   `docker compose up -d tcg-dashboard`

This repo’s dashboard service builds image `tcg-watcher-tcg-dashboard` and container name `tcg-dashboard`. If you normally use a different compose (e.g. service name `dashboard`, container `Miru`), rebuild and start that compose from **this** worktree so the image includes the updated `pm/app.py`.

### Duplicate / legacy
- Only one `app.py` for the library in this repo: `pm/app.py`. No duplicate dashboard templates.

---

## B. Miru AI Dev page (historical section; current live port is 18765)

### Runtime
- **Served by**: Direct Python process: `python -m miru_ai.server --host 0.0.0.0 --port 18765`.
- **Working directory** when started must be the repo root (so `tools/` is relative to it). Startup scripts (e.g. `run_miru_dev.ps1`, `windows/start_op_miru.ps1`) set `WorkingDirectory` to the repo root.

### Source-of-truth files (this repo)
| Asset | Path | Notes |
|-------|------|--------|
| App + routes | `miru_ai/server.py` | Flask app, `template_folder` and `static_folder` resolve from the canonical package root. |
| Dev page template | `miru_ai/templates/miru_ai.html` | Used by `render_template("miru_ai.html", ...)`. |
| Dev CSS | `miru_ai/static/miru_ai.css` | Linked as `url_for('static', filename='miru_ai.css')?v={{ asset_version }}`. |
| Dev JS | `miru_ai/static/miru_ai.js` | Linked as `url_for('static', filename='miru_ai.js')?v={{ asset_version }}`. |

### How paths are resolved
- `PACKAGE_ROOT = Path(__file__).resolve().parent` → directory containing `miru_ai/server.py` (i.e. `miru_ai/`).
- `TEMPLATE_DIR = PACKAGE_ROOT / "templates"` → `miru_ai/templates/`.
- `STATIC_DIR = PACKAGE_ROOT / "static"` → `miru_ai/static/`.
- So the **live Dev page is the `miru_ai/templates` and `miru_ai/static` from the same checkout as the running `miru_ai.server` package**. Compatibility copies under `tools/` are not the canonical ownership boundary.

### Cache-busting
- `asset_version=compute_asset_version()` is passed into the template; it is derived from file mtimes of the server script, templates, and static files.
- Template uses `?v={{ asset_version }}` on CSS and JS. When you change those files, the query string changes and browsers load the updated assets.

### Intended UI markers (current template/JS)
- "System Health" section, `devHealthPanel`, Pushover status card
- `id="devUpdatedAtLocal"` and "Local time updates in your browser."
- Validation audit panels

### If the live 18765 page looks old
1. Confirm the process was started from **this** repo (this worktree): check the command line or startup script and that it launched `python -m miru_ai.server` from this repo.
2. Restart the Miru AI server from this repo root so it loads `miru_ai/templates` and `miru_ai/static` from here.
3. Hard-refresh or clear cache for the Dev page; the `?v=` query string should update after file changes once the server is restarted.

### Duplicate / legacy
- Canonical Dev template and assets in this repo: `miru_ai/templates/miru_ai.html`, `miru_ai/static/miru_ai.css`, `miru_ai/static/miru_ai.js`. Compatibility copies under `tools/` may remain temporarily for launcher safety, but they are not the ownership source of truth.

---

## Verification

**Library (8080):** After switching to the image built from this repo, fetch `http://127.0.0.1:8080/` and confirm the response contains: `View Card`, `cardDetailModal`, `titleBlock`, `viewbtn`.

**Dev (18765):** Fetch `http://127.0.0.1:18765/dev` and confirm the response contains: `System Health`, `devHealthPanel`, `devUpdatedAtLocal`, `Pushover`. Fetch the linked `miru_ai.js?v=...` and confirm it contains `renderPushoverStatus` and `formatBrowserLocalTimestamp`.
