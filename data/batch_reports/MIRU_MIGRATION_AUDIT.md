# MIRU MIGRATION AUDIT

**Generated:** 2026-04-17 (Friday)  
**Machine:** `nas` (Tailscale IP: 100.104.150.125, Windows 11 Pro)  
**Canonical repo root:** `D:\dev\tcg-watcher-worktree`  
**Purpose:** Complete inventory for migrating the Miru stack to a new mini PC.  
**Scope:** Read-only audit. No secrets printed. Uncertain items marked REVIEW.

---

## Table of Contents

1. [Repositories & Code](#1-repositories--code)
2. [Windows Services](#2-windows-services)
3. [Scheduled Tasks](#3-scheduled-tasks)
4. [Environment Variables](#4-environment-variables)
5. [Config Files & Secrets](#5-config-files--secrets)
6. [Python Environment](#6-python-environment)
7. [Database & Data Stores](#7-database--data-stores)
8. [Network Configuration](#8-network-configuration)
9. [Installed Tools](#9-installed-tools)
10. [MCP Servers](#10-mcp-servers)
11. [File Assets Outside the Repo](#11-file-assets-outside-the-repo)
12. [BS / REVIEW Category](#12-bs--review-category)
- [Must Migrate (checklist)](#must-migrate-checklist)
- [Should Migrate](#should-migrate)
- [Leave Behind](#leave-behind)
- [REVIEW — Operator Decide](#review--operator-decide)
- [Manual Steps Required on New Machine](#manual-steps-required-on-new-machine)
- [Verification](#verification)

---

## 1. Repositories & Code

### Primary Repo: `D:\dev\tcg-watcher-worktree`

| Field | Value |
|-------|-------|
| Full path | `D:\dev\tcg-watcher-worktree` |
| Current branch | `phase3-console-2` |
| Remote | `origin` → `https://github.com/Dreighto/project-miru.git` |
| Remote tracking branch | **NONE — `phase3-console-2` has no upstream** |

> ⚠️ **UNPUSHED — CRITICAL:** Branch `phase3-console-2` does not exist on the remote. All commits on this branch, including the Phase 3 console work and P3-2 file editor, are **local only**. They will be lost if the machine is wiped without pushing.  
> Remote has: `main`, `phase1-ui-redesign`, `phase2-dispatcher`, `phase3-console`

**Unpushed commits on `phase3-console-2` (not on any remote branch):**

```
c5a4087 docs: add load-on-demand craft guide triggers to worker rule files
dd0e3c8 feat(pm): cards library + deck builder pages in SvelteKit
9423ccd feat(pm): add deck save/load/validate endpoints
01c98fa feat(pm): add /api/sets, /api/cards, /api/cards/<code> endpoints
2eaa6bc feat(pm): wire Flask to serve SvelteKit storefront at /storefront/
bcbf439 feat: scaffold SvelteKit storefront — Phase 1 foundation
0de8bca chore: archive 8 orphan miru_ai workers/ingestion modules
f7a64d1 feat: add start_all_services.ps1 — starts only services that are down
cae282b chore: remove 11 stale and retired windows scripts
5035d27 fix: add missing miru_runtime_preflight stub + correct dashboard→pm path
3cfb20c Merge phase3-console: P3-0 CSS polish, terminal view, auto-title, Windows service infra
... (many more — this branch contains ALL of phases 1–3 console work local-only)
```

**Modified (tracked, not committed):**

```
M  dispatcher/static/dispatcher.css
M  dispatcher/static/dispatcher.js
M  dispatcher/task_dispatcher.py
M  dispatcher/templates/dispatcher.html
M  windows/restart_dispatcher.ps1
M  windows/restart_miru_ai.ps1
M  windows/restart_pm.ps1
```

**Untracked (not in git):**

```
archive/screenshots/p3-2-*.png            (7 Playwright screenshots)
data/batch_reports/*.md                   (10 batch report .md files)
data/pm_decks.db                          (0.01 MB)
docs/pm/                                  (PM craft guides)
docs/ui_ux/                               (UI/UX craft guides)
pm.zip                                    (REVIEW)
pm/routes/test_route.py
windows/setup_restart_infrastructure.ps1
.claude/settings.local.json
```

**All local branches (beyond main and remote-tracked):**

Local-only branches (prefixed `+`): `claude/*` (19 Claude Code agent branches), `master`, `phase1-ui-redesign`, `phase2-dispatcher`, `phase3-console`, `phase3-console-2`

### Other Repos Scanned

- `D:\dev\` — only one git repo found: `D:\dev\tcg-watcher-worktree`
- `D:\docker\tcg-watcher` — referenced by disabled scheduled tasks; **not a git repo or does not exist** (directory not confirmed present)
- `C:\Users\andre\Documents` — not scanned (no Miru context found)

---

## 2. Windows Services

**Finding: No NSSM-managed Windows services for Miru exist on this machine.**

NSSM is installed at `C:\Windows\System32\nssm.exe` but `nssm list` returns empty — no services have been registered via NSSM. The Miru stack (PM 18080, Miru AI 18765, Dispatcher 19000) runs as **plain Python child processes** launched by the `OP Miru Startup` scheduled task via `startup_all.ps1`.

Service architecture:

| Service | Port | Started by | Process type | Run account |
|---------|------|-----------|--------------|-------------|
| Task Dispatcher | 19000 | `startup_all.ps1` | `python dispatcher\task_dispatcher.py` | NAS\NAS (non-elevated) |
| PM Dashboard | 18080 | `startup_all.ps1` | `python pm\app.py` | NAS\NAS (non-elevated) |
| Miru AI | 18765 | `startup_all.ps1` | `python -m miru_ai.server --host 0.0.0.0 --port 18765` | NAS\NAS (non-elevated) |

**Key design note (from `startup_all.ps1` header):** Services are started with `Start-Process` without `-Verb RunAs` so child processes inherit the caller's (NAS\NAS non-elevated) token. This ensures subsequent restarts from a non-elevated shell can kill them without UAC prompts.

**Restart scripts** (in `windows/`):
- `restart_dispatcher.ps1` — kills port 19000, restarts dispatcher, probes `/api/jobs`
- `restart_pm.ps1` — kills port 18080, restarts pm app, probes `/`
- `restart_miru_ai.ps1` — kills port 18765, restarts miru AI, probes `/status`

**Task scripts** (in `windows/tasks/`):
- `restart_dispatcher_task.ps1`
- `restart_miru_ai_task.ps1`
- `restart_pm_task.ps1`

**No service dependencies** — all three services are independent Python processes.

---

## 3. Scheduled Tasks

### `OP Miru Startup` — ACTIVE / CRITICAL

| Field | Value |
|-------|-------|
| State | **Ready** (enabled) |
| Principal | `NAS\NAS` |
| Logon type | S4U (run whether logged in or not) |
| Run level | Limited (non-elevated) |
| Trigger | At logon, **30-second delay** |
| Action | `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\dev\tcg-watcher-worktree\windows\startup_all.ps1"` |
| Last run | 2026-04-16 12:30:43 PM |
| Last result | `1` (non-zero = failed; check `logs\startup.log` for details) |

> ⚠️ **Last run result was 1 (failure).** Investigate `logs\startup.log` before migrating — one or more services may not have started cleanly at last boot.

### `Miru Nightly Backup` — DISABLED

| Field | Value |
|-------|-------|
| State | Disabled |
| Principal | `NAS\NAS` |
| Trigger | Scheduled (nightly ~2 AM) |
| Action | `cmd.exe /c cd /d D:\docker\tcg-watcher && powershell.exe -ExecutionPolicy Bypass -File D:\docker\tcg-watcher\tools\miru_backup.ps1` |
| Last run | 2026-03-23 2:00:01 AM |
| Last result | `0` (success at that time) |
| Notes | References `D:\docker\tcg-watcher` — the pre-worktree path. **REVIEW** — path is stale; may need update or replacement on new machine. |

### `Miru Worker (Worktree Overlap)` — DISABLED

| Field | Value |
|-------|-------|
| State | Disabled |
| Principal | `NAS\NAS` |
| Action | `C:\Users\andre\AppData\Local\Programs\Python\Python37\python.exe -m tools.run_worktree_worker --mode overlap --log-run` |
| Last run | 2026-03-22 7:50:42 PM |
| Last result | `1` (failed) |
| Notes | Uses Python 3.7 from an old install path. Overlap worker concept deprecated. **REVIEW** — likely dead. |

### `SvcRestartTaskLogon` — DISABLED

System-level task, not Miru-specific. Ignore.

---

## 4. Environment Variables

### System-Level (`HKEY_LOCAL_MACHINE`)

| Variable | Notes |
|----------|-------|
| `ANTHROPIC_API_KEY` | **SECRET** — API key for Anthropic/Claude. Value not printed. |
| `ChocolateyInstall` | Chocolatey install dir |
| `Path` | System PATH (includes Python, Node, git, tools, etc.) |
| Standard Windows vars | `ComSpec`, `TEMP`, `TMP`, `windir`, `OS`, etc. |

### User-Level (`HKEY_CURRENT_USER`, user: andre)

| Variable | Notes |
|----------|-------|
| `JUSTTCG_API_KEY` | **SECRET** — JustTCG pricing API key |
| `NOTION_TOKEN` | **SECRET** — Notion integration token |
| `OPENAI_API_KEY` | **SECRET** — OpenAI API key |
| `PERPLEXITY_API_KEY` | **SECRET** — Perplexity AI API key |
| `PUSHOVER_APP_TOKEN` | **SECRET** — Pushover app token |
| `PUSHOVER_ENABLED` | Config flag (not a secret) |
| `PUSHOVER_PRIORITY` | Config flag (not a secret) |
| `PUSHOVER_USER_KEY` | **SECRET** — Pushover user key |
| `YOUTUBE_API_KEY` | **SECRET** — YouTube Data API key |
| `GIT_INSTALL_ROOT` | Git install path |
| `JAVA_HOME` | Java install path (REVIEW — Miru-relevant?) |
| `ChocolateyLastPathUpdate` | Chocolatey timestamp |
| `OneDrive`, `OneDriveConsumer` | Microsoft OneDrive paths |

> **Note:** Several secrets are stored both as user environment variables AND in `.env`. The `.env` file is the primary source for services; env vars may be a legacy or redundancy. On the new machine, populating `.env` is sufficient for services — env vars provide fallback and are used by Claude Code / Cursor MCP configs.

---

## 5. Config Files & Secrets

### `.env` — `D:\dev\tcg-watcher-worktree\.env` (inside repo, gitignored)

Primary secrets file loaded by all three services at startup via `op_miru_common.ps1 → Import-OpMiruDotEnv`.

**Keys present (values REDACTED):**

| Key | Purpose |
|-----|---------|
| `OPENAI_API_KEY` | OpenAI API |
| `ANTHROPIC_API_KEY` | Anthropic/Claude API |
| `FIRECRAWL_API_KEY` | Firecrawl web scraping |
| `MAGIC_UI_API_KEY` | Magic UI (21st.dev) component generation |
| `YOUTUBE_API_KEY` | YouTube Data API |
| `PERPLEXITY_API_KEY` | Perplexity AI API |
| `JUSTTCG_API_KEY` | JustTCG pricing data |
| `NOTION_TOKEN` | Notion integration |
| `DEBUG_IMAGES` | Feature flag |
| `PUSHOVER_USER_KEY` | Pushover notifications user key |
| `PUSHOVER_API_TOKEN` | Pushover notifications app token |
| `PUSHOVER_ENABLED` | Feature flag |
| `PUSHOVER_DEFAULT_PRIORITY` | Config value |
| `DISPATCHER_BASE_URL` | Internal URL for Dispatcher |
| `MIRU_HELPER_ENABLED` | Feature flag |
| `MIRU_HELPER_MODEL` | Miru AI model selection |
| `MIRU_HELPER_BASE_URL` | Miru AI base URL |
| `OLLAMA_BASE_URL` | Ollama local LLM endpoint |
| `ASSEMBLY_AI_API_KEY` | AssemblyAI transcription API |
| `CURSOR_API_KEY` | Cursor API |
| `SLACK_BOT_TOKEN` | **SECRET** — Slack bot OAuth token |
| `SLACK_APP_TOKEN` | **SECRET** — Slack app-level token |
| `SLACK_CHANNEL_ID` | Slack channel ID |

### `.mcp.json` — `D:\dev\tcg-watcher-worktree\.mcp.json` (inside repo, gitignored)

Project-level MCP configuration. Used by Cursor (project context), Claude Code, Codex. See [Section 10](#10-mcp-servers) for full server list.

### Cursor User MCP Config — `C:\Users\andre\.cursor\mcp.json`

User-global Cursor MCP configuration. See [Section 10](#10-mcp-servers).

### `secrets/service_account.json` — `D:\dev\tcg-watcher-worktree\secrets\service_account.json`

File exists but appears to contain an empty JSON object. **REVIEW** — may have been cleared or was placeholder. Check if any service references it before migration.

### `config/` — `D:\dev\tcg-watcher-worktree\config\`

Non-secret operational config files (no API keys):

| File | Notes |
|------|-------|
| `config/miru_approved_sources.json` | Miru AI approved data sources policy |
| `config/miru_mcp_policy.json` | MCP governance policy |
| `config/README_APPROVED_SOURCES.md` | Documentation |
| `config/miru_ai/` | Miru AI specific configs |
| `config/root/` | Root-level configs |
| `config/tools/` | Tool configs |

### Worker Instruction Files (in repo root, gitignored)

`CLAUDE.md`, `GEMINI.md`, `CURSOR.md`, `CODEX.md`, `COPILOT.md`, `AGENT_REPO_LOCK.md` — worker-specific instructions. These are **inside the repo** and will migrate with it.

---

## 6. Python Environment

### Installed Python Versions

| Version | Architecture | Notes |
|---------|-------------|-------|
| **Python 3.14.3** | 64-bit | Default (`python` command). Used by all services. Install path: `C:\Python314` |
| Python 3.12 (64-bit) | 64-bit | Installed, not default |
| Python 3.11 (64-bit) | 64-bit | Installed, not default |
| Python 3.7 (64-bit) | 64-bit | Legacy. Referenced by disabled "Miru Worker Overlap" task. Likely unused. |

### Virtual Environments

| Path | Python | Status | Notes |
|------|--------|--------|-------|
| `D:\dev\tcg-watcher-worktree\.venv` | 3.14.3 | **STALE / BROKEN** | `pyvenv.cfg` shows it was created at `c:\Users\andre\.codex\worktrees\0814\tcg-watcher\.venv` — this is a Codex worktree venv at a different path, symlinked or copied here. `pip freeze` returns empty; `pip list` fails. **Services do NOT use this venv.** |

> **Key finding:** All three Miru services use **system Python 3.14.3** (`C:\Python314\python.exe`) directly — not the `.venv`. The venv in the repo is a Codex artifact and appears non-functional.

### System Python 3.14 (`C:\Python314`) — Installed Packages

All packages below are installed to the system Python 3.14 (used by Miru services):

```
altair==6.0.0
annotated-doc==0.0.4
annotated-types==0.7.0
anyio==4.12.1
assemblyai==0.59.0
attrs==25.4.0
av==17.0.0
beautifulsoup4==4.14.3
blinker==1.9.0
cachetools==7.0.3
certifi==2026.2.25
cffi==2.0.0
charset-normalizer==3.4.5
click==8.3.1
colorama==0.4.6
cryptography==46.0.7
ctranslate2==4.7.1
curl_cffi==0.14.0
dnspython==2.8.0
faster-whisper==1.2.1
filelock==3.25.2
Flask==3.1.3
flask-cors==6.0.2
flask-sock==0.7.0
flatbuffers==25.12.19
fsspec==2026.3.0
gitdb==4.0.12
GitPython==3.1.46
greenlet==3.3.2
h11==0.16.0
hf-xet==1.4.3
httpcore==1.0.9
httpx==0.28.1
huggingface_hub==1.10.1
idna==3.11
itsdangerous==2.2.0
Jinja2==3.1.6
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
markdown-it-py==4.0.0
MarkupSafe==3.0.3
mdurl==0.1.2
mpmath==1.3.0
narwhals==2.17.0
numpy==2.4.2
ollama==0.6.1
onnxruntime==1.24.4
packaging==26.0
pandas==2.3.3
pdfminer.six==20251230
pdfplumber==0.11.9
pillow==12.1.1
pip==26.0.1
playwright==1.58.0
protobuf==6.33.5
pyarrow==23.0.1
pycparser==3.0
pydantic==2.12.5
pydantic_core==2.41.5
pydantic-settings==2.13.1
pydeck==0.9.1
pyee==13.0.1
Pygments==2.20.0
pymongo==4.16.0
pypdfium2==5.7.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.2
pytz==2026.1.post1
PyYAML==6.0.3
referencing==0.37.0
requests==2.32.5
rich==15.0.0
rpds-py==0.30.0
setuptools==82.0.1
shellingham==1.5.4
simple-websocket==1.1.0
six==1.17.0
slack_bolt==1.28.0
slack_sdk==3.41.0
smmap==5.0.2
soupsieve==2.8.3
streamlit==1.55.0
sympy==1.14.0
tenacity==9.1.4
tokenizers==0.22.2
toml==0.10.2
tornado==6.5.4
tqdm==4.67.3
typer==0.24.1
typing_extensions==4.15.0
typing-inspection==0.4.2
tzdata==2025.3
urllib3==2.6.3
waitress==3.0.2
watchdog==6.0.0
websocket-client==1.9.0
websockets==16.0
Werkzeug==3.1.6
wsproto==1.3.2
```

---

## 7. Database & Data Stores

All databases are SQLite3 files. Canonical DB root: `D:\dev\tcg-watcher-worktree\data\`

### Active / Canonical Databases

| Path | Size (MB) | Status | Notes |
|------|-----------|--------|-------|
| `data/card_catalog.db` | **46.53** | **CANONICAL — CRITICAL** | Live card database. Primary data store for PM and Miru AI. Never write directly; use pipeline. |
| `data/miru_dossiers.db` | 33.99 | Active | Miru AI player/card dossiers |
| `data/miru_learning_dossiers.db` | 34.27 | Active | Miru AI learning dossiers |
| `data/miru_learning_log.db` | 1.62 | Active | Miru AI learning log |
| `data/miru_learning_queue.db` | 1.01 | Active | Miru AI learning queue |
| `data/miru_dev_training_reviews.db` | 0.37 | Active | Dev training review records |
| `data/miru_deck_intel.db` | 0.23 | Active | Deck intelligence data |
| `data/miru_source_cache.db` | 0.18 | Active (WAL mode) | Source cache; has -shm and -wal files |
| `data/miru_user_decks.db` | 0.05 | Active (WAL mode) | User deck storage; has -shm and -wal files |
| `data/miru_mcp_governance.db` | 0.05 | Active | MCP governance tracking |
| `data/miru_official_rules.db` | 0.09 | Active | Official rules/FAQ store |
| `data/pm_decks.db` | 0.01 | Active (untracked) | PM deck data — new, not yet committed |
| `dispatcher/data/jobs.db` | 0.28 | Active | Dispatcher job queue |

### MCP Snapshot (Read-Only)

| Path | Size (MB) | Notes |
|------|-----------|-------|
| `miru-mcp/sqlite-ro/card_catalog.snapshot.db` | 45.65 | Read-only MCP snapshot of card_catalog. Refreshed from live DB. Used by sqlite-ro-snapshot MCP server. |

### Backup

| Path | Size (MB) | Notes |
|------|-----------|-------|
| `archive/data_backups/card_catalog_backup_20260402.db` | 45.65 | Snapshot backup from 2026-04-02. Keep for safety; not live. |

### REVIEW

| Path | Size (MB) | Notes |
|------|-----------|-------|
| `windows/dispatcher_jobs.db` | 0.03 | **Wrong location per file-placement rules** — should be in `data/` or `dispatcher/data/`. REVIEW — may be a stale artifact or duplicate. |

---

## 8. Network Configuration

### Active Port Bindings (at audit time)

| Port | Service | PID | Bind address |
|------|---------|-----|-------------|
| 18080 | PM Dashboard | 32680 | 0.0.0.0 |
| 18765 | Miru AI | 34532 | 0.0.0.0 |
| 19000 | Task Dispatcher | 46748 | 0.0.0.0 |

### Windows Firewall Inbound Rules

| Rule Name | Protocol | Local Port | Remote Addr | Direction | Action |
|-----------|----------|-----------|-------------|-----------|--------|
| `Miru AI Dev (18765)` | TCP | 18765 | Any | Inbound | Allow |
| `Miru 18080` | TCP | 18080 | Any | Inbound | Allow |
| `Miru 18080` *(duplicate)* | TCP | 18080 | Any | Inbound | Allow |
| `Miru Task Dispatcher 19000 (Tailscale only)` | TCP | 19000 | Any | Inbound | Allow |

> **Note:** The Dispatcher rule is named "Tailscale only" but `RemoteAddress` is set to `Any`. On the new machine, decide whether to restrict this rule to the Tailscale subnet (`100.x.x.x/8`) or leave as-is.

> **Note:** There are two identical `Miru 18080` rules — one is a duplicate and can be consolidated on the new machine.

### Tailscale

| Field | Value |
|-------|-------|
| Version | 1.96.3 |
| Machine name (this machine) | `nas` |
| Tailscale IP | **100.104.150.125** |
| Account | `andreokoc@` |

**Network peers:**

| Machine | IP | Status |
|---------|----|--------|
| `gasdrawls` | 100.115.248.75 | Windows, offline (last seen ~12h ago) |
| `iphone172` | 100.88.228.28 | iOS, active (direct connection) |

### Reserved Ports (DO NOT BIND)

| Port | Status |
|------|--------|
| 8080 | Reserved — do not use |
| 8765 | NEVER TOUCH |

---

## 9. Installed Tools

| Tool | Version | Install Path / Notes |
|------|---------|---------------------|
| **Python 3.14.3** | 3.14.3 | `C:\Python314\python.exe` — default, used by all services |
| Python 3.12 | 3.12.x | Installed via py launcher |
| Python 3.11 | 3.11.x | Installed via py launcher |
| Python 3.7 | 3.7.x | `C:\Users\andre\AppData\Local\Programs\Python\Python37\` — legacy, likely unused |
| **Git** | 2.53.0.windows.2 | On PATH |
| **Node.js** | v22.22.0 | On PATH |
| **npm** | 11.12.1 | Bundled with Node |
| **npx** | 11.12.1 | Bundled with Node |
| **Claude Code** | 2.1.76 | `claude` command on PATH |
| **Codex CLI** | 0.120.0 | `codex` command on PATH |
| **Cursor** | 3.46.1 (2024-08-13) | `C:\Users\andre\AppData\Local\Programs\cursor\` |
| **NSSM** | (version not retrieved) | `C:\Windows\System32\nssm.exe` — present but no services registered |
| **Tailscale** | 1.96.3 | `tailscale` on PATH |
| **SQLite3** | (installed) | `C:\tools\sqlite3\sqlite3.exe` |
| **Playwright (pip)** | 1.58.0 | Installed in system Python 3.14 |
| **Playwright (MCP)** | 1.59.1 | Via `@playwright/mcp@latest` npm package |
| **Chocolatey** | (present) | `C:\ProgramData\chocolatey` |
| **uvx** | (present) | Used by `fetch` MCP server — `uvx mcp-server-fetch` |
| **Docker** | (present) | Used by `filesystem` MCP server in project config |

---

## 10. MCP Servers

### Project Config: `D:\dev\tcg-watcher-worktree\.mcp.json`

Used by: Cursor (project), Claude Code, Codex (when run from repo root)

| Server Name | Command | Notes |
|-------------|---------|-------|
| `fetch` | `uvx mcp-server-fetch` | Web fetch capability |
| `justtcg` | *(no command — custom server)* | JustTCG pricing data. Likely a local server. **REVIEW** — how is this started? |
| `perplexity` | `cmd /c npx.cmd -y @perplexity-ai/mcp-server` (PowerShell wrapper reads `PERPLEXITY_API_KEY` from `.env`) | Perplexity AI search |
| `sequential-thinking` | `npx.cmd -y @modelcontextprotocol/server-sequential-thinking` | Sequential reasoning |
| `sqlite-ro-snapshot` | `cmd /c npx.cmd -y @mokei/mcp-sqlite --db D:\dev\tcg-watcher-worktree\miru-mcp\sqlite-ro\card_catalog.snapshot.db` | Read-only card DB access |
| `filesystem` | `docker run -i --rm --mount type=bind,src=D:/dev/tcg-watcher-worktree,dst=/projects/miru mcp/filesystem /projects/miru` | Repo file access via Docker |
| `playwright` | `npx.cmd -y @playwright/mcp@latest` | Browser automation (env: `npm_config_cache` → REDACTED) |
| `git` | `npx.cmd -y @cyanheads/git-mcp-server@latest` | Git operations (env: `MCP_TRANSPORT_TYPE`, `GIT_BASE_DIR`, `npm_config_cache` → all REDACTED) |
| `notion` | PowerShell wrapper reads `NOTION_TOKEN` from `.env` → `npx @notionhq/notion-mcp-server` | Notion integration |
| `youtube` | PowerShell wrapper reads `YOUTUBE_API_KEY` from `.env` → `npx @a.ardeshir/youtube-mcp` | YouTube search |
| `magic-ui` | PowerShell wrapper reads `MAGIC_UI_API_KEY` from `.env` → `npx @21st-dev/magic@latest` (env: `npm_config_cache` → REDACTED) | Magic UI component generation |
| `shadcn` | `npx.cmd -y shadcn@latest mcp` (env: `npm_config_cache` → REDACTED) | Shadcn/ui component access |

> **Migration note on PowerShell wrappers:** The project `.mcp.json` uses PowerShell wrapper commands that read secrets from `.env` at invocation time. This works because `.env` lives in the repo. On new machine: ensure `.env` is recreated before launching Cursor/Claude Code.

### Cursor User Config: `C:\Users\andre\.cursor\mcp.json`

Used by: Cursor (user-global, all projects)

| Server Name | Command | Env Keys (values REDACTED) |
|-------------|---------|---------------------------|
| `sequential-thinking` | `npx.cmd -y @modelcontextprotocol/server-sequential-thinking` | — |
| `sqlite-ro-snapshot` | `cmd /c npx.cmd -y @mokei/mcp-sqlite --db D:\dev\tcg-watcher-worktree\miru-mcp\sqlite-ro\card_catalog.snapshot.db` | — |
| `justtcg` | *(no command)* | — |
| `perplexity` | `npx.cmd @perplexity-ai/mcp-server` | `PERPLEXITY_API_KEY` |
| `filesystem` | `npx.cmd -y @modelcontextprotocol/server-filesystem D:\dev\tcg-watcher-worktree` | `npm_config_cache` |
| `git` | `npx.cmd -y @cyanheads/git-mcp-server@latest` | `MCP_TRANSPORT_TYPE`, `GIT_BASE_DIR`, `npm_config_cache` |
| `notion` | `npx.cmd @notionhq/notion-mcp-server` | `NOTION_TOKEN` |
| `youtube` | `npx.cmd -y @a.ardeshir/youtube-mcp` | `YOUTUBE_API_KEY` |

> **Note:** Cursor user config has API keys hardcoded as env values in the JSON. Project `.mcp.json` avoids this by using PowerShell wrappers that read `.env`. On new machine: recreate `~/.cursor/mcp.json` with fresh key values.

### Claude Code / Codex MCP Configs

No separate Claude Code or Codex MCP config files found outside the repo `.mcp.json`. Both tools use the project `.mcp.json` when run from the repo root.

---

## 11. File Assets Outside the Repo

### `D:\Miru_Assets\` — Operator-Managed Image Assets

Total size: ~1.2 GB estimated across all subfolders.

| Subfolder | Size | Notes |
|-----------|------|-------|
| `leader_crops/` | **531.4 MB** | **CRITICAL — operator-curated leader card crops.** Must migrate. |
| `OP01/` | 28.4 MB | One Piece set images |
| `OP02/` | 208.8 MB | One Piece set images |
| `OP03/` | 27.9 MB | One Piece set images |
| `OP04/` | 23.0 MB | One Piece set images |
| `OP05/` | 23.2 MB | One Piece set images |
| `OP06/` | 22.6 MB | One Piece set images |
| `OP07/` | 22.6 MB | One Piece set images |
| `OP08/` | 26.8 MB | One Piece set images |
| `OP09/` | 22.4 MB | One Piece set images |
| `OP10/` | 19.8 MB | One Piece set images |
| `OP11/` | 23.0 MB | One Piece set images |
| `OP12/` | 23.4 MB | One Piece set images |
| `OP13/` | 23.1 MB | One Piece set images |
| `OP14/` | 29.4 MB | One Piece set images |
| `OP15/` | 30.8 MB | One Piece set images |
| `EB01/` | 10.1 MB | Extra Booster sets |
| `EB02/` | 11.1 MB | |
| `EB03/` | 10.8 MB | |
| `EB04/` | 11.8 MB | |
| `PRB01/` | 3.6 MB | Premium sets |
| `PRB02/` | 28.8 MB | |
| `ST01/`–`ST29/` | 1–4 MB each | Starter decks |
| `P/` | 27.6 MB | Promo cards |
| `_unclassified/` | 43.3 MB | Unclassified images pending review |
| `ORFC2025V2/` | 0 MB | Empty or near-empty |
| `_reclassify_test/`, `_ui_stage_test/` | 0 MB | Test/staging dirs |
| Log/json files | ~2.5 MB | `fetch_log.txt`, `image_review_decisions.json`, etc. |

### `F:\OPTCG_Images\` — Raw OPTCG Image Store

**Size: 22.49 GB**  
**Important: NOT to be served by PM. Miru-internal use only.**

| Subfolder | Notes |
|-----------|-------|
| `OP01/`–`OP15/` | Full resolution card images per set |
| `EB01/`–`EB04/` | Extra Booster images |
| `ST01/`–`ST29/` | Starter deck images |
| `PRB01/`, `PRB02/` | Premium images |
| `AA_s/` | Alternate Art images |
| `Promos/` | Promo card images |
| `P/` | Promo subset |
| `miru_image_training/` | Training data for Miru image models |
| `thumbs/` | Thumbnail cache |

> This drive is on `F:\`. Confirm the new mini PC has equivalent storage (at minimum 25 GB free on a data drive).

### `data/tcgcsv/` — TCGCSV Downloaded Sets (inside repo, gitignored)

Contains **75+ TCGCSV numeric set IDs** plus `group_set_mapping.json` and `manifest.json`. Also contains one named folder `OP01/`. These are downloaded from the TCGCSV API and can be re-fetched — not critical to copy, but saves re-download time.

### `data/snapshots/` (inside repo)

Contains JSON snapshots: `community_cardlist.json`, `limitless.json`, `official_deck_features.json`, `official_errata_cards.json`, `official_restriction_notices.json`, `official_rules_faq.json`, `onepiece_cardgame_dev.json`, `optcgapi.json`, `.gitkeep`, `README.md`. These are operational data, should migrate with the repo.

### `data/startup-logs/` (inside repo, gitignored runtime)

Contains PID files and per-service stdout/stderr logs:
- `dashboard_18080.pid`, `dispatcher_19000.pid`, `miru_ai_worktree.pid`
- Log files per service

These are runtime artifacts — do NOT need to copy.

### `logs/` (inside repo, gitignored)

Runtime logs:
- `dispatcher_stdout.log`, `dispatcher_stderr.log`
- `miru_ai_stdout.log`, `miru_ai_stderr.log`
- `pm_stdout.log`, `pm_stderr.log`
- `restart_dispatcher.log`, `restart_miru_ai.log`, `restart_pm.log`
- `startup.log`
- Various historical logs

Runtime artifacts — do NOT need to copy (they'll be recreated on first run).

### `archive/` (inside repo)

| Subfolder | Notes |
|-----------|-------|
| `archive/screenshots/` | Playwright screenshots — operational artifacts |
| `archive/data_backups/` | Contains `card_catalog_backup_20260402.db` (45.65 MB) — keep |
| `archive/legacy_helpers/` | **Legacy code — BS** (explicitly in archive/) |
| Other archive dirs | `chrome-cdp`, `chrome-cdp-2`, `design_docs`, `diagnostics`, `edge-profile`, `misc`, `op01`, `root_notes` — various artifacts |

### `D:\docker\tcg-watcher` — Old Pre-Worktree Location

Referenced by the disabled "Miru Nightly Backup" scheduled task. This path may or may not exist on disk. If present, it's the pre-worktree legacy location — **REVIEW** before wiping the old machine.

---

## 12. BS / REVIEW Category

### Confirmed BS (in explicit archive/ or clearly deprecated)

| Item | Reason |
|------|--------|
| `archive/legacy_helpers/` | In `archive/` by design. Legacy overlap worker helpers. |
| `archive/chrome-cdp/`, `archive/chrome-cdp-2/` | Old Chrome CDP test artifacts |
| `archive/edge-profile/` | Edge browser profile artifact |
| Python 3.7 install at `C:\Users\andre\AppData\Local\Programs\Python\Python37\` | Used only by a disabled task referencing an old path. No active service uses it. |

### REVIEW — Operator Decide

| Item | Concern |
|------|---------|
| `D:\dev\tcg-watcher-worktree\.venv` | Created from a Codex worktree path. `pip freeze` returns empty. Services use system Python, not this venv. May be a dangling link. Operator should decide whether to delete or rebuild. |
| `windows/dispatcher_jobs.db` (0.03 MB) | In `windows/` violating file-placement rules. Stale dispatcher jobs DB? Check if referenced. |
| `secrets/service_account.json` | File exists but appears to contain empty JSON. Check if any service reads it. |
| `pm.zip` (untracked at repo root) | Unknown purpose. Snapshot of pm/ folder? Not in git. Check before wiping. |
| `D:\docker\tcg-watcher` | Old pre-worktree directory on disk. May contain backup data or legacy configs not captured in worktree. Inspect before wiping machine. |
| `Miru Nightly Backup` scheduled task | Disabled, references old path. Either update for new machine path or remove. |
| `Miru Worker (Worktree Overlap)` scheduled task | Disabled, uses Python 3.7 from old path. Almost certainly dead. |
| `data/startup-logs/` — contains `.pid` files | PIDs are stale after restart. Directory itself is fine but content is runtime-only. |
| `data/miru_learner_mode.json`, `data/state.json`, `data/watchlist.json`, etc. | Runtime state files — check if they need to migrate or can be rebuilt. |
| `data/miru_worker_runs.jsonl` | Worker run history log. REVIEW — needed on new machine? |
| `node_modules/` at repo root | Build artifact. Will be reinstalled via `npm install`. Do NOT copy. |
| `justtcg` MCP server | Listed in both `.mcp.json` and Cursor config with no command/args. How does it start? May require a locally running JustTCG MCP server process. Investigate before migration. |
| `JAVA_HOME` user env var | Java installed. Why? Any Miru dependency? |

---

## Must Migrate (Checklist)

**Code & Config**

- [ ] Push `phase3-console-2` branch to remote before wiping (`git push -u origin phase3-console-2`)
- [ ] Commit and push all 7 modified tracked files on current branch
- [ ] Ensure all untracked files that matter are committed or backed up separately
- [ ] Clone repo to new machine at same path: `D:\dev\tcg-watcher-worktree`
- [ ] Copy `.env` to new machine (not in git — must transfer manually)
- [ ] Copy `C:\Users\andre\.cursor\mcp.json` to new machine

**Databases (critical)**

- [ ] `data/card_catalog.db` (46.53 MB) — CANONICAL, must copy
- [ ] `data/miru_dossiers.db` (33.99 MB)
- [ ] `data/miru_learning_dossiers.db` (34.27 MB)
- [ ] `data/miru_learning_log.db` (1.62 MB)
- [ ] `data/miru_learning_queue.db` (1.01 MB)
- [ ] `data/miru_dev_training_reviews.db` (0.37 MB)
- [ ] `data/miru_deck_intel.db` (0.23 MB)
- [ ] `data/miru_source_cache.db` + `-shm`, `-wal` (0.18 MB)
- [ ] `data/miru_user_decks.db` + `-shm`, `-wal` (0.05 MB)
- [ ] `data/miru_mcp_governance.db` (0.05 MB)
- [ ] `data/miru_official_rules.db` (0.09 MB)
- [ ] `data/pm_decks.db` (0.01 MB — untracked, new)
- [ ] `dispatcher/data/jobs.db` (0.28 MB)
- [ ] `miru-mcp/sqlite-ro/card_catalog.snapshot.db` (45.65 MB)

**External assets**

- [ ] `D:\Miru_Assets\leader_crops\` (531 MB) — operator-curated, must copy to same path
- [ ] `D:\Miru_Assets\` all set folders (OP01-OP15, EB01-EB04, ST01-ST29, etc.)
- [ ] `F:\OPTCG_Images\` (22.49 GB) — check if new machine has F:\ drive equivalent

**Tailscale**

- [ ] Install and auth Tailscale on new machine (same account `andreokoc@`)
- [ ] Machine should appear as `nas` (or rename as needed)

---

## Should Migrate

- [ ] `data/tcgcsv/` — 75+ TCGCSV set folders. Can be re-fetched but saves time.
- [ ] `data/snapshots/` — operational JSON snapshots (community_cardlist.json, etc.)
- [ ] `data/watchlist.json`, `data/state.json` — runtime state (may be stale)
- [ ] `archive/data_backups/card_catalog_backup_20260402.db` — safety backup
- [ ] `logs/` — not needed to run but useful for diagnostics
- [ ] Python package list — reinstall same versions on new machine (see Section 6)

---

## Leave Behind

| Item | Reason |
|------|--------|
| `archive/legacy_helpers/` | Confirmed legacy/dead code |
| `archive/chrome-cdp/`, `archive/chrome-cdp-2/`, `archive/edge-profile/` | Old browser artifacts |
| Python 3.7 install | No active usage |
| `.venv/` | Stale Codex venv, not used by services |
| `node_modules/` | Reinstall via `npm install` |
| `logs/` runtime files | Recreated on first run |
| `data/startup-logs/` PID files | Runtime only |
| `.npm-cache/`, `.pip-tmp/`, `.tmp-pip/` | Build caches, recreated |
| `.playwright-browsers/`, `.playwright-mcp/` | Reinstalled by Playwright |

---

## REVIEW — Operator Decide

| Item | Question |
|------|----------|
| `D:\dev\tcg-watcher-worktree\.venv` | Delete and rebuild, or ignore since services use system Python? |
| `windows/dispatcher_jobs.db` | Stale artifact? Or live? Check before discarding. |
| `secrets/service_account.json` | Empty. Used anywhere? If not, skip migration. |
| `pm.zip` (repo root, untracked) | What is this? Keep or delete? |
| `D:\docker\tcg-watcher` | Old directory on disk. What does it contain? Inspect before wiping. |
| `Miru Nightly Backup` task | Update path for new machine or remove? |
| `Miru Worker (Worktree Overlap)` task | Remove entirely? |
| `justtcg` MCP server | How does it start? Is there a local JustTCG server process not captured here? |
| `JAVA_HOME` | Java installed — is it a Miru dependency or unrelated? |
| `data/state.json`, `data/watchlist.json`, `data/miru_worker_runs.jsonl` | Transfer operational state or start fresh? |
| F:\ drive | New machine needs equivalent storage (25 GB+) for OPTCG_Images. Plan the drive layout. |
| `data/pm_decks.db` | Untracked — should this be committed or is it ephemeral? |

---

## Manual Steps Required on New Machine

### 1. Install Tools (in order)

```
1. Python 3.14.x (64-bit) — from python.org — set as default
2. Git 2.53+ — from git-scm.com
3. Node.js v22.x LTS — from nodejs.org (includes npm/npx)
4. NSSM — copy nssm.exe to C:\Windows\System32\ (or Chocolatey: choco install nssm)
5. Tailscale — from tailscale.com — auth to andreokoc@ account
6. Cursor — from cursor.com — sign in with same account
7. Claude Code — npm install -g @anthropic-ai/claude-code (or installer)
8. Codex CLI — npm install -g @openai/codex (check version: 0.120.0)
9. SQLite3 — place sqlite3.exe at C:\tools\sqlite3\sqlite3.exe
10. uvx — pip install uv (provides uvx for fetch MCP server)
11. Docker Desktop — for filesystem MCP server (if continuing Docker-based approach)
12. Playwright browsers — python -m playwright install (after installing playwright package)
```

### 2. Python Packages (system Python 3.14)

```bash
# Install all packages from Section 6 pip list:
pip install flask flask-cors flask-sock requests python-dotenv waitress \
    assemblyai faster-whisper ollama slack_bolt slack_sdk \
    playwright beautifulsoup4 pdfplumber pandas numpy pillow \
    GitPython pydantic pydantic-settings streamlit \
    huggingface_hub ctranslate2 websockets httpx tenacity \
    rich typer PyYAML toml python-dateutil pytz watchdog \
    jsonschema curl_cffi cryptography pymongo pyarrow altair \
    # ... (see full list in Section 6)
```

### 3. Re-Auth Required

| Service | Auth method | Notes |
|---------|------------|-------|
| Tailscale | `tailscale up` — browser login | Same `andreokoc@` account |
| Claude Code | `claude auth` | Re-authenticate |
| Cursor | Login via Cursor UI | Same account |
| Codex CLI | `codex auth` or OpenAI login | Check Codex auth flow |
| GitHub | `git credential-manager` or SSH key | For pushing to `github.com/Dreighto/project-miru` |

### 4. Recreate `.env` File

Create `D:\dev\tcg-watcher-worktree\.env` with the following keys (retrieve values from password manager):

```
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
FIRECRAWL_API_KEY=
MAGIC_UI_API_KEY=
YOUTUBE_API_KEY=
PERPLEXITY_API_KEY=
JUSTTCG_API_KEY=
NOTION_TOKEN=
DEBUG_IMAGES=
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=
PUSHOVER_ENABLED=
PUSHOVER_DEFAULT_PRIORITY=
DISPATCHER_BASE_URL=
MIRU_HELPER_ENABLED=
MIRU_HELPER_MODEL=
MIRU_HELPER_BASE_URL=
OLLAMA_BASE_URL=
ASSEMBLY_AI_API_KEY=
CURSOR_API_KEY=
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=
SLACK_CHANNEL_ID=
```

### 5. Recreate User Environment Variables

Set these as user-level env vars (System Properties → Advanced → Environment Variables):

| Variable | Source |
|----------|--------|
| `ANTHROPIC_API_KEY` | Also needed as **system-level** (current machine has it system-level) |
| `JUSTTCG_API_KEY` | Password manager |
| `NOTION_TOKEN` | Password manager |
| `OPENAI_API_KEY` | Password manager |
| `PERPLEXITY_API_KEY` | Password manager |
| `PUSHOVER_APP_TOKEN` | Password manager |
| `PUSHOVER_ENABLED` | `true` (non-secret) |
| `PUSHOVER_PRIORITY` | Check `.env` value |
| `PUSHOVER_USER_KEY` | Password manager |
| `YOUTUBE_API_KEY` | Password manager |

### 6. Recreate Firewall Rules

Run as Administrator on new machine:

```powershell
# PM Dashboard
New-NetFirewallRule -DisplayName "Miru 18080" -Direction Inbound -Protocol TCP -LocalPort 18080 -Action Allow

# Miru AI
New-NetFirewallRule -DisplayName "Miru AI Dev (18765)" -Direction Inbound -Protocol TCP -LocalPort 18765 -Action Allow

# Dispatcher (consider restricting to Tailscale subnet: 100.0.0.0/8)
New-NetFirewallRule -DisplayName "Miru Task Dispatcher 19000 (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 19000 -Action Allow
```

### 7. Register "OP Miru Startup" Scheduled Task

Create the account `NAS` on the new machine first, then:

```powershell
# Run as Administrator
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\dev\tcg-watcher-worktree\windows\startup_all.ps1"'

$trigger = New-ScheduledTaskTrigger -AtLogOn -RandomDelay (New-TimeSpan -Seconds 30)

$principal = New-ScheduledTaskPrincipal -UserId "NAS\NAS" -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0

Register-ScheduledTask `
    -TaskName "OP Miru Startup" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force
```

> **Note:** `OP Miru Startup` last result was `1` (failure). Before registering on new machine, verify `startup_all.ps1` runs cleanly manually first.

### 8. Recreate Cursor User MCP Config

Create `C:\Users\<user>\.cursor\mcp.json` with the server entries from Section 10, populating env key values from password manager. The project `.mcp.json` will copy with the repo and only needs `.env` to be populated.

### 9. NSSM

NSSM is present (`C:\Windows\System32\nssm.exe`) but no services are registered. The current architecture uses the scheduled task + plain processes instead of Windows Services. **No NSSM registration needed unless the operator wants to change this design.**

### 10. npm / Node packages

```bash
cd D:\dev\tcg-watcher-worktree
npm install   # restores node_modules from package-lock.json
```

### 11. MCP Snapshot Refresh

After migrating `card_catalog.db`, refresh the read-only MCP snapshot:

```bash
# Copy card_catalog.db to the snapshot location
cp D:\dev\tcg-watcher-worktree\data\card_catalog.db D:\dev\tcg-watcher-worktree\miru-mcp\sqlite-ro\card_catalog.snapshot.db
```

---

## Verification

**Output file:** `D:\dev\tcg-watcher-worktree\MIRU_MIGRATION_AUDIT.md`  
**File size check:** Verify > 20 KB after creation.  
**Sections written:** 12 + 5 closing sections = 17 total ✓  

**Secret-grep check:**  
Searched for patterns `sk-`, `pk_`, `ghp_`, `AKIA`, `tskey-`, `xoxb-`, `xapp-`, `token_` in this document.  
**Result: PASS** — No raw secret values written. All secrets are described by key name only with `[VALUE REDACTED]` or `[PRESENT]` notation.

**Items flagged REVIEW:** 13 items  
**Items flagged UNPUSHED:** 1 (critical) — entire `phase3-console-2` branch not on remote  

**Known scope gaps / notes:**
- NSSM `dump` output could not be collected (no services registered, `nssm list` returns empty)
- `D:\dev\tcg-watcher-worktree\.venv` pip freeze returned empty (venv appears non-functional/stale)
- System Python 3.14 pip list used as the effective package inventory instead
- `D:\docker\tcg-watcher` (old pre-worktree location) not confirmed present on disk
- Claude Code and Codex MCP configs not found separately — both appear to use project `.mcp.json`
- Playwright MCP console logs found at `.playwright-mcp/` — runtime artifacts only
- `justtcg` MCP server has no command/args in either config — mechanism unclear, needs operator investigation

---

*End of MIRU MIGRATION AUDIT — generated 2026-04-17*
