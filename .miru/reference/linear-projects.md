# Reference — Linear Projects

```text
Reference: linear-projects
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: creating a Linear ticket and you need a projectId.
Last reviewed: 2026-05-09
```

The `linear_projects` table in the miru_memory DB is the authoritative source.
Quick reference below.

The behavioral rule that every ticket MUST include a `projectId` lives in
`.miru/overlays/workflow-dispatch.md`. This file is the lookup table.

---

**Team: Project Miru (key: PRO, team_id: f9d6193c-4572-40a9-b834-c408439f1aa1)**

| Project                       | ID                                     | Route tickets here for                                        |
| ----------------------------- | -------------------------------------- | ------------------------------------------------------------- |
| PM Storefront                 | `ff3233bb-a958-484b-9009-b19a6a5063a5` | Storefront UI, card browsing, user-facing PM features         |
| Miru Orchestration / Autonomy | `2ba0133d-6f39-41a6-9846-9566e7c895ec` | Worker dispatch, orchestration, autonomy rules, routing logic |
| Tooling / MCP Gateway         | `cb5c362c-c1f4-4f55-b119-578fa017ca7d` | MCP server config, gateway, tool permissions                  |
| Automation / Integrations     | `d0701b07-d4c6-4f18-a72a-3e4e817b50f5` | n8n workflows, Telegram bots, alerts, watchdogs               |
| Memory / Context System       | `b94573e3-be3b-4c2a-8022-8fbf87e8581f` | Memory files, context boot, session continuity                |
| Docs / Canon / Process        | `9816755f-1bec-40c6-8c8b-17a2be9a688e` | CLAUDE.md, AGENTS.md, operating docs, process rules           |
| Research / Experiments        | `ebe8640f-e79e-4b88-b450-c6fe0e3d3d28` | Spikes, evals, benchmarks, proofs of concept                  |

**Team: NASDOOM (key: NAS, team_id: aaddbe1a-a8a2-48fe-bebf-4adb34d67618)**

| Project           | ID                                     | Route tickets here for                                  |
| ----------------- | -------------------------------------- | ------------------------------------------------------- |
| NASDOOM Dashboard | `db48a3f5-73e7-4289-bbcc-0732028f5041` | NAS dashboard UI, SvelteKit, Plex/Sonarr/Radarr/SABnzbd |

**Team: LogueOS (key: LOS, team_id: 98159aeb-6c6b-44e6-aa90-d50d51e58761)** — added 2026-05-09

| Project               | ID                                     | Route tickets here for                                                                                                                                    |
| --------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LogueOS Console       | `e68181cf-ed3c-459d-b253-d2bf84b3060a` | SvelteKit dashboard work — UI, components, monitoring screens. Repo: `Dreighto/LogueOS-Console`. Worker dispatches use `target_repo=LogueOS-Console`.     |
| LogueOS Framework     | `34fa16f5-365e-483d-aa87-2010a49585b5` | Methodology docs at `D:\dev\LogueOS\` (01-07 + workers/), framework evolution, eventual extraction into `Dreighto/LogueOS` repo.                          |
| LogueOS Orchestration | `b0b0f119-782e-4e70-8de9-8ec1a3cd359a` | PARKED. Future extraction of project-miru orchestration layer (dispatch_listener, gateway, n8n routing, Gatekeeper, Hermes) into standalone LogueOS repo. |
| LogueOS (catch-all)   | `c03d8e7f-bd03-4136-9faa-a6734cba7abd` | Default project for LogueOS-team work that doesn't fit Console / Framework / Orchestration. Use only when the right project isn't obvious.                |

**Never use** the legacy "Project Miru" catch-all (`7c2b40d5-058a-457d-84c7-d57d6bf3f281`). Always pick the specific project above. If unsure: default to Miru Orchestration / Autonomy for internal system work, or Docs / Canon / Process for rule/doc changes.

**New project creation:** If a ticket genuinely does not fit any existing project, create a new Linear project for it — but only when the mismatch is real, not just adjacent. The bar is high: forcing a ticket into the wrong project is worse than having many projects, but creating a project for one ticket is wasteful. If unsure, ask the operator.

**Timeline:**

- **2026-05-04** — projectId requirement set. Root cause: tickets were created without `projectId` and landed at team level, making them unfindable by project.
- **2026-05-07** — "New project creation" rule above added by operator.
- **2026-05-09** — LogueOS team (LOS) created with 4 projects. Coupled with multi-repo dispatch infrastructure landing the same day (`infra/multi-repo-dispatch` PR): `target_repo` parameter on `dispatch_worker` MCP tool, per-repo worktree pools in `services/dispatch_listener/src/worktree.js`. LOS tickets dispatch to `target_repo=LogueOS-Console` and land in `D:\dev\LogueOS-Console-w*` worktrees, separate from `D:\dev\miru-w*`.
