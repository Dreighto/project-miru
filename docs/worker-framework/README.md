# Dreighto Worker Framework

Universal operating baseline for all autonomous workers across all projects.

**One framework. Any project. Any worker. Flip a switch.**

---

## What this is

This framework defines how every worker operates — regardless of which project they're
on, which tool they are, or who dispatched them. Universal rules live here. Project-
specific rules layer on top in the project repo. Workers read one document; it contains
everything they need.

---

## Files

| File        | Who reads it                | What it covers                                                                          |
| ----------- | --------------------------- | --------------------------------------------------------------------------------------- |
| `AGENTS.md` | All workers, every dispatch | Communication, PR review loop, merge policy, try-harder discipline, completion contract |
| `CLAUDE.md` | Claude Code (CC)            | Role, pre-flight gates, file ownership, completion markers, hygiene                     |
| `GEMINI.md` | Gemini CLI                  | Role, configuration, UI quality standards, tap targets, design tokens                   |

---

## Starting a new project

**5 steps. Under 10 minutes.**

### 1. Create the project repo

```bash
gh repo create Dreighto/<project-name> --private
git clone https://github.com/Dreighto/<project-name>
cd <project-name>
```

### 2. Copy the framework files

```bash
# From the worker-framework repo (or docs/worker-framework/ in any Miru worktree)
cp AGENTS.md CLAUDE.md GEMINI.md <project-root>/
```

### 3. Add the project header to each file

At the top of each copied file, add:

```markdown
# [Filename] — [Project Name] Overlay

# Framework source: Dreighto/worker-framework | Last synced: YYYY-MM-DD

# Universal baseline is embedded below. Project-specific rules follow the divider.
```

### 4. Add project-specific rules

Append a project overlay section at the bottom of each file:

```markdown
---

## PROJECT OVERLAY — [Project Name]

### Ports and Services

- XXXX = Service Name — ACTIVE

### File Boundaries

- `service-a/` — all code for Service A
- `service-b/` — all code for Service B

### Restart Rules

- Service A: `powershell -ExecutionPolicy Bypass -File windows\restart_a.ps1`

### [Any other project-specific rules]
```

### 5. Set up project tooling

- Copy kill switch and worktree cleanliness scripts to `tools/`
- Set up `pre-commit` with the project's hooks
- Configure Linear project IDs
- Set up `.env` with project-specific keys

Workers boot up, read the file, and operate immediately.

---

## Keeping the framework in sync

When a universal rule changes in this repo:

1. Update the relevant file here (PR + self-merge for rule edits).
2. Propagate to active projects — update `Last synced:` date and copy the changed
   section into each project's corresponding file.
3. If a project has a project-specific variant of the rule that conflicts: the
   project-specific rule wins for that project. Document the divergence.

**Who syncs:** The worker (CC) that changes the framework is responsible for propagating
the change to all active projects in the same PR or in a follow-up ticket. The operator
should not have to manually sync docs across projects.

---

## What stays universal vs what stays project-specific

| Universal (lives here)           | Project-specific (lives in project repo) |
| -------------------------------- | ---------------------------------------- |
| Operator communication format    | Port numbers and service names           |
| PR review and merge rules        | Linear project IDs                       |
| Try-harder discipline            | Service file boundaries                  |
| Completion contract format       | Database rules and paths                 |
| Role definitions per worker type | Project-specific restart scripts         |
| Tap target minimums (44px)       | Adopted lessons with project ticket refs |
| Merge policy tiers               | Project design tokens                    |
| Append-only log discipline       | Kill switch and halt file paths          |
| Pre-flight gate pattern          | Auth tokens and API key locations        |

If you're unsure which category a rule belongs to: if it would apply to a _completely
different project with a completely different codebase_, it's universal. If it's
specific to this project's architecture, it's project-specific.

---

## Worker roster (current)

| Worker           | Tool         | Primary lane                               |
| ---------------- | ------------ | ------------------------------------------ |
| Claude Code (CC) | `claude` CLI | Python backend, tests, verification, canon |
| Gemini CLI       | `gemini` CLI | Frontend, UI, HTML/CSS/JS                  |
| Cursor           | Cursor IDE   | TBD per project                            |
| Codex            | OpenAI Codex | TBD per project                            |

Each worker has a rule file in this repo (CLAUDE.md, GEMINI.md, etc.) that defines
their role, permissions, and operating constraints universally. Projects may add
worker-specific overlays for project-specific constraints.
