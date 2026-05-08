# CH Tool Operations — Capability Index

> Boot context for Claude Chat. Read this on every session start.
> For multi-tool composition patterns, load `docs/ch_operations/CH_PLAYBOOK.md` on demand.

**Canon definition:** "Canon" means **Notion pages** and **memory DB** — the persistent truth surfaces. Repo files are repo files, not canon. When verifying or updating canon, that means Notion and the `miru_memory` DB.

---

## Capability Index

- **Read / Inspect** — file reads, DB queries, health checks, log tails
- **Track / Query** — ticket lookups, activity polling, execution history
- **Create / Mutate** — ticket creation, messaging, memory writes, dispatch
- **Dispatch / Route** — worker availability, orchestration, W2 triggers
- **Communicate** — notification patterns and Telegram messaging
- **Gotcha Reference** — common failure modes and technical pitfalls

---

## Read / Inspect (Passive Observation)

| Need to...              | Use                                     | Module             | Watch out for                                   |
| ----------------------- | --------------------------------------- | ------------------ | ----------------------------------------------- |
| Read a repo file        | `fs_read_text_file`                     | fs_tools           | Path must be under `D:\dev\miru*`               |
| Read multiple files     | `fs_read_multiple_files`                | fs_tools           | Array of paths; same boundary rule              |
| Read card DB            | `sqlite_all` / `sqlite_get`             | sqlite-ro-snapshot | Read-only — never write                         |
| Check service health    | `system_check_health_endpoints`         | system_tools       | Returns JSON with port status                   |
| Check open ports        | `system_check_ports`                    | system_tools       | Verifies expected ports are listening           |
| Read PR diff            | `github_get_pr_diff`                    | github_tools       | Large diffs truncated                           |
| Read PR reviews         | `github_list_pr_reviews`                | github_tools       | Returns review status + body                    |
| Read PR review comments | `github_get_pr_review_comments`         | github_tools       | Inline comments on changed lines                |
| Read PR check runs      | `github_get_pr_check_runs`              | github_tools       | CI status, CodeRabbit, Bugbot                   |
| Read routing history    | `n8n_read_routing_history`              | n8n_tools          | Append-only — latest entries at bottom          |
| Tail a log file         | `system_tail_safe_log`                  | system_tools       | Max 200 lines                                   |
| Check worker status     | `worker_status` / `worker_availability` | worker_tools       | Checks heartbeat + completion log               |
| Read gateway audit      | `gateway_audit_tail`                    | audit_read_tools   | Shows recent tool profile enforcement           |
| List directory          | `fs_list_directory`                     | fs_tools           | Non-recursive; use `fs_directory_tree` for deep |
| Search files by name    | `fs_search_files`                       | fs_tools           | Pattern matching on filenames                   |
| Read file metadata      | `fs_get_file_info`                      | fs_tools           | Size, dates, permissions                        |

---

## Track / Query (State Monitoring)

| Need to...            | Use                          | Module          | Watch out for                                                                                         |
| --------------------- | ---------------------------- | --------------- | ----------------------------------------------------------------------------------------------------- |
| Get Linear ticket     | `get_issue`                  | linear MCP      | Use `get_issue` for single, `list_issues` for bulk                                                    |
| Check ticket state    | `get_issue`                  | linear MCP      | State name must match exactly                                                                         |
| List open PRs         | `github_list_open_prs`       | github_tools    | Returns summary of all open PRs                                                                       |
| Get single PR         | `github_get_pr`              | github_tools    | Full PR details including merge state                                                                 |
| Poll for completion   | `activity_since`             | activity_tools  | Returns cross-system timeline (Linear, GitHub, n8n, file modifications) — does not parse log contents |
| Query n8n execution   | `n8n_get_execution`          | n8n_tools       | ID from `n8n_list_recent_executions`                                                                  |
| Get execution summary | `n8n_get_execution_summary`  | n8n_tools       | Lighter than full execution data                                                                      |
| List n8n executions   | `n8n_list_recent_executions` | n8n_tools       | Most recent first                                                                                     |
| Read memory table     | `read_query`                 | miru_memory MCP | SQL query against memory DB                                                                           |
| List memory tables    | `list_tables`                | miru_memory MCP | Shows all available tables                                                                            |
| Describe memory table | `describe_table`             | miru_memory MCP | Column names and types                                                                                |
| Check dispatch status | `worker_availability`        | worker_tools    | Returns idle/busy/stalled                                                                             |
| Read recent commits   | `github_list_recent_commits` | github_tools    | Default branch history                                                                                |
| Get repo status       | `github_get_repo_status`     | github_tools    | Branch protection, default branch                                                                     |

---

## Create / Mutate (State Change)

Each row includes the authority gate CH must check before acting.

| Need to...                 | Use                                                 | Authority gate              | Hard limit                                             |
| -------------------------- | --------------------------------------------------- | --------------------------- | ------------------------------------------------------ |
| Create Linear ticket       | `save_issue`                                        | CH freely                   | Must include `projectId` — see CLAUDE.md project table |
| Update Linear state        | `save_issue` (with state)                           | CH freely                   | State name must match exactly                          |
| Add Linear comment         | `save_comment`                                      | CH freely                   | —                                                      |
| Send Telegram message      | `telegram_send_message`                             | CH freely                   | Operator chat only                                     |
| Write memory row           | `write_query`                                       | CH freely                   | SQL INSERT/UPDATE against memory DB                    |
| Write memory insight       | `append_insight`                                    | CH freely                   | miru_memory MCP (separate server from gateway)         |
| Dispatch worker            | `dispatch_worker`                                   | Check availability first    | 5/min rate limit                                       |
| Create/update n8n workflow | `n8n_update_workflow`                               | Operator-merge PR           | Never in production without test                       |
| Activate/deactivate n8n    | `n8n_activate_workflow` / `n8n_deactivate_workflow` | CH freely                   | Verify workflow ID first                               |
| Restart service            | `service_restart`                                   | CH freely                   | Use approved restart scripts only                      |
| Write canon doc            | `docs_patch_file` / `docs_write_file`               | Standing authority (VP Ops) | Allowlisted paths only                                 |
| Append to canon doc        | `docs_append_file`                                  | Standing authority (VP Ops) | Allowlisted paths only                                 |
| Git commit + push          | `git_commit_and_push`                               | CH (allowlisted files)      | No workflow JSON, no DB, no JSONL                      |
| Delete GitHub branch       | `github_delete_branch`                              | CH freely                   | Only after confirmed merge                             |
| Create GitHub PR           | `create_pull_request` (GitHub MCP)                  | CH freely                   | Not in gateway — use GitHub MCP directly               |
| Add PR comment             | `github_create_pr_comment`                          | CH freely                   | Posts comment on existing PR, not PR creation          |

---

## Dispatch / Route (Orchestration)

Cross-references `docs/dispatch_contract.md` for the full prompt template and authority tiers.

| Need to...            | Use                        | Pre-check                        | Post-check                                            |
| --------------------- | -------------------------- | -------------------------------- | ----------------------------------------------------- |
| Check worker idle     | `worker_availability`      | —                                | Returns idle/busy/stalled                             |
| Dispatch task         | `dispatch_worker`          | Worker must be idle              | Poll `activity_since` for completion                  |
| Check dispatch result | `activity_since`           | minutes (time window)            | Scan results for trace_id or ticket_id                |
| Read dispatch log     | `n8n_read_routing_history` | —                                | Latest entry = most recent routing                    |
| Trigger W2 manually   | `n8n_trigger_w2_route`     | —                                | Starts the full routing pipeline (in n8n_write_tools) |
| VP Ops verify ticket  | `vp_ops_verify_ticket`     | Ticket must be in terminal state | Returns verification result                           |

---

## Communicate (Notifications)

| Need to...              | Use                        | Notes                                                  |
| ----------------------- | -------------------------- | ------------------------------------------------------ |
| Send operator a message | `telegram_send_message`    | Use for escalations, status updates, approval requests |
| Add PR comment          | `github_create_pr_comment` | For review feedback or status notes on PRs             |
| Add Linear comment      | `save_comment`             | For ticket-level communication with workers            |
| Search Perplexity       | `perplexity_search`        | Research tasks only — not a decision-maker             |

---

## Gotcha Reference

Common failure modes. The Decision Trigger column maps to `agent_decisions.jsonl` trigger types — log a calibration entry when a gotcha fires.

| Gotcha                                | Symptom                                            | Fix                                                  | Decision Trigger              |
| ------------------------------------- | -------------------------------------------------- | ---------------------------------------------------- | ----------------------------- |
| Missing projectId on ticket           | Ticket invisible in project view                   | Always pass `projectId` from CLAUDE.md project table | `scope_interpretation`        |
| `linear_create_issue` vs `save_issue` | Gateway tool lacks projectId field                 | Use Linear MCP's `save_issue` directly               | `fallback_or_retry`           |
| Polling too early                     | `activity_since` returns empty                     | Wait 60s after dispatch before first poll            | `fallback_or_retry`           |
| Stale worker status                   | `worker_availability` says idle but worker crashed | Cross-check with `system_check_health_endpoints`     | `verification_interpretation` |
| Append-only violation                 | Pre-commit hook or test fails                      | Never read-modify-write JSONL files — append only    | `canon_interpretation`        |
| Wrong state name in Linear            | State transition silently fails                    | Check `list_issue_statuses` for exact names          | `fallback_or_retry`           |
| n8n execution not found               | `n8n_get_execution` returns error                  | Get ID from `n8n_list_recent_executions` first       | `fallback_or_retry`           |
| Tool profile denied                   | Worker can't call a tool it expects                | Check `gateway_audit_tail` for profile denial logs   | `verification_interpretation` |

Decision trigger types (full list): `worker_selection`, `scope_interpretation`, `canon_interpretation`, `risk_classification`, `alternative_rejected`, `confidence_claim`, `fallback_or_retry`, `escalation_or_non_escalation`, `verification_interpretation`.

---

## Load-on-Demand Trigger

Before composing 3+ tools for a multi-step operation, read `docs/ch_operations/CH_PLAYBOOK.md` — load only the section matching your pattern, not the whole file.

When encountering a tool failure not covered by the gotcha table above, check the playbook's Error Recovery Index.
