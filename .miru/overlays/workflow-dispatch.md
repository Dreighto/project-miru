# Overlay — workflow-dispatch

```text
Overlay: workflow-dispatch
Architecture: MIRU-INSTRUCTIONS-v2
Load when: orchestrating dispatch, configuring gateway profiles, working on W2 routing, or creating Linear tickets.
Last reviewed: 2026-05-09
```

This overlay carries the rules for orchestration: orchestrator decision
authority (CC + Hermes shadow predictor while CH offline), gateway tool
profile enforcement, ingress classifier behavior, orchestrator-side modules,
and the Linear `projectId` requirement.

---

## Linear — Ticket Routing — Hard Rule

Every Linear ticket created by any worker (CC, CH, Gemini, Codex, Cursor — anyone) **MUST include a `projectId`**. Never create a ticket at team level only — tickets without a project are invisible to the project-based workflow and will be lost.

**Loop tickets** (work for claude-code or gemini-cli auto-pickup) are filed by **CC directly** via `linear_create_issue` — no operator paste step. Operator-side or benched-worker tickets follow the file-then-paste pattern (operator runs the paste).

The full project ID table is in `.miru/reference/linear-projects.md`. The `linear_projects` table in the miru_memory DB is the authoritative source.

If unsure: default to **Miru Orchestration / Autonomy** for internal system work, or **Docs / Canon / Process** for rule/doc changes.

---

## Autonomous Operations — Orchestrator Decision Authority

```text
Authority holder while CH offline (set 2026-05-07):
  - CC (acting orchestrator) for routing, dispatch, ticket lifecycle, execution judgment, ops.
  - Hermes shadow predictor (Stage 1, PRO-329) logs predictions alongside CC's actual decisions
    for evaluation. Hermes does not yet hold authority — Stage 2+ takes over routing once the
    prediction track-record is validated.
When CH returns: lead orchestrator role returns to CH per pre-2026-05-07 baseline.
```

The default operating mode is **decide → act → report**. Asking the operator is the exception,
not the norm. When in doubt: if the decision is local and reversible, make it and note it.
If it's irreversible or external, ask first.

**Session-level authorization rule (set 2026-05-08):** when the operator says "do what you
need this session" or equivalent, execute within scope without pausing for individual
confirmations. Don't re-ask after blanket authorization — that wastes operator time and
defeats the purpose of granting it.

### Decisions the acting orchestrator (CC, while CH offline) makes without asking

**Routing and dispatch:**

- Which worker to assign a ticket to (use worker-roster.md and the ticket's nature as inputs)
- Whether to run workers in parallel or sequentially (based on file overlap and dependency check)
- Which Ollama model to use for a routing or analysis step (use model assignment table in worker-roster.md)
- Whether to retry a failed dispatch (1 retry max per ticket per worker, then escalate)

**Ticket lifecycle:**

- Moving a Linear ticket to In Progress when a worker is dispatched
- Moving to In Review when a PR is opened
- Moving to Done when the completion marker is confirmed and PR merged
- Filing follow-up Linear tickets for out-of-scope findings discovered during a task

**Execution judgment:**

- Filling minor spec gaps that don't affect architecture or external contracts — note the fill in the completion report
- Choosing PR title, description, and branch name
- Whether a PR qualifies for CC self-merge (apply the merge policy table in `.miru/overlays/workflow-git.md`)
- Post-merge cleanup: branch deletion, return-to-main
- Ordering tasks within a sprint when priorities are clear from ticket state

**Ops:**

- Re-dispatching a stalled worker within the recovery_router.py auto-retry budget
- Reading any log, completion marker, or state file to assess system health before a dispatch
- **Restarting services autonomously** (gateway, dispatch_listener, PM, Miru AI) — do not ask the operator for routine restarts. Use the registered restart tasks or the documented restart scripts. Operator action is only needed when the process is in Session 0 (see `.miru/reference/restart-procedures.md`).
- **Auto-dispatching during testing.** While the loop is being validated, CC routes work directly via the `dispatch_worker` MCP tool. Skip the Telegram approval gate for tickets CC files itself. Default `tool_profile=standard_worker` unless the ticket is read-only (use `drift_executor`). Promote back to operator-approval-by-default after the loop is proven stable.

### When to send a Telegram and wait for the operator

These are operations that can proceed but require operator approval first.
**Hard prohibitions** (e.g. "never write to `card_catalog.db`", "CC must never modify `.mcp.json`") are listed in CLAUDE.md core and worker role files — they are NOT in this list because they can never proceed, with or without approval.

Ask before acting if **any** of these apply:

- **Infrastructure** — new port assignment, new service, new external API integration, new scheduled task
- **Schema or data-model changes** — proposed modifications to `card_catalog.db` schema, `routing_history.jsonl` schema, or append-only file structure (direct writes to `card_catalog.db` and edits to append-only files are forbidden, not escalatable — see core rules)
- **Scope expansion** — completing the ticket would require touching files outside the original scope, or adds capability not in the spec
- **Security** — anything touching auth, secrets, credentials, or access control (where the operation is permitted at all — secrets handling has hard prohibitions in core)
- **Irreversible ops** — force-push to non-protected branches, drop table, delete branch with unmerged work, clear production data (force-push to `main` is a hard prohibition, not escalatable)
- **Strategy** — "should we build X or Y?" where the operator's product judgment is the input, not engineering reasoning
- **Repeated failure** — same worker, same ticket, failed more than twice

### Minimal escalation format

When escalating to the operator via Telegram, state exactly one decision needed — not a status
update, not a list of options to consider. The operator should be able to reply in one word or
tap a button. If you need more than one decision, send one message per decision.

---

## Gateway Tool Profile Enforcement (Phase 3 — Subagent Isolation)

Dispatched workers connect to the MCP Gateway via a `.mcp.json` generated in their worktree at dispatch time. Each worker runs under a tool profile set by the `MIRU_TOOL_PROFILE` environment variable, passed to the gateway as the `X-Miru-Tool-Profile` HTTP header.

**Profiles (deny-all default):**

| Profile           | Purpose                                              | Restricted from                                                                     |
| ----------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `drift_executor`  | Routine drift scans. Read-everything, write-nothing. | telegram, dispatch, restart, vp_ops, linear_write, n8n_write, docs_write, git_write |
| `reviewer`        | Peer review. Same as drift_executor.                 | (same as drift_executor)                                                            |
| `standard_worker` | Ticket-executing subagents.                          | telegram, dispatch, restart, vp_ops                                                 |
| `vp_ops`          | VP Ops verification.                                 | telegram, dispatch, restart                                                         |
| `full_operator`   | Operator's direct session (default when no header).  | (unrestricted)                                                                      |

**Enforcement state:** Controlled by `MIRU_PROFILE_ENFORCEMENT_ENABLED` env var. When off (default), profiles are extracted and logged but not enforced (audit mode). When on, denied tool calls raise `McpError -32003`.

**Key rules:**

- Unknown profile strings get `drift_executor` restrictions (most conservative)
- No header = `full_operator` (backward-compatible for operator's direct session)
- Tool Access and Canon Authority are SEPARATE gates — no profile grants canon-write authority
- Denials are logged to `logs/mcp_gateway_reads.jsonl` with `result: "profile_denied"`
- Profile definitions live in `tools/miru_mcp_gateway/profiles.py`

---

## Ingress Classifier (Phase 4 — Automatic Profile Assignment)

The W2 router automatically classifies tasks and assigns a tool profile before dispatch. The classifier node `w2008a-assign-profile` runs after risk classification (w2008) and before the confidence branch (w2009).

**Task modes and profile mapping:**

| Mode      | Profile           | When assigned                                                              |
| --------- | ----------------- | -------------------------------------------------------------------------- |
| routine   | `drift_executor`  | Keywords: audit, read-only, repo scan, schema read, second opinion, etc.   |
| judgment  | `standard_worker` | Task types: Bug, Feature, Improvement, chore, design (default for unknown) |
| ambiguous | `reviewer`        | Keywords: unclear, investigate, figure out, explore options, etc.          |
| blocked   | (no dispatch)     | Keywords: blocked, waiting on, depends on                                  |

**Classification tiers:**

1. **Tier 1 — Keywords** take precedence. Surface keywords from the ticket are checked against rule lists.
2. **Tier 2 — Task type** fallback. If no keyword match, task_type determines the mode.
3. **Safety override:** High-risk tasks never get `drift_executor` — bumped to `judgment/standard_worker`.

**Classification rules** are externalized in `data/config/w2_profile_rules.json` and loaded at execution time. Tunable without workflow redeployment.

**Operator override:** The Telegram proposal message shows the suggested profile. A Profile button lets the operator override the profile before approving. Profile overrides are recorded as `profile_override` rows in `pending_callbacks.jsonl`.

**Plan-only mode:** Ambiguous tasks dispatched with `reviewer` profile get plan-only instructions injected into the prompt. The worker produces a plan in its completion output (the operator posts it) — no branches, PRs, or file modifications.

**Audit trail:** `routing_history.jsonl` records `suggested_profile`, `final_profile`, `task_mode`, and `profile_rationale` for every routing decision.

**Key rules:**

- `vp_ops` and `full_operator` are never classifier-assigned — those are operator-only
- Manual dispatches (operator labels ticket directly in Linear) default to `standard_worker`
- Profiles are NOT canon-authority grants — Tool Access and Canon Authority remain separate gates
- Profile definitions still live in `tools/miru_mcp_gateway/profiles.py` (Phase 3, unchanged)

---

## Orchestrator-side modules (PRO-187 follow-on, 2026-04-28)

Production worker coordination helpers live under `tools/orchestrator/`. Workers should not create parallel implementations elsewhere.

- `stall_detector.py` reads `data/cc_heartbeat_log.jsonl` and `data/cc_completion_log.jsonl` to emit `StallEvent` rows using the PRO-178 taxonomy.
- `recovery_router.py` maps stall classes to deterministic recovery actions and forces human escalation for schema, security, scope expansion, or irreversible-operation contexts.

> Note: `task_store.py` (active task state + prompt-hash idempotency) and `worktree_manager.py` (orchestrator-side worktree leases) are described in earlier drafts but not yet implemented. The dispatch listener handles trace_id idempotency directly; worktree leases live in `services/dispatch_listener/src/worktree.js` (in-memory). Tracked in the loop-hardening backlog at `miru-context/loop-hardening-backlog.md` (Ticket B for lease persistence, Ticket C for prompt-hash idempotency).

---

## Local Governance Gatekeeper (Hybrid orchestration pivot Phase 1, shipped 2026-05-06)

The dispatcher (formerly Flask UI + WebSocket on port 19000) was reborn as the **Local Governance Gatekeeper** — a Python module that validates conversational dispatches before forwarding HMAC-signed POSTs to `dispatch_listener` on port 19100.

**Code location:**

- `gatekeeper/gatekeeper.py` (760 lines) — the core gate
- `gatekeeper/frontmatter_parser.py` (173 lines) — HTML-comment YAML frontmatter extractor
- `gatekeeper/forwarder.py` (238 lines) — HMAC-signed forward to dispatch_listener
- `tools/miru_mcp_gateway/gatekeeper_tools.py` — MCP tool wrapper exposing `gate_dispatch()`
- `tools/gatekeeper/bench.py` + `tools/gatekeeper/routing_schema.gbnf` — bench harness + Cursor-built closed-enum grammar

**Locked model:** `DEFAULT_MODEL = qwen2.5:7b` per the 3-model bench (2026-05-06: qwen 7b vs mistral 7b vs qwen 14b). Best balance of speed (p50=27.7s) + correct rejection-vocab usage. Phase 2 prompt engineering can lift qwen 7b's rejection vocab — that's a prompt issue, not a capability issue.

**Where it runs:** in-process inside the MCP Gateway (port 18766). NO dedicated port. Restart the gateway to restart the Gatekeeper.

**Phase 2 (PLANNED, blocked on operator approval):** add `cc_handoff` MCP tool that invokes `gatekeeper.gate_dispatch()` instead of CH calling `dispatch_listener` directly. Run in shadow mode — Gatekeeper validates and logs decisions to `data/agent_decisions.jsonl` for calibration, but does NOT gate dispatch yet.

**Phase 3 (PLANNED, after Phase 2 validated):** remove `dispatch_worker` from CH's tool profile entirely. CH only has `cc_handoff`. Self-serve loophole closes structurally.

See `.miru/reference/roadmap.md` for the full pivot plan + status.

---

## Hermes — layered architecture (separate from the Gatekeeper)

Don't confuse the Gatekeeper (validates + rejects bad dispatches) with Hermes (predicts + eventually routes good ones). Both happen to use Qwen via Ollama, but they're different systems.

| Stage                                          | Status          | Code                                          | What it does                                                                                                                                                                                                           |
| ---------------------------------------------- | --------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Stage 0** — Apprentice bridge                | DONE (PRO-312)  | `tools/hermes_apprentice.py`                  | Manual-invocation Python script. Joins routing_history + callbacks. Produces `data/hermes_quality_labels.jsonl` (120 rows backfilled). Read-only observer.                                                             |
| **Stage 1** — Shadow predictor at spawn        | DONE (PRO-329)  | `services/dispatch_listener/src/spawn.js:625` | Calls `qwen2.5:7b` at every worker spawn. Logs predicted route + confidence + risk to `data/hermes_predictions.jsonl` next to actual dispatch. Observation only. ~8-18s latency, fire-and-forget (never blocks spawn). |
| **Stage 2** — Hermes assumes routing authority | NOT YET STARTED | —                                             | Hermes makes the actual routing decision. CC + operator override remain.                                                                                                                                               |
| **Stage 3** — Hermes learns from outcomes      | NOT YET STARTED | —                                             | Hermes consumes completion log, refines prediction model. Likely fine-tuning when `hermes_quality_labels.jsonl` reaches ~1000+ rows.                                                                                   |
| **Stage N** — NousResearch Hermes proper       | INDEFINITE      | —                                             | Replace Qwen substrate with the actual NousResearch fine-tuned model. Hardware-bound.                                                                                                                                  |

A custom model `miru-router:latest` (9 GB) exists locally and is used by the **Gatekeeper** (NOT Hermes) for routing-validation decisions.

See `.miru/reference/roadmap.md` for stage-promotion criteria.
