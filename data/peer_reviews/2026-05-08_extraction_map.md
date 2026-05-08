# Instruction Architecture Migration — Extraction Map

**Status:** Step 1 of 8 (extraction map only — no file changes yet).
**Created:** 2026-05-08
**Architecture version:** MIRU-INSTRUCTIONS-v2

This map is the canonical blueprint for the migration. Every section in the
current `CLAUDE.md` (726 lines) and `AGENTS.md` (297 lines) is accounted for.
Each entry has: source location, destination, category, and rationale.

---

## Categories

- **CRITICAL** — stays in slim `CLAUDE.md` core (always loaded). Blast-radius rules only.
- **OVERLAY** — moves to `.miru/overlays/<file>.md` (loaded by task type).
- **REFERENCE** — moves to `.miru/reference/<file>.md` (factual lookup, on demand).
- **AGENTS** — moves to or stays in `AGENTS.md` (universal worker baseline, communication-style rules).
- **KEEP** — already in the right place (no action needed).
- **DEPRECATED** — content is outdated or duplicative; drop with note.

---

## Source: CLAUDE.md (726 lines)

| #   | Section                                       | Lines   | Category  | Destination                             | Notes                                               |
| --- | --------------------------------------------- | ------- | --------- | --------------------------------------- | --------------------------------------------------- |
| 1   | Title                                         | 1       | CRITICAL  | `CLAUDE.md` (rewritten)                 | Becomes "Project Miru — Worker Core" agent-agnostic |
| 2   | Copy-paste content for manual routing         | 3-9     | AGENTS    | `AGENTS.md`                             | GMI: "communication style, not blast-radius"        |
| 3   | Ports — Permanent Reference                   | 11-17   | REFERENCE | `.miru/reference/ports-and-services.md` | Pure facts                                          |
| 4   | Repo Boundary                                 | 19-26   | CRITICAL  | `CLAUDE.md`                             | Blast-radius. Tighten to ~5 lines                   |
| 5   | Kill Switch — Pre-flight Gate                 | 28-43   | CRITICAL  | `CLAUDE.md`                             | Pre-flight #1, cannot skip                          |
| 6   | Worktree Cleanliness Gate                     | 45-58   | CRITICAL  | `CLAUDE.md`                             | Pre-flight #2, cannot skip                          |
| 7   | No Overlap Rule                               | 60-64   | CRITICAL  | `CLAUDE.md`                             | 4 lines, prevents concurrent edits                  |
| 8   | Linear — Ticket Routing                       | 66-92   | REFERENCE | `.miru/reference/linear-projects.md`    | Project ID table is reference                       |
| 8a  | Linear — projectId requirement (the rule)     | 68      | OVERLAY   | `.miru/overlays/workflow-dispatch.md`   | The rule "must include projectId"                   |
| 9   | Notion — Read/Write Rules                     | 94-105  | OVERLAY   | `.miru/overlays/domain-ops.md`          | Notion-specific, not universal                      |
| 10  | Adopted Lessons (header)                      | 107-109 | OVERLAY   | `.miru/overlays/adopted-lessons.md`     | All adopted lessons in one overlay                  |
| 11  | Test JS as it lives in workflow JSON          | 111-124 | OVERLAY   | `.miru/overlays/adopted-lessons.md`     | PRO-189 retro                                       |
| 12  | Lock design in Linear ticket                  | 126-150 | OVERLAY   | `.miru/overlays/adopted-lessons.md`     | PRO-180 retro                                       |
| 13  | PR Merge Policy — entire section              | 152-262 | OVERLAY   | `.miru/overlays/workflow-git.md`        | Largest single section, all git/PR rules            |
| 14  | Append-only data files                        | 264-280 | CRITICAL  | `CLAUDE.md`                             | Blast-radius — data corruption if violated          |
| 15  | MCP Tool Usage Rules                          | 282-292 | OVERLAY   | `.miru/overlays/domain-ops.md`          | MCP-specific                                        |
| 16  | Gateway Tool Profile Enforcement              | 294-316 | OVERLAY   | `.miru/overlays/workflow-dispatch.md`   | Orchestration topic                                 |
| 17  | Ingress Classifier                            | 318-350 | OVERLAY   | `.miru/overlays/workflow-dispatch.md`   | Orchestration topic                                 |
| 18  | Database Rules                                | 352-357 | REFERENCE | `.miru/reference/database-rules.md`     | Path facts + access constraints                     |
| 19  | Scheduled Tasks — no focus stealing           | 359-377 | OVERLAY   | `.miru/overlays/domain-ops.md`          | Windows ops topic                                   |
| 20  | Restart Rules                                 | 379-384 | REFERENCE | `.miru/reference/restart-procedures.md` | Pure command list                                   |
| 21  | File Placement — service boundaries           | 386-422 | REFERENCE | `.miru/reference/file-placement.md`     | Lookup tables, NEVER list                           |
| 22  | Autonomous Operations — CH Decision Authority | 426-477 | OVERLAY   | `.miru/overlays/workflow-dispatch.md`   | CH-specific orchestration                           |
| 23  | Worker-specific: CH + CC                      | 481-498 | KEEP      | `CLAUDE.md` worker section              | Becomes the CC role file content                    |
| 24  | Completion Contract — terminal states         | 500-511 | CRITICAL  | `CLAUDE.md`                             | Mandate stays in core                               |
| 25  | Automated PR review completion sequence       | 513-529 | OVERLAY   | `.miru/overlays/workflow-git.md`        | Already in AGENTS.md too — dedupe                   |
| 26  | Stall classification                          | 531-549 | OVERLAY   | `.miru/overlays/workflow-completion.md` | Provisional, terminal-state-adjacent                |
| 27  | Hygiene gate                                  | 551-560 | OVERLAY   | `.miru/overlays/workflow-git.md`        | Pre-PR check                                        |
| 28  | Heartbeat emission                            | 562-596 | OVERLAY   | `.miru/overlays/workflow-completion.md` | Schema + cadence                                    |
| 29  | Orchestrator-side modules                     | 598-605 | OVERLAY   | `.miru/overlays/workflow-dispatch.md`   | tools/orchestrator/ note                            |
| 30  | Craft Guides — load on demand                 | 607-645 | OVERLAY   | `.miru/overlays/domain-ui.md`           | UI craft trigger list                               |
| 31  | Completion-marker convention                  | 647-726 | OVERLAY   | `.miru/overlays/workflow-completion.md` | Full schema                                         |

**CLAUDE.md → CRITICAL total:** ~70 lines (sections 1, 4, 5, 6, 7, 14, 23, 24)
**CLAUDE.md → OVERLAY total:** ~480 lines distributed across 6 overlays
**CLAUDE.md → REFERENCE total:** ~80 lines distributed across 5 reference files
**CLAUDE.md → AGENTS:** ~7 lines (copy-paste rule)

---

## Source: AGENTS.md (297 lines)

| #   | Section                                 | Lines   | Category | Destination                      | Notes                                      |
| --- | --------------------------------------- | ------- | -------- | -------------------------------- | ------------------------------------------ |
| A1  | Header / framework note                 | 1-22    | KEEP     | `AGENTS.md`                      | Stays as-is                                |
| A2  | Operator Communication Standard         | 24-73   | KEEP     | `AGENTS.md`                      | Universal, every worker needs it           |
| A3  | Automated PR Review Completion Sequence | 77-132  | OVERLAY  | `.miru/overlays/workflow-git.md` | Dedupe — also in CLAUDE.md item 25         |
| A4  | gh CLI Auth Bootstrap                   | 136-172 | OVERLAY  | `.miru/overlays/workflow-git.md` | Tied to PR creation                        |
| A5  | Return-to-main                          | 175-192 | CRITICAL | `CLAUDE.md`                      | Universal hard rule, blast-radius adjacent |
| A6  | Try Harder Discipline                   | 196-242 | KEEP     | `AGENTS.md`                      | Universal communication discipline         |
| A7  | WIP Commit Checkpoints                  | 246-297 | OVERLAY  | `.miru/overlays/workflow-git.md` | Tied to git workflow                       |

**AGENTS.md → CRITICAL:** A5 promoted to core (return-to-main is universal)
**AGENTS.md → OVERLAY:** A3, A4, A7 (~110 lines moved to workflow-git overlay)
**AGENTS.md → KEEP:** A1, A2, A6 (~140 lines — universal communication rules)

---

## Destination File Sizes (estimated)

### CRITICAL — `CLAUDE.md` (slim core, always loaded)

| Section                                 | Source            | Est. Lines    |
| --------------------------------------- | ----------------- | ------------- |
| Header (Project Miru — Worker Core)     | new               | 3             |
| Repo Boundary (tightened)               | CLAUDE.md 19-26   | 5             |
| Kill Switch                             | CLAUDE.md 28-43   | 8             |
| Worktree Cleanliness Gate               | CLAUDE.md 45-58   | 6             |
| No Overlap Rule                         | CLAUDE.md 60-64   | 4             |
| Append-only data files                  | CLAUDE.md 264-280 | 12            |
| Completion Contract terminal states     | CLAUDE.md 500-511 | 6             |
| Return-to-main                          | AGENTS.md 175-192 | 6             |
| Fail-closed directive                   | new               | 3             |
| Discovery Index (overlays + references) | new               | 18            |
| Worker-specific (CC role section)       | CLAUDE.md 481-498 | 6             |
| **Total**                               |                   | **~77 lines** |

Within budget (target 65-80).

### OVERLAY — `.miru/overlays/workflow-git.md`

| Source                                                               | Lines          |
| -------------------------------------------------------------------- | -------------- |
| PR Merge Policy (CLAUDE.md 152-262)                                  | 110            |
| PR review completion sequence (CLAUDE.md 513-529 / AGENTS.md 77-132) | 56 (deduped)   |
| Hygiene gate (CLAUDE.md 551-560)                                     | 10             |
| gh CLI Auth Bootstrap (AGENTS.md 136-172)                            | 36             |
| WIP Commit Checkpoints (AGENTS.md 246-297)                           | 50             |
| **Total**                                                            | **~262 lines** |

### OVERLAY — `.miru/overlays/workflow-completion.md`

| Source                                           | Lines          |
| ------------------------------------------------ | -------------- |
| Completion-marker convention (CLAUDE.md 647-726) | 80             |
| Heartbeat emission (CLAUDE.md 562-596)           | 35             |
| Stall classification (CLAUDE.md 531-549)         | 19             |
| **Total**                                        | **~134 lines** |

### OVERLAY — `.miru/overlays/workflow-dispatch.md`

| Source                                               | Lines          |
| ---------------------------------------------------- | -------------- |
| CH Decision Authority (CLAUDE.md 426-477)            | 52             |
| Gateway Tool Profile Enforcement (CLAUDE.md 294-316) | 23             |
| Ingress Classifier (CLAUDE.md 318-350)               | 33             |
| Orchestrator-side modules (CLAUDE.md 598-605)        | 8              |
| Linear projectId requirement (CLAUDE.md 68)          | 4              |
| **Total**                                            | **~120 lines** |

### OVERLAY — `.miru/overlays/domain-ui.md`

| Source                                        | Lines         |
| --------------------------------------------- | ------------- |
| Craft Guides trigger list (CLAUDE.md 607-645) | 39            |
| **Total**                                     | **~39 lines** |

### OVERLAY — `.miru/overlays/domain-ops.md`

| Source                                     | Lines         |
| ------------------------------------------ | ------------- |
| Notion Read/Write Rules (CLAUDE.md 94-105) | 12            |
| MCP Tool Usage Rules (CLAUDE.md 282-292)   | 11            |
| Scheduled Tasks (CLAUDE.md 359-377)        | 19            |
| **Total**                                  | **~42 lines** |

### OVERLAY — `.miru/overlays/adopted-lessons.md`

| Source                                                   | Lines         |
| -------------------------------------------------------- | ------------- |
| Header (CLAUDE.md 107-109)                               | 3             |
| Test JS as it lives in workflow JSON (CLAUDE.md 111-124) | 14            |
| Lock design in Linear ticket (CLAUDE.md 126-150)         | 25            |
| **Total**                                                | **~42 lines** |

### REFERENCE — `.miru/reference/ports-and-services.md`

| Source                        | Lines        |
| ----------------------------- | ------------ |
| Ports table (CLAUDE.md 11-17) | 7            |
| **Total**                     | **~7 lines** |

### REFERENCE — `.miru/reference/linear-projects.md`

| Source                                  | Lines         |
| --------------------------------------- | ------------- |
| Linear project tables (CLAUDE.md 70-92) | 23            |
| **Total**                               | **~23 lines** |

### REFERENCE — `.miru/reference/file-placement.md`

| Source                                                              | Lines         |
| ------------------------------------------------------------------- | ------------- |
| Service boundaries + Where new files go + NEVER (CLAUDE.md 386-422) | 37            |
| **Total**                                                           | **~37 lines** |

### REFERENCE — `.miru/reference/database-rules.md`

| Source                             | Lines        |
| ---------------------------------- | ------------ |
| Database Rules (CLAUDE.md 352-357) | 6            |
| **Total**                          | **~6 lines** |

### REFERENCE — `.miru/reference/restart-procedures.md`

| Source                            | Lines        |
| --------------------------------- | ------------ |
| Restart Rules (CLAUDE.md 379-384) | 6            |
| **Total**                         | **~6 lines** |

### AGENTS.md (kept content)

| Source                                    | Lines          |
| ----------------------------------------- | -------------- |
| Header / framework note                   | 22             |
| Operator Communication Standard           | 50             |
| Try Harder Discipline                     | 47             |
| Copy-paste content (moved from CLAUDE.md) | 7              |
| **Total**                                 | **~126 lines** |

---

## Budget Summary

| Loaded                     | Lines    | Tokens (est.) |
| -------------------------- | -------- | ------------- |
| CLAUDE.md (always)         | 77       | 950           |
| AGENTS.md (always)         | 126      | 1500          |
| **Always-loaded baseline** | **203**  | **~2450**     |
| 1-2 task overlays (avg)    | 150      | 1800          |
| Reference lookups (0-1)    | 25       | 300           |
| **Total per task**         | **~378** | **~4550**     |
| **Current total (always)** | **1023** | **~12200**    |

**Reduction:** ~63% on always-loaded; ~75% versus current full-load.

---

## Discovery Index (for slim CLAUDE.md core)

This goes into the slim CLAUDE.md as the routing table. Format per GMI: name + trigger + 1-line description.

```
## When to load which overlay

OVERLAYS — load when starting work that matches the trigger:
- .miru/overlays/workflow-git.md — LOAD IF committing, opening, or merging a PR.
  Contains: merge policy decision tree, hygiene gate, PR review sequence, gh
  auth, WIP commits, post-merge cleanup.
- .miru/overlays/workflow-completion.md — LOAD IF reaching a terminal task state.
  Contains: completion marker schema, heartbeat emission, stall classification.
- .miru/overlays/workflow-dispatch.md — LOAD IF orchestrating dispatch, gateway
  profiles, or W2 routing. Contains: CH decision authority, gateway profile
  enforcement, ingress classifier, orchestrator modules, Linear projectId rule.
- .miru/overlays/domain-ui.md — LOAD IF touching frontend (pm/, miru_ai/static/,
  templates). Contains: craft guide trigger list, when to read docs/ui_ux/ and
  docs/pm/ files.
- .miru/overlays/domain-ops.md — LOAD IF touching scheduled tasks, services,
  Notion writes, or MCP config. Contains: scheduled-task focus rule, MCP usage
  rules, Notion read/write rules.
- .miru/overlays/adopted-lessons.md — LOAD IF doing a non-trivial code change
  (more than typo/lint). Contains: workflow JSON test rule, design-in-Linear-
  ticket rule.

REFERENCE — fetch when you need the specific fact:
- .miru/reference/ports-and-services.md — FETCH IF you need a port number.
- .miru/reference/linear-projects.md — FETCH IF creating a Linear ticket.
  Contains the projectId table.
- .miru/reference/file-placement.md — FETCH IF creating a new file and unsure
  where it goes.
- .miru/reference/database-rules.md — FETCH IF reading or proposing changes to
  card_catalog.db.
- .miru/reference/restart-procedures.md — FETCH IF restarting a service.
```

---

## Migration Validation Plan

### Coverage check

- Every line range in this map must appear in exactly one destination file.
- Script: `tools/validate_instruction_migration.py` — parses this map and the
  destination files, reports any missing/duplicate/orphaned content.

### No-duplication check

- Special attention to PR review completion sequence (in both CLAUDE.md and
  AGENTS.md today). Single canonical version goes in workflow-git.md.

### Behavioral smoke test

- After migration, dispatch one low-risk ticket (lint fix or doc typo).
- Verify worker loads: CLAUDE.md core + AGENTS.md baseline + workflow-git.md.
- Verify worker does NOT load: workflow-completion.md (terminal-state-only),
  domain-ui.md, domain-ops.md.
- Verify worker emits correct completion marker (means workflow-completion was
  fetched mid-task via discovery index).

### Stale-context guard

- Add to slim CLAUDE.md core:
  ```
  Instruction Architecture Version: MIRU-INSTRUCTIONS-v2
  If your boot context does not show this version, STOP and reload.
  ```
- Update n8n dispatcher prompt template to require this version.

---

## Risks & Open Questions

1. **Overlay injection by dispatcher** — n8n needs to map task signals to
   overlay names. The ingress classifier already extracts `task_mode` and
   keywords. Need to add an overlay-injection node or extend `w2008a` to
   include overlay file paths in the dispatch payload.

2. **Mid-session overlay updates** — workers won't see overlay changes until
   restart. Mitigation: version bump in core forces stale-context detection.

3. **Worker drops the discovery index** — context compaction could summarize
   the index. Mitigation: keep the index at the bottom of core (boundary
   position, attention-strong) and short enough to survive summarization.

4. **AGENTS.md and CLAUDE.md duplication of return-to-main** — currently both
   have it. Single canonical source: CLAUDE.md core. AGENTS.md keeps a one-line
   pointer.

5. **Worker role files (CURSOR.md, CODEX.md, GEMINI.md)** — out of scope for
   this migration. They stay as-is. CC's role goes in CLAUDE.md (since CLAUDE.md
   IS the CC core for native boot).

---

## Next Steps (after this map is approved)

1. Operator review of this map — confirm category assignments.
2. CC drafts slim CLAUDE.md from the map.
3. CC creates overlay files via exact-line extraction (no rewrites).
4. CC creates reference files.
5. CC writes validation script.
6. Run validation script — confirm coverage + no duplicates.
7. GMI semantic review of the result.
8. Operator review of slim CLAUDE.md.
9. Smoke test on a real ticket.
10. Merge as one PR with version bump.
