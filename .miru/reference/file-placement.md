# Reference — File Placement

```text
Reference: file-placement
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: creating a new file and unsure where it goes.
Last reviewed: 2026-05-10
```

Every file created must go in the correct location. These rules are non-negotiable.

---

## Cross-repo placement (added 2026-05-10)

Project Miru is no longer single-repo. Workers may land in any worktree from `WORKTREE_POOLS`. Before creating a file, check which repo's worktree you're in:

- `D:\dev\miru-w*` or `D:\dev\miru` → project-miru (rules below apply)
- `D:\dev\LogueOS-Console-w*` or `D:\dev\LogueOS-Console` → SvelteKit dashboard repo. Files belong in `src/lib/`, `src/routes/`, `src/lib/components/`, `src/lib/server/`, `static/`, etc. — standard SvelteKit conventions. Do NOT place dispatch/gateway/n8n code here.
- Any other path → STOP. You're outside the dispatch loop's known repo set; ask the operator.

If you're editing in `D:\dev\miru*` and the file you're about to create is dashboard-shaped (Svelte component, +page route, /api endpoint), STOP — wrong repo. Dispatch the work to gemini-cli with `target_repo=LogueOS-Console` instead.

---

## Service boundaries — files belong to their service (project-miru)

- `miru_ai/` — ALL code for the Miru AI service (port 18765): Python modules, workers, templates, static, tools, migrations
- `pm/` — ALL code for the PM Dashboard (port 18080): app.py, templates, static
- `gatekeeper/` — Local Governance Gatekeeper: dispatch validation core, frontmatter parser, forwarder (relocated from dispatcher/ in PRO-306)
- `dispatcher/` — **DELETED** (PRO-303). Legacy Task Dispatcher removed. Only `data/jobs.db` remains locally (gitignored archive). Do not place new files here.
- `shared/` — Only utilities imported by 2+ services. Not a dumping ground.
- `windows/` — Windows operational scripts (.ps1, .cmd) for service management ONLY. No Python service code here.

## Where new files go

- New Python module for miru_ai → `miru_ai/` (appropriate subfolder: core/, workers/, governance/, ingestion/)
- New Python module for pm → `pm/`
- Standalone data/AI utility scripts → `tools/`
- Test files → `tests/`
- Documentation → `docs/`
- Config JSON → `config/` (exception: `data/config/` for runtime config loaded inside Docker via bind-mount, e.g. `w2_profile_rules.json`)
- Batch run outputs, reports, audit CSVs → `data/batch_reports/`
- Official snapshots → `data/snapshots/`
- DB overlay/correction files → `data/overlays/`
- Runtime logs → `logs/` (gitignored — never commit logs)
- Test temp artifacts → `tests/_tmp/` (gitignored)
- Debug screenshots → `archive/screenshots/`

## NEVER do these

- Never create service code (.py, .html, .css, .js) at repo root
- Never create temp, scratch, or debug files at repo root
- Never write \*.log files to repo root or data/ root — always use `logs/`
- Never write \*.db files to repo root — always use `data/`
- Never write \*.png screenshots to repo root — use `archive/screenshots/`
- If a file belongs to miru_ai, pm, or gatekeeper — it lives in that service directory, nowhere else
- Never create files in `data/startup-logs/` — that path is deprecated; use `logs/`
