# Reference -- LOS-10 Step 6 rename map

```text
Reference: los-10-rename-map
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: planning, executing, or auditing the LOS-10 cutover.
Last reviewed: 2026-05-10
```

The rename pass runs as a single mechanical sweep at LOS-10 Step 6,
combined with `git filter-repo --path-rename` so that the imported
history in LogueOS-Orchestrator reflects the FINAL names rather than the
`miru-*` interim. Every name change below is locked — operators MUST NOT
introduce new `miru-*` identifiers in code that is destined for
LogueOS-Orchestrator after this map ships.

---

## Service / module / path renames

| Pre-cutover (project-miru)            | Post-cutover (LogueOS-Orchestrator)      | Notes                                                                                                  |
| ------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `tools/miru_mcp_gateway/`             | `tools/logueos_mcp_gateway/`             | The Python MCP gateway. References by name in dispatch listener + tests.                               |
| service name "miru-gateway"           | service name "logueos-gateway"           | Used in scripts/log lines/MCP server name.                                                             |
| service name "miru-dispatch-listener" | service name "logueos-dispatch-listener" | Used in Windows scheduled tasks + startup scripts.                                                     |
| `services/dispatch_listener/`         | `services/dispatch_listener/`            | Path unchanged — service name is renamed but module path stays.                                        |
| `data/templates/multi-repo/`          | `data/templates/multi-repo/`             | Path unchanged.                                                                                        |
| `miru-context/`                       | `miru-context/`                          | Directory kept as-is (historical canon naming). Filed for future review per source-of-truth meta-rule. |

## Env var renames

All env vars used by the dispatch listener or workers move from `MIRU_*`
to `LOGUEOS_*`. New env vars introduced after 2026-05-09 already use
the `LOGUEOS_*` prefix (set in PR #181 / LOGUEOS_CANON_SNAPSHOT_ID).

| Pre-cutover                   | Post-cutover               | Source files                                                                                                            |
| ----------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `MIRU_ROUTING_KEY`            | `LOGUEOS_ROUTING_KEY`      | services/dispatch_listener/src/spawn.js (active Anthropic key). Mapped to ANTHROPIC_API_KEY for spawned claude workers. |
| `MIRU_TRACE_ID`               | `LOGUEOS_TRACE_ID`         | services/dispatch_listener spawn env. Read by tools/emit_completion.py.                                                 |
| `MIRU_MCP_GATEWAY_PORT`       | `LOGUEOS_MCP_GATEWAY_PORT` | services/dispatch_listener/src/canon_probe.js. Defaults to 18766.                                                       |
| `MIRU_MCP_GATEWAY_HOST`       | `LOGUEOS_MCP_GATEWAY_HOST` | Same. Defaults to 127.0.0.1.                                                                                            |
| `MIRU_*` (any other instance) | `LOGUEOS_*`                | Sweep tools/ + services/ + scripts/ with grep before cutover.                                                           |

**Already-post-rename env vars (do not re-rename):**

- `LOGUEOS_CANON_SNAPSHOT_ID` — introduced 2026-05-10 in tools/emit_completion.py and services/dispatch_listener/src/spawn.js. Authoritative source for the canon snapshot pinned to each marker.

## Identifier renames (in code comments + docstrings)

These are not load-bearing but should be normalized during the same
mechanical sweep so audits don't see stale `miru-*` pointers in the
imported history:

- "miru-gateway" (string literal in docs, comments, log messages) → "logueos-gateway"
- "miru-dispatch-listener" (same) → "logueos-dispatch-listener"
- "project-miru" (in references to dispatch-loop substrate, NOT the actual GitHub repo) → "LogueOS-Orchestrator"
- "Miru dispatch loop" → "LogueOS dispatch loop"

**DO NOT rename:**

- `project-miru` the GitHub repo name (that repo continues to exist as Miru-specific business logic + worker rule canon).
- `Project Miru` the trading-card retail business name (that's the unrelated venture).
- `card_catalog.db` and other Miru-specific runtime artifacts (they stay in project-miru, not imported here).
- `miru_ai/` directory (Miru's chatbot product; stays in project-miru).
- `pm/` directory (Miru-specific HTML/CSS templates; stays in project-miru).

## DGAS append-only JSONL renames

The audit chain files are intentionally NOT renamed at cutover — their
filenames are part of the canonical append-only invariant tested by
`tests/test_jsonl_append_only_invariant.py`. The v1 chain in
project-miru remains at `data/cc_completion_log.jsonl` (frozen). The
v2 chain in LogueOS-Orchestrator starts at
`data/cc_completion_log.jsonl` (fresh file, same name). The boundary
manifest at `data/dgas_boundary/DGAS_BOUNDARY_MANIFEST.json` is the
cryptographic anchor between them.

## Filter-repo invocation (Step 6 mechanical extraction)

The mechanical extraction is driven by `tools/los_10_filter_repo.sh`
(see PR #TBD when this is committed). It runs `git filter-repo
--path-rename` for each path-level rename above and a sed-like
substitution for env var + string-literal renames. The script:

1. Refuses to run on the live project-miru working tree (writes to a
   throwaway clone instead).
2. Refuses to run with uncommitted changes anywhere in scope.
3. Prints a dry-run preview by default; requires `--execute` for the
   real run.
4. Outputs the result to a sibling directory which is then `git push
--force` to LogueOS-Orchestrator's `migration-import` branch (NOT
   main). Main is updated via PR after manual review.

## Audit checklist (run before merging the Step 6 PR into LogueOS-Orchestrator/main)

- [ ] No "miru-" identifiers remain in code paths that operate in LogueOS-Orchestrator (excluding the allow-list above).
- [ ] All env vars in services/ + tools/ use the `LOGUEOS_` prefix where they read worker-dispatch state.
- [ ] `services/dispatch_listener/` smoke-test passes on side port (Step 7) before merge.
- [ ] The DGAS boundary manifest sha in commit footer matches the manifest at `data/dgas_boundary/DGAS_BOUNDARY_MANIFEST.json`.
- [ ] `tools/verify_dgas_boundary.py` (from Gist) passes against the new layout.
- [ ] Worker rule canon (CLAUDE.md, AGENTS.md, .miru/, miru-context/) shows the LOGUEOS-INSTRUCTIONS-v3 version stamp (a fresh stamp marks the cutover; older stamps mean the canon was not refreshed).

## Reversion plan

If Step 6 produces a broken layout, the rollback is **immediate**:

1. `git checkout main` in LogueOS-Orchestrator (reverts to the pre-cutover scaffold).
2. Leave project-miru's dispatch loop running (it never stopped during Step 6 — the cutover happens at Step 8 only).
3. File a `los-10-rollback` ticket with the failure mode + which step in this map produced it. Update this map before re-running.
