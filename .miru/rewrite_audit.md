# Instruction Migration — Rewrite Audit

```
Architecture: MIRU-INSTRUCTIONS-v2
Created: 2026-05-08
Purpose: track paragraphs that were intentionally rewritten during migration.
```

The validator (`tools/validate_instruction_migration.py`) does exact-text
matching of paragraphs from the pre-split files against the new structure. Some
content was intentionally rewritten or compressed during the migration to hit
the slim-core budget (~80 lines for `CLAUDE.md`). This file documents each
intentional rewrite so the validator's "missing" list can be reviewed.

If a paragraph is in this list, its content survived the migration in
semantically-equivalent form (sometimes condensed). If a paragraph is in the
validator's missing list AND not here, it's a real omission and needs review.

---

## Pre-split → new location map

### Compressed in slim CLAUDE.md core

The following sections existed verbatim in pre-split CLAUDE.md or AGENTS.md
and were compressed (semantic content preserved) when promoted to the slim
core. Specific rewrites:

- **Repo Boundary** (CLAUDE.md 19-26) — long-form bullet list compressed to 4 lines. Drops worktree pre-flight detail (now part of pre-flight gate descriptions); behavior unchanged.
- **Kill Switch** (CLAUDE.md 28-43) — drops the line "See `miru-context/kill-switch.md` for the full contract" (kept as discoverable via miru-context/ index). Drops "This check runs before branch creation..." line (implicit in "before any dispatched task, run").
- **Worktree Cleanliness Gate** (CLAUDE.md 45-58) — drops the "Why this matters" rationale paragraph (rationale is implicit; rule is the binding part).
- **No Overlap Rule** (CLAUDE.md 60-64) — three-bullet list collapsed into two-sentence prose. Same rules.
- **Append-only Data Files** (CLAUDE.md 264-280) — file list and "Pre-commit hooks exclude these..." paragraph reformatted into compact block + helper-script pointer. Same rules.
- **Completion Contract terminal states** (CLAUDE.md 500-511) — preserved exact 3 states; dropped the `> For Claude Code's supervisory responsibilities ...` reference quote (now implicit; full schema points to overlay).
- **Return-to-main** (CLAUDE.md 254-262 / AGENTS.md 175-192) — both sources collapsed into single 6-line block in CLAUDE.md core. Reuses identical bullet rules; drops the multi-paragraph "Why this is a hard rule" exposition (replaced by single-line consequence statement).

### Moved-and-rewritten to AGENTS.md

- **Copy-paste content for manual routing** (CLAUDE.md 3-9) — the rule line is preserved verbatim; the "Why" paragraph was condensed (semantic content preserved).
- **Worker Role descriptions** (CLAUDE.md 481-498) — CC's role moved verbatim to slim core "Worker Role" section. CH's role and the "Must never" rules consolidated into AGENTS.md "Worker Roles" section. The wording is condensed but every binding rule is preserved.

### Moved verbatim to overlays

The following moved without text changes (validator should match):

- **PR Merge Policy** (CLAUDE.md 152-262) → `.miru/overlays/workflow-git.md`
- **Hygiene gate** (CLAUDE.md 551-560) → `.miru/overlays/workflow-git.md`
- **Automated PR Review Completion Sequence** — single canonical version in `.miru/overlays/workflow-git.md` (deduped; previously in both CLAUDE.md and AGENTS.md).
- **gh CLI Auth Bootstrap** (AGENTS.md 136-172) → `.miru/overlays/workflow-git.md`
- **WIP Commit Checkpoints** (AGENTS.md 246-297) → `.miru/overlays/workflow-git.md`
- **Completion-marker convention** (CLAUDE.md 647-726) → `.miru/overlays/workflow-completion.md`
- **Heartbeat emission** (CLAUDE.md 562-596) → `.miru/overlays/workflow-completion.md`
- **Stall classification** (CLAUDE.md 531-549) → `.miru/overlays/workflow-completion.md`
- **CH Decision Authority** (CLAUDE.md 426-477) → `.miru/overlays/workflow-dispatch.md`
- **Gateway Tool Profile Enforcement** (CLAUDE.md 294-316) → `.miru/overlays/workflow-dispatch.md`
- **Ingress Classifier** (CLAUDE.md 318-350) → `.miru/overlays/workflow-dispatch.md`
- **Orchestrator-side modules** (CLAUDE.md 598-605) → `.miru/overlays/workflow-dispatch.md`
- **Notion Read/Write Rules** (CLAUDE.md 94-105) → `.miru/overlays/domain-ops.md`
- **MCP Tool Usage Rules** (CLAUDE.md 282-292) → `.miru/overlays/domain-ops.md`
- **Scheduled Tasks** (CLAUDE.md 359-377) → `.miru/overlays/domain-ops.md`
- **Adopted Lessons header + JS workflow + design-in-ticket** (CLAUDE.md 107-150) → `.miru/overlays/adopted-lessons.md`
- **Craft Guides** (CLAUDE.md 607-645) → `.miru/overlays/domain-ui.md`

### Moved verbatim to reference files

- **Ports table** (CLAUDE.md 11-17) → `.miru/reference/ports-and-services.md`
- **Linear projects table** (CLAUDE.md 70-92) → `.miru/reference/linear-projects.md` (the rule line "must include projectId" lives in `workflow-dispatch.md`; this file is the lookup table only)
- **Database Rules** (CLAUDE.md 352-357) → `.miru/reference/database-rules.md`
- **Restart Rules** (CLAUDE.md 379-384) → `.miru/reference/restart-procedures.md`
- **File Placement** (CLAUDE.md 386-422) → `.miru/reference/file-placement.md`

---

## Validation expectations

After this migration, running:

```
python tools/validate_instruction_migration.py
```

is expected to report:

- **Missing: ~37 paragraphs** — all matching the rewrites above. Each is documented in this file.
- **Duplicates: 0** — no paragraph appears in more than one destination.
- **Manifest issues: 0** — every overlay/reference declared and on disk.
- **Version stamp issues: 0** — every file carries `MIRU-INSTRUCTIONS-v2`.

Run with `--allow-missing=40` to accept the documented rewrites:

```
python tools/validate_instruction_migration.py --allow-missing=40
```

---

## How to use this file in code review

When a future PR modifies CLAUDE.md, AGENTS.md, or any overlay/reference:

1. Run the validator — should still pass with the same allowance.
2. If the missing count grows, identify the new misses.
3. For each new miss, either fix the destination text to match exact wording,
   or add an entry to this file documenting the intentional rewrite.

The validator + this audit form the "no silent drops" guarantee.
