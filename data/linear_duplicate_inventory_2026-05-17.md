# Linear duplicate-ticket inventory — Project Miru

**Date:** 2026-05-17
**Author:** CC (in-session investigation, no Linear writes performed)

## Total

**608** issues in `Duplicate` state on team Project Miru (paginated to completion: 250 + 250 + 108, hasNextPage=false on page 3).

## Source breakdown

| Source | Count | Failed n8n node | Workflow active? |
| --- | ---: | --- | --- |
| **n8n: W2 — Worker Selection Router** (`6aCG6L5Z4VvqWogq`) | 538 | `w2999a-router-failure-code` (537) / `w2003a-dedupe-guard` (1) | **inactive** (deactivated; last exec 2026-05-16T18:57Z error) |
| **n8n: W1 — Planning Intake → Task Draft Sync** (`tFEbP14EnGQ69YZn`) | 13 | (not parsed) | **inactive** (deactivated 2026-05-12) |
| **n8n: Drift Scanner — Linear ↔ Completion Marker** (`saoYxdMRcWXAD2LI`) | 1 | `dsw007-send-telegram` | **ACTIVE** — errored today 2026-05-17T16:00Z (execution 117225) |
| Manual / other (legitimate dupes) | 56 | — | — |
| **TOTAL** | **608** | | |

## Date distribution

| Date | Count |
| --- | ---: |
| 2026-05-16 | 379 |
| 2026-05-15 | 159 |
| 2026-04-24 | 18 |
| 2026-04-27 | 16 |
| 2026-04-25 | 15 |
| 2026-05-02 | 6 |
| 2026-04-23 | 5 |
| 2026-04-11 | 4 |
| 2026-05-01 | 2 |
| (other days, 1 each) | 6 |

88% of the pile was filed on the two-day burst 2026-05-15..16 from the W2 router. ID ranges: PRO-362..PRO-864 are almost entirely the burst; PRO-1..PRO-95 hold most of the manual + earlier-era dupes.

## Safe-to-purge call

* **551 bot-filed dupes** (W2 + W1 + Drift Scanner): safe to bulk-cancel/delete. The two high-volume sources are already deactivated and cannot refill the pile. Drift Scanner volume is 1 dupe in 2 weeks despite running daily, so even at status quo the regrowth rate is negligible.
* **56 manual dupes**: review case-by-case before deletion. Many are legitimate Linear `duplicate_of` relations (e.g. test-fixture cleanups, planning-iteration drafts). The Duplicate state already closes them; deleting destroys the audit trail.

## Open question for operator

Drift Scanner still errors on `dsw007-send-telegram` (failed today). Volume is low but it's a live source of new Duplicate tickets. Options:

1. Fix the send-telegram node (Telegram bot token / chat-id config drift?).
2. Deactivate the workflow until fixed.
3. Leave it; tolerate 1 dupe per month.

Not in PRO-904 scope. Filing as a follow-up ticket recommended only if operator wants tracking.

## What I did NOT do

* Did not delete or modify any Linear tickets.
* Did not deactivate or modify any n8n workflow.
* Did not deduplicate the 56 manual dupes (no rules-based heuristic was reliable; needs human review).
