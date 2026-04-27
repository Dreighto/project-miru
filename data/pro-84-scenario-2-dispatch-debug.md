# PRO-84 Scenario 2 — Dispatch Button Tap Debug

**Date:** 2026-04-27
**Branch context:** Started on `dreighto/pro-84-w7w4-telegram-dispatch-button`; pre-flight discovered I'm now on `main`.
**Status:** Diagnosis complete, no fixes applied. Operator decides path.

## TL;DR

**Three compounding problems**, in priority order:

1. **W7 has duplicate `telegramTrigger` nodes with the same `webhookId`.** PRO-116 (or follow-on work) deployed a parallel pipeline by cloning every existing node with a `1` suffix, including the Telegram Trigger. n8n logged the resulting webhook conflict during the 04:18:27 activation. The non-suffixed trigger won the registration race during scenario 1 (execution 3293 fired through it for action='c'), but **non-deterministic registration on the next workflow save likely flipped the win to the duplicate, which has no PRO-84 dispatch routing in its chain.** That swallows action='d' silently — no execution fires.

2. **`W4_LISTENER_HMAC_SECRET` passthrough has been reverted on `main`.** Even if W4 fires, `w4023-build-listener-request` will abort with the missing-secret guard. Confirmed UNSET in the running n8n container.

3. **Branch / working-tree state divergence.** I'm on `main`, not the PRO-84 branch. The W4 file does not exist on disk (only in the PRO-84 branch's commits). The W7 file on disk is pristine (no PRO-84 changes). Local W2 router still has the uncommitted 0.6 threshold change. The PRO-84 local branch still has both commits intact.

## State table

| Surface                                                          | State                                                                                        |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Local branch                                                     | `main` (operator instruction said do not switch — but it was switched)                       |
| PRO-84 branch local                                              | exists, tip `196d459` (my 2 commits intact)                                                  |
| PRO-84 branch origin                                             | exists, matches local (was pushed earlier without my action)                                 |
| `docker/n8n/workflows/w7-telegram-callback-handler.json` on disk | pristine, no PRO-84 nodes                                                                    |
| `docker/n8n/workflows/w4-dispatch-button-handler.json` on disk   | **missing** (only in PRO-84 commits)                                                         |
| `docker/n8n/docker-compose.yml` on disk                          | no `W4_LISTENER_HMAC_SECRET` line                                                            |
| `docker/n8n/.env.example` on disk                                | no `W4_LISTENER_HMAC_SECRET` placeholder                                                     |
| `docker/n8n/workflows/w2_worker_selection_router.json` on disk   | uncommitted change: `rightValue: 0.6` (PRO-125 temp)                                         |
| n8n container env `W4_LISTENER_HMAC_SECRET`                      | **UNSET**                                                                                    |
| n8n container env `TELEGRAM_CALLBACK_SECRET`                     | SET (length 64)                                                                              |
| n8n W7 runtime (`rJiLlMFKQh8t4Y9K`)                              | 75 nodes, active=true, updated 04:20:13                                                      |
| n8n W4 runtime (`TwRAHqoZqNhGRHKo`)                              | 27 nodes, active=true, updated 00:28:17                                                      |
| Listener `/health`                                               | 200 OK                                                                                       |
| Telegram bot webhook URL                                         | `https://room.taila28611.ts.net/webhook/w7-telegram-callback/webhook` (no errors, 0 pending) |

## Investigation steps and findings

### 1. W7 build-dispatch-message (already deployed)

Confirmed via `n8n_get_workflow_summary` and connection graph walk. The deployed W7 contains all 7 PRO-84 nodes I added during recovery: `w7-dispatch-action-branch`, `w7-call-w4`, `w7-determine-dispatch-target`, `w7-gate-dispatch-emit`, `w7-build-dispatch-message`, `w7-send-dispatch-message`, `w7-store-pending-dispatch`. They are wired into the **non-suffixed** chain: `w7005-validate-branch → w7-dispatch-action-branch → w7-call-w4` (true output) and `→ w7006-lookup-pending` (false output).

Button label and callback_data construction are in `w7-build-dispatch-message`. Button `text`: `'🚀 Dispatch <worker>'` for claude-code/codex/gemini, `'🔗 Open in Linear (Cursor)'` for cursor. callback_data: `T(12)A(1)N(8)S(8)H(32)` — action byte = `'d'`, full length 61, signed with HMAC-SHA256(token+'d'+nonce+ts_hex) using `TELEGRAM_CALLBACK_SECRET`, truncated to 32 hex chars. Matches the existing W7 mint pattern verbatim.

### 2. W4 webhook trigger (already deployed)

`w4001-webhook` node config:

- method: `POST`
- path: `w4-dispatch`
- responseMode: `responseNode`
- webhookId: `w4-dispatch-button`

Public URL would be `https://room.taila28611.ts.net/webhook/w4-dispatch`. Internal-from-W7 call uses `http://localhost:5678/webhook/w4-dispatch` (same n8n instance). HMAC validation is **inside the workflow** (Code node `w4003-hmac-validate` re-validates using `TELEGRAM_CALLBACK_SECRET`), not at the webhook layer.

### 3. callback_data shape vs W4 expectations

W7 mints callback_data of 61 chars (well under Telegram's 64-byte limit). W7's `w7-call-w4` POSTs a parsed JSON to W4's webhook with the fields W4 needs: `{token, action, nonce, ts_hex, hmac_hex, ts_unix_seconds, callback_age_seconds, callback_query_id, chat_id, message_id, from_user_id}`. W4's `w4002-parse-input` reads from `$('w4001-webhook').item.json.body`, validates required fields, then passes through the chain. **This piece is fine.** The bug is upstream — W7 never reaches `w7-call-w4` for the Dispatch tap because the trigger doesn't fire the right chain.

### 4. n8n container logs

```
2026-04-27T04:18:27.811Z  There is a conflict with one of the webhooks.
```

This is the smoking-gun log. It fires during workflow activation when two trigger nodes try to register the same webhook URL. There is no other relevant activity in the last 30 minutes. No 404s, no HMAC rejections, no W4 webhook hits.

### 5. W7 runtime node list (75 nodes — 39 more than my deploy)

Deployed W7 has every original node duplicated with a `1` suffix:

- `w7001-telegram-trigger` AND `w7001-telegram-trigger1` (both webhookId `w7-telegram-callback`)
- `w7002-ack-callback` AND `w7002-ack-callback1`
- ... through `w7011-mark-decided1`
- Plus new `w7-mcp-*` nodes (PRO-116 MCP write branch)
- Plus `w7-action-router-` (empty suffix), `w7-action-router-5`, `-6`, `-7`

**My PRO-84 nodes appear only once each** (no `1` suffix). They live exclusively in trigger A's chain.

Walk from each trigger:

- **Trigger A** (`w7001-telegram-trigger`) → ... → `w7005-validate-branch` → `w7-dispatch-action-branch` [PRO-84] → ... reaches all dispatch nodes ✓
- **Trigger B** (`w7001-telegram-trigger1`) → ... → `w7005-validate-branch1` → `w7006-lookup-pending1` → `w7007-found-branch1` → MCP routing → action-router-5/6/7 → `w7-noop-unknown-action1`. **Zero PRO-84 dispatch nodes reachable.** Action `'d'` falls through to noop here.

### 6. Routing handoff: W7 → W4

Confirmed: `w7-dispatch-action-branch.true` → `w7-call-w4` (HTTP POST to `localhost:5678/webhook/w4-dispatch`). This wiring is intact in trigger A's chain. **It was never reached for the 04:46:40 tap because no W7 execution fired.**

### 7. Recent W7 executions

| ID   | started_at | mode    | status    | last_node                                                                                      |
| ---- | ---------- | ------- | --------- | ---------------------------------------------------------------------------------------------- |
| 3293 | 04:46:36   | webhook | success   | `w7-store-pending-dispatch` (action `'c'` — picker selected claude-code, emit dispatch button) |
| 3292 | 04:46:33   | webhook | success   | (likely the Override-picker open — earlier callback)                                           |
| 3285 | 04:42:24   | webhook | success   | (PRO-128 scenario 1, earlier)                                                                  |
| 3284 | 04:42:13   | webhook | success   | (PRO-128 scenario 1, earlier)                                                                  |
| 3242 | 04:12:17   | webhook | **error** | (worth flagging — predates the conflict log by 6 min)                                          |

**No W7 execution after 3293 (04:46:39).** The dispatch tap at ~04:46:40 produced zero workflow activity.

## Why the Dispatch tap (action='d') didn't fire when picker tap (action='c') just did

Strongest hypothesis: **non-deterministic webhook routing on duplicate triggers.** When n8n re-registers webhooks on workflow saves, the winning trigger between the two collidants can flip. Scenario 1 picked trigger A at one moment (action='c' fired through PRO-84 chain). The next tap may have routed to trigger B, which has no `w7-dispatch-action-branch` and falls through to `w7-noop-unknown-action1` — no visible execution because n8n may not register it as a workflow execution if the only thing the trigger does is route to a noop with no output.

Less-likely alternatives:

- **B**: Telegram dropped the callback (would show pending_update_count > 0 — but it shows 0)
- **C**: HMAC mismatch (would create an execution that ends at `w7-noop-rejected` — but no execution fired)
- **D**: callback_data malformed (would create an execution that aborts in `w7003-parse-payload` — but no execution fired)

The signature of "no execution at all" combined with "0 pending updates" most cleanly matches "trigger B won the race and silently swallowed it."

## Recommended fix paths (operator decides)

**Critical: do nothing until the duplicate-trigger problem is resolved.** Even if I deploy W4 again with a fixed `webhookId` or repair the env passthrough, the W7 trigger collision will keep producing flaky behavior.

### Path 1 — Surgical: delete the duplicate `1`-suffix nodes from W7's runtime, redeploy

1. Resolve which set of nodes is the canonical one (the `1` chain is the MCP write branch from PRO-116; the no-suffix chain has my PRO-84 work). Decide whether MCP write should merge into the no-suffix chain or stand on its own with a different webhookId.
2. Deduplicate manually in n8n UI or rebuild W7 JSON from scratch combining both feature sets cleanly.
3. Redeploy W7 with single Telegram Trigger.
4. Re-add `W4_LISTENER_HMAC_SECRET` passthrough to `docker-compose.yml`.
5. Recreate n8n container.
6. Resume PRO-84 testing.

### Path 2 — Branch reset: checkout PRO-84, redeploy from there, fix environment

1. Cherry-pick or carry over the MCP write branch work from main into the PRO-84 branch (or accept that the PRO-84 redeploy will overwrite the MCP write nodes in the n8n DB and the operator will replay PRO-116 deployment after).
2. `git checkout dreighto/pro-84-w7w4-telegram-dispatch-button`
3. Redeploy W7 + W4 from PRO-84 branch.
4. Recreate n8n container with `W4_LISTENER_HMAC_SECRET` passthrough restored.
5. Resume testing.
6. (PRO-116's MCP work has to be re-applied to W7 after merging PRO-84 — coordinate with that thread.)

### Path 3 — Pause and consolidate

PRO-84 and PRO-116 are stepping on each other. Operator pauses both, plans a single coherent W7 evolution that incorporates both feature sets without duplicate nodes, ships that as a separate PR, then resumes PRO-84 testing on that base.

## What I did NOT do (per diagnostic-only scope)

- Did not edit any workflow JSON
- Did not redeploy any workflow
- Did not switch branches
- Did not commit anything
- Did not modify `docker-compose.yml` or `.env.example`
- Did not restart the n8n container

## Pending operator decision before any further action

1. Which fix path (1/2/3 or other)?
2. Switch back to PRO-84 branch — yes/no?
3. Recover the missing W4 file from PRO-84 commits — yes/no?
4. Coordinate with the other thread that's been deploying to W7's n8n DB?
