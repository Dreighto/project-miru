# Runtime Authority Matrix (Project Miru / tcg-watcher)

Last updated: 2026-04-03

This is the canonical map for which runtime paths are authoritative right now.
Use this file first before editing launchers, ports, or Dev controls.

## Canonical Runtime Authority

| Surface | Authoritative repo | Canonical launcher / start path | Runtime entry point | Canonical ports |
|---|---|---|---|---|
| Main stable Project Miru site | `D:\docker\tcg-watcher` | RETIRED — do not use. Legacy NAS stack (D:\docker) is dead. | `dashboard` Docker service from main repo | `8080` |
| Main Miru AI | `D:\docker\tcg-watcher` | RETIRED — do not use. Legacy NAS stack (D:\docker) is dead. | legacy main repo Miru AI path | `8765` |
| Worktree Project Miru test site | `D:\dev\tcg-watcher-worktree` | `windows/start_op_miru_worktree.ps1` | `pm/app.py` (native) or `docker-compose.worktree.yml` | `18080` |
| Worktree Miru AI / Dev control surface | `D:\dev\tcg-watcher-worktree` | `windows/start_op_miru_worktree.ps1` | `python -m miru_ai.server` -> `miru_ai/server.py` | `18765` |

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
  - Implementation: `miru_ai/server.py`
  - PID/log path: `data/startup-logs/miru_learner_worktree.pid` and `data/startup-logs/miru_learner_worktree_*.log`

## Dev Page Alignment (Worktree)

- Worktree Dev UI should be served from `miru_ai/templates/miru_ai.html` and `miru_ai/static/miru_ai.{css,js}` in this worktree.
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
- Do not treat files under deleted historical worktrees as runtime authority; the only valid worktree root is `D:\dev\tcg-watcher-worktree`.
- Learner start/stop should be driven from the worktree Dev page (`18765`) so process guardrails are applied.

## Worker Path Law

- Active worker/data scripts must derive paths from `Path(__file__).resolve()` or script-root-relative paths.
- Do not hardcode `C:\Users\andre\.codex\worktrees\0814\tcg-watcher` in any active runtime or worker script.
- Canonical data/log roots for worker code in this repo are `data/` and `logs/` under `D:\dev\tcg-watcher-worktree`.
