---
name: n8n-workflow
description: Use this skill whenever the user asks about, modifies, debugs, tests, or adds to any n8n workflow in this repo. Triggers include: any mention of n8n, workflow, trigger, webhook, Telegram callback, callback_query, callback_data, callback token, dispatch, dispatcher, W1, W2, W7, W8, W2 router, W2 watchdog, W7 callback handler, CC Completion Ping, pending_callbacks, routing_history, dispatch_dlq, /miru-data/, miru-data/config, w2_routing_rules, deploy-workflow.ps1, n8n_list_workflows, n8n_get_workflow_summary, n8n_list_recent_executions, n8n_get_execution_summary. Also fires for any filename matches against `docker/n8n/workflows/*.json`, `docker/n8n/scripts/*`, or anything under `docs/n8n/`. Do NOT use for general workflow / orchestration questions unrelated to this repo's n8n stack.
---

# n8n Workflow Skill (Project Miru)

This skill is a thin wrapper. The canonical content lives at:

**`docs/n8n/N8N_SKILL.md`** — read this file first.

It covers:

1. n8n core patterns (Code node, If node coercion, HTTP Request, webhook silent failure, error propagation, testing)
2. Telegram + approval loop (callback_query, answerCallbackQuery, raw HTTP vs Telegram node, approval gate timeout)
3. LLM routing (current state: rule-based; future: LLM router shadow mode post-PRO-84)
4. Linear integration (GraphQL auth, state transitions, idempotency, rate limits)
5. End-to-end testing (manual triggers, payload simulation, execution tracing, Code node logging)
6. Workflow-specific cross-references (which patterns appear in W1 vs W2 vs W7 vs W8)
7. Verification gaps (open questions awaiting research enrichment in PRO-112 Phase 2)

## Companion doc

**`docs/n8n/WORKFLOW_MAP.md`** — read this for the live workflow inventory: workflow IDs, node counts, triggers, purposes, state file ownership, hard rules, the interconnections diagram. The map is the "what exists today"; N8N_SKILL.md is the "how to work with it."

## How to use this skill

1. Read `docs/n8n/N8N_SKILL.md` end-to-end on first auto-load in a session.
2. Read `docs/n8n/WORKFLOW_MAP.md` for the workflow you're touching.
3. Apply the patterns relevant to the user's task.
4. If you find drift between either doc and the actual workflow JSON, STOP and report — do not silently "fix" the doc as a side effect of other work. Drift is its own ticket.

## When to NOT use this skill

- The user is asking about Claude Code itself, the operator's environment, or anything outside the n8n stack.
- The user is asking about other automation tools (GitHub Actions, cron jobs in `windows/`, scheduled tasks, Dispatcher service, Miru AI).
- The user is asking about the storefront (PM / SvelteKit) or the card catalog DB.
