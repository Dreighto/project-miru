# CLAUDE.md Restructure — Section Inventory & Proposed Routing

Generated 2026-05-08 during instruction architecture research.

## Current State

- CLAUDE.md: 726 lines, 30+ sections
- AGENTS.md: 297 lines, 12 sections
- 5 existing skills in .claude/skills/
- 26 miru-context/ reference files
- Craft guides: docs/ui_ux/ (9 files), docs/pm/ (9 files)

## Category Classification

### CORE (must survive compaction — always loaded, ~60-80 lines)

These are existential guardrails. If a worker forgets these, Bad Things happen.

| Section                                    | Lines | Why it's CORE                             |
| ------------------------------------------ | ----- | ----------------------------------------- |
| Repo Boundary                              | ~8    | Workers outside repo = catastrophic       |
| Kill Switch                                | ~12   | Pre-flight gate #1, cannot skip           |
| Worktree Cleanliness Gate                  | ~12   | Pre-flight gate #2, cannot skip           |
| No Overlap Rule                            | ~4    | Prevents concurrent edit conflicts        |
| Append-only data files                     | ~12   | 9 files, violating = data corruption      |
| Completion Contract (terminal states only) | ~6    | CONFIRMED_WORKING / INCONCLUSIVE / FAILED |
| Return-to-main (from AGENTS.md)            | ~4    | Session end state, prevents branch drift  |
| Copy-paste in code blocks                  | ~3    | Operator workflow, all workers            |

**Estimated: ~61 lines** — right in the sweet spot.

### REFERENCE (facts, not instructions — load on demand)

These are lookup tables and factual data. Workers query them when needed.

| Section                                        | Lines | Proposed location                       |
| ---------------------------------------------- | ----- | --------------------------------------- |
| Ports — Permanent Reference                    | ~6    | miru-context/ports.md (already implied) |
| Linear project IDs (full table)                | ~24   | miru-context/linear-projects.md         |
| Service boundaries (which dir = which service) | ~10   | miru-context/service-boundaries.md      |
| File placement maps                            | ~20   | miru-context/file-placement.md          |
| Database Rules (sqlite paths, read-only)       | ~6    | miru-context/database-rules.md          |
| Restart commands                               | ~6    | miru-context/restart-procedures.md      |

**Estimated: ~72 lines moved out of CLAUDE.md**

### OVERLAY: git-and-pr (loaded for any task involving commits/PRs)

| Section                              | Lines | Notes                                           |
| ------------------------------------ | ----- | ----------------------------------------------- |
| PR Merge Policy (full decision tree) | ~110  | Including post-merge cleanup, branch deletion   |
| Hygiene gate                         | ~10   | pre-commit run requirement                      |
| Automated PR review sequence         | ~18   | Wait for reviewers, fix findings, confirm green |
| CC implementation quality gate       | ~35   | New rule (operator's unstaged addition)         |

**Estimated: ~173 lines → skill file**

### OVERLAY: completion-and-reporting (loaded when emitting completion markers)

| Section                      | Lines | Notes                                    |
| ---------------------------- | ----- | ---------------------------------------- |
| Completion-marker convention | ~75   | Full schema, when to write, how to write |
| Heartbeat emission           | ~35   | Schema, cadence, stall detection         |
| Stall classification         | ~20   | 4 classes, PROVISIONAL                   |

**Estimated: ~130 lines → skill file**

### OVERLAY: orchestration (loaded for dispatch/coordination work)

| Section                                       | Lines | Notes                              |
| --------------------------------------------- | ----- | ---------------------------------- |
| Autonomous Operations — CH Decision Authority | ~50   | What CH decides alone vs escalates |
| Gateway Tool Profile Enforcement              | ~25   | Phase 3 profiles table             |
| Ingress Classifier                            | ~35   | Phase 4 auto-assignment            |
| Orchestrator-side modules                     | ~10   | stall_detector, recovery_router    |

**Estimated: ~120 lines → skill file**

### OVERLAY: adopted-lessons (loaded for relevant task types)

| Section                              | Lines | Notes                              |
| ------------------------------------ | ----- | ---------------------------------- |
| Test JS as it lives in workflow JSON | ~15   | Triggered by workflow JSON changes |
| Lock design in Linear ticket         | ~25   | Triggered by non-trivial dispatch  |

**Estimated: ~40 lines → skill file or folded into relevant overlays**

### OVERLAY: platform-rules (loaded for specific platform work)

| Section                             | Lines | Notes                 |
| ----------------------------------- | ----- | --------------------- |
| MCP Tool Usage Rules                | ~12   | MCP gateway work      |
| Scheduled Tasks — no focus stealing | ~20   | Windows task creation |
| Notion Read/Write Rules             | ~12   | Notion operations     |

**Estimated: ~44 lines → skill file**

### WORKER-SPECIFIC (stays in worker files, not CLAUDE.md)

| Section                        | Lines | Current location                     |
| ------------------------------ | ----- | ------------------------------------ |
| Worker-specific: CH + CC roles | ~18   | CLAUDE.md → split to worker files    |
| File ownership (CC vs CH)      | ~6    | CLAUDE.md → worker files             |
| Must never (CC constraints)    | ~6    | CLAUDE.md → CLAUDE.md worker section |

### ALREADY MODULAR (no change needed)

| Section                       | Lines | Notes                           |
| ----------------------------- | ----- | ------------------------------- |
| Craft Guides — load on demand | ~40   | Already uses the right pattern! |

## Token Budget Estimate

| Layer                  | Lines | Est. Tokens | When loaded |
| ---------------------- | ----- | ----------- | ----------- |
| Core                   | ~65   | ~800        | Always      |
| AGENTS.md baseline     | ~100  | ~1200       | Always      |
| Task overlay (avg 1-2) | ~150  | ~1800       | Per task    |
| Reference lookup (0-1) | ~25   | ~300        | On demand   |
| **Total per session**  | ~340  | ~4100       |             |
| **Current total**      | ~1023 | ~12000      | Always      |

**~65% token reduction** from current state.

## Proposed Directory Structure

```
CLAUDE.md                           # SLIM CORE (~65 lines)
AGENTS.md                           # Universal worker baseline (unchanged)
CURSOR.md                           # Cursor-specific (unchanged)
CODEX.md                            # Codex-specific (unchanged)
GEMINI.md                           # Gemini-specific (unchanged)

.claude/skills/
├── git-and-pr/SKILL.md             # PR policy, merge rules, hygiene, review sequence
├── completion-reporting/SKILL.md   # Completion markers, heartbeat, stall classification
├── orchestration/SKILL.md          # CH decision authority, gateway profiles, classifier
├── platform-rules/SKILL.md         # MCP, scheduled tasks, Notion, restarts
├── adopted-lessons/SKILL.md        # Battle-tested patterns (or fold into relevant skills)
├── benchmark-operator/SKILL.md     # (existing)
├── n8n-workflow/SKILL.md           # (existing)
├── operator-handoff/SKILL.md       # (existing)
├── refactor-triage/SKILL.md        # (existing)
└── sustainability-evals/SKILL.md   # (existing)

miru-context/
├── ports.md                        # Port assignments (reference)
├── linear-projects.md              # Linear project IDs (reference)
├── service-boundaries.md           # Which dir = which service (reference)
├── file-placement.md               # Where new files go (reference)
├── database-rules.md               # DB access rules (reference)
├── restart-procedures.md           # Service restart commands (reference)
├── ... (existing 26 files)
```

## Migration Risk Assessment

| Risk                                                 | Severity | Mitigation                                                 |
| ---------------------------------------------------- | -------- | ---------------------------------------------------------- |
| Governance gap: critical rule in overlay not loaded  | HIGH     | Keep ALL safety-critical rules in CORE, never in overlays  |
| Skill selection overhead: worker picks wrong overlay | MEDIUM   | Clear skill descriptions + task-type triggers              |
| Cross-cutting rules split awkwardly                  | MEDIUM   | Completion contract core stays in CORE, details in overlay |
| Workers bypass overlay loading                       | LOW      | Pre-flight can check task type and warn                    |
| Migration breaks existing muscle memory              | LOW      | Gradual rollout, one overlay at a time                     |
