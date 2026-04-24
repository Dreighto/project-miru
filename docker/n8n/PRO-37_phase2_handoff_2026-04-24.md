# PRO-37 Phase 2 Handoff — Pushover → Telegram + W7 Callback Handler

**Date:** 2026-04-24 (completed)
**Ticket:** [PRO-37](https://linear.app/project-miru/issue/PRO-37)
**Branch:** `dreighto/pro-37-swap-n8n-notification-layer-from-pushover-telegram-with` (pushed, no PR opened)
**Status:** CONFIRMED WORKING — all test gates green, branch pushed, awaiting operator decision on PR

---

## TL;DR for Claude Chat

The n8n notification layer no longer uses Pushover for anything. All nine outgoing notifications across four workflows now go through Telegram, and approval buttons are live: an operator tapping **Approve / Override / Triage / Request Revision** on a Telegram message triggers the new **W7 — Telegram Callback Handler** workflow, which verifies an HMAC on the callback, looks up the intent row, applies the corresponding Linear label mutation, edits the message to strip buttons, and appends a row to `routing_history.jsonl` with Phase 2 provenance fields (`callback_source`, `button_tapped`, `callback_age_seconds`, etc.).

W1 stayed active throughout. W2 router and W2 watchdog stayed inactive throughout (calibration debt from the W2 build is still unresolved and out of scope for Phase 2). Phone round-trip verified on all four actions plus idempotency, HMAC tamper, and >10-min replay defense.

---

## Commits (5, pushed)

On branch `dreighto/pro-37-swap-n8n-notification-layer-from-pushover-telegram-with`:

| SHA | Chunk |
|---|---|
| `0888136` | env wiring for Telegram callback secret and chat id |
| `6b4ebef` | add W7 Telegram callback handler workflow |
| `4d04198` | swap W1 informational nodes from Pushover to Telegram |
| `8d4d792` | swap W2 router to Telegram with 4-button approval keyboard |
| `8f7e719` | swap W2 watchdog to Telegram with 2-button stale keyboard |

Branch parent is `ad62e82` (PRO-37 Phase 2 prereq — Tailscale HTTPS + Funnel). The branch was forked from the prior PRO-33 feature branch rather than `main`, so it carries four inherited commits on top of `origin/main` in addition to the five Phase 2 chunks. Worth a line in the PR body or a `git rebase --onto main` before the PR goes up — up to operator.

---

## Files changed

### New
- `docker/n8n/workflows/w7-telegram-callback-handler.json` — 13-node handler (see architecture below)

### Modified
- `docker/n8n/.env.example` — added `TELEGRAM_CHAT_ID`, `TELEGRAM_CALLBACK_SECRET` with comments
- `docker/n8n/docker-compose.yml` — env passthrough for both new vars (Pushover var kept for rollback)
- `docker/n8n/workflows/w1-planning-intake.json` — 3 informational Telegram swaps (a011, a014, a016); no buttons; `name` fields preserved
- `docker/n8n/workflows/w1-error-handler.json` — 1 informational Telegram swap (e006); no buttons
- `docker/n8n/workflows/w2_worker_selection_router.json` — 4 Telegram swaps (w2013 proposal with 4 buttons, plus w2016/w2998b/w2999c informational); new upstream Code node `w2012a-mint-callback-token` inserted before w2013
- `docker/n8n/workflows/w2_pending_approval_watchdog.json` — 1 Telegram swap (w2w04 stale with 2 buttons); three new upstream nodes inserted (`w2w03b-linear-fetch-context`, `w2w03c-enrich`, `w2w03d-mint-callback-token`) because the watchdog's original query only returned issue metadata, not label context needed for mutations
- `.gitignore` — added `data/pending_callbacks.jsonl`

### Not modified (intentional)
- `docker/n8n/scripts/deploy-workflow.ps1` — extending it to accept `{{TELEGRAM_CRED_ID}}` placeholders is logged as design-debt; for now the Telegram credential UUID `5tWWGl8jkCOHqJQg` is hardcoded in every swapped workflow JSON
- Pushover credential (`pushover-n8n`) remains in the n8n vault untouched, available for manual rollback

---

## Architecture: how a callback round-trip works

```
  [sender workflow]                      [handler workflow]

  Linear state shift                     TelegramTrigger
        │                                      │
  mint-callback-token            ──────►  ack-callback (answerQuery, continueOnFail)
  (sign per-button HMAC,                        │
   append intent row,                     parse-payload (slice 61-byte cb)
   build inline_keyboard)                       │
        │                                  hmac-validate (timingSafeEqual + 10min window)
  sendMessage (HTTP)                            │
        │                                  validate-branch ─► reject ─► noop-rejected
        ▼                                       │
    user taps button ──────────────────►   lookup-pending (read pending_callbacks.jsonl by token)
                                                 │
                                           found-branch ─► already_decided ─► noop-duplicate
                                                 │
                                           build-mutation (branch on action)
                                                 │
                                           linear-mutate (issueUpdate + commentCreate)
                                                 │
                                           edit-message (strip buttons, show outcome)
                                                 │
                                           mark-decided (append decided row + routing_history row)
```

### callback_data encoding (fixed 61 bytes)

```
  T(12)   A(1)   N(8)   S(8)     H(32)
  token   act    nonce  ts_hex   hmac_hex
```

- `token`: 12 hex chars, one per outgoing proposal (execution-unique)
- `action`: one char — `a` Approve, `o` Override, `t` Triage, `r` Request Revision
- `nonce`: 8 hex chars, per-button random
- `ts_hex`: 8 hex chars = unix-minutes since anchor `2026-01-01T00:00:00Z` (`unix 1767225600`). Rolls over ~8000 years from anchor.
- `hmac_hex`: first 16 bytes of HMAC-SHA256 hex = 32 chars. Secret = `TELEGRAM_CALLBACK_SECRET` env var.

61 bytes ≤ Telegram's 64-byte callback_data ceiling, with 3 bytes of headroom.

### State files (bind-mounted `D:\dev\miru\data` → `/miru-data`)

- `pending_callbacks.jsonl` — append-only. Two kinds of rows:
  - `kind: "intent"` written at mint time with `token`, `trace_id`, `issue_id`, `labels_map`, `issue_existing_label_ids`, etc.
  - `kind: "decided"` written after handler applies mutation. Idempotency key = `(token, action)`.
- `routing_history.jsonl` — canonical schema plus Phase 2 extensions: `callback_source`, `button_tapped`, `button_set`, `callback_age_seconds`, `worker_response_ref` (`callback-<token>`), `operator_override_flag`, `decided_at`, `decided_by_user_id`.

### W7 idempotency + rejection paths

- **Invalid HMAC or stale timestamp** → `w7-noop-rejected` NoOp. Silent to the operator; nothing touches Linear or history.
- **Token not in pending_callbacks.jsonl** → `w7-noop-rejected`. (Fresh deploy, lost state, or forged callback.)
- **Token already has a `decided` row** → `w7-noop-duplicate` NoOp. Protects against double-tap.
- **All validators pass** → full chain: `linear-mutate` → `edit-message` → `mark-decided`.

Silent rejection is by design — we don't want to echo back "your callback was rejected" because that leaks handler state. Pushover spinner clears via `ack-callback` which runs unconditionally (`continueOnFail: true`).

---

## Deployment state at time of handoff

| Workflow | ID | Active? | Notes |
|---|---|---|---|
| W1 — Planning Intake → Task Draft Sync | `tFEbP14EnGQ69YZn` | **ACTIVE** | Uptime preserved through swap |
| W1 — Error Handler | `l5wzFuWnJ2zSoMM2` | **ACTIVE** | Uptime preserved through swap |
| W7 — Telegram Callback Handler | `rJiLlMFKQh8t4Y9K` | **ACTIVE** | New; TelegramTrigger webhook registered via Tailscale Funnel |
| W2 — Worker Selection Router | `6aCG6L5Z4VvqWogq` | **INACTIVE** | Stays off — calibration debt unresolved |
| W2 — Pending-Approval Watchdog | `9hRoVyMWkbi0Wba5` | **INACTIVE** | Stays off — tied to W2 router debt |

Fixture workflow (`TEST FIXTURE — W2 proposal Telegram seed`, id `EGbPF6kb9dz2MNgZ`) and source file `docker/n8n/workflows/w2-tg-test-fixture.json` were both deleted after Phase 5.

---

## Test matrix (all green)

All tests run against a live Telegram chat on the operator's phone over Tailscale Funnel (`https://room.taila28611.ts.net/webhook/w7-telegram-callback/webhook`).

### Action round-trips (via fixture → PRO-46, then final re-verify via Python script → PRO-47)

| Action | Exec ID | Expected labels after tap | Actual |
|---|---|---|---|
| Approve (`a`) | #81 | `claude-code` kept; `pending-approval` removed | ✓ |
| Override (`o`) | #85 | `claude-code` removed; `triage` added; `pending-approval` removed | ✓ |
| Triage (`t`) | #87 | `triage` added; `pending-approval` removed (claude-code still present because the fixture re-seeded it) | ✓ |
| Request Revision (`r`) | #88 | `manual-intervention-required` added; `pending-approval` removed | ✓ |
| Approve (re-verify) | fresh | PRO-47 ended up `[claude-code]` | ✓ |

### Security + idempotency gates

| Gate | Result |
|---|---|
| Idempotency (second tap on same message) | Routed to `w7-noop-duplicate`; line counts in both JSONL files unchanged |
| HMAC tamper (curl with last hex char flipped) | Routed to `w7-noop-rejected`; `reject_reason=hmac mismatch` |
| Replay defense (curl with ts_hex set to 15 min in the past) | Routed to `w7-noop-rejected`; `reject_reason=callback older than 10 min (age=901s)` |

### Per-tap UX gates

For each live action tap:
- Spinner on tapped button cleared within ~1s (ack-callback fires before handler work)
- Message text rewritten to `Decided: <label>` + metadata line
- Inline buttons stripped
- `routing_history.jsonl` line count +1 with `callback_source=telegram`, correct `button_tapped`, `callback_age_seconds` within 10-min window, `decided_at` timestamp in UTC

---

## Key decisions + lessons (for canon sync)

### Lessons that should land in Notion canon page 16

1. **n8n Code node `$input` pitfall after API calls.** If a Code node sits immediately after an HTTP Request / Linear / Telegram node, `$input.item.json` is the API response, not the upstream business data. Must use `$('PriorCodeNode').item.json` to reach upstream fields. Audited every workflow JSON for this — all safe now, and the W7 handler's `w7003-parse-payload` uses `$('w7001-telegram-trigger').item.json` explicitly.

2. **n8n TelegramTrigger webhook secret is deterministic.** n8n derives the expected `X-Telegram-Bot-Api-Secret-Token` as `${workflow.id}_${node.id}` stripped of non-alphanumeric/underscore/hyphen chars. Source: `/usr/local/lib/node_modules/n8n/.../Telegram/GenericFunctions.js:148-151` inside the container. This is how we crafted the HMAC tamper + replay tests via curl (no Telegram tap needed) — POST straight to the Tailscale Funnel URL with that header.

3. **Telegram HTML parse_mode is safer than Markdown V1** for our use case. Arbitrary Linear titles contain `[ ] _ * ( )` which break Markdown V1's entity parser. W7 uses HTML with `&<>` escaping via a small `esc()` helper; no Telegram parse errors encountered.

4. **n8n Telegram node's `inlineKeyboard.rows[].row.buttons` expression slot is finicky.** We sidestepped it by using raw `httpRequest` to `api.telegram.org/bot.../sendMessage` with the whole `reply_markup` object built upstream in the mint Code node. Pattern used in both W2 router's `w2013` and W2 watchdog's `w2w04`. Informational Telegram nodes without buttons still use `n8n-nodes-base.telegram` fine.

5. **Telegram credential UUID is hardcoded.** `deploy-workflow.ps1:243` currently rejects any unrecognized `{{*_CRED_ID}}` placeholder. Extending the deploy script for `{{TELEGRAM_CRED_ID}}` is logged as design-debt. If the Telegram credential is ever rotated in n8n, all five workflow JSONs will need simultaneous re-edit; this is a known gotcha.

6. **Anchor epoch must match between mint and handler.** `2026-01-01T00:00:00Z` = `unix 1767225600`. Encoded in four places: both mint Code nodes (`w2012a-mint-callback-token`, `w2w03d-mint-callback-token`), the W7 parse node (`w7003-parse-payload`), and the HMAC validator (`w7004-hmac-validate`). Any drift between these would silently reject everything.

7. **`name` field preservation on Pushover→Telegram swap.** W1 lesson #1 already said this: n8n connections key on `name`, not `id` or `type`. Every swap preserved the literal string including the word "Pushover" (e.g., `a011-pushover-dupe` now runs a Telegram send but still has `name: "Pushover: Dedupe ping"`). This is intentional; it kept connection blocks untouched. Cosmetic rename is a future cleanup task, not for this phase.

### Design-debt explicitly deferred

- **Deploy script `{{TELEGRAM_CRED_ID}}` placeholder support** — see lesson 5 above.
- **`pending_callbacks.jsonl` GC** — append-only, unbounded. Not a problem at current volumes; needs a truncation/rotation scheme long-term.
- **Node name rename from `*-pushover-*` to `*-telegram-*`** — cosmetic; would require connections block rewrite and buys nothing functional.
- **W2 router + W2 watchdog activation** — blocked on the original W2 calibration work. Phase 2 deliberately did not touch that.
- **Branch carries inherited commits** — branch was forked from the PRO-33 branch rather than `main`, so a PR on PRO-37 would show four inherited commits on top of the five Phase 2 ones. Operator may want to rebase or note in PR body.

---

## Rollback path

Because Pushover credential and nodes are not mentioned in any live workflow JSON but the credential `pushover-n8n` is still present in the n8n vault, rollback is surgical:

1. `git revert 0888136..8f7e719` (or `git reset --hard ad62e82` on the branch before merge)
2. Redeploy each reverted workflow JSON via `deploy-workflow.ps1`
3. W7 stays harmless if deactivated — it only fires on callback_query webhooks from the Telegram bot; deactivating unregisters the webhook.

No data migration needed. `pending_callbacks.jsonl` is gitignored and is additive only.

---

## Environment required on the host

Both new env vars live in `D:\dev\miru\.env` (already populated during PRO-37 Phase 2 prereq) and are passed through `docker/n8n/docker-compose.yml` into the container:

- `TELEGRAM_CHAT_ID` — numeric chat id of the operator's personal Telegram chat
- `TELEGRAM_CALLBACK_SECRET` — 64-char hex (32 raw bytes). Rotating this silently invalidates any in-flight callbacks signed under the old secret; documented in `.env.example`.
- `TELEGRAM_BOT_TOKEN` — already present from prereq; used by all Telegram sends + the TelegramTrigger.

The Tailscale Funnel HTTPS endpoint (`https://room.taila28611.ts.net/`) from the PRO-37 Phase 2 prereq is load-bearing — the TelegramTrigger registers its webhook with Telegram's Bot API using that public URL. If the Funnel state changes, the webhook must be re-registered (n8n re-activates the trigger on workflow save/re-activate).

---

## What Claude Chat may want to do next

1. **Move PRO-37 ticket to review / done** once operator confirms the PR decision.
2. **Canon update (Notion page 16 Lessons)** — merge the 7 lessons above.
3. **Open follow-up tickets** for the three deferred design-debts (deploy-script placeholder, JSONL GC, node rename).
4. **Decide W2 activation policy** — W2 calibration is still the gate. Once that's resolved, activating W2 router + watchdog should be low-risk because both were deployed and schema-validated during Phase 2, just left inactive.
5. **Archive the Pushover credential** from n8n once rollback window is considered closed (suggest 1–2 weeks of stable Telegram operation before pulling).

---

## Completion contract

**STATUS: CONFIRMED WORKING.** Five chunked commits pushed to `origin/dreighto/pro-37-swap-n8n-notification-layer-from-pushover-telegram-with`. W1 uptime preserved. W2 router + watchdog deployed and verified inactive. W7 handler active and round-tripped on all four actions plus three security gates. Fixture artifacts and test Linear issues (PRO-46, PRO-47) cleaned up.
