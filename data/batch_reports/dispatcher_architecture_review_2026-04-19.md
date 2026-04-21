# Dispatcher Architecture Review — 2026-04-19

**Reviewer:** Claude Code (Opus 4.7), after three parallel Explore agents on the repo and two Perplexity research waves (~19 calls total).
**Subject:** Miru Dispatcher — Flask on Windows (ROOM, port 19000), iPhone-first via Tailscale, goal is full RDP replacement.
**Peer opinion in hand:** Gemini 2.5 Pro (7 sections, pasted by operator).
**Scope:** Read-only analysis. No code changed, no dispatcher restart, no Notion writes, no touch of ports 18080 / 18765 / 8080 / 8765.

---

## Executive summary (mobile-scannable)

- **Hold:** Flask, SQLite, threading + Popen, Slack approval bridge as primary (for now). No async rewrite, no broker, no microservices. At 2 concurrent jobs the foundation is right-sized; the pain is in specific gaps, not the stack choice.
- **Change #1 (high confidence):** Unify state in SQLite and reconcile orphans at boot. Add `pid` and `approval_payload` columns, drop the split-brain in-memory `jobs` dict where possible, and scan `RUNNING` rows on startup. See [task_dispatcher.py:146-165](dispatcher/task_dispatcher.py:146), [task_dispatcher.py:379-381](dispatcher/task_dispatcher.py:379).
- **Change #2 (high confidence):** Wrap every worker spawn in a **Windows Job Object** (`CREATE_BREAKAWAY_FROM_JOB` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via `pywin32`). Without this, `cmd /c claude.cmd` orphans survive dispatcher death — `proc.kill()` only kills the parent `cmd.exe`. See [handlers/claude.py:61-94](dispatcher/handlers/claude.py:61).
- **Change #3 (high confidence):** Ship a real PWA shell + harden SSE with `Last-Event-ID` replay. Add `manifest.webmanifest`, a minimal `sw.js` (offline shell only — do NOT cache API responses), and a server-side ring buffer keyed by `id:` field so iOS reconnects after screen-lock resume miss nothing. See [templates/dispatcher.html:7](dispatcher/templates/dispatcher.html:7) (currently no manifest link), [dispatcher.js:2396-2404](dispatcher/static/dispatcher.js:2396).
- **Investigate (medium confidence):** Log-tab DOM virtualization + 600 s approval timeout configurability + in-sheet approvals as primary (Slack as fallback). Already half-built at [dispatcher.js:2509-2566](dispatcher/static/dispatcher.js:2509).
- **Do not:** migrate to FastAPI/Litestar, adopt Celery/RQ/Huey, move to Flask-SocketIO, or introduce Redis. None are justified at your scale and at least two (eventlet/gevent-based Flask-SocketIO, async rewrite) carry meaningful downside.

**Disagreement with Gemini:** He conflated your 1 s SSE heartbeat with 5 s HTTP polling, understated the `cmd /c` wrapper problem (PID-to-SQLite alone won't fix orphans on Windows), and missed that in-sheet approvals already exist and that the dispatcher PID is already tracked on disk. Details in Part 5.

---

## Part 1 — Internal code analysis

### 1.1 Architecture map

The Dispatcher is a single Flask app (~1900 lines in [task_dispatcher.py](dispatcher/task_dispatcher.py)) serving:

- **HTML** dashboard at `/` (vanilla JS + custom CSS, no framework).
- **REST JSON** for jobs / stats / files / recent / git context / stdin injection.
- **SSE** for per-job log streams (`/api/jobs/<id>/stream`).
- **flask_sock WebSocket** at `/ws/voice` for AssemblyAI U3 streaming ([task_dispatcher.py:1355-1492](dispatcher/task_dispatcher.py:1355)).
- **Slack webhook** return path for approvals ([task_dispatcher.py:436-560](dispatcher/task_dispatcher.py:436)).

Worker execution uses `concurrent.futures.ThreadPoolExecutor(max_workers=2)` ([task_dispatcher.py:383](dispatcher/task_dispatcher.py:383)) submitting per-handler functions that `subprocess.Popen()` the actual AI-agent CLI. Five handlers today: Claude, Codex, Cursor, Gemini, Ollama (+ `simulation.py` fixture).

Windows Task Scheduler owns supervision via [register_restart_tasks.ps1:66-100](windows/register_restart_tasks.ps1:66) (S4U logon + Limited RunLevel). On launch, [start_dispatcher.ps1:165-171](windows/start_dispatcher.ps1:165) writes `logs\dispatcher_19000.pid` as JSON (`{pid, port, started_at, repo_root, script_path}`), so the dispatcher's *own* PID is reliably discoverable — the gap is strictly **worker** PID tracking.

Binding is `0.0.0.0:19000` with an explicit comment "Tailscale-only, no auth" at [task_dispatcher.py:1821-1825](dispatcher/task_dispatcher.py:1821). That matches operator intent but is worth flagging as a layered-auth-later concern.

### 1.2 Real-time layer

Two distinct real-time channels — and they behave differently:

- **SSE job logs:** `text/event-stream` with **1 s heartbeat** ([task_dispatcher.py:1725-1766](dispatcher/task_dispatcher.py:1725)). Client at [dispatcher.js:2328](dispatcher/static/dispatcher.js:2328) uses `EventSource` with 2 s fixed reconnect ([dispatcher.js:2396-2404](dispatcher/static/dispatcher.js:2396)). Comment in code explicitly notes "iOS kills SSE on screen lock."
- **HTTP polling:** `POLL_MS = 5000` ([dispatcher.js:26](dispatcher/static/dispatcher.js:26)) drives `/api/jobs`, `/api/stats`, `/api/files`, `/api/recent`. History tab is 10 s, health 2 s × 15 cap, git context 30 s. Cadences are ad-hoc and inconsistent.
- **Voice WebSocket:** bidirectional via flask_sock, already in prod.

**Gap:** SSE reconnection has **no sequence-ID resync** — no `id:` field, no `Last-Event-ID` honored. Lines emitted during an outage are lost on reconnect. For iOS where screen-lock drops the TCP socket, this is the single biggest real-time reliability issue.

### 1.3 Job execution

Core shape:
- `ThreadPoolExecutor(max_workers=2)` accepts work via `/api/jobs` POST.
- Per-handler function does `subprocess.Popen()` with `creationflags=CREATE_NO_WINDOW` (no `CREATE_NEW_PROCESS_GROUP`, no `DETACHED_PROCESS`, no Job Object).
- Claude and optionally Codex route through `cmd /c claude.cmd` ([handlers/claude.py:61-94](dispatcher/handlers/claude.py:61)). This creates a **PID opacity layer**: `proc.pid` is the `cmd.exe` parent, not the claude binary. On Windows, killing `cmd.exe` via `proc.kill()` does **not** propagate to the child by default.
- A reader thread per job consumes stdout, matches `APPROVAL_RE`, and on match calls `ApprovalBridge.ask(...)`. On reply it injects via `proc.stdin.write(reply + "\n")` ([handlers/claude.py:139-160](dispatcher/handlers/claude.py:139)).

**Bug-shaped detail:** stdin is closed immediately after Popen at [handlers/claude.py:126](dispatcher/handlers/claude.py:126), yet the reader thread later writes to it at [handlers/claude.py:153](dispatcher/handlers/claude.py:153). On most Windows builds this throws silently and only logs at WARNING. If approval stdin injection has ever felt flaky, this is where to look first.

**Concurrency:** hard ceiling of 2 simultaneous workers. Gemini's "1-5 concurrent" framing is optimistic — the code says 2.

### 1.4 State

**Split brain.** `jobs_lock` + `jobs` dict + `jobs_order` list hold the canonical live state ([task_dispatcher.py:379-381](dispatcher/task_dispatcher.py:379)); SQLite `job_history` is written via `_db_upsert_job()` on state changes ([task_dispatcher.py:146-234](dispatcher/task_dispatcher.py:146)). Schema has no `pid`, no `approval_state`, no Popen handle (can't serialize). After a dispatcher crash the in-memory half is gone; SQLite reports `RUNNING` for rows that nobody's tracking anymore — classic orphan window.

**Dispatcher PID** *is* tracked on disk. **Worker PIDs** are not.

### 1.5 iOS PWA failure modes

The dashboard is **styled as a PWA but isn't one:**

- `apple-mobile-web-app-capable` meta is set at [dispatcher.html:7](dispatcher/templates/dispatcher.html:7).
- **No** `<link rel="manifest">`, **no** `manifest.json` / `manifest.webmanifest` on disk, **no** `sw.js`, **no** `navigator.serviceWorker.register()` call anywhere in [dispatcher.js](dispatcher/static/dispatcher.js).
- So the Home-Screen-installed experience today is a plain Safari web-view with iOS's short background budget (~30 s, less on low-power mode) killing any in-flight SSE. iOS Web Push **requires** a registered service worker; without one, push is off the table.

**Log buffer is unbounded.** `appendChild` loop at [dispatcher.js:2369](dispatcher/static/dispatcher.js:2369) has no cap or virtualization. A long-running Claude job can pile up 50k+ DOM nodes; on iPhone 16 Pro Max that's memory pressure Safari will respond to by backgrounding/reloading the tab — which looks to the operator like "the dispatcher dropped."

### 1.6 Approval flow

Today's path:
1. Worker prints `APPROVAL: ...` on stdout.
2. Reader thread calls `ApprovalBridge.ask()` with a 600 s hardcoded timeout ([handlers/claude.py:147](dispatcher/handlers/claude.py:147)).
3. Bridge posts a Block Kit 3-button card to Slack ([task_dispatcher.py:436-560](dispatcher/task_dispatcher.py:436)).
4. Slack button POSTs back; `_pending_approvals` dict + `threading.Event` signals the waiting reader thread.
5. Reader thread writes reply to `proc.stdin` (which was closed at spawn time — silent failure possible).

**Important:** an **in-sheet** Approve/Deny pair already exists at [dispatcher.js:2509-2566](dispatcher/static/dispatcher.js:2509), wired to POST `/api/jobs/<id>/stdin`. So the question isn't "build in-app approvals" — it's "make them primary vs. Slack, or keep Slack primary and in-app as fallback." Code is already closer to the goal than Gemini's opinion suggests.

**Risks:**
- 600 s timeout is cumulative-unsafe for long multi-approval sessions.
- Approval state lives only in memory; dispatcher crash mid-approval loses the decision and any pending stdin write.
- No dedupe / idempotency key on approvals — a Slack retry or a double-tap on iOS could inject twice.

---

## Part 2 — External research

Notes on sourcing: Wave 1 and Wave 2 combined ~19 Perplexity calls across `perplexity_research`, `perplexity_search`, and `perplexity_ask`. Where this report cites canonical docs I am confident in the URL; where findings come from practitioner aggregation I attribute to the research wave and tool that produced it. A small number of URLs from the raw Perplexity responses did not survive this session's context window — the substantive findings did.

### 2.1 Solo-operator multi-agent dispatch systems (April 2026)

The public landscape for "solo developer running paid AI coding agents from mobile over Tailscale" is small. Representative patterns that showed up repeatedly in Perplexity research:

- **Stan / netclode** (detailed engineering write-up at netclode.com, fetched directly): Go + Connect RPC + Kata Containers (microVM isolation per agent) + Redis Streams (event log) + JuiceFS (shared agent workspace) + SwiftUI iOS app over Tailscale. Much more ambitious than Miru and addresses a different scale point (multi-user SaaS), but the Tailscale-as-VPN + iOS-native-client pattern is directly analogous.
- **OpenDevin / Devika / SWE-agent** self-hosted deploys: nearly all run a Python web backend (FastAPI or Flask) + in-browser UI. Almost none address mobile as a primary surface.
- **Reddit r/selfhosted and r/LocalLLaMA** threads on "running Claude Code / Aider / Continue from my phone" show that **most people just RDP / SSH in** — the dashboard-layer solution Miru is building is rare in public. That means there's very little public prior art to copy from; the corollary is that any choice the operator makes will be somewhat bespoke.
- **HN and r/ExperiencedDevs** discussions on orchestrating multiple paid agents converge on: (a) concurrency low (1-3), (b) human approval critical, (c) observability and replay of logs more valuable than scale.

**Takeaway:** the Dispatcher is architecturally correct for its niche. The closest public reference (netclode) solved a harder problem with a heavier stack, but that heavier stack isn't needed at the solo-operator scale point.

### 2.2 iOS Safari + PWA + WebSocket reliability (April 2026)

Well-documented reality:

- iOS Safari aggressively suspends background tabs and kills inactive network connections on screen lock; TCP sockets (including WebSocket and SSE) are dropped within seconds of backgrounding. ([developer.apple.com/documentation/webkit](https://developer.apple.com/documentation/webkit), multiple WebKit bug tracker threads referenced in Perplexity Wave 1.)
- PWAs added to the Home Screen in iOS 16.4+ run in a slightly different container and gain service-worker support and limited Web Push, but **background execution budget is still ~30 s** per resume event. ([webkit.org](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).)
- Low Power Mode aggressively extends these limits downward. Users don't usually notice in normal tabs; in a "dispatcher I leave open" use case it's constant.

**Mitigations that work in practice (per practitioner threads):**
- Service worker with `fetch` handler for offline shell + `visibilitychange` handler that **re-subscribes** SSE/WS on resume.
- **`Last-Event-ID` replay** on SSE reconnect — built into the spec, trivially supported by `EventSource`, and the single highest-leverage fix for iOS log dropouts. ([html.spec.whatwg.org Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html).)
- Keep the HTML shell small — ship stats and recent jobs in the initial HTML, hydrate the rest — so a cold resume renders something immediately.

### 2.3 iOS Web Push for home-screen PWAs (April 2026)

- Supported since iOS 16.4 **only for PWAs installed to Home Screen** — not regular Safari tabs. ([webkit.org/blog/13878/](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).)
- Requires a valid service worker, HTTPS (or installed PWA, which implies HTTPS or Tailscale-resolved trust), `Notification.requestPermission()` flow, and VAPID keys.
- 2026 practitioner consensus: **still flaky in edge cases** — quiet silent drops under Low Power Mode, occasionally after iOS updates. Not the load-bearing channel for an approval that *must* be seen.
- Alternative: **ntfy / Pushover / APNs via a native iOS shortcut** are more reliable than browser Web Push today. ntfy is self-hostable and often cited on r/selfhosted as "the approval-notification hammer."

**Takeaway:** add Web Push eventually but do not put approvals on it as the sole channel. Slack, in-sheet, and a secondary ntfy/Pushover path are all more reliable today.

### 2.4 Windows orphaned subprocess recovery patterns

Research surfaced a clear winner for Python on Windows:

- **Windows Job Objects** (`CreateJobObject` + `AssignProcessToJobObject` + `SetInformationJobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) — when the Dispatcher process dies, every worker assigned to its Job Object is killed by the OS. No orphan window. Accessible from Python via `pywin32` (`win32job`) or direct `ctypes` calls. ([learn.microsoft.com — Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).)
- Must spawn with `CREATE_BREAKAWAY_FROM_JOB` + explicit `AssignProcessToJobObject`, otherwise the child inherits the parent's job (which on Windows 10/11 is often a scheduler job the user doesn't control).
- `taskkill /PID <pid> /T /F` is the cheaper approximation — kills a process and its entire tree. Good for *graceful cancel*, weak for *crash recovery* (by then there's no dispatcher to run taskkill).
- Without Job Objects, the `cmd /c claude.cmd` layer at [handlers/claude.py:61-94](dispatcher/handlers/claude.py:61) makes `proc.kill()` unreliable: it kills cmd.exe, but the `claude.exe` child may have detached.

**Takeaway:** Job Objects are the 90%-solution for Miru. Roughly ~40 lines of Python plus a shared helper. Single biggest reliability win from this report.

### 2.5 Python job queue landscape for solo operators

Framework-by-framework, filtered for "solo Windows user, no broker, no Redis dependency":

- **Celery** — overkill; requires Redis/RabbitMQ, Windows support has always been second-class. Not recommended. ([docs.celeryq.dev](https://docs.celeryq.dev/).)
- **RQ** — Redis-dependent. Not recommended for this scale. ([python-rq.org](https://python-rq.org/).)
- **Dramatiq** — Redis or RabbitMQ. Same issue.
- **Huey with SQLite backend** — genuinely runs broker-less on Windows, persists tasks, supports retries and priorities, is actively maintained. The only "real" queue framework that fits Miru's constraints. ([huey.readthedocs.io](https://huey.readthedocs.io/).)
- **APScheduler** — schedule-centric, not queue-centric; not a good fit for ad-hoc operator-triggered jobs.
- **Status quo (ThreadPoolExecutor + Popen)** — works. At 2 concurrent jobs, adding Huey buys you: durable queue across restart, retry policy, priority levels. It does not buy you scale (you're not scale-limited).

**Takeaway:** Huey is the only credible migration target. But the win is *durability* and *retry semantics*, not performance. Whether it's worth the churn depends on whether the operator wants "jobs submitted at 2am survive a 3am Windows Update reboot." For now, the ThreadPoolExecutor path is defensible.

### 2.6 Flask-SocketIO vs. flask_sock vs. Channels/Starlette

- **flask_sock** (already in use for voice): thin raw-WebSocket layer, no Socket.IO protocol, no rooms/namespaces. Works with Flask threading mode natively. ([flask-sock docs on PyPI](https://pypi.org/project/flask-sock/).)
- **Flask-SocketIO**: full Socket.IO protocol with rooms, namespaces, auto-reconnect, binary support. Requires `eventlet` or `gevent` for WebSocket transport — and **eventlet is explicitly in maintenance mode / winding down** (primary maintainer stepped back in 2024, monkey-patching approach incompatible with several modern libs). Threading mode exists but **does not support WebSocket transport** — falls back to long-polling, which defeats the purpose. ([python-socketio.readthedocs.io](https://python-socketio.readthedocs.io/en/latest/), confirmed in multiple r/flask threads from Wave 2 research.)
- **Django Channels / Starlette** — requires a framework rewrite. Not on the table for Miru.
- **htmx + SSE**: emerging pattern where the server streams HTML fragments via SSE (not JSON), client replaces DOM regions via `hx-swap`. With `Last-Event-ID` it handles reconnects natively. **Cheaper than any WebSocket migration** for log-tailing and status updates. ([htmx.org/essays/](https://htmx.org/essays/), htmx SSE extension docs.)

**Takeaway:** migrating to Flask-SocketIO is a **trap** right now. eventlet risk is real, threading-mode is WebSocket-blind. The better path is to **harden the existing SSE** (add `id:` field, server-side ring buffer, honor `Last-Event-ID` on `/api/jobs/<id>/stream`) and keep flask_sock for voice.

### 2.7 AI agent approval patterns (human-in-the-loop, HITL)

Research surfaced a rough taxonomy:

1. **Terminal prompt** — original Claude Code / Aider. Doesn't work remote-from-mobile.
2. **Config-based auto-approve lists** — permissive automation with allowlist. OK for narrow scopes, dangerous for open-ended agents.
3. **In-browser modal / side-sheet** — LangGraph Studio, Devin, Anthropic's Agent Builder preview. Highest-bandwidth UX for the operator.
4. **Slack Block Kit** — popular for team workflows (e.g., devinBot, Slack's own AI Workflows). Reliable on iOS (native push, well-tested). Lowest-friction remote path today.
5. **Mobile push via ntfy / Pushover / APNs** — high-reliability poke, but tap-through is to another surface.
6. **Dedicated "approvals queue" page** — scales when approvals pile up, becomes the primary surface for long-running agents.
7. **Layered fallbacks** — primary channel + secondary channel + dead-letter queue. Best-in-class designs (e.g., Netflix-scale HITL writeups) combine 3+4 with 6 as the durable store.

Practitioner rule of thumb (multiple sources): **any approval that MUST be seen needs two independent channels**. For Miru: in-sheet (primary when operator is active on the dashboard) + Slack (fallback when screen is locked / dashboard is backgrounded) covers the case cleanly. Web Push as a third channel once the PWA ships.

**Takeaway:** Miru is already 60% of the way there — in-sheet buttons exist, Slack exists. The gap is: (a) making in-sheet primary when the client is connected, (b) server-side idempotency to prevent double-approval on Slack retry, (c) durable approval state so a dispatcher crash mid-decision isn't fatal.

---

## Part 3 — Comparative evaluation

| Axis | Current choice | Verdict | Confidence | One-line rationale |
|---|---|---|---|---|
| Web framework | Flask (+ flask_sock) | **Hold** | High | Threading-mode Flask fits 2-concurrent-job scale; async rewrite pays for nothing. |
| Job execution | ThreadPoolExecutor + Popen | **Hold**, with wrapper change | High | Keep the pattern. Wrap every spawn in a Windows Job Object. Huey migration is optional, not required. |
| Real-time layer | flask_sock (voice) + SSE (logs) + HTTP polling | **Change** | High | Harden SSE (`id:` + replay); keep flask_sock; replace some HTTP polls with SSE or htmx SSE. Do not migrate to Flask-SocketIO. |
| State | In-memory dict + SQLite | **Change** | High | Single-source-of-truth SQLite; add `pid`, `approval_payload`, reconcile on boot. In-memory cache OK as derived view. |
| Approvals | Slack primary + in-sheet buttons (built, not primary) | **Change** | Medium | Promote in-sheet to primary when client connected; keep Slack as fallback; add idempotency keys + durable approval state. |
| Frontend | Vanilla JS + custom CSS | **Hold**, with specific additions | Medium | Don't adopt Svelte/Alpine. Do add a real PWA manifest + SW + virtualized log viewer. |
| Auth | Tailscale-only, bind 0.0.0.0 | **Investigate** | Low | Matches operator intent. Add a cheap second factor (per-device token in a cookie) when PWA ships — defense in depth, not replacement. |

### 3.1 Candidate frameworks *not* in the prompt but surfaced in research

- **htmx** — viable and cheap. Keeps Flask, lets the server stream HTML fragments instead of JSON. Particularly good for log streaming with SSE extension. Does not force a rewrite; can be adopted per-component.
- **Litestar / Starlette** — don't.
- **Connect RPC + SwiftUI native client** — the netclode path. Right for a 2027+ Miru, wrong for now.
- **NATS** as a message layer — credible alternative to SQLite coordination if the operator ever adds a second machine. Not needed today.

---

## Part 4 — Migration shape & risk (per Change verdict)

### 4.1 Change #1 — Unified SQLite state + reconciliation

**Shape (staged, not big-bang):**
1. Add columns `pid INTEGER`, `approval_payload TEXT`, `last_reconciled_at INTEGER` to `job_history` via `ALTER TABLE`.
2. On every `_db_upsert_job()` call, write `pid` from `job.proc.pid` (the cmd.exe PID today, the Job-Object-guarded child PID once Change #2 lands).
3. On dispatcher boot, before opening the HTTP port: scan `SELECT id, pid, status FROM job_history WHERE status = 'RUNNING'`. For each row, `OpenProcess(pid)` to check liveness. Live → adopt into `jobs` dict as `RECONCILED`. Dead → set to `ORPHAN_LOST` with explanatory note.
4. UI treats `ORPHAN_LOST` as terminal-but-degraded — visible in history, not counted as running.
5. Keep the in-memory `jobs` dict **only** for fields SQLite can't hold (Popen handle, threading.Event, live stdout queue). Everything else reads from SQLite.

**AI-worker hours:** 4-6 hours with parallelism.
**Top regret patterns:**
- Writing to `jobs` dict and SQLite in different orders → reconciliation sees phantom states. Mitigation: single `_db_upsert_job()` is always authoritative; dict updates happen via the same function.
- PID reuse on Windows — a new process can take a dead worker's PID. Mitigation: persist `started_at` and check `GetProcessTimes()` on probe (pywin32 exposes it).
- SQLite write contention under SSE tail. Mitigation: WAL mode (likely already on; verify), batched writes where possible.
**Rollback:** new columns are nullable; drop them + revert dispatcher code. Zero data loss risk.

### 4.2 Change #2 — Windows Job Objects for worker spawns

**Shape:**
1. New helper `dispatcher/_job_objects.py`: `create_kill_on_close_job() -> handle`, `assign_process_to_job(handle, pid)`.
2. In each handler, spawn with `creationflags = CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_BREAKAWAY_FROM_JOB`.
3. Assign the child to a fresh Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
4. `ResumeThread()` the child.
5. Store the Job handle on the in-memory `job` struct (not serialized — ephemeral).
6. On dispatcher graceful shutdown: close all Job handles → OS kills all children.
7. On dispatcher crash: OS closes the handles as the process dies → OS kills all children. No orphans.

**AI-worker hours:** 3-5 hours with parallelism.
**Top regret patterns:**
- Forgetting `CREATE_BREAKAWAY_FROM_JOB` — child inherits ambient job, can't be reassigned, Job-Object semantics silently don't apply.
- `cmd /c claude.cmd` wrapper layer means you must assign the **cmd.exe** PID, and the child claude.exe is automatically bound (Job Objects are inherited). Verify with a test run that killing the Job kills both.
- Antivirus / Windows Defender occasionally flags `CreateJobObject` + immediate kill patterns. Mitigation: whitelist the dispatcher Python binary.
**Rollback:** feature-flag the helper; fall back to plain Popen on failure. Low risk.

### 4.3 Change #3 — Real PWA shell + hardened SSE

**Shape (two sub-steps that can ship independently):**

**3a — Minimal PWA:**
1. Add `dispatcher/static/manifest.webmanifest` (name, short_name, icons, start_url, display=standalone, theme_color).
2. Add `dispatcher/static/sw.js` — **offline shell only, no API caching.** Cache the HTML shell + CSS + JS bundle. Network-first for everything else.
3. Link manifest in [dispatcher.html:7](dispatcher/templates/dispatcher.html:7): `<link rel="manifest" href="/static/manifest.webmanifest">`.
4. Add `<script>` block registering SW at DOMContentLoaded.
5. Add a `visibilitychange` handler that re-opens EventSource on `visibilityState === 'visible'`.

**3b — SSE hardening:**
1. Server: maintain a bounded ring buffer per running job (e.g., last 2000 log lines), keyed by monotonic `id:` value.
2. `/api/jobs/<id>/stream` reads `Last-Event-ID` header. If present, replay from `id+1` from the ring buffer before streaming live.
3. Emit `id:` on every `data:` line.
4. Client: no change required (EventSource handles `Last-Event-ID` automatically), but add a visible "reconnected, X lines replayed" toast for debuggability.

**AI-worker hours:** PWA shell 3-4h; SSE hardening 4-6h. Total 7-10h.
**Top regret patterns:**
- Caching API responses in SW → stale approvals shown, operator acts on a stale card. **Do not cache any `/api/` path.**
- Ring buffer too small for a chatty Claude job (Claude can emit thousands of lines/minute). Mitigation: tune to 5-10k, drop oldest with explicit `event: truncation` marker so client knows.
- iOS occasionally returns an empty `Last-Event-ID` after long suspend. Mitigation: fall back to full log replay from SQLite (cheap — job log is written to file, can be tailed).
**Rollback:** service worker can be unregistered remotely by shipping an empty `sw.js`. Manifest is inert if not linked. SSE ring buffer can be disabled via env flag.

### 4.4 Investigate (medium confidence) — DOM virtualization + approval polish

**Shape:**
- Log viewer: swap naive `appendChild` at [dispatcher.js:2369](dispatcher/static/dispatcher.js:2369) for a cap-based buffer (keep last N lines in DOM, rest in-memory only, "load earlier" button). No virtualization library needed at 2000-line cap.
- Approval timeout: make the 600 s at [handlers/claude.py:147](dispatcher/handlers/claude.py:147) per-handler configurable via env var.
- Approval idempotency: add a UUID to each Slack Block Kit payload; reject duplicate callbacks.
- In-sheet promotion: when an operator session is connected to the job's SSE stream, send approval via SSE event *first*; only escalate to Slack after N seconds of no response.

**AI-worker hours:** 4-7h combined.
**Risk:** medium — these touch user-facing flow. Ship behind a feature flag per handler; roll out on simulation.py first.

---

## Part 5 — Peer comparison with Gemini 2.5 Pro

Below: Gemini's position on each section, then my grounded response. I read Gemini's opinion as "correct on direction, under-specified on Windows realities and frontend state."

### 5.1 Architecture map

**Gemini:** "Flask + threading + SQLite is appropriate for this scale; no reason to move to async."
**My response:** Agree. Adding: Gemini did not note that flask_sock is **already in prod for voice** ([task_dispatcher.py:1355-1492](dispatcher/task_dispatcher.py:1355)). That changes the "real-time stack" conversation from "add WS" to "use the WS you already have." Confidence: high.

### 5.2 Real-time layer

**Gemini:** "5-second SSE polling feels disjointed; consider Flask-SocketIO for unified WebSocket."
**My response:** Two disagreements. (1) SSE heartbeat is 1 s, not 5 s ([task_dispatcher.py:1725-1766](dispatcher/task_dispatcher.py:1725)); 5 s is the HTTP polling cadence for the *list* endpoints, which is a different channel entirely ([dispatcher.js:26](dispatcher/static/dispatcher.js:26)). (2) Flask-SocketIO's eventlet dependency is **winding down**; threading-mode Flask-SocketIO does not support WebSocket transport. This is a 2026-specific reality Gemini's pre-2025 training likely missed. Better path: harden the existing SSE with `Last-Event-ID` replay (Part 4.3b). Confidence: high, research-grounded in Wave 2.

### 5.3 Job execution

**Gemini:** "Add PID column to SQLite so you can kill orphans after restart."
**My response:** Directionally right, operationally incomplete. On Windows with `cmd /c claude.cmd` wrappers ([handlers/claude.py:61-94](dispatcher/handlers/claude.py:61)), the PID you'd store is cmd.exe's — killing cmd.exe doesn't reliably kill the claude.exe child. The correct primitive is **Windows Job Objects** (Part 4.2), which solve the problem at the OS level regardless of wrapper layers. PID-in-SQLite alone is a false-sense-of-safety fix on this stack. Confidence: high.

### 5.4 State

**Gemini:** "Drop the in-memory jobs dict; make SQLite the source of truth; reconcile on startup."
**My response:** Agree on all three. Adding: the in-memory dict can't be fully dropped — Popen handles and threading.Events aren't serializable. Structure the in-memory state as a *derived cache* of SQLite, not a parallel source of truth (Part 4.1). Confidence: high.

### 5.5 iOS PWA

**Gemini:** "iOS Safari will kill the connection on screen lock; consider Web Push and install-to-home-screen."
**My response:** Agree on the symptom, under-specified on cause. Gemini didn't notice that **there is no PWA at all** today: no manifest, no SW ([templates/dispatcher.html:7](dispatcher/templates/dispatcher.html:7) sets `apple-mobile-web-app-capable` but nothing else). So Web Push isn't just "add it" — it requires the entire PWA foundation first. Also missed: unbounded DOM log buffer at [dispatcher.js:2369](dispatcher/static/dispatcher.js:2369) is itself a cause of iOS tab backgrounding under memory pressure, independent of connection drops. Confidence: high.

### 5.6 Approval flow

**Gemini:** "Slack-only approvals are brittle; add an in-app option."
**My response:** The in-app option **already exists** — [dispatcher.js:2509-2566](dispatcher/static/dispatcher.js:2509) has Approve/Deny buttons wired to `/api/jobs/<id>/stdin`. Gemini likely didn't read the frontend deeply. The real gap is (a) making in-sheet **primary** when the client is connected, (b) idempotency keys to handle Slack retries, (c) durable approval state so dispatcher crashes mid-decision aren't fatal, (d) the hardcoded 600 s timeout at [handlers/claude.py:147](dispatcher/handlers/claude.py:147). Plus the silent-stdin-close bug (reader thread writes to a closed stdin — [handlers/claude.py:126](dispatcher/handlers/claude.py:126) vs. [handlers/claude.py:153](dispatcher/handlers/claude.py:153)). Confidence: high.

### 5.7 Overall recommendation

**Gemini:** "Don't rewrite. Targeted fixes around state + iOS + approvals."
**My response:** Agree on spirit. My top-3 Changes (Part 6) are a sharpened version of Gemini's direction, with the Windows-specific primitives (Job Objects) and the 2026-specific framework reality (Flask-SocketIO winding down) made explicit. Confidence: high.

### 5.8 Where I might be wrong

Honest self-check:
- **Flask-SocketIO may actually be fine in threading mode for some use cases** — I'm confident eventlet is declining but Socket.IO *long-polling* fallback could carry logs acceptably. If the operator wants bidirectional RPC, not just log streaming, this reopens.
- **Huey + SQLite may be worth adopting anyway** — the durable-queue win is real and the migration cost might be less than I'm estimating if the operator ever wants jobs to survive a Windows Update reboot.
- **PWA adoption might hit iOS-specific papercuts I didn't research deeply** — icons sizes, safe-area insets on iPhone 16 Pro Max notch, status-bar behavior in standalone mode. Expect ~1 day of iOS polish.

---

## Part 6 — Final recommendation

### Top 3 changes (do these)

1. **Windows Job Objects for every worker spawn.** [HIGH CONFIDENCE.] Source: Microsoft docs + Perplexity Wave 2 practitioner threads. This is the single biggest reliability win — kills orphans at the OS level, survives dispatcher crashes, makes PID tracking meaningful. Effort: ~3-5 AI-worker hours. Files: new `dispatcher/_job_objects.py`, touch all five handlers.

2. **Unified SQLite state + startup reconciliation.** [HIGH CONFIDENCE.] Source: code read + research consensus. Adds `pid`, `approval_payload`; reconciles `RUNNING` rows at boot; keeps in-memory dict as derived cache. Prerequisite for Job Objects to be meaningful (you need the PID persisted to reconcile). Effort: ~4-6 AI-worker hours.

3. **Real PWA shell + SSE hardening with `Last-Event-ID`.** [HIGH CONFIDENCE.] Source: WebKit docs + HTML spec + Wave 1/2 research. Unlocks: reliable iOS background behavior, future Web Push path, no more lost log lines on screen-lock reconnects. Effort: ~7-10 AI-worker hours, ship in two sub-steps.

### Top 3 holds (do NOT change these)

1. **Flask as the web framework.** [HIGH CONFIDENCE.] Async rewrite is unjustified at 2-concurrent-jobs. flask_sock already handles the one bidirectional channel you have. No FastAPI / Litestar migration.

2. **SQLite as the persistence layer.** [HIGH CONFIDENCE.] Right tool for a single-machine solo operator. No Redis, no Postgres. WAL mode + proper reconciliation is enough. The netclode-style stack is aspirational, not required.

3. **Slack bridge as approval fallback.** [MEDIUM-HIGH CONFIDENCE.] Keep it — it is the most reliable channel that exists today for "operator is mobile, screen is locked." In-sheet becomes primary when operator is active; Slack is the always-on safety net. Re-evaluate once Web Push on the installed PWA has six months of production uptime.

### Do not do these (explicit rejections)

- **Do not migrate to Flask-SocketIO.** [HIGH CONFIDENCE.] eventlet is winding down; threading mode lacks WebSocket. Hardened SSE is strictly better for log streaming on this stack in 2026.
- **Do not adopt Celery / RQ / Dramatiq.** [HIGH CONFIDENCE.] All require a broker; Windows support is second-class. Huey is the only one that fits and is not required at current scale.
- **Do not add auth beyond Tailscale now.** [MEDIUM CONFIDENCE.] Matches operator intent. Layer a per-device cookie token when the PWA ships, for defense in depth. Don't block mainline work on it.

### Explicit "investigate before committing"

- **Huey + SQLite migration.** [MEDIUM CONFIDENCE.] Worth a one-day spike if the operator wants durable queueing across reboots. Not a priority.
- **htmx SSE extension for list-tab updates.** [MEDIUM CONFIDENCE.] Could replace the 5s polling for `/api/jobs` and `/api/recent` with server-pushed HTML fragments. Cheaper than a Socket.IO migration; can be adopted per-endpoint. Worth a spike.
- **In-sheet approvals as primary.** [MEDIUM CONFIDENCE.] The code is 60% built. Final question is UX: is a Slack ping + silent dashboard worse or better than a dashboard card + silent Slack? Two-week trial would settle it.

---

## Appendix A — Raw agent findings

**Agent 1 — backend execution (task_dispatcher.py, handlers/*):**
- Confirmed `ThreadPoolExecutor(max_workers=2)` at [task_dispatcher.py:383](dispatcher/task_dispatcher.py:383).
- Confirmed split-brain state: `jobs_lock`/`jobs`/`jobs_order` at [task_dispatcher.py:379-381](dispatcher/task_dispatcher.py:379) vs. `job_history` SQLite schema at [task_dispatcher.py:146-165](dispatcher/task_dispatcher.py:146) (no pid column).
- Confirmed `_db_upsert_job()` at [task_dispatcher.py:187-234](dispatcher/task_dispatcher.py:187).
- Confirmed `ApprovalBridge` Slack integration at [task_dispatcher.py:436-560](dispatcher/task_dispatcher.py:436).
- Confirmed `cmd /c claude.cmd` wrapper at [handlers/claude.py:61-94](dispatcher/handlers/claude.py:61).
- Identified stdin-close-then-write bug pattern across handlers: close at [claude.py:126](dispatcher/handlers/claude.py:126), write at [claude.py:153](dispatcher/handlers/claude.py:153).
- Confirmed hardcoded 600 s `ApprovalBridge(timeout_seconds=600)` at [claude.py:147](dispatcher/handlers/claude.py:147).
- Confirmed `CREATE_NO_WINDOW` only — no `CREATE_NEW_PROCESS_GROUP`, no `DETACHED_PROCESS`, no Job Objects anywhere.

**Agent 2 — frontend / PWA / real-time (static/dispatcher.js, templates/dispatcher.html):**
- Confirmed `POLL_MS = 5000` at [dispatcher.js:26](dispatcher/static/dispatcher.js:26).
- Confirmed SSE 2 s fixed reconnect at [dispatcher.js:2396-2404](dispatcher/static/dispatcher.js:2396) with "iOS kills SSE on screen lock" comment.
- Confirmed in-sheet Approve/Deny buttons wired to `/api/jobs/<id>/stdin` at [dispatcher.js:2509-2566](dispatcher/static/dispatcher.js:2509).
- Confirmed unbounded DOM `appendChild` log buffer at [dispatcher.js:2369](dispatcher/static/dispatcher.js:2369).
- Confirmed `apple-mobile-web-app-capable` at [dispatcher.html:7](dispatcher/templates/dispatcher.html:7).
- Confirmed NO manifest link, NO `manifest.json`/`manifest.webmanifest` on disk, NO `sw.js`, NO `navigator.serviceWorker.register()` anywhere in codebase.

**Agent 3 — startup / wrappers / supervision (windows/*.ps1):**
- Confirmed Dispatcher PID tracked at [start_dispatcher.ps1:165-171](windows/start_dispatcher.ps1:165) as JSON `{pid, port, started_at, repo_root, script_path}`.
- Confirmed Windows Task Scheduler S4U logon + Limited RunLevel at [register_restart_tasks.ps1:66-100](windows/register_restart_tasks.ps1:66).
- Confirmed no pre-boot reconciliation logic in startup scripts — nothing that scans `job_history` before Flask comes up.
- Confirmed restart scripts present at `windows/restart_dispatcher.ps1` as expected.

**Gemini 2.5 Pro opinion (paraphrased from operator paste):** seven sections — architecture, real-time, execution, state, PWA, approvals, overall. Directionally aligned with this review; see Part 5 for per-section comparison.

---

**End of report.**
**Path:** `D:\dev\miru\data\batch_reports\dispatcher_architecture_review_2026-04-19.md`
**Length:** six parts + executive summary + appendix.
**Citations:** ~15 canonical-doc URLs + file:line references throughout. Where specific Perplexity source URLs from Wave 1/2 did not survive this session's context window, findings are attributed to the wave and research tool that produced them; the substantive claims remain research-grounded.
