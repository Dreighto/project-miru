# Linear Triage Framework — Portable Template

A reusable triage rule set for any Linear project using the Miru worker stack.
Requires Business or Enterprise plan. Takes ~20 minutes to configure per project.

---

## What this gives you

- Issues auto-labeled with the right worker (`claude-code`, `cursor`, etc.) on creation
- Bugs auto-promoted to High + Todo so they don't sit in Backlog
- Triage Intelligence catching duplicates before they pile up
- A consistent intake discipline that carries forward to every project

---

## Prerequisites

- Linear Business or Enterprise plan (Triage Rules + Triage Intelligence require it)
- Team already created in Linear with at least one project
- Workspace labels created: `Bug`, `Feature`, `Improvement`, `chore`, `design`,
  `research`, `blocked`, `needs-review`, plus any worker labels in use
  (`claude-code`, `cursor`, `codex`, `gemini`)

---

## Step 1 — Enable Triage for the team

```
Linear → [Team Name] → Settings → Triage → Enable
                                 → Triage Responsibility → [TRIAGE_OWNER]
```

Optional but recommended: set all new issues to start in Triage so nothing
bypasses the intake rules.

```
Team Settings → Workflow → Default status for new issues → Triage
```

---

## Step 2 — The 5-rule template

Configure rules in this exact order (top = highest priority, fires first).
Replace all `[PLACEHOLDERS]` before saving.

---

### Rule 1 — Bug fast-track

| Field     | Value                                                                                     |
| --------- | ----------------------------------------------------------------------------------------- |
| Condition | Label = `Bug` **OR** title contains: `broken` / `error` / `crash` / `fix` / `not working` |
| Priority  | High                                                                                      |
| Label     | `[WORKER_FOR_BUGS]` — usually `claude-code`                                               |
| Status    | Todo (skip Backlog — bugs need action)                                                    |

---

### Rule 2 — Frontend / UI surface

| Field     | Value                                                          |
| --------- | -------------------------------------------------------------- |
| Condition | Label = `design` **OR** title contains: `[SURFACE_A_KEYWORDS]` |
| Priority  | Medium                                                         |
| Label     | `[WORKER_A]` — the worker that owns this surface               |
| Status    | Backlog                                                        |

`[SURFACE_A_KEYWORDS]` examples: `Component —` / `svelte` / `UI` / `layout` / `modal` / `card` / `page` / `style`

---

### Rule 3 — Backend / integration surface

| Field     | Value                                                       |
| --------- | ----------------------------------------------------------- |
| Condition | Title contains: `[SURFACE_B_KEYWORDS]` OR `[SERVICE_NAMES]` |
| Priority  | Medium                                                      |
| Label     | `[WORKER_B]` — the worker that owns this surface            |
| Status    | Backlog                                                     |

`[SURFACE_B_KEYWORDS]` examples: `API` / `route` / `endpoint` / `proxy` / `server` / `store`

`[SERVICE_NAMES]` — project-specific external services (e.g. Sonarr, Stripe, Postgres)

---

### Rule 4 — Chores

| Field     | Value                                                                                      |
| --------- | ------------------------------------------------------------------------------------------ |
| Condition | Label = `chore` **OR** title contains: `cleanup` / `refactor` / `rename` / `type` / `lint` |
| Priority  | Low                                                                                        |
| Label     | `[WORKER_FOR_CHORES]` — usually `claude-code`                                              |
| Status    | Backlog                                                                                    |

---

### Rule 5 — Catch-all (always last)

| Field     | Value                                                |
| --------- | ---------------------------------------------------- |
| Condition | (no condition — matches everything not caught above) |
| Priority  | Medium                                               |
| Label     | `needs-review`                                       |
| Assign    | `[TRIAGE_OWNER]`                                     |
| Status    | Backlog                                              |

---

## Step 3 — Triage Intelligence guidance

Paste this into: `Team Settings → Triage → Triage Intelligence → Additional Guidance`

Customize the bracketed sections before saving.

```
[SURFACE_A_DESCRIPTION] work should go to [WORKER_A].
[SURFACE_B_DESCRIPTION] work should go to [WORKER_B].
Bug reports and broken functionality should go to [WORKER_FOR_BUGS] at High priority.
Chores and refactors should go to [WORKER_FOR_CHORES] at Low priority.
Flag duplicates aggressively — [DUPLICATE_CONTEXT].
```

Example (filled in for a SvelteKit project):

```
UI, Svelte components, and layout work should go to cursor.
API routes, server-side logic, and service integrations should go to claude-code.
Bug reports and broken functionality should go to claude-code at High priority.
Chores and refactors should go to claude-code at Low priority.
Flag duplicates aggressively — this project has many similar component tickets.
```

---

## Step 4 — New project setup checklist

Copy this block into the project's `docs/linear-workflow.md` when standing up a new project:

```
## Triage Configuration

- [ ] Triage enabled for [TEAM_NAME] team
- [ ] Triage Responsibility set to [TRIAGE_OWNER]
- [ ] Default new issue status set to Triage
- [ ] Rule 1 (Bug fast-track) created
- [ ] Rule 2 ([SURFACE_A] surface) created
- [ ] Rule 3 ([SURFACE_B] surface) created
- [ ] Rule 4 (Chores) created
- [ ] Rule 5 (Catch-all) created
- [ ] Triage Intelligence guidance pasted in
- [ ] `needs-review` label created (grey #6B7280) if not already in workspace
```

---

## Fill-in reference — variables per project

| Variable                  | What it means                            | Example (NASDOOM)                                       |
| ------------------------- | ---------------------------------------- | ------------------------------------------------------- |
| `[TRIAGE_OWNER]`          | Who monitors the triage inbox            | Dreighto                                                |
| `[WORKER_A]`              | Worker for frontend/UI surface           | cursor                                                  |
| `[WORKER_B]`              | Worker for backend/API surface           | claude-code                                             |
| `[WORKER_FOR_BUGS]`       | Worker that fixes bugs                   | claude-code                                             |
| `[WORKER_FOR_CHORES]`     | Worker that handles chores               | claude-code                                             |
| `[SURFACE_A_KEYWORDS]`    | Title keywords that signal frontend work | Component —, svelte, UI, layout, modal                  |
| `[SURFACE_B_KEYWORDS]`    | Title keywords that signal backend work  | API, route, endpoint, proxy, server                     |
| `[SERVICE_NAMES]`         | Project-specific external services       | Sonarr, Radarr, SABnzbd, Plex, Tautulli                 |
| `[SURFACE_A_DESCRIPTION]` | Plain English for AI guidance            | UI, Svelte components, and layout work                  |
| `[SURFACE_B_DESCRIPTION]` | Plain English for AI guidance            | API routes, server-side logic, and service integrations |
| `[DUPLICATE_CONTEXT]`     | Hint for AI on what duplicates look like | this project has many similar component tickets         |

---

## Label taxonomy (portable — no Miru automation internals)

These labels travel with every project. Do not carry over Miru-specific labels
(`pending-approval`, `re-routing`, `intake-draft`, `test-w2`, `n8n-error-queue`, etc.)
— those are automation plumbing, not issue metadata.

| Label          | Color            | Purpose                                      |
| -------------- | ---------------- | -------------------------------------------- |
| `Bug`          | Red `#EB5757`    | Something is broken                          |
| `Feature`      | Purple `#BB87FC` | New capability                               |
| `Improvement`  | Blue `#4EA7FC`   | Enhancement to existing behavior             |
| `chore`        | Grey `#6B7280`   | Cleanup, refactor, maintenance               |
| `design`       | Pink `#C54668`   | Visual / UX work                             |
| `research`     | Blue `#6E90C9`   | Investigation, spike, proof of concept       |
| `blocked`      | Orange `#F2994A` | Waiting on something external                |
| `needs-review` | Grey `#6B7280`   | Catch-all: triage owner must manually review |
| `claude-code`  | Orange `#D97757` | Assigned to Claude Code worker               |
| `cursor`       | Blue `#4FA8D8`   | Assigned to Cursor worker                    |
| `codex`        | Green `#10A37F`  | Assigned to Codex worker                     |
| `gemini`       | Blue `#1A73E8`   | Assigned to Gemini worker                    |

---

## Per-project instances

| Project           | Instance doc                                                        |
| ----------------- | ------------------------------------------------------------------- |
| NASDOOM Dashboard | `D:\nasdoom\docs\linear-workflow.md` → Triage Configuration section |
