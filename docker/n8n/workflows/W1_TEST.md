# W1 — Planning Intake test plan

End-to-end verification for the W1 workflow (`W1 — Planning Intake → Task
Draft Sync`) and its error handler (`W1 — Error Handler`). Run from ROOM.

## Prerequisites

- Both workflows deployed via `..\scripts\deploy-workflow.ps1`.
- Error Workflow wired manually: main W1 → **Settings → Error Workflow →
  W1 — Error Handler** → Save.
- Both workflows **activated** in the UI.
- Phone logged into Pushover so you can see push notifications.

## Webhook URL

The manual-webhook trigger in W1 is at:

```
http://localhost:15678/webhook/w1-intake
http://room.taila28611.ts.net:15678/webhook/w1-intake
```

POST body shape: `{ "notion_page_id": "<notion-page-id>" }`.

## Test 1 — Happy path (create new draft)

1. **Create a throwaway intake page** in the AI Inbox Notion database
   (ID `639d5c255fe849bab3c66380cabbd360`):
   - Title: `TEST - W1 validation <iso-timestamp>`
   - Body: `Test intake for W1 workflow validation. Safe to delete after verification.`
2. Copy the Notion page ID (from the page URL, the 32-char hex tail).
3. POST to the webhook:

   ```powershell
   $pageId = '<paste-here>'
   curl.exe -X POST http://localhost:15678/webhook/w1-intake `
     -H 'Content-Type: application/json' `
     -d "{`"notion_page_id`":`"$pageId`"}"
   ```

4. **Verify (all five must be true):**
   - [ ] n8n **Executions** tab shows the workflow completed green.
   - [ ] A new Linear issue exists in Project Miru team, state **Todo**,
         title matches the Notion page title, has `intake-draft` label,
         priority **Medium** (unless keyword bumped it).
   - [ ] The Notion intake page now has a **bookmark** block at the bottom
         pointing at the new Linear issue URL.
   - [ ] Phone received a Pushover notification titled **"W1 draft created"**
         at priority 0 (normal).
   - [ ] No Linear issue with label `n8n-error-queue` appeared.

## Test 2 — Dedupe path

1. Without changing anything, re-POST the exact same webhook body from
   Test 1.
2. **Verify:**
   - [ ] n8n execution completed green.
   - [ ] **No** second Linear issue was created.
   - [ ] A comment appeared on the existing Linear issue from Test 1
         with text "Another intake page references this task: ...".
   - [ ] Phone received a Pushover notification titled **"W1 dedupe matched"**
         at priority 0.

## Test 3 — Error-path (error handler fires)

Pick ONE of these to force a failure without corrupting production state:

**Option A (recommended):** In the Linear node `Linear: Create issue`,
temporarily edit the `teamId` variable in the request body to an invalid UUID
(e.g. replace one character). Save, then re-POST the webhook. Revert after.

**Option B:** Temporarily rename the `linear-n8n` credential in n8n so the
HTTP Request nodes can't auth. Revert after.

**Verify:**
- [ ] Main W1 execution shows red/failed in Executions.
- [ ] A new Linear issue appeared with label `n8n-error-queue`,
      title `n8n W1 — Planning Intake → Task Draft Sync error — <iso>`,
      body including failed node name + error message + execution URL.
- [ ] Phone received a Pushover notification titled
      **"n8n workflow failed"** at priority **1** (high).
- [ ] The original Notion intake test page was NOT back-linked (or was only
      partially touched — acceptable if `Linear: Create issue` failed
      upstream of back-link).

**Revert** your forced-failure change before moving on.

## Test 4 — Partial success (back-link failure branch)

Skip unless specifically verifying partial-success recovery. To force a
back-link failure: temporarily edit the Notion HTTP Request in `Notion:
Back-link` to point at an invalid page ID. Run once. Verify:

- [ ] Linear issue was created (Test 1 success path happened).
- [ ] Linear issue has an additional label `notion-backlink-failed`.
- [ ] A comment on the Linear issue says "Notion back-link failed after
      retries...".
- [ ] Phone received Pushover priority **1** titled **"W1 back-link failed"**.
- [ ] The error handler did NOT fire (this is a handled partial success, not
      a workflow error).

## Cleanup

- Close any Linear test issues as **Cancelled**.
- Delete the Notion intake test pages.
- If Test 3 created an `n8n-error-queue` issue, close it as **Cancelled**.
- n8n execution logs auto-prune; low-volume test runs can be ignored.
