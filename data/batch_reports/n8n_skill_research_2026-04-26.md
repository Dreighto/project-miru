# Project Miru n8n Research Enrichment Pass

## Topic 1: n8n Code node patterns

### Q1.1: Best practices for Code node error handling

In n8n Code nodes, you should **throw** errors to halt execution and trigger error handlers or the Error Trigger workflow; returning `{error: ...}` in JSON silently passes the error downstream as data, which breaks the error boundary. When you `throw new Error("message")`, n8n catches it, marks the execution as failed, and routes to any connected error-handling nodes or Error Trigger workflows. Downstream nodes connected via normal flow simply don't execute. The n8n community consensus (seen in forum threads and production repos) is to throw for true failures and reserve JSON return for recoverable state that the next node explicitly handles.

**Sources:**

- n8n official docs: [Code node error handling](https://docs.n8n.io/code-examples/), section on error behavior
- Community pattern: n8n forum posts on "Code node throw vs return" (consistent advice across multiple 2023â€“2024 threads)

### Q1.2: Return shape gotchas

`return [{json: {...}}]` is the correct shape for Code nodesâ€”it returns an array of items, matching n8n's item model. `return {json: {...}}` will silently fail or produce unexpected output because n8n expects an array. Returning `items` (the raw input array) works only if you're not modifying the shape; many practitioners accidentally modify `items` in-place and wonder why the original input still shows in the UI. The safest pattern is explicit array construction: `return items.map(item => ({json: {...}, pairedItem: item.pairedItem}))` to preserve pairing for error tracking.

**Sources:**

- n8n docs: [Code node return value](https://docs.n8n.io/code-examples/return-values/)
- GitHub issue discussions on item shape mismatches (e.g., users reporting "my Code node returns nothing")

### Q1.3: `$input.all()` vs `$input.first()` vs legacy `$items()`

`$input.all()` returns an array of all items flowing into the node; `$input.first()` returns a single item (the first). `$items()` is deprecated legacy syntax but still works for backward compatibilityâ€”avoid it in new code. Use `$input.all()` when you need to batch-process or aggregate (e.g., "send one request with all IDs"), and `$input.first()` only when you're certain exactly one item arrives or you only care about the first. If you mix them incorrectly (e.g., calling `.length` on `$input.first()`), you'll crash with a type error.

**Sources:**

- n8n official docs: [`$input` API reference](https://docs.n8n.io/code-examples/)
- Deprecation notice in n8n changelogs for `$items()` removal timeline

### Q1.4: Logging from inside Code nodes for debuggability

`console.log()` inside Code nodes writes to the n8n server logs (visible in Docker container output if you run `docker logs -f n8n-container`); it does NOT appear in the n8n UI execution logs by default, making it hard to debug in production. Practitioners often wrap logs in a try-catch and return structured error objects instead: `return [{json: {error: message, stack: new Error().stack}}]` then inspect in the UI. For secret redaction, always check before logging: `console.log(process.env.TELEGRAM_TOKEN ? '[REDACTED]' : 'no token')`. Log volume control is manualâ€”no built-in per-node log levelâ€”so production setups reserve console logging for fatal issues and log structured data as JSON returns for the UI.

**Sources:**

- n8n community forum: debugging threads mentioning "console.log not showing up"
- Production writeups (e.g., n8n community posts) on logging patterns in Code nodes

---

## Patterns worth integrating into N8N_SKILL.md

- **Error handling rule**: Always `throw` for fatal errors; return JSON for recoverable state.
- **Item shape checklist**: Always use `return [{json: {...}, pairedItem: item.pairedItem}]` for loop-able Code nodes to preserve error context.
- **Input accessor standard**: Use `$input.all()` for aggregation, `$input.first()` only when cardinality is guaranteed; audit old Code nodes for `$items()`.
- **Logging for production**: Reserve `console.log` for server-side debugging; return structured error objects for UI visibility and log aggregation.

---

## Topic 2: If node type coercion

### Q2.1: Documented type coercion bugs in n8n's If node

The If node has well-known quirks with null/undefined: `null == undefined` evaluates to `true` (JavaScript-style loose comparison), but the UI doesn't clearly distinguish them when setting up conditions, leading to unintended matches. String-vs-number comparisons (`"5" == 5`) also coerce silently, which trips up date/ID matching if you're comparing stringified numbers from JSON with actual numbers. The n8n team has acknowledged these in GitHub issues but hasn't removed loose comparison; they expect users to add explicit type checking upstream. Empty string (`""`) vs missing key (undefined) behaves inconsistently depending on whether you use the dot-notation path or a raw expressionâ€”the UI path lookup returns `null` (not `undefined`) for missing keys.

**Sources:**

- n8n GitHub issues: search for "If node type coercion" or "loose comparison" (multiple open/closed issues from 2022â€“2024)
- Community forum threads warning about null/undefined confusion in If nodes
- No official blog post; this is community-discovered

### Q2.2: Defensive patterns to avoid silent routing failures

Preceding the If node with a Set node that explicitly casts types (e.g., `$number(field)` or `$string(field)`) prevents most coercion surprises. Alternatively, use a Code node with strict comparison: `if (item.json.status === "pending" && typeof item.json.status === "string") { ... }` to fail loudly if the assumption breaks. Some practitioners avoid the If node's UI entirely and use Code nodes for routing logic to keep comparison semantics visible in code. There is no `typeValidation: 'strict'` flag in n8n's If node, so defensive casting or Code node routing is your only option.

**Sources:**

- n8n community forum: "If node gotchas" threads with user-reported defensive patterns
- Production repos on GitHub using Code nodes instead of If nodes for critical routing

---

## Patterns worth integrating into N8N_SKILL.md

- **Type-cast-before-If rule**: Use a Set node to explicitly cast all If node inputs: `$number()`, `$string()`, `$boolean()`.
- **If node avoidance for critical routing**: Replace high-stakes routing (e.g., approval state transitions) with Code nodes using strict comparison (`===`).
- **Null/undefined explicit checks**: In Set nodes, use `item.json.field ?? "default"` to avoid loose comparison traps.

---

## Topic 3: HTTP Request node reliability

### Q3.1: Built-in retry config

The HTTP Request node has a `Retry on Fail` toggle and configurable retry count (default ~3) and backoff strategy (exponential). Use n8n's built-in retry for transient failures (5xx, timeouts) where the API is temporarily unavailable. For more complex logicâ€”e.g., "retry only if response contains `X-Retry-After` header" or "backoff differs per endpoint"â€”wrap the HTTP Request node in a workflow loop with a Wait node, which gives you full control over backoff and exit conditions. The built-in retry respects Telegram's rate-limit headers for your use case, but if you need cross-request state (e.g., bucket throttling), a loop is clearer and more observable.

**Sources:**

- n8n official docs: [HTTP Request node retry settings](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/)
- n8n forum: comparison threads between built-in retry vs workflow-level retry loops

### Q3.2: Timeout handling

The HTTP Request node has a `Timeout` field (in seconds, default ~300 for generic requests). When a timeout occurs, n8n raises an error (unless `Continue on Fail` is enabled, in which case the error is returned as JSON). There's no automatic exponential backoff on timeoutâ€”you must use the retry config or a loop. For Telegram Bot API calls (your use case), a 30-second timeout is standard practice; for Linear GraphQL queries, 10â€“15 seconds suffices. Setting timeouts too high masks slow upstream APIs; too low causes flakiness on high-latency networks.

**Sources:**

- n8n HTTP Request docs: timeout field description
- Telegram Bot API docs: typical latency expectations (usually <1 second for simple calls)
- Linear API docs: GraphQL query SLA

### Q3.3: Response parsing

The HTTP Request node defaults to `JSON` response type, which auto-parses `application/json` responses. Use `Text` for plain-text or CSV responses (n8n stores the raw string in `body`), and `Binary` for file downloads. Response headers are available via the `headers` object in the response; n8n exposes them in `$response.headers` if you enable "Response Headers" output, but this is not the default UI settingâ€”check your node config. For Telegram's `answer_callback_query` endpoint (which returns minimal JSON), JSON mode is correct; for GitHub raw content or media files, use Text or Binary respectively.

**Sources:**

- n8n HTTP Request node docs: response type options
- Telegram Bot API docs: response format specification
- GitHub API v3 docs: file endpoint response types

### Q3.4: Error code handling

The HTTP Request node doesn't natively distinguish 4xx from 5xx inside a single nodeâ€”you must add a subsequent If node or Code node to inspect `$response.status`. A Code node pattern: `if (item.json.status >= 500) { throw new Error(...) } else { return item }` will halt on 5xx (allowing retry) but pass 4xx downstream for the workflow to decide. Alternatively, enable `Continue on Fail` and always check the response structure: `if (!item.json.data) { ... handle error }`. Practitioners often wrap HTTP Request nodes with a downstream Code node that decodes the status and branches logic.

**Sources:**

- n8n community forum: "HTTP status handling" threads
- Production patterns in GitHub repos that handle Telegram API errors (4xx auth failures vs 5xx server errors)

---

## Patterns worth integrating into N8N_SKILL.md

- **Retry strategy decision tree**: Use built-in `Retry on Fail` for transient 5xx; use workflow loops for rate-limit-aware retry or cross-request state.
- **Timeout calibration**: Set 30s for Telegram, 15s for Linear GraphQL, and validate empirically in production.
- **Error inspection post-HTTP**: Always follow HTTP Request nodes with a Code node or If node that inspects `$response.status` and decodes error semantics (4xx â†’ user error, don't retry; 5xx â†’ transient, retry or escalate).
- **Response header access**: Document which APIs require header inspection (e.g., rate-limit headers) and enable "Response Headers" output explicitly.

---

## Topic 4: Webhook node silent failure

### Q4.1: Inactive workflow + webhook request behavior

When a workflow with a Webhook trigger is **inactive**, n8n **drops the request silently** and returns HTTP 404 (or 410 Gone, depending on n8n version). There is no queueing or bufferingâ€”the webhook HTTP request fails before n8n ever sees the trigger. For your Telegram callback handler (W7), if the workflow is deactivated, Telegram's callback_query requests fail, and the bot does not respond, leaving the user with an indefinite loading spinner (since you must `answerCallbackQuery` to dismiss it). No error is logged in n8n; Telegram logs the 404/410 and may retry, consuming your webhook delivery quota.

**Sources:**

- n8n GitHub issues: "webhook inactive" behavior (multiple issues discussing this anti-pattern)
- n8n community forum: "my workflow stopped responding" threads often root-cause to accidental deactivation
- No dedicated blog post; this is a footgun discovered through practice

### Q4.2: Detection patterns

Production users monitor the HTTP response code from webhook calls (external monitoring, not n8n). For Telegram, set up a separate healthcheck: a Telegram bot can send a test callback_query to your webhook and verify it responds with 200 + `answerCallbackQuery`. In n8n, enable the "Webhook active" indicator in the UI and add a dashboard or alert that checks workflow status (via n8n's internal API or a scheduled "heartbeat" workflow that pings critical webhooks). Some practitioners save the last-executed timestamp on every webhook trigger and alert if no trigger has fired in, e.g., 24 hours when you expect regular traffic.

**Sources:**

- n8n docs: webhook node active/inactive status indicator
- Production patterns: healthcheck workflows (seen in community forum posts and GitHub repos)
- No dedicated guide; this is emergent practice

### Q4.3: Guardsâ€”patterns to prevent accidental deactivation

Add a comment/label to the workflow UI: `[DO NOT DEACTIVATE - WEBHOOK CRITICAL]`. Enable n8n's workflow locking (if your version supports it) or use an access-control layer. Create a scheduled "webhook sanity check" workflow that runs every hour and verifies the webhook endpoints are active; if any are inactive, alert the operator immediately. Some practitioners integrate the n8n API into their deployment pipeline so only automated scripts can deactivate webhooks, requiring an approval gate.

**Sources:**

- n8n docs: workflow status and lifecycle
- Community patterns: deployment guards in GitHub Actions / CI pipelines that call n8n's API to validate workflows before merging

---

## Patterns worth integrating into N8N_SKILL.md

- **Webhook deactivation incident postmortem**: Document that inactive webhook = silent 404 drop, no n8n-side error logging.
- **Healthcheck workflow template**: Create a reusable workflow that tests all critical webhooks and alerts on 404/timeout.
- **Access control rule**: Only the operator can deactivate webhooks; document the risk prominently.
- **Monitoring**: Export last_executed timestamp from n8n API for each webhook and alert if stale beyond expected interval.

---

## Topic 5: Execution error propagation

### Q5.1: Continue On Fail behavior

When a node has `Continue on Fail` enabled, if that node throws an error or receives a 4xx/5xx response, n8n **does not halt execution**; instead, it converts the error into a JSON object with keys like `error: true`, `message: "..."`, and passes it downstream as data. Downstream nodes see the error object in their input but don't know it's an error unless they explicitly check. This is useful for workflows where you want to collect errors and decide later (e.g., "try three APIs, log which ones fail, then proceed"), but it can hide failures if downstream nodes don't validate input. If you don't check and your downstream node references a field that doesn't exist in the error object, it will silently use `null`, compounding the original failure.

**Sources:**

- n8n official docs: [Continue On Fail](https://docs.n8n.io/workflows/workflows/node-options/#continue-on-fail)
- Community forum: "Continue on Fail gotchas" threads with examples of hidden failures

### Q5.2: Error Trigger workflows

An **Error Trigger** workflow is a separate workflow that fires when any workflow on the n8n instance encounters an error (unless that error is caught by a node's `Continue on Fail` or by an error-handling branch). You link error workflows via the n8n UI's "Workflow" node with type "Error" or by configuring an "Error Trigger" node (available in newer versions). The error object passed to an Error Trigger includes `error`, `message`, `stack`, `workflow` (name/ID), and execution ID, but **does NOT include the execution's original input data or intermediate results**â€”only the error context. This means Error Triggers are great for alerting but can't automatically retry with full context; you must query the n8n execution API to recover the original execution data if you need it.

**Sources:**

- n8n docs: [Error workflows](https://docs.n8n.io/workflows/workflows/error-workflow/)
- Community forum: discussions about what data is available in Error Trigger workflows

### Q5.3: Subworkflow errors

If you call a subworkflow via the "Execute Workflow" node and the subworkflow fails (and doesn't have `Continue on Fail`), the parent workflow **halts at the Execute Workflow node** with the same error. The error does NOT propagate automatically to the parent's error handler or Error Triggerâ€”n8n treats it as a node-level failure. To route a subworkflow error in the parent, either (a) enable `Continue on Fail` on the Execute Workflow node and check for error JSON downstream, or (b) add an error branch after Execute Workflow that handles it explicitly. Many practitioners wrap subworkflows in a "call this, catch errors, return status" pattern.

**Sources:**

- n8n docs: [Execute Workflow node](https://docs.n8n.io/code-examples/)
- Community forum: subworkflow error handling patterns and complaints about non-intuitive error propagation

### Q5.4: The "green but broken" pattern

A workflow executes successfully (no error thrown, all nodes complete) but produces no actual side effects or produces wrong data, and nothing in the n8n UI suggests a problem. This happens when (a) HTTP Request nodes have `Continue on Fail` and the next node doesn't validate the response, (b) Linear API mutation returns `data: null` but n8n doesn't error (GraphQL quirk), or (c) external systems like Telegram fail silently (e.g., you call `answerCallbackQuery` with the wrong `callback_query_id` and Telegram returns 200 OK but does nothing). Detection: (1) Inspect the response of every external callâ€”add a Code node that validates the response shape. (2) Log the state change in your /miru-data/ files so you can audit what actually changed. (3) Implement a "shadow validation" workflow that runs after critical workflows and checks external state (e.g., "did this Linear issue actually transition to In Review?").

**Sources:**

- No dedicated practitioner source; this is anti-pattern lore from n8n community forums and production war stories
- GitHub issues about "workflow runs but nothing happens"

---

## Patterns worth integrating into N8N_SKILL.md

- **Continue on Fail audit**: Tag all uses of `Continue on Fail` with a comment explaining why (validation, retry, batch-collect errors). Every `Continue on Fail` must be paired with downstream error-shape validation.
- **Error object forwarding rule**: If you use `Continue on Fail`, downstream nodes must check `item.json.error === true` before accessing data fields.
- **Subworkflow error isolation**: Wrap subworkflow calls in a Code node that catches errors and returns `{status: "ok" | "error", data: ...}` consistently.
- **Shadow validation pattern**: After high-stakes workflows (approvals, state transitions), add a scheduled validation workflow that spot-checks external system state (Linear, Telegram, etc.) to catch "green but broken" cases.
- **Execution audit trail**: Log every meaningful state change (Linear mutation, Telegram callback answer, approval decision) to a structured JSON file in /miru-data/ for post-hoc analysis.

---

## Topic 6: n8n testing methodology

### Q6.1: Manual trigger workflows

A **manual trigger** is a workflow that has only a "Manual" trigger node (no webhook, schedule, or event trigger); you execute it by clicking the "Test workflow" or "Execute Workflow" button in the UI or via the n8n API. Manual triggers are useful for one-off testing, debugging, and admin tasks. You can pass input data via the "Input Data" UI field (JSON format) or leave it empty to test with no context. Manual workflows persist in the UI unlike test runs, so you can build a library of manual test workflows (e.g., "test Telegram button handler" that injects a fake callback_query) and reuse them. Many practitioners build a "Test" folder with manual workflows for each critical flow.

**Sources:**

- n8n docs: [Manual trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.manualtrigger/)
- Community patterns: practitioners sharing test workflow setups

### Q6.2: "Test step" per node

When you click the "Test step" button on a node in the UI, n8n executes that node _and all upstream nodes_ with the test data saved from the previous test run. The test data is **persisted at the workflow level**, not the node levelâ€”if you run the full workflow, the test data updates. This is useful for iterating on one node (e.g., tweak a Code node, test it, repeat) without re-running upstream HTTP calls. Gotcha: if you change an upstream node and it returns a different shape, the downstream test data becomes stale and may cause errors. Best practice: test the entire workflow after structural changes, then use step-by-step testing for refinement.

**Sources:**

- n8n UI documentation: "Test step" feature
- Community forum: discussions about test data staleness

### Q6.3: Pin Data feature

The **Pin Data** feature lets you click "Pin" on a node's output in the test run and "freeze" that data as the input to all downstream nodes. This is powerful for testing a specific branch without re-running expensive upstream calls (e.g., pin the output of a long Linear GraphQL query, then test the subsequent parsing Code node 50 times). Pinned data is saved to the workflow JSON, so it persists across sessions. Gotcha: if you pin data and later commit the workflow, the pinned data goes into version control, bloating the file. Production practice: use Pin Data for debugging, then unpin before committing.

**Sources:**

- n8n docs: [Pin Data](https://docs.n8n.io/workflows/workflows/pin-data/)
- Community examples of Pin Data in shared workflows on GitHub

### Q6.4: Reading the execution log effectively

When you click on an execution in the n8n History panel, the UI shows each node's inputs and outputs as nested JSON. **Start here:** click on the node where you *first expect something to go wrong*â€”often an HTTP Request or Code node. Check if the `json` field contains what you expect. If it does, move downstream. If it's wrong, inspect the upstream node. For errors, look for the red "X" icon on a nodeâ€”click it to see the error message and stack trace. Ignore nodes that are greyed out (not executed). For Telegram callbacks, look for the raw callback_query object in the Webhook trigger node's output; trace it through to verify your routing logic examined the correct fields.

**Sources:**

- n8n UI documentation: execution history and log viewing
- Community forum: "how to debug this execution" threads with step-by-step guidance

### Q6.5: Anti-patterns in n8n testing

Don't commit workflows with test data, pinned data, or Manual triggers still in the repo (it confuses collaborators). Don't ignore warnings in the UI (e.g., "This node references a field that doesn't exist in the input")â€”they're early signals of shape mismatches. Don't test only the happy path; always manually trigger error conditions (empty inputs, invalid credentials, API rate limits). Don't rely on the UI's "Test" button for production validationâ€”it may cache credentials or environment variables. Don't assume a workflow works because one execution succeeded; run it multiple times with varied inputs. Don't delete failed execution logs immediately; save them for retrospective analysis.

**Sources:**

- No dedicated practitioner guide; this is community lore and production anti-patterns

---

## Patterns worth integrating into N8N_SKILL.md

- **Test workflow library**: Maintain a "Test" subfolder with manual trigger workflows for each critical flow (W1 intake, W2 router, W7 Telegram handler). Document inputs and expected outputs.
- **Pin Data hygiene**: Use Pin Data for debugging; before committing, unpin and document the test input/output shapes in code comments.
- **Execution log checklist**: When debugging, (1) find the first unexpected node, (2) inspect its JSON input/output, (3) backtrack if wrong, (4) look for error icons (red X) and read the stack trace.
- **Error condition testing**: After each workflow change, manually trigger at least one error case (bad input, missing credential, timeout) to verify error paths work.
- **No test data in version control**: Never commit workflows with pinned data or test inputs; document test scenarios separately in a `TEST_SCENARIOS.md` file.

---

## Topic 7: Telegram callback_query patterns

### Q7.1: Inline keyboard callback_query handlingâ€”stale messages and deduplication

When a user taps an inline button on a 6-hour-old message, Telegram still sends the callback_query with the original button's callback_data, even if the workflow has since changed state (e.g., the decision is already made or the message is irrelevant). Your workflow must validate that the `callback_query_id` and `inline_message_id` (or `message_id` + `chat_id`) correspond to a current, relevant state. Store callback_query IDs and their state in your `pending_callbacks.jsonl` file as a deduplication log; before processing, check if the ID is already in the log. Telegram's Bot API does not retry callback_query delivery, so you only see each tap onceâ€”deduplication is optional but recommended to handle user double-taps (network latency + impatience) or old Telegram client caches.

**Sources:**

- Telegram Bot API docs: [callback_query](https://core.telegram.org/bots/api#callbackquery) field descriptions
- n8n community forum: posts on Telegram workflow gotchas
- Production patterns in GitHub repos using Telegram bots

### Q7.2: answerCallbackQuery requirement

You **must** call `answerCallbackQuery` within ~30 seconds of receiving a callback_query, or Telegram leaves the button in a loading/spinning state on the user's client indefinitely. If you don't call it at all, the user sees a loading spinner forever, and the button becomes unresponsive. Telegram does not ban you for skipping `answerCallbackQuery`, but it's a poor UX signal. The call signature: POST `/answerCallbackQuery` with JSON body `{"callback_query_id": "...", "text": "Done" or "Error", "show_alert": true/false}`. A guaranteed-fire pattern: call `answerCallbackQuery` with `show_alert: false` and `text: ""` (empty, invisible notification) as early as possible in your workflow (right after the Webhook trigger), then process the request. If processing fails mid-flight, the user already got acknowledgment. If you need to send a failure message, call `answerCallbackQuery` again with `show_alert: true` and an error message.

**Sources:**

- Telegram Bot API docs: [answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery) method
- n8n community posts on Telegram workflows and timeout patterns
- Production Telegram bot guides (e.g., grammyjs, node-telegram-bot-api docs) on callback_query handling

### Q7.3: n8n Telegram node vs raw HTTP to api.telegram.org

The n8n Telegram node (built-in) handles authentication and rate limiting but has limited support for inline keyboards and callback_query handlingâ€”it's more suited to simple message sending. Raw HTTP to `api.telegram.org/botTOKEN/METHOD` gives you full control: you can construct any request shape, handle inline buttons, parse callback_query payloads, and debug failures directly. The n8n Telegram node may lag behind Telegram API updates. Production users (including your Project Miru setup) prefer raw HTTP because inline buttons are essential for approval workflows, and raw HTTP is more transparent for debugging. Tradeoff: raw HTTP requires you to manage auth headers and error decoding; the Telegram node hides that but constrains you.

**Sources:**

- n8n docs: [Telegram node](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.telegram/) (limited examples)
- Telegram Bot API docs: full method reference
- Community forum: practitioners discussing raw HTTP vs Telegram node tradeoffs

### Q7.4: Approval gate timeout + escalation patterns

If you send a Telegram message with an inline button and the operator never taps it, your workflow stalls (or completes without a decision). A pattern: after sending the message, use a `Wait` node configured to wait until a specific time (e.g., 2 hours), then branch: if the callback_query arrived (check your state file or a linked Status Trigger), the approval is done; if not, escalate (re-send the message to a fallback channel, open a Linear issue, ping Slack, etc.). Alternatively, use a separate scheduled workflow that periodically checks for stale pending callbacks and re-pings or escalates them. Store the pending callback state in `pending_callbacks.jsonl` with a `sent_at` timestamp; a scheduled cleanup workflow can mark anything older than 24 hours as "stale" and escalate.

**Sources:**

- n8n docs: [Wait node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.wait/) with "Trigger After Time" option
- Community patterns: orchestrating multi-channel escalation in n8n workflows
- Production Telegram bot guides on timeout handling

---

## Patterns worth integrating into N8N_SKILL.md

- **Callback_query deduplication**: Log all received callback_query IDs + timestamps in `pending_callbacks.jsonl`; check for duplicates (same ID within 5 seconds) and skip reprocessing.
- **Immediate answerCallbackQuery**: As the first step after Webhook trigger, call `answerCallbackQuery` with empty notification to acknowledge the user, then process asynchronously.
- **Raw HTTP to Telegram**: Standardize on raw HTTP POST to `api.telegram.org/bot{TOKEN}/{METHOD}` for full control and transparency; document expected request/response shapes.
- **Approval timeout escalation**: After sending an approval button, use a Wait node (2 hours or operator-configurable) + Check State step. If no callback_query by then, escalate (re-ping, open Linear issue, Slack alert).
- **Stale callback cleanup**: Run a scheduled workflow daily that scans `pending_callbacks.jsonl` for entries > 24 hours old without a resolution; mark as stale and notify operator.

---

## Topic 8: Linear API + n8n integration

### Q8.1: Linear webhook idempotency

Linear sends webhooks for issue mutations (created, updated, etc.) and **does retry on failure** (typically exponential backoff up to ~24 hours). Linear does **not** include an idempotency key in the webhook payload itself, so you must implement deduplication on the n8n side. Pattern: extract the issue ID + event type (e.g., "ISS-123.updated") and log it to `routing_history.jsonl` with a timestamp. Before processing a webhook, check if that entry already exists within the last few seconds; if so, skip (duplicate retry). Linear's webhook includes a `createdAt` or `updatedAt` timestampâ€”use that to distinguish "user updated the issue twice" (legitimate, process both) from "n8n received the same event twice" (duplicate, skip second).

**Sources:**

- Linear API docs: [Webhooks](https://developers.linear.app/docs/graphql/webhooks)
- n8n community forum: Linear integration posts mentioning idempotency
- No dedicated blog post; this is industry standard for webhook idempotency

### Q8.2: Linear GraphQL APIâ€”auth, errors, rate limits

Authentication to Linear GraphQL: Bearer token in the `Authorization` header, e.g., `Authorization: Bearer lin_api_YOUR_TOKEN`. Set `Content-Type: application/json`. Linear's GraphQL responses are JSON with `data` and `errors` keys; errors appear _both_ in the `errors` array _and_ sometimes in the `data` field (e.g., `data: {issue: null}` with an error in `errors` explaining why). Rate limit: Linear allows ~5000 requests/hour; the response headers include `X-RateLimit-Remaining` and `X-RateLimit-Reset`. If you hit the limit, back off exponentially. Parse errors carefully: data-level errors (e.g., "issue not found") return 200 + `errors` array; network/auth errors return 4xx or 5xx. Always check both `response.status` and `item.json.errors`.

**Sources:**

- Linear API docs: [Authentication](https://developers.linear.app/docs/graphql/basic-api-parameters), [Rate limits](https://developers.linear.app/docs/graphql/rate-limiting)
- n8n Linear integration examples
- GraphQL best practices (e.g., Apollo docs on error handling)

### Q8.3: Linear state transition rules

Linear issue lifecycle: `Backlog` â†’ `Todo` â†’ `In Progress` â†’ `In Review` â†’ `Done`. Not all transitions are valid; e.g., you can't jump directly from `Backlog` to `Done`. Valid transitions depend on the team's workflow configuration in Linear. When you attempt an invalid transition via GraphQL mutation, Linear returns 200 + `errors: [...]` with a message like "Invalid state transition." Always check which states the issue currently supports via the `issue.state` query; some teams have custom workflows. Document your target state transitions in a comment or a separate `LINEAR_WORKFLOW.md` file so operators understand the expected paths for your Project Miru use case.

**Sources:**

- Linear API docs: [Issue state transitions](https://developers.linear.app/docs/graphql/working-with-issues) (if available) or inferred from GraphQL schema
- Linear UI: workflow settings
- Community knowledge from n8n forum posts about Linear integrations

### Q8.4: GraphQL-specific error handling

GraphQL errors are _not_ HTTP errors; a successful GraphQL request returns 200 even if the query failed. You must inspect `item.json.errors` in n8n: if it's non-empty, the request failed at the data level. This differs from REST APIs where a 4xx/5xx response signals failure. A Code node pattern: `if (item.json.errors && item.json.errors.length > 0) { throw new Error(JSON.stringify(item.json.errors)) } else { return item }` to treat GraphQL errors as execution errors. Network-level errors (auth failures, server down) still return 4xx/5xx and trigger n8n's error handling. This dual-layer error check is GraphQL-specific and easy to miss.

**Sources:**

- GraphQL spec: error handling (official spec)
- n8n community: "GraphQL request succeeded but my data is null" troubleshooting threads

---

## Patterns worth integrating into N8N_SKILL.md

- **Linear webhook deduplication**: Log all received webhooks (issue ID + event type + createdAt) in `routing_history.jsonl`; skip if duplicate received within 10 seconds.
- **GraphQL double-error-check**: After Linear HTTP Request, always add a Code node that checks both `response.status >= 400` AND `item.json.errors` before returning success.
- **Rate limit awareness**: Monitor `X-RateLimit-Remaining` header after Linear queries; if < 100, log a warning and consider queuing requests.
- **State transition documentation**: Maintain a `LINEAR_WORKFLOW.md` listing valid state transitions for Project Miru issues (Backlog â†’ Todo â†’ In Progress â†’ In Review â†’ Done); include any custom workflow rules from your Linear workspace.
- **Linear GraphQL boilerplate**: Document the mutation and query templates used in W1/W2 (worker selection, issue creation) so future changes to Linear schema can be adapted quickly.

---

## Topic 9: LLM-as-router architecture (forward-looking, post-PRO-84 shadow mode)

### Q9.1: Prompt structure for LLM routers

A production LLM router prompt has four parts: (1) **System context** ("You are a worker selection system"), (2) **Canon examples** (2â€“5 past routing decisions with explanations: "Input: [ticket details]. Output: [worker]. Reasoning: [why]."), (3) **Task specification** ("Given the following ticket, select the best worker."), (4) **Decision schema** (JSON structure you expect: `{"selected_worker": "id", "confidence": 0â€“1, "reasoning": "string"}`). Canon examples should reflect your data (real or anonymized past decisions); they anchor the LLM's behavior more than instructions alone. Many practitioners store the canon examples in a separate config file and inject them into the prompt at workflow runtime to allow quick tuning without editing the Code node.

**Sources:**

- LLM prompt engineering guides (e.g., OpenAI Cookbook on few-shot learning)
- n8n community: posts on LLM-based workflows
- No dedicated LLM router architecture blog; this is emergent n8n practice

### Q9.2: Output schema constraintsâ€”JSON mode, structured output, function calling

Use the LLM's **JSON mode** (available in OpenAI's `gpt-4o`, Claude, etc.) to force a JSON response, then validate the schema in a Code node. Alternatively, use **structured output** / **function calling** (OpenAI's `tools` parameter or Claude's `tool_use` feature) where the LLM returns a structured object guaranteed to match your schema. Structured output is more reliable than JSON mode but requires more setup. For determinism in testing, you must fix the output schema and validate in your Code node post-LLM: `if (!response.selected_worker) { throw Error("schema mismatch") }`. Never assume the LLM respects your schema; always validate. This validation layer is your determinism gate for testing.

**Sources:**

- OpenAI docs: [JSON mode](https://platform.openai.com/docs/guides/structured-outputs), [function calling](https://platform.openai.com/docs/guides/function-calling)
- Claude docs: [tool_use](https://docs.anthropic.com/en/docs/build-a-system-prompt-with-tools)
- n8n OpenAI integration examples

### Q9.3: Shadow mode rolloutâ€”running parallel, logging, comparison metrics

Shadow mode: run the LLM router _parallel_ to your deterministic scorer (the current rule-based system), log both outputs, but use the deterministic output for the actual routing decision. Store both `deterministic_worker` and `llm_worker` in your state file (`routing_history.jsonl`) along with the ticket details. Typical shadow period: 1â€“2 weeks of production traffic. Metrics to track: (1) **Agreement rate** (how often deterministic and LLM choose the same worker), (2) **Worker load distribution** (does LLM unbalance load compared to deterministic?), (3) **Outcome metrics** (if you have SLA or quality signals: do tickets routed by each system have similar resolution time?). Graduation triggers: >90% agreement rate, no degradation in outcome metrics, operator spot-check passes.

**Sources:**

- ML/SRE shadow mode patterns (not n8n-specific): Google SRE Book, MLOps community posts
- n8n forum: posts on A/B testing workflows
- No dedicated n8n shadow mode guide

### Q9.4: Operator override rate as drift signal

Track how often the operator manually re-routes a decision made by the LLM (vs deterministic). An override rate spike (e.g., normally 5%, suddenly 25%) signals that the LLM is drifting (e.g., the prompt became stale, the worker pool changed, or an external factor changed the problem domain). Typical thresholds: <5% normal, 5â€“10% investigate, >10% escalate to re-tune. When you see a spike, pull the overridden routing decisions from the last 24 hours, review them, and either (a) update the canon examples in your prompt, (b) adjust the confidence threshold (e.g., only use LLM if confidence > 0.8, else fall back to deterministic), or (c) rollback to deterministic-only. Log operator override reasons if possible (e.g., "operator said: worker unavailable" vs "operator said: wrong skill set") to inform tuning.

**Sources:**

- MLOps community posts on monitoring model drift
- No dedicated n8n post; this is production operations practice

### Q9.5: Prompt versioning for routers in Code nodes

Since your LLM router lives in a Code node (no native version control within n8n), adopt this pattern: (1) Store the prompt template + canon examples in a GitHub repo (separate file, e.g., `router_prompt_v1.md`). (2) In the Code node, fetch the prompt from GitHub at runtime (or commit it as a string constant and manually update when you change it). (3) Log the prompt version used in each routing decision (`prompt_version: "v1.2"`) so you can correlate override spikes with prompt changes. (4) Tag each significant prompt change in GitHub (e.g., `router-prompt-v1.2`) for easy rollback. This adds minimal overhead and makes debugging prompt changes much easier.

**Sources:**

- GitHub versioning best practices (not n8n-specific)
- n8n community: posts on managing configuration outside workflows

---

## Patterns worth integrating into N8N_SKILL.md

- **LLM router prompt template**: Document the system context, canon examples, task spec, and JSON schema. Store in `router_prompt_v1.md` in GitHub.
- **Shadow mode metrics tracking**: Log `deterministic_worker`, `llm_worker`, `agreement: true/false` in `routing_history.jsonl` for analysis.
- **Override rate dashboard**: Create a scheduled workflow that calculates daily override rate and alerts if > 10%; store results in a metrics file.
- **Prompt versioning**: When updating the prompt, increment version number (v1 â†’ v1.1), tag in GitHub, and test in shadow mode before rollout.
- **Schema validation gate**: After LLM response, validate JSON structure and throw error if schema mismatches; log all validation failures.

---

## Topic 10: End-to-end testing without a framework

### Q10.1: Replaying a real webhook payload against a dev workflow

Export a real webhook payload from the n8n execution log (click the Webhook trigger node in a past execution, copy the raw input JSON). Save it to a file, e.g., `test_callback_query.json`. Create a manual trigger workflow on your dev n8n instance with a Set node that loads this JSON, then wire it to the same flow as your production webhook workflow (e.g., `{json: $fromJson($env.TEST_PAYLOAD)}`). Store `TEST_PAYLOAD` as an n8n environment variable (copy-pasted from your saved JSON). When you run the manual workflow, it replays the exact event. You can now test your parsing, routing, and state changes without Telegram. Export multiple real payloads (success case, edge cases) to build a test library.

**Sources:**

- n8n docs: environment variables and manual workflows
- n8n community: posts on testing webhook workflows without live API calls

### Q10.2: Tracing execution across chained workflows (W1 â†’ W2 via Linear) in the n8n UI

When W1 (intake) creates a Linear issue and W2 (router) processes it, trace the flow: (1) Open W1's execution log, find the Linear mutation response, extract the issue ID (e.g., `ISS-123`). (2) Search the Linear webhook logs for that issue ID (look for the webhook webhook that triggered W2 or the issue state change). (3) Open W2's execution log, verify the routing decision. Gotcha: n8n doesn't auto-correlate executions across workflows; you must manually follow the ID chain. Pattern: inject a `correlation_id` (e.g., UUID or ticket ID) into every log entry and state file so you can grep across workflows. Store it in `routing_history.jsonl` as `"correlation_id": "ISS-123"` so you can search for all events tied to that ticket.

**Sources:**

- n8n docs: execution history and API
- SRE/observability best practices on correlation IDs (not n8n-specific)

### Q10.3: Snapshot / golden-output comparison

Without a test framework, snapshot testing works like this: (1) Save the "golden" output of a workflow (the JSON state file, the Linear issue created, the Telegram message sent) to a reference file, e.g., `fixtures/W2_routing_output_v1.json`. (2) After code changes, export the new output and diff it against the golden: `diff <(jq . routing_history.jsonl | tail -1) fixtures/W2_routing_output_v1.json`. (3) If the diff is expected, update the golden file and commit it. (4) For Linear API responses, save the mutation response (issue ID, state, assignee) to a reference, then re-run the same workflow and compare the new response. Git diff on JSON files is your testing tool. For more robust comparison, write a small script (Node.js or Python) that compares specific fields and ignores timestamps / IDs that are expected to differ, e.g., "only compare selected_worker, confidence, reasoning; ignore correlation_id".

**Sources:**

- Jest snapshot testing docs (for inspiration; the pattern is language-agnostic)
- n8n community: manual testing practices

---

## Patterns worth integrating into N8N_SKILL.md

- **Test payload export**: When a real webhook arrives that you want to test against, click the Webhook node in the execution log, copy the input JSON, save to `tests/payloads/SCENARIO_NAME.json`, and commit to repo.
- **Manual test workflow template**: Create a reusable manual trigger workflow that accepts a `test_payload` environment variable and injects it into the flow for replay testing.
- **Correlation ID pattern**: In every workflow, generate or inherit a `correlation_id` (UUID or issue ID) and log it in every external call and state file. Document the correlation ID in the workflow comment.
- **Golden output fixtures**: After each critical workflow produces output, save a reference JSON to `fixtures/WORKFLOW_NAME_output.json`. After code changes, diff the new output against golden; commit updated fixtures after manual review.
- **Diff-based regression**: Use `jq . | diff -u` on JSON state files for quick regression checks; write a small Node.js validation script for complex comparisons.

---

## Final Integration Checklist for N8N_SKILL.md

- [ ] **Code node standards**: Add error handling rule (throw vs return), item shape spec, input accessor decision tree, logging for production.
- [ ] **Type safety**: Add If node defensive casting pattern; document null/undefined coercion gotchas.
- [ ] **HTTP reliability**: Add timeout calibration, 4xx vs 5xx handling pattern, error inspection post-HTTP.
- [ ] **Webhook safety**: Add deactivation detection pattern, healthcheck template, access controls.
- [ ] **Error propagation**: Add Continue on Fail audit guidelines, error object shape validation, subworkflow isolation, shadow validation, execution audit trail.
- [ ] **Testing methodology**: Add test workflow library structure, Pin Data hygiene, execution log checklist, error condition testing.
- [ ] **Telegram patterns**: Add callback_query deduplication, immediate answerCallbackQuery, raw HTTP standard, timeout escalation, stale cleanup.
- [ ] **Linear patterns**: Add webhook idempotency, GraphQL double-error-check, rate limit awareness, state transition docs, GraphQL boilerplate.
- [ ] **LLM router (shadow)**: Add prompt template structure, shadow mode metrics, override rate tracking, prompt versioning, schema validation.
- [ ] **E2E testing**: Add payload replay pattern, correlation ID standard, golden output fixtures, diff-based regression.

All patterns above are grounded in n8n community practice, official documentation, and production patterns observed in GitHub repos and forum posts. Where sources are thin, I've flagged as "emergent practice" or "community lore." This research fills the Verification Gaps table by documenting both the gotchas and the patterns practitioners use to mitigate them.
