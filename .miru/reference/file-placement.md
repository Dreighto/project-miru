# Reference — File Placement

```
Reference: file-placement
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: creating a new file and unsure where it goes.
Last reviewed: 2026-05-08
```

Every file created must go in the correct location. These rules are non-negotiable.

---

## Service boundaries — files belong to their service

- `miru_ai/` — ALL code for the Miru AI service (port 18765): Python modules, workers, templates, static, tools, migrations
- `pm/` — ALL code for the PM Dashboard (port 18080): app.py, templates, static
- `gatekeeper/` — Local Governance Gatekeeper: dispatch validation core, frontmatter parser, forwarder (relocated from dispatcher/ in PRO-306)
- `dispatcher/` — **DELETED** (PRO-303). Legacy Task Dispatcher removed. Only `data/jobs.db` remains locally (gitignored archive).
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
- If a file belongs to miru_ai, pm, or dispatcher — it lives in that service directory, nowhere else
- Never create files in `data/startup-logs/` — that path is deprecated; use `logs/`
