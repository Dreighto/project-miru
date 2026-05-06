# W4 Dispatch Listener

Tiny host webhook service that receives HMAC-signed POSTs from n8n and spawns
the requested worker CLI (claude / gemini) as a child process. Built
for [PRO-83](https://linear.app/project-miru/issue/PRO-83); consumed by
PRO-84 once the W7 Dispatch button ships.

- Bind: `127.0.0.1:19100` only — never `0.0.0.0`, never any Tailscale interface.
- Auth: HMAC-SHA256 of the raw request body, header `X-W4-HMAC`, secret
  `W4_LISTENER_HMAC_SECRET` from `D:\dev\miru\.env`.
- Allowlist (hardcoded; adding any binary requires a code change + PR):
  `claude-code`, `gemini`. Codex was removed from the autonomous dispatch
  roster 2026-05-04 (connectivity gate; PR #93 stripped from this allowlist).
- n8n container reaches it via `http://host.docker.internal:19100/dispatch`.

## Why a top-level `services/` parent

This is the first Node service in a Python-dominated repo. Putting it at
`dispatch_listener/` would collide semantically with `dispatcher/` (the
existing Flask Task Dispatcher on port 19000); the two are different beasts on
different ports in different stacks. `services/dispatch_listener/` keeps the
naming clear and gives any future Node-side host services a sensible home.

## Deployment: Scheduled Task (NOT NSSM)

PRO-83 originally specified an NSSM Windows service. We tried that first, then
pivoted to a Scheduled Task because of a real, reproducible Windows quirk on
the deployment machine (ROOM):

> NSSM's default service identity is `LocalSystem`. With `LocalSystem`,
> `fs.statSync` against `C:\Users\Dreighto\AppData\Roaming\npm\` returns
> success for `gemini.cmd` but `ENOENT` for `claude.cmd` and `codex.cmd`,
> despite identical NTFS ACLs on all three files. The asymmetry appears to be
> Smart App Control / AppContainer redirection that selectively shadows those
> two binaries from non-user identities. We could not make it stat
> `claude.cmd` from `LocalSystem` no matter how we configured the service env.

The fix that aligns with the existing repo convention (Dispatcher / PM /
Miru AI all run as Scheduled Tasks — see `windows\register_restart_tasks.ps1`)
is to register a Scheduled Task with **S4U logon** as the operator. S4U gives
the task the operator's identity at boot without storing a password, which in
turn gives it full visibility of `%APPDATA%\npm\` and the AppContainer
redirects.

**Future-thread Claude Chat: please do not re-litigate the NSSM-vs-Scheduled-Task
choice for this listener.** It was decided 2026-04-25 after empirically proving
that NSSM-as-LocalSystem cannot dispatch to claude/codex on this machine. The
Scheduled Task path is locked.

### Scheduled Task spec

| Field                | Value                                                      |
| -------------------- | ---------------------------------------------------------- |
| Task name            | `MiruDispatchListener`                                     |
| Trigger              | `AtStartup` (15-second delay)                              |
| Action               | `powershell.exe -File windows\start_dispatch_listener.ps1` |
| Working directory    | `D:\dev\miru`                                              |
| Principal            | Operator user, `LogonType=S4U`, `RunLevel=Highest`         |
| Restart on failure   | `RestartCount=999`, `RestartInterval=PT1M`                 |
| Battery              | `AllowStartIfOnBatteries`, `DontStopIfGoingOnBatteries`    |
| Multiple instances   | `IgnoreNew`                                                |
| Execution time limit | `TimeSpan.Zero` (long-running daemon)                      |

The wrapper at `windows\start_dispatch_listener.ps1` invokes `node` against
`services\dispatch_listener\src\index.js` with `Start-Process
-RedirectStandardOutput -RedirectStandardError`. It also owns a respawn loop:
if the listener exits non-zero (crash, kill -9, etc.) the wrapper waits 30s
and respawns it, up to 50 times. Graceful exits (exit code 0 — what
`Stop-ScheduledTask` produces via the listener's SIGTERM handler) break the
loop. We do this in the wrapper instead of relying on Task Scheduler's
`RestartOnFailure` because empirically that setting does NOT fire on this
machine when the action exits with code 1 (verified after `taskkill /F`:
`LastTaskResult=1`, task transitions to `Ready`, but no new instance starts
within several minutes). The XML still has `RestartOnFailure` configured as a
final-fallback safety net once the wrapper's respawn budget is exhausted.

## HTTP surface

### `POST /dispatch`

Headers: `X-W4-HMAC: <hex>`, `Content-Type: application/json`.

Body:

```json
{
  "schema_version": "v1",
  "trace_id": "<uuid or 12-hex token>",
  "worker": "claude-code|gemini",
  "prompt_path": "data/n8n_inbox/<trace_id>.json",
  "timeout_seconds": 600
}
```

Responses:

| Status | When                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------ |
| `202`  | HMAC ok, allowlist ok, no prior receipt — child spawned, response is `{ trace_id, status: "spawned", spawned_at }` |
| `400`  | Missing or malformed fields, or prompt file unreadable                                                             |
| `401`  | HMAC mismatch / missing — DLQ row written                                                                          |
| `403`  | Worker not in allowlist — DLQ row written                                                                          |
| `409`  | Terminal or in-flight `<trace_id>.result.json` already exists                                                      |
| `500`  | Spawn failed (binary missing, OS error) — DLQ row written                                                          |

### `GET /health`

Returns `{ status: "ok", listener: "dispatch_listener", port: 19100 }`. Used by
the install script's post-start verification.

## Artifacts

- Receipt: `data/n8n_inbox/<trace_id>.result.json` (placeholder on accept,
  terminal on child exit). Schema is locked in PRO-83.
- DLQ: `data/dispatch_dlq.jsonl` — append-only, one JSON object per line.
- Per-trace stdout/stderr: `logs/dispatch_listener_traces/<trace_id>.{stdout,stderr}.log`.
- Service stdout/stderr: `logs/dispatch_listener_stdout.log`,
  `logs/dispatch_listener_stderr.log`.

## Operations

Install (registers the Scheduled Task; **must run from an elevated PowerShell**):

```powershell
powershell -ExecutionPolicy Bypass -File windows\install_dispatch_listener.ps1
```

Uninstall (also **must run elevated**):

```powershell
powershell -ExecutionPolicy Bypass -File windows\uninstall_dispatch_listener.ps1
```

Smoke test (does not require elevation, but the task must be installed first):

```powershell
powershell -ExecutionPolicy Bypass -File services\dispatch_listener\test\smoke.ps1
```

### Manual lifecycle commands (also require elevation because `RunLevel=Highest`)

```powershell
Start-ScheduledTask    -TaskName "MiruDispatchListener"
Stop-ScheduledTask     -TaskName "MiruDispatchListener"
Get-ScheduledTaskInfo  -TaskName "MiruDispatchListener"
```

If you need to trigger these from a non-elevated shell, register a separate
on-demand `MiruRestartDispatchListener` task with `LogonType=Interactive` and
`RunLevel=Limited` (same pattern Dispatcher / PM / Miru AI use for their
restart tasks). That's out of scope for PRO-83 but trivial to add later.

## Phase 1 decisions (locked 2026-04-25 by operator)

- **Working directory:** child workers are spawned with `cwd = D:\dev\miru`
  (the operator's live tree). Worktree isolation is deferred until concurrency
  becomes a real concern — single-operator scale doesn't justify the complexity.
- **`status` / `summary` / `files_touched`:** the listener cannot parse a
  worker's self-report yet, so Phase 1 receipts set:
  - `status = "INCONCLUSIVE"` when `exit_code === 0`
  - `status = "FAILED"` when `exit_code !== 0` or on timeout
  - `summary = ""`, `files_touched = []`

  W5 (Execution Monitor, future ticket) will populate these from real worker
  output. The schema is forward-compatible.

- **Listener restart while a child is mid-flight:** the in-flight worker is
  killed along with the listener (the spawn is no longer `detached: true` --
  empirically `detached:true` was incompatible with file-fd-based stdin on
  this Windows install, and the worker would exit 1 with empty stdout/stderr).
  Mid-flight work is lost; operator can re-dispatch. On startup the listener
  sweeps `data/n8n_inbox/*.result.json` for `status: "spawned"` rows older
  than one hour and DLQs them with `error_class: "listener_restarted"` --
  this catches any placeholder receipts that were written before the listener
  died but didn't get a terminal write.
- **`claude-code` field → `claude.cmd` binary:** the request field name matches
  W2's `chosen_worker` convention; the binary name matches the npm install. The
  mapping lives in `src/allowlist.js`.

## Known limitations (tracked in [PRO-110](https://linear.app/project-miru/issue/PRO-110))

Three Bugbot findings on commit `8ab22d6` were severity-weighted and deferred
from this PR to a focused hardening ticket. They're tracked, not forgotten.

- **Orphan-sweep timing window mismatch (Medium).** `TIMEOUT_MAX` is 30 min but
  the startup orphan sweep checks for placeholders older than 1 hour. If the
  listener restarts 30-60 min into a still-running worker's lifetime the sweep
  skips the placeholder (it stays `spawned` forever). If the restart happens
  > 1 hour into a worker's lifetime the sweep writes a `FAILED`/`listener_restarted`
  > receipt for a worker that may still be alive, and the natural exit later can't
  > update the rotated receipt. Realistic fix: tie the sweep threshold to
  > `TIMEOUT_MAX` (or check whether the prompt's timeout has actually elapsed
  > rather than using a fixed wall-clock).
- **Orphan-sweep partial-write rollback gap (Low).** If the sweep's
  `writeTerminalReceipt` succeeds but `writeDlqEntry` throws (or vice versa),
  the inner `catch` only logs. On the next restart the receipt now reads
  `status: "FAILED"` and the sweep skips it, so the missing DLQ row is never
  re-attempted. The "exactly one DLQ row per orphan" guarantee isn't actually
  enforced. Realistic fix: write DLQ first then receipt (or use a two-phase
  marker so a partial write is detectable on restart).
- **Worker child inherits `W4_LISTENER_HMAC_SECRET` via `process.env` (Low).**
  The spawned cmd inherits the listener's env verbatim, including the HMAC
  secret. Workers (`claude`, `gemini`) routinely log env, send
  telemetry, or are themselves AI models that may surface env values in
  transcripts. Realistic fix: filter `W4_LISTENER_HMAC_SECRET` (and any other
  listener-only secrets) from the child's `env` in `spawn.js`.

## Out of scope for PRO-83

- The W4 n8n workflow itself (PRO-84).
- W7 Telegram Dispatch button extension (PRO-84).
- Cursor Background Agents API integration (PRO-85, separate endpoint).
- W5 Execution Monitor that consumes receipts (future).
- Worktree isolation for the spawned worker `cwd` (deferred).
- Daily GC of `dispatch_dlq.jsonl` (future, follows PRO-77 GC pattern).
- Telegram alert thread for DLQ rows (PRO-80 Phase C).
- A separate `MiruRestartDispatchListener` Interactive/Limited task for
  no-elevation manual lifecycle commands.
