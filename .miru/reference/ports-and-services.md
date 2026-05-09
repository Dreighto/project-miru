# Reference — Ports and Services

```text
Reference: ports-and-services
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: you need a port number or service mapping.
Last reviewed: 2026-05-09
```

## Ports — Permanent Reference

| Port  | Service              | Status   | Notes                                                                         |
| ----- | -------------------- | -------- | ----------------------------------------------------------------------------- |
| 15678 | n8n                  | ACTIVE   | Workflow runtime. Restart via `MiruN8nWatchdog` task or n8n container.        |
| 18080 | Project Miru UI (PM) | ACTIVE   | Flask dashboard. Restart: `windows\restart_pm.ps1` (or `MiruRestartPM` task). |
| 18765 | Miru AI              | ACTIVE   | Restart: `windows\restart_miru_ai.ps1` (or `MiruRestartMiruAI` task).         |
| 18766 | MCP Gateway          | ACTIVE   | FastMCP gateway for remote workers. Restart: `MiruRestartMcpGateway` task.    |
| 19000 | Dispatcher           | ACTIVE   | `dispatcher\task_dispatcher.py`. Started by `OP Miru Startup`.                |
| 19100 | Dispatch Listener    | ACTIVE   | Node service in `services\dispatch_listener\`. See `restart-procedures.md`.   |
| 11434 | Ollama               | EXTERNAL | Local dependency, not Miru-owned. Hermes-Qwen models served from here.        |
| 8080  | (reserved)           | RESERVED | Do not touch.                                                                 |
| 8765  | (forbidden)          | NEVER    | Do not touch under any circumstances.                                         |

## Service ownership notes

- **dispatch_listener (19100)** must run in the operator's interactive Windows session (Session 1/2), not Session 0 — otherwise non-elevated workers cannot kill/restart it. See `.miru/reference/restart-procedures.md` and PRO-336 for the boot-path fix.
- **MCP Gateway (18766)** is the connector entry for remote workers and the claude.ai connector via Tailscale Funnel. Any middleware change here MUST be smoke-tested against a real claude.ai connector before merging — unit tests can pass while live access breaks (see `adopted-lessons.md`).
- **Hermes shadow predictor (PRO-329 Stage 1)** runs against an Ollama-served Qwen model on the standard 11434 port. No dedicated port for Hermes itself — it's invoked at worker spawn time by `services\dispatch_listener\src\spawn.js`.
