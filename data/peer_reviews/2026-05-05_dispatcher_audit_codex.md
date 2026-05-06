# Dispatcher Resurrection Audit

## Summary verdict

The `dispatcher/` tree is not a clean dispatch-only service. It is a preserved Windows-side Flask dashboard plus executor runner, SQLite job history, Slack approval bridge, file browser, runtime restart controls, voice transcription proxy, job UI, and old router evaluation utilities. For resurrection as a Local Governance Gatekeeper between Claude Chat and `dispatch_listener` on port 19100, keep only the dispatch validation/execution concepts, the Claude/Gemini handler knowledge if direct local CLI dispatch remains needed, and the PRO-201 injection prefilter. Strip the old roadmap UI surfaces and the Slack-bolt approval subsystem.

High-confidence cleanup direction:

- Keep or refactor: `task_dispatcher.py` dispatch core, `handlers/claude.py`, `handlers/gemini.py`, `handlers/ollama.py` audit-only, `router/prompt_loader.py`, `dispatcher/data/jobs.db` only if old job history must be migrated, and `dispatcher/data/.gitkeep`.
- Strip: `handlers/cursor.py`, `handlers/codex.py`, templates, static UI assets, static mockup, generated `__pycache__` files, `test_approval.txt`, Slack-bolt integration, file browser routes, runtime health/restart routes, voice streaming route, admin UI routes, HTML dashboard routes, and old router corpus/replay scripts unless they are deliberately archived for router research.
- Refactor before reuse: `task_dispatcher.py` currently conflates API, storage, worker execution, UI, Slack approval, service control, and file browsing. A resurrection PR should split this into a small gatekeeper API module, explicit worker allowlist, optional storage module, and a forwarding client for `dispatch_listener` at `http://127.0.0.1:19100`.

Full tree verified under `D:\dev\miru\dispatcher\` includes tracked source/assets plus local runtime artifacts: `data/jobs.db` and multiple `__pycache__/*.pyc` files. I did not query `jobs.db`; schema notes below are inferred from `task_dispatcher.py` only.

## Per-file findings

### `dispatcher/__init__.py`

- Purpose: Package marker with a docstring identifying dispatcher UI assets and templates.
- Verdict: Refactor.
- Import dependencies: Imports nothing from dispatcher; no dispatcher file imports it directly.
- Hidden coupling: The docstring still frames the package as UI/assets, not governance.
- Risk notes: Safe to keep as a package marker; deleting can break package-style imports if future code imports `dispatcher.*`.

### `dispatcher/task_dispatcher.py`

- Purpose: Monolithic Flask plus WebSocket service on port 19000 for job dispatch, UI rendering, job history, Slack approval, file browser, service health/restart, voice streaming, and live log SSE.
- Verdict: Refactor heavily.
- Import dependencies: Imports `get_handler` and `resolve_executor_mode` from `handlers`; imported string-style by `handlers/claude.py`, `handlers/gemini.py`, and `handlers/codex.py` via `from task_dispatcher import ApprovalBridge`.
- Hidden coupling: Uses `.env` at repo root; env vars `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_CHANNEL_ID`, `DISPATCHER_BASE_URL`, `APPDATA`, `ANTHROPIC_API_KEY`, and `ASSEMBLY_AI_API_KEY`; hardcoded ports 19000, 18080, 18765; hardcoded repo path `D:\dev\miru`; SQLite path `dispatcher/data/jobs.db`; Windows scripts under `windows/start_dispatcher.ps1`, `windows/restart_pm.ps1`, and `windows/restart_miru_ai.ps1`; route strings listed below; Slack action IDs `approval_approve`, `approval_deny`, `approval_review`; virtual file pin `__screenshots__` to `C:\temp\playwright-shots`.
- Risk notes: Do not delete wholesale until replacement gatekeeper routes are defined, because tests and handlers depend on symbols in this file. Removing Slack without refactoring handler approval imports will leave `claude.py`, `gemini.py`, and `codex.py` with broken runtime imports when an approval prompt is detected.

Routes to keep or refactor for the new role:

- `POST /api/jobs`: Refactor into the governance entrypoint or proxy to dispatch_listener 19100.
- `GET /api/jobs`, `GET /api/jobs/<job_id>`, `GET /api/jobs/<job_id>/stream`: Keep only if the gatekeeper owns status/log tracking; otherwise forward or strip.
- `POST /api/jobs/<job_id>/cancel`, `POST /api/jobs/cancel-all`, `POST /api/jobs/<job_id>/stdin`: Keep only if dispatch_listener supports equivalent control; otherwise strip with UI.

Routes to drop as old-roadmap/UI/runtime-control surface:

- `GET /`, `GET /jobs/<job_id>`: HTML dashboard/detail pages.
- `GET /api/stats`, `GET /api/history`: Dashboard/history aggregation over old SQLite model.
- `GET /api/files`, `GET /api/file`, `POST /api/files/download-zip`: File browser and ZIP export.
- `GET /api/health`, `POST /api/restart/<service>`: PM/Miru runtime health and restart control.
- `GET /admin/dispatcher/logs`, `POST /admin/dispatcher/restart`: Operator dashboard admin endpoints.
- `GET /api/git/context`: UI branch badge endpoint.
- `SOCK /api/voice/stream`: AssemblyAI browser voice transcription proxy.

Slack-bolt block to strip:

- Lines 85-124: Slack imports, env config, client construction, pending approval globals.
- Lines 417-715: `send_slack_notification`, `ApprovalBridge`, `_setup_slack_listener`, Slack event and Block Kit action handlers, Socket Mode daemon.
- Lines 883-886: completion Slack notifications from `run_job`.
- Line 1819: `_setup_slack_listener()` startup call.
- Tests under `tests/test_blockkit_approval.py` are coupled to this block and should be removed or rewritten in the same cleanup PR if Slack is stripped.

Pushover coupling:

- Functional Pushover code was not found in `dispatcher/`; the only in-file dispatcher reference is the module docstring line describing "optional Pushover notifications". Outside this directory, `windows/start_dispatcher.ps1` still comments that it loads `.env` so the child inherits Pushover keys, but that is outside task scope.

### `dispatcher/data/.gitkeep`

- Purpose: Keeps `dispatcher/data/` present in Git.
- Verdict: Keep if any dispatcher data directory remains; strip only if the resurrected gatekeeper has no local data.
- Import dependencies: None; no imports.
- Hidden coupling: Directory presence supports `DB_PATH.parent.mkdir(...)` but is not required at runtime because the code creates the directory.
- Risk notes: Safe to delete only if the cleanup also removes local dispatcher data persistence.

### `dispatcher/data/jobs.db`

- Purpose: Runtime SQLite job history database for old dispatcher jobs.
- Verdict: Refactor/migrate decision required.
- Import dependencies: Not imported; opened by `task_dispatcher.py` through `sqlite3.connect(DB_PATH)`.
- Hidden coupling: Path is hardcoded as `DISPATCHER_ROOT / "data" / "jobs.db"`; table name is `job_history`.
- Risk notes: Deleting loses old dispatcher history. I did not query it per instruction; schema inferred from `_CREATE_TABLE` is `job_id`, `created_at`, `finished_at`, `prompt`, `model`, `effort`, `handler_name`, `executor_mode`, `status`, `result_text`, `error_message`, `input_tokens`, `output_tokens`, `estimated_cost`, `run_duration_ms`, and `title`.

### `dispatcher/handlers/__init__.py`

- Purpose: Registry mapping public model names to handler callables and executor mode tags.
- Verdict: Refactor.
- Import dependencies: Imports `.simulation`, `.claude`, `.cursor`, `.ollama`, `.gemini`, `.codex`; imported by `task_dispatcher.py`.
- Hidden coupling: `HANDLER_MAP` keys are API-visible model strings: `Ollama`, `Claude`, `Cursor`, `Gemini`, `Codex`; `resolve_executor_mode` treats `ollama_handler` as `local`, simulation as `simulated`, everything else as `real`.
- Risk notes: Must remove `Cursor` and `Codex` here when stripping their files, or import-time failures will break the whole service. Decide whether `Ollama` remains callable despite production roster slimming; task says `ollama.py` is critical and audit-only.

### `dispatcher/handlers/claude.py`

- Purpose: Runs Claude Code CLI in non-interactive print mode and streams output into the job.
- Verdict: Refactor.
- Import dependencies: Imports no dispatcher module at module import time; dynamically imports `ApprovalBridge` from `task_dispatcher` inside the stdout reader if an approval prompt is detected; imported by `handlers/__init__.py`.
- Hidden coupling: CLI path `%APPDATA%\npm\claude.cmd`; fallback `claude` on PATH; sets `CLAUDE_CODE_GIT_BASH_PATH` to `C:/Program Files/Git/bin/bash.exe`; uses `--dangerously-skip-permissions`; effort strings `Quick`, `Standard`, `Deep`; approval regex patterns; job fields `id`, `effort`, `prompt`, `cancel_event`, `output_lines`, `proc`, `status`, and `output`.
- Risk notes: If Slack/ApprovalBridge is stripped, this handler must either remove approval bridging or depend on a new local governance approval mechanism. Keeping the handler unchanged can silently auto-fail approval writes because stdin is closed immediately after launch.

### `dispatcher/handlers/cursor.py`

- Purpose: Experimental Cursor headless handler through Cursor's bundled Claude Agent SDK CLI.
- Verdict: Strip.
- Import dependencies: Imports no dispatcher modules; imported by `handlers/__init__.py`.
- Hidden coupling: `%LOCALAPPDATA%\Programs\cursor\resources\app\extensions\cursor-agent\dist\claude-agent-sdk\cli.js`; `node`; repo root from `__file__`; env `CLAUDECODE` removed; `CLAUDE_CODE_GIT_BASH_PATH`; API model string `Cursor`.
- Risk notes: Must remove from `HANDLER_MAP`, UI worker picker, CSS worker badge, and any tests/docs that still present Cursor as dispatchable. Cursor remains IDE-only per task context.

### `dispatcher/handlers/codex.py`

- Purpose: Runs OpenAI Codex CLI in `exec --full-auto` mode with line streaming.
- Verdict: Strip.
- Import dependencies: Dynamically imports `ApprovalBridge` from `task_dispatcher` inside the output reader; imported by `handlers/__init__.py`.
- Hidden coupling: `codex` on PATH or `%APPDATA%\npm\codex.cmd`; env `OPENAI_API_KEY`, `NO_COLOR`, `TERM`; cwd repo root; model string `Codex`; approval regexes; output noise filters for Codex/MCP logs.
- Risk notes: Removing the file requires removing `Codex` from `HANDLER_MAP`, UI model selectors, CSS worker styles, mockups, and any route validation. Codex is benched per task context, so leaving it available would contradict the new production roster.

### `dispatcher/handlers/gemini.py`

- Purpose: Runs Gemini CLI in non-interactive `-p --yolo` mode and streams output into the job.
- Verdict: Refactor.
- Import dependencies: Dynamically imports `ApprovalBridge` from `task_dispatcher` inside the output reader; imported by `handlers/__init__.py`.
- Hidden coupling: `gemini` on PATH or `%APPDATA%\npm\gemini.cmd`; hardcoded cwd `D:\dev\miru`; env `NO_COLOR`, `TERM`, `PYTHONUNBUFFERED`; kills child `node.exe` processes whose command line matches `gemini`; effort strings `Quick`, `Standard`, `Deep`.
- Risk notes: Keep as the Gemini CLI dispatch lane, but replace the task_dispatcher approval import if Slack is stripped. The broad child-kill PowerShell command can kill unrelated Gemini node processes and should be reconsidered before resurrection.

### `dispatcher/handlers/ollama.py`

- Purpose: Runs prompts against local Ollama HTTP API using effort-to-model mapping.
- Verdict: Keep, audit-only.
- Import dependencies: Imports no dispatcher modules; imported by `handlers/__init__.py`.
- Hidden coupling: Env `OLLAMA_BASE_URL` defaulting to `http://127.0.0.1:11434`; hardcoded effort model map `Quick -> gemma3:latest`, `Standard -> qwen3.5:latest`, `Deep -> gemma4:e4b`; Ollama routes `/api/tags` and `/api/chat`; job token/cost fields.
- Risk notes: Task explicitly says critical, do not alter. If production dispatch roster excludes Ollama, remove only the public route/allowlist exposure while preserving this file for the component that still depends on it.

### `dispatcher/handlers/simulation.py`

- Purpose: Fallback handler that sleeps briefly and echoes the prompt.
- Verdict: Strip or keep only for tests/dev.
- Import dependencies: Imports no dispatcher modules; imported by `handlers/__init__.py` as fallback.
- Hidden coupling: Job fields `cancel_event`, `model`, `effort`, `prompt`, `output`, `status`; simulated status semantics.
- Risk notes: Production gatekeeper should fail closed on unknown worker rather than silently simulate dispatch. If retained, make it opt-in test-only.

### `dispatcher/handlers/__pycache__/__init__.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/__init__.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact; not source-imported directly.
- Hidden coupling: Tied to CPython 3.14 bytecode and source timestamps.
- Risk notes: Safe to delete; Python will regenerate if needed.

### `dispatcher/handlers/__pycache__/claude.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/claude.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete.

### `dispatcher/handlers/__pycache__/codex.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/codex.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete with or without the source file.

### `dispatcher/handlers/__pycache__/cursor.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/cursor.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete with or without the source file.

### `dispatcher/handlers/__pycache__/gemini.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/gemini.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete.

### `dispatcher/handlers/__pycache__/ollama.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/ollama.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete; preserving source is what matters.

### `dispatcher/handlers/__pycache__/simulation.cpython-314.pyc`

- Purpose: Python bytecode cache for `handlers/simulation.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete.

### `dispatcher/router/__init__.py`

- Purpose: Empty router package marker.
- Verdict: Keep if `prompt_loader.py` remains package-importable; otherwise strip with router directory.
- Import dependencies: Imports nothing; no dispatcher source imports it directly.
- Hidden coupling: Enables `dispatcher.router.*` imports in tests or future code.
- Risk notes: Deleting while keeping `prompt_loader.py` can break package imports.

### `dispatcher/router/build_corpus.py`

- Purpose: Builds `data/replay_corpus.jsonl` from `data/routing_history.jsonl` for old LLM Router T1+ evaluation.
- Verdict: Strip or archive outside resurrected service.
- Import dependencies: Imports no dispatcher modules; not imported by dispatcher files.
- Hidden coupling: Reads `data/routing_history.jsonl`; writes `data/replay_corpus.jsonl`; expects event source `w2_manual_label_emit`; worker labels include old router-era names.
- Risk notes: Deleting removes a historical router evaluation utility, not runtime dispatch behavior. If router research remains active, move it out of dispatcher before stripping.

### `dispatcher/router/prompt_loader.py`

- Purpose: Provides PRO-201 OWASP-style prompt injection prefilter, safe Anthropic API payload construction, and append-only rejection logging.
- Verdict: Keep/refactor into gatekeeper.
- Import dependencies: Imports no dispatcher modules; referenced by `tests/test_prompt_loader.py`.
- Hidden coupling: Writes `data/routing_history.jsonl`; emits `source="prompt_loader_prefilter"`, `chosen_worker="triage"`, `operator_disposition="auto_triage_injection"`, `outcome="triage"`; pattern names such as `ignore_instructions`, `xml_system_tag`, and `role_delimiter_system`; model payload assumes Anthropic Messages API shape.
- Risk notes: This is the clearest governance-relevant component. If the new gatekeeper screens Claude Chat dispatches before forwarding to port 19100, this should be retained, but the `chosen_worker="triage"` outcome may need a new disposition vocabulary.

### `dispatcher/router/replay_score.py`

- Purpose: Scores router predictions against `data/replay_corpus.jsonl` and reports agreement/confusion metrics.
- Verdict: Strip or archive outside resurrected service.
- Import dependencies: Contains an example self-import in docstring (`from dispatcher.router.replay_score import run_replay`); imports no dispatcher modules at runtime.
- Hidden coupling: Reads `data/replay_corpus.jsonl`; deterministic baseline labels include `cursor`, `claude-code`, and `triage`; keyword sets are old W2 router heuristics.
- Risk notes: Deleting removes evaluation tooling. Keep only if current governance routing still needs replay scoring.

### `dispatcher/router/__pycache__/prompt_loader.cpython-314.pyc`

- Purpose: Python bytecode cache for `router/prompt_loader.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete.

### `dispatcher/static/dispatcher.css`

- Purpose: Large CSS bundle for the old mobile-first dispatcher dashboard, file browser, job sheets, health cards, worker badges, and live log UI.
- Verdict: Strip.
- Import dependencies: Linked by `templates/dispatcher.html` and `templates/job_detail.html`; not imported by Python.
- Hidden coupling: CSS classes and worker badge selectors for `claude`, `gemini`, `codex`, `cursor`, `ollama`; UI states `waiting_approval`, file-browser `fb-*`, job-sheet `js-*`, restart card, health view, bottom nav, local theme storage variables.
- Risk notes: Delete only with templates/static JS. Keeping it without UI has no gatekeeper value.

### `dispatcher/static/dispatcher.js`

- Purpose: Browser client for the old dispatcher dashboard, including job submit/list/detail, SSE logs, file browser, history tab, health/restart controls, voice WebSocket, git context, theming, and UI interactions.
- Verdict: Strip.
- Import dependencies: Loaded by `templates/dispatcher.html`; not imported by Python.
- Hidden coupling: Calls `/api/jobs`, `/api/jobs/<id>`, `/api/jobs/<id>/cancel`, `/api/jobs/cancel-all`, `/api/jobs/<id>/stdin`, `/api/jobs/<id>/stream`, `/api/stats`, `/api/history`, `/api/files`, `/api/file?path=`, `/api/files/download-zip`, `/api/voice/stream`, `/api/health`, `/api/restart/<service>`, `/admin/dispatcher/logs`, `/admin/dispatcher/restart`, `/api/git/context`, and `/jobs/<id>`; assumes DOM IDs in `dispatcher.html`; uses `localStorage` keys `miru-mode` and `miru-theme`.
- Risk notes: If any backend routes are stripped while this UI remains served, the dashboard will break noisily. Best cleanup is to remove UI and its routes together.

### `dispatcher/static/mockups/miru_dispatcher_mockup_v2.html`

- Purpose: Standalone mobile UI mockup for the old dispatcher dashboard.
- Verdict: Strip.
- Import dependencies: No dispatcher imports; not referenced by runtime files found in dispatcher.
- Hidden coupling: Hardcoded worker choices include Claude, Cursor, Codex, and Gemini; quick links include old ports 18080 and 18765.
- Risk notes: Delete or archive as design history. It should not ship in resurrected governance service.

### `dispatcher/templates/dispatcher.html`

- Purpose: Main HTML template for the old dispatcher dashboard.
- Verdict: Strip.
- Import dependencies: Rendered by `task_dispatcher.py` route `GET /`; loads `static/dispatcher.css` and `static/dispatcher.js`.
- Hidden coupling: Template variables `cache_bust`, `ql_pm_url`, `ql_miru_url`, `ql_dispatcher_url`; model selector values `Claude`, `Ollama`, `Cursor`, `Gemini`; bottom nav views `jobs`, `history`, `files`, `health`; CDN dependencies for Google Fonts, jsDelivr Geist, highlight.js, and lucide; hardcoded service labels/ports 18080, 18765, 19000.
- Risk notes: Removing this requires removing `index()` or replacing it with a minimal health/diagnostic response. Keeping it contradicts "old roadmap stays decommissioned."

### `dispatcher/templates/job_detail.html`

- Purpose: HTML template for standalone job detail pages.
- Verdict: Strip.
- Import dependencies: Rendered by `task_dispatcher.py` route `GET /jobs/<job_id>`; loads `static/dispatcher.css`.
- Hidden coupling: Template variables `job_id`, `job_id_short`, `status`, `model`, `effort`, `created_at`, `finished_at`, `duration`, `executor_mode`, `handler_name`, `usage_block`, `prompt`, and `output`; links back to `/`.
- Risk notes: Safe to remove if HTML UI is dropped. If API job details remain, serve JSON only.

### `dispatcher/test_approval.txt`

- Purpose: Tiny local test artifact, likely used for manual approval/file-write testing.
- Verdict: Strip.
- Import dependencies: None; no references found.
- Hidden coupling: None found.
- Risk notes: Safe to delete unless an operator has an undocumented manual test relying on its exact presence.

### `dispatcher/__pycache__/task_dispatcher.cpython-314.pyc`

- Purpose: Python bytecode cache for `task_dispatcher.py`.
- Verdict: Strip.
- Import dependencies: Generated runtime artifact.
- Hidden coupling: CPython 3.14 cache.
- Risk notes: Safe to delete; Python will regenerate if the source remains.

## Cross-file dependency graph

Runtime Python graph inside dispatcher:

```text
task_dispatcher.py
  imports handlers.get_handler
  imports handlers.resolve_executor_mode

handlers/__init__.py
  imports handlers/simulation.py
  imports handlers/claude.py
  imports handlers/cursor.py
  imports handlers/ollama.py
  imports handlers/gemini.py
  imports handlers/codex.py

handlers/claude.py
  dynamically imports task_dispatcher.ApprovalBridge on approval prompt

handlers/gemini.py
  dynamically imports task_dispatcher.ApprovalBridge on approval prompt

handlers/codex.py
  dynamically imports task_dispatcher.ApprovalBridge on approval prompt

router/prompt_loader.py
  no dispatcher imports
  tested by tests/test_prompt_loader.py

router/build_corpus.py and router/replay_score.py
  no dispatcher runtime imports
  depend on data/*.jsonl files by string path
```

UI/backend route coupling:

```text
task_dispatcher.py GET /
  renders templates/dispatcher.html
  loads static/dispatcher.css
  loads static/dispatcher.js

static/dispatcher.js
  calls job APIs, history/stats APIs, file APIs, voice WebSocket,
  health/restart APIs, admin APIs, git context API, and HTML job detail route

task_dispatcher.py GET /jobs/<job_id>
  renders templates/job_detail.html
  loads static/dispatcher.css
```

External and test coupling outside dispatcher:

- `tests/test_prompt_loader.py` imports `prompt_loader` by modifying `sys.path` to `dispatcher/router`.
- `tests/test_blockkit_approval.py` imports `task_dispatcher`, mocks Slack modules, and validates `ApprovalBridge`; remove or rewrite if Slack is stripped.
- Windows startup scripts and service catalog files reference port 19000 and `dispatcher/task_dispatcher.py`; those are outside this audit scope but will matter when resurrection changes the runtime contract.

## Recommended cleanup PR scope

1. Create the new gatekeeper boundary first.

- Define a minimal API contract for Claude Chat -> gatekeeper -> dispatch_listener 19100.
- Replace `VALID_MODELS = {"Ollama", "Claude", "Cursor", "Gemini", "Codex"}` with an explicit new allowlist. Based on task context, dispatchable production workers should be Claude Code backend and Gemini CLI frontend; preserve `ollama.py` without changing it.
- Move PRO-201 `scan_for_injection` into the request path before forwarding.

2. Strip old roadmap surfaces in one coordinated slice.

- Remove templates, static assets, mockup, file browser routes, voice route, health/restart routes, admin routes, git context route, and HTML job detail route.
- Remove `flask_sock`, `mimetypes`, `io`, `zipfile`, and AssemblyAI dependencies when their routes go.
- Remove generated `__pycache__` files and `test_approval.txt`.

3. Strip decommissioned workers and Slack cleanly.

- Remove `handlers/cursor.py` and `handlers/codex.py`; update `handlers/__init__.py`, route validation, CSS/UI references, and tests/docs.
- Remove Slack-bolt imports/config/listener/ApprovalBridge and replace handler approval behavior with either fail-closed local governance or no interactive approvals.
- Update or delete `tests/test_blockkit_approval.py` in the same PR.

4. Decide storage and history intentionally.

- If the gatekeeper only forwards dispatches, replace `jobs.db` with append-only governance logs or no DB.
- If local status/history remains useful, migrate the schema away from old executor/UI fields and document retention.
- Do not silently delete `jobs.db` until the operator confirms old history is disposable.

## Risks and unknowns

- Unknown: The exact dispatch_listener 19100 contract was not inspected in this audit. The cleanup PR should read `services/dispatch_listener/` before changing the external API shape.
- Unknown: `ollama.py` is marked critical but production roster slimming excludes everything except Claude Code and Gemini CLI. Treat this as an architect/operator decision: keep the file untouched, but decide whether `Ollama` remains exposed as a dispatch target.
- Risk: Handler approval paths are string-imported from `task_dispatcher`, so removing Slack/ApprovalBridge without handler changes can produce runtime failures only when an approval prompt appears.
- Risk: UI and backend routes are tightly coupled. Partial deletion will leave broken buttons, polling errors, or dead routes.
- Risk: `dispatcher/data/jobs.db` is a runtime artifact with possible historical value. I did not query it per instruction; schema is inferred from source only.
- Risk: Port 19000 is referenced outside `dispatcher/` in Windows startup scripts, service docs, and context files. The resurrection PR should update those after the new gatekeeper contract is chosen.
- Risk: Pushover is decommissioned, but only a dispatcher docstring mentions it inside this tree. Broader Pushover cleanup exists elsewhere and should not be bundled unless the cleanup PR explicitly expands scope.
