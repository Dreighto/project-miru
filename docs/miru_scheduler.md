# Miru worktree worker — Windows Task Scheduler

Stage 3 adds a **scheduler-friendly entry path** and **lightweight run logging** so scheduled one-shot runs are inspectable. No daemon; overlap-aware; review-first.

## Scheduler command (recommended)

**Exact recommended invocation** (always include `--log-run` for visibility):

```bat
python -m tools.run_worktree_worker --mode overlap --log-run
```

Or use the wrapper (same effect; "Start in" is unambiguous):

```bat
run_miru_worker_overlap.bat
```

- **Overlap** is the default scheduled mode: overlap-aware, no-new-work short-circuit, worktree-only.
- **Stdout:** exactly one JSON object per run (parseable by scripts). Any DRY_RUN warning is on stderr only.

- **Program/script (Task Scheduler):**  
  `C:\path\to\worktree\run_miru_worker_overlap.bat`  
  (or full path to `python.exe` with arguments: `-m tools.run_worktree_worker --mode overlap --log-run`)

- **Start in (working directory):**  
  `C:\path\to\worktree`  
  Must be the worktree root so `data/` and `tools/` resolve correctly.

- **Recommended interval:**  
  Every **15–30 minutes**. Overlap mode short-circuits when there is no new work (same snapshot + same overlap), so frequent runs are safe.

## Preferred scheduled mode: overlap

- **Overlap** is the preferred mode for scheduling:
  - Stops cleanly when snapshot has no meta-bearing codes (blocker).
  - Short-circuits when snapshot and overlap set are unchanged (no_new_work).
  - When there is work, runs growth then rebuild sync; all paths are worktree-only.

- **Bulk** and **sync_only** remain available for manual use. Bulk is not recommended on a tight schedule (full snapshot pass).

## Snapshots and sources

- **Snapshots are manual.** Place or update card-list JSON under `data/snapshots/` (e.g. `community_cardlist.json`). The worker does not fetch snapshots.
- **Approved sources only.** The worker uses the same approved-source, snapshot-only, review-first behavior as the rest of Miru.

## Run logging

With `--log-run`:

- **Latest run:**  
  `data/miru_worker_last_run.json`  
  One JSON object: `timestamp`, `mode`, `action`, `blocker` or `no_new_work_reason`, `overlap_count`, `tasks_ok` / `tasks_failed`, sync summary when applicable.

- **Rolling history:**  
  `data/miru_worker_runs.jsonl`  
  One JSON object per line (append-only). Use for quick inspection of recent scheduled runs.

All paths are worktree-local; no main-repo or external logging.

## Optional: register the task (PowerShell)

Run from an elevated PowerShell if you want to register the task (once per worktree):

```powershell
$worktree = "C:\Users\andre\.codex\worktrees\0814\tcg-watcher"   # set to your worktree root
$action = New-ScheduledTaskAction -Execute "python" -Argument "-m tools.run_worktree_worker --mode overlap --log-run" -WorkingDirectory $worktree
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "MiruWorktreeWorkerOverlap" -Action $action -Trigger $trigger -Description "Miru worktree overlap worker (one-shot every 30 min)"
```

Adjust `$worktree`, interval, and task name as needed. This is optional; you can also create the task via Task Scheduler GUI using the command and "Start in" above.

## No daemon / no queue orchestration

- The worker is **one-shot**: it runs once and exits. No daemon loop.
- No queue-based task seeding yet; execution remains `run_once`-based and deterministic.
- Safe to run from Task Scheduler; output is JSON and logs are file-based for inspection.
