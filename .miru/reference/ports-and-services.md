# Reference — Ports and Services

```text
Reference: ports-and-services
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: you need a port number or service mapping.
Last reviewed: 2026-05-09 (verified against actually-listening sockets)
```

## Ports — Permanent Reference

| Port  | Service              | Status         | Notes                                                                                                                                                                                                                                         |
| ----- | -------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 15678 | n8n                  | ACTIVE         | Workflow runtime via Docker + WSL relay. Restart via `MiruN8nWatchdog` task.                                                                                                                                                                  |
| 18080 | Project Miru UI (PM) | ACTIVE         | Flask dashboard. Runs in Session 0. Restart: `windows\restart_pm.ps1`.                                                                                                                                                                        |
| 18765 | Miru AI              | ACTIVE         | Runs in Session 0. Restart: `windows\restart_miru_ai.ps1`.                                                                                                                                                                                    |
| 18766 | MCP Gateway          | ACTIVE         | FastMCP, Session 2. Hosts Gatekeeper as in-process module. Restart: `MiruRestartMcpGateway`.                                                                                                                                                  |
| 19100 | Dispatch Listener    | ACTIVE         | Node service in `services\dispatch_listener\`. See `restart-procedures.md`.                                                                                                                                                                   |
| 11434 | Ollama               | EXTERNAL       | Local dependency, not Miru-owned. Hosts qwen2.5:7b/14b, qwen2.5-coder:7b/14b, llama3.2:3b, mistral:7b-instruct, embeddinggemma, miru-router (custom).                                                                                         |
| 19000 | (decommissioned)     | DECOMMISSIONED | Was the old `dispatcher\task_dispatcher.py` (Flask UI + WebSocket + Slack-bolt). Stripped to 60-line deprecation stub by PR #94 (PRO-301, 2026-05-06). The dispatcher was reborn as the Local Governance Gatekeeper — see "Gatekeeper" below. |
| 8080  | (reserved)           | RESERVED       | Do not touch.                                                                                                                                                                                                                                 |
| 8765  | (forbidden)          | NEVER          | Do not touch under any circumstances.                                                                                                                                                                                                         |

## Services that DO NOT have ports (in-process modules)

- **Local Governance Gatekeeper** — Python module at `gatekeeper/gatekeeper.py` (760 lines), `gatekeeper/frontmatter_parser.py` (173), `gatekeeper/forwarder.py` (238). MCP-tool wrapper at `tools/miru_mcp_gateway/gatekeeper_tools.py`. Bench harness + GBNF grammar at `tools/gatekeeper/`. Locked model: `DEFAULT_MODEL = qwen2.5:7b` (per 3-model bench, 2026-05-06). Validates conversational dispatches before HMAC-signed POST to dispatch_listener (19100). NO dedicated port — invoked by the gateway in-process.
- **Hermes Stage 0 (apprentice bridge)** — Python script at `tools/hermes_apprentice.py` (24 tests, PRO-312). Manual invocation. Joins `data/routing_history.jsonl` + callbacks. Produces `data/hermes_quality_labels.jsonl` (120 rows backfilled).
- **Hermes Stage 1 (shadow predictor)** — Embedded in `services/dispatch_listener/src/spawn.js:625` (PRO-329). Calls Ollama qwen2.5:7b at every spawn. Logs to `data/hermes_predictions.jsonl`. Observation only — no routing authority yet. ~8-18s latency per prediction (fire-and-forget, never blocks spawn).

## Service ownership notes

- **dispatch_listener (19100)** must run in the operator's interactive Windows session (Session 1+), not Session 0 — otherwise non-elevated workers cannot kill/restart it. See `.miru/reference/restart-procedures.md` and PRO-336 for the boot-path fix.
- **MCP Gateway (18766)** is the connector entry for remote workers and the claude.ai connector via Tailscale Funnel. Any middleware change here MUST be smoke-tested against a real claude.ai connector before merging — unit tests can pass while live access breaks (see `adopted-lessons.md`). Localhost-bind on `full_operator` profile shipped via DGAS Tier 1 (PR #136).
- **Gatekeeper** runs IN-PROCESS in the gateway. No separate restart procedure — restart the gateway and the Gatekeeper restarts with it.
