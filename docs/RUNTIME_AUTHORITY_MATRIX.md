# Runtime Authority Matrix (Project Miru / tcg-watcher)

Last updated: 2026-03-17

This is the canonical map for which runtime paths are authoritative right now.
Use this file first before editing launchers, ports, or Dev controls.

## Canonical Runtime Authority

| Surface | Authoritative repo | Canonical launcher / start path | Runtime entry point | Canonical ports |
|---|---|---|---|---|
| Main stable Project Miru site | `D:\docker\tcg-watcher` | Main repo startup flow (`windows/start_op_miru.ps1`) | `dashboard` Docker service from main repo | `8080` |
| Main Miru AI | `D:\docker\tcg-watcher` | Main repo startup flow (`windows/start_op_miru.ps1` or equivalent) | `tools/miru_ai_server.py` (main repo) | `8765` |
| Worktree Project Miru test site | `C:\Users\andre\.codex\worktrees\0814\tcg-watcher` | `windows/start_op_miru_worktree.ps1` | `dashboard/app.py` (native) or `docker-compose.worktree.yml` | `18080` |
| Worktree Miru AI / Dev control surface | `C:\Users\andre\.codex\worktrees\0814\tcg-watcher` | `windows/start_op_miru_worktree.ps1` | `tools/miru_ai_server.py` (worktree) | `18765` |

## Worktree Canonical Control Paths

- Full worktree runtime launcher: `windows/start_op_miru_worktree.ps1`
- Worktree stop script: `windows/stop_op_miru_worktree.ps1`
- Worktree startup verification: `windows/test_op_miru_worktree.ps1`
- Worktree worker scheduler wrapper: `run_miru_worker_overlap.bat`
- Worker Python entry: `python -m tools.run_worktree_worker --mode overlap --log-run`
- Worker run logs/state:
  - `data/miru_worker_last_run.json`
  - `data/miru_worker_runs.jsonl`
- Worktree learner process control API (Dev page on 18765):
  - `POST /api/dev/start-learner`
  - `POST /api/dev/stop-learner`
  - Implementation: `tools/miru_ai_server.py`
  - PID/log path: `data/startup-logs/miru_learner_worktree.pid` and `data/startup-logs/miru_learner_worktree_*.log`

## Dev Page Alignment (Worktree)

- Worktree Dev UI should be served from `tools/templates/miru_ai.html` and `tools/static/miru_ai.{css,js}` in this worktree.
- The Dev page control actions should run against the local worktree server on `18765` (not main `8765`) when started via `windows/start_op_miru_worktree.ps1`.
- Use `GET /api/dev/debug-routes` on the running instance to verify `server_file` and `cwd` point to the expected repo path.

## Worktree Non-Canonical / Legacy (Do Not Use As Primary Launcher)

- `windows/start_op_miru.ps1` (legacy/main-runtime launcher shape, 8080/8765 assumptions)
- `windows/README.md` (legacy startup doc)
- `docs/SOURCE_OF_TRUTH.md` (historical 8080/8765 troubleshooting context)
- `run_miru_dev.ps1` (Miru AI-only convenience launcher; not the canonical full worktree runtime path)

## Safety Notes

- If your target is worktree testing, use only `18080` and `18765`.
- Do not treat files under `D:\docker\tcg-watcher` as worktree runtime authority.
- Do not treat files under `C:\Users\andre\.codex\worktrees\0814\tcg-watcher` as main production authority.
- Learner start/stop should be driven from the worktree Dev page (`18765`) so process guardrails are applied.
