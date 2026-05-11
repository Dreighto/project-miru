'use strict';

const fs = require('fs');
const path = require('path');
const { spawn, execFile, execSync, execFileSync } = require('child_process');

const log = require('./log');
const { spec } = require('./allowlist');
const { writeTerminalReceipt } = require('./receipt');
const { writeDlqEntry } = require('./dlq');
const { predictDispatchAsync } = require('./predict');
const { probeBeforeSpawn } = require('./canon_probe');

const STDERR_TAIL_BYTES = 4096;
const STDOUT_SCAN_BYTES = 8192;

// Closed taxonomy of terminal causes. Every spawn ends with exactly one.
const TERMINAL_CAUSES = Object.freeze(['spawn_error', 'timeout', 'exit_clean', 'exit_nonzero']);

function computeTerminalCause(timedOut, exitCode) {
  if (timedOut) return 'timeout';
  if (exitCode === 0) return 'exit_clean';
  return 'exit_nonzero';
}

// Scan stdout tail for terminal status markers emitted by workers.
// Workers print "STATUS: CONFIRMED WORKING" or "CONFIRMED_WORKING" in stdout.
// Returns 'CONFIRMED_WORKING' if found, null otherwise.
function readTailRaw(filePath, maxBytes) {
  try {
    const stat = fs.statSync(filePath);
    const size = stat.size;
    if (size === 0) return '';
    const start = Math.max(0, size - maxBytes);
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      return buf.toString('utf8');
    } finally {
      fs.closeSync(fd);
    }
  } catch (_e) {
    return '';
  }
}

// PRO-335: recognize all four canonical terminal statuses from CLAUDE.md
// + the ESCALATE pattern. Capture the diagnostic block following the status
// line so result.json carries the worker's actual reason, not an empty string.
//
// Returns { status, category, summary }:
//   status   = 'CONFIRMED_WORKING' | 'INCONCLUSIVE' | 'FAILED' | null
//   category = string | null   (e.g. 'HUMAN-REQUIRED' for ESCALATE)
//   summary  = string          (diagnostic block, capped at 4096 chars)
//
// ESCALATE is mapped to INCONCLUSIVE-with-summary so downstream consumers
// don't need a new terminal state in TERMINAL_STATES. The category field
// preserves the escalation reason (HUMAN-REQUIRED, SCOPE_EXPANSION, etc.).
function scanStdoutForStatus(stdoutTail) {
  const empty = { status: null, category: null, summary: '' };
  if (!stdoutTail) return empty;

  // Patterns ordered by specificity: explicit STATUS: lines first, then the
  // loose fallback for CONFIRMED_WORKING anywhere. First match wins.
  const patterns = [
    { re: /STATUS:\s*\bCONFIRMED[\s_]WORKING\b/i, status: 'CONFIRMED_WORKING' },
    { re: /STATUS:\s*ESCALATE:\s*([A-Z_-]+)\b/i, status: 'INCONCLUSIVE', escalate: true },
    { re: /STATUS:\s*INCONCLUSIVE\b/i, status: 'INCONCLUSIVE' },
    { re: /STATUS:\s*FAILED\b/i, status: 'FAILED' },
    { re: /\bCONFIRMED_WORKING\b/i, status: 'CONFIRMED_WORKING' },
  ];

  for (const p of patterns) {
    const m = stdoutTail.match(p.re);
    if (!m) continue;
    // Capture from the matched status line through the end of stdout. This
    // is the worker's diagnostic block — the explanation that today gets
    // dropped on the floor.
    const block = stdoutTail.slice(m.index).trim();
    const SUMMARY_CAP = 4096;
    const TRUNCATION_MARKER = '\n... [truncated]';
    const summary =
      block.length > SUMMARY_CAP
        ? block.slice(0, SUMMARY_CAP - TRUNCATION_MARKER.length) + TRUNCATION_MARKER
        : block;
    const category = p.escalate ? m[1] || null : null;
    return { status: p.status, category, summary };
  }

  return empty;
}

function killProcessTree(child) {
  if (!child || !child.pid) return;

  if (process.platform === 'win32') {
    try {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      killer.on('error', () => {
        try {
          child.kill('SIGTERM');
        } catch (_e) {
          // already exited
        }
      });
      killer.unref();
      return;
    } catch (_e) {
      // fall through to the direct child fallback below
    }
  }

  try {
    if (process.platform !== 'win32') {
      process.kill(-child.pid, 'SIGTERM');
    } else {
      child.kill('SIGTERM');
    }
  } catch (_e) {
    // already exited
  }
}

function readTail(filePath, maxBytes) {
  try {
    const stat = fs.statSync(filePath);
    const size = stat.size;
    if (size === 0) return '';
    const start = Math.max(0, size - maxBytes);
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      const text = buf.toString('utf8');
      const lines = text.split(/\r?\n/);
      return lines.slice(-20).join('\n');
    } finally {
      fs.closeSync(fd);
    }
  } catch (_e) {
    return '';
  }
}

function safeStatSize(filePath) {
  try {
    return fs.statSync(filePath).size;
  } catch (_e) {
    return 0;
  }
}

// PRO-316/PRO-338: run clean_worktree.py before spawning to remove known-safe
// gitignored artifacts (test-results/, playwright-report/, .pytest_cache/,
// __pycache__, node_modules/.cache) that would fail the worker's worktree
// cleanliness gate.
//
// PRO-338 (2026-05-10): the script now lives in project-miru's tools/ and is
// invoked via absolute path with --cwd <worker_cwd>. Previously this ran
// `python tools/clean_worktree.py` with cwd=<worker_cwd>, which broke for
// non-miru worktrees (LogueOS-Console-w1) where tools/ doesn't exist —
// the listener log had a worktree_auto_clean_failed warning on every
// multi-repo dispatch, and the cleanup never actually happened. Now the
// script is project-miru's tooling regardless of where the worker lives.
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const CLEAN_WORKTREE_SCRIPT = path.join(REPO_ROOT, 'tools', 'clean_worktree.py');

function cleanWorktree(cwd, traceId, opts) {
  // opts.execFileSync is for tests; default to the real execFileSync.
  // execFileSync (not execSync) so we pass argv as an array — the shell never
  // sees the `cwd` value, which prevents shell-injection if a worktree path
  // ever contained metacharacters. (CodeRabbit Major on PR #160; in practice
  // cwd comes from a hardcoded WORKTREE_POOLS map, but defensive coding is
  // cheap and the test mocks adapt trivially.)
  const execFile = (opts && opts.execFileSync) || execFileSync;
  try {
    const output = execFile('python', [CLEAN_WORKTREE_SCRIPT, '--cwd', String(cwd)], {
      // No `cwd` here — the script reads --cwd from argv. Running from
      // REPO_ROOT (or anywhere) doesn't matter; --cwd controls the target.
      timeout: 15000,
      encoding: 'utf8',
      windowsHide: true,
    });
    log.info('worktree_auto_clean', {
      trace_id: traceId,
      cwd,
      output: (output || '').trim().slice(0, 500),
    });
    return { ok: true };
  } catch (err) {
    const stderr = String(err.stderr || err.message || '').trim();
    log.warn('worktree_auto_clean_failed', {
      trace_id: traceId,
      cwd,
      stderr: stderr.slice(0, 500),
    });
    return { ok: false, error: stderr.slice(0, 300) };
  }
}

// PRO-334: Derive the parking branch name from a worktree path.
// e.g. "D:\dev\miru-w1" → "_parking_w1", "D:\dev\miru-cursor" → "_parking_cursor"
// path.win32.basename is used so Windows-style paths parse correctly on POSIX too.
// Suffix is lowercased so case-insensitive Windows paths (e.g. "D:\dev\MIRU-W1")
// canonicalize to the same parking branch name as their lowercase counterpart —
// without this, verifyWorktreeParked would reject `_parking_w1` as
// `wrong_parking_branch` if the cwd happened to use uppercase casing.
// Legacy project-miru slots — explicit allowlist (NOT a regex). The legacy
// short-form parking branch convention (`_parking_w1`, `_parking_cursor`) is
// frozen at exactly these basenames. Any other `miru-*` basename (e.g. a
// hypothetical future `miru-tools-w1`) goes through the new full-basename
// pattern below and gets `_parking_miru-tools-w1`.
//
// Per CodeRabbit feedback on PR #157: a regex like /^miru-(.+)$/ would silently
// misclassify modern repo names that happen to start with `miru-`. The Set
// makes the legacy boundary explicit and impossible to extend by accident.
const LEGACY_MIRU_SLOT_BASENAMES = new Set([
  'miru-w1',
  'miru-w2',
  'miru-w3',
  'miru-w4',
  'miru-w5',
  'miru-w6',
  'miru-cursor',
]);

// LOS-14 anchor for the centralized worktree pool. Derived from the basename
// of LOGUEOS_WORKTREE_BASE so this matches whatever pool root the operator
// configured. Default: 'worktrees' (basename of D:\dev\worktrees).
//
// CR R1 on PR #187: previously hardcoded as 'worktrees', which silently broke
// parkingBranchForCwd if LOGUEOS_WORKTREE_BASE pointed somewhere whose
// basename wasn't 'worktrees' (e.g. D:\custom\disp-pool). Spawning a worker
// in such a worktree would then trigger pre_spawn_dirty_refusal because
// parkingBranchForCwd returned null. Reading from env at module load
// matches how WORKTREE_POOLS in worktree.js consumes LOGUEOS_WORKTREE_BASE.
const LOS14_POOL_ANCHOR = path.win32
  .basename(path.win32.normalize(process.env.LOGUEOS_WORKTREE_BASE || 'D:\\dev\\worktrees'))
  .toLowerCase();

function parkingBranchForCwd(cwd) {
  const cwdStr = String(cwd);
  const name = path.win32.basename(cwdStr);
  // Legacy: only the exact basenames in the allowlist get the short-form
  // parking branch (`_parking_w1`). The lowercase normalization handles
  // case-insensitive Windows paths.
  const lowerName = name.toLowerCase();
  if (LEGACY_MIRU_SLOT_BASENAMES.has(lowerName)) {
    const suffix = lowerName.slice('miru-'.length);
    return `_parking_${suffix}`;
  }
  // Multi-repo worktrees (added 2026-05-09 for LOS team and beyond):
  // <RepoName>-w<N> → _parking_<RepoName>-w<N>. Full basename preserved
  // (case-sensitive) because cross-repo collisions are possible (e.g.,
  // LogueOS-Console-w1 and LogueOS-Framework-w1 must produce different
  // parking branches even though both end in -w1). Pattern guard ensures
  // we only match worktree-shaped basenames — random paths return null.
  if (/^[A-Za-z0-9._-]+-w\d+$/i.test(name)) return `_parking_${name}`;
  // LOS-14 layout (added 2026-05-11): <LOGUEOS_WORKTREE_BASE>\<repo>\w<N> →
  // _parking_<repo>-w<N>. Same parking-branch shape as the multi-repo
  // pattern above, just derived from grandparent/parent/basename instead
  // of just basename. We require ALL THREE guards:
  //   - basename matches exactly w<N>
  //   - parent dir name is a valid repo identifier
  //   - grandparent dir basename matches LOS14_POOL_ANCHOR (= basename
  //     of LOGUEOS_WORKTREE_BASE, default 'worktrees'). Without this
  //     anchor, a bare `D:\dev\w1` could match with parent=`dev` and
  //     create `_parking_dev-w1` which would be wrong on every axis.
  // The anchor check says "this path is part of the dispatch pool, not
  // some random other checkout that happens to end in /w1". CR R1 on
  // PR #187: anchor is now derived from LOGUEOS_WORKTREE_BASE rather
  // than hardcoded to 'worktrees', so an operator who overrides the
  // pool root to a non-default location (e.g. D:\custom\disp-pool)
  // still gets correct parking-branch derivation.
  if (/^w\d+$/i.test(name)) {
    const parentDir = path.win32.dirname(cwdStr);
    const parent = path.win32.basename(parentDir);
    const grandparent = path.win32.basename(path.win32.dirname(parentDir));
    if (
      parent &&
      /^[A-Za-z0-9._-]+$/.test(parent) &&
      grandparent.toLowerCase() === LOS14_POOL_ANCHOR
    ) {
      return `_parking_${parent}-${name.toLowerCase()}`;
    }
  }
  return null;
}

// PRO-334: Pre-spawn guard — verify the worktree is on its exact _parking_* branch
// and git status is clean. Injectable execSync for testability.
function verifyWorktreeParked(cwd, traceId, _deps) {
  const exec = (_deps && _deps.execSync) || execSync;
  const expectedBranch = parkingBranchForCwd(cwd);
  if (!expectedBranch) {
    log.warn('pre_spawn_dirty_refusal', {
      trace_id: traceId,
      cwd,
      reason: 'unrecognized_worktree',
    });
    return { ok: false, reason: 'unrecognized_worktree' };
  }
  try {
    const branch = exec('git rev-parse --abbrev-ref HEAD', {
      cwd,
      timeout: 10000,
      encoding: 'utf8',
      windowsHide: true,
    }).trim();
    if (branch !== expectedBranch) {
      log.warn('pre_spawn_dirty_refusal', {
        trace_id: traceId,
        cwd,
        reason: 'wrong_parking_branch',
        branch,
        expected_branch: expectedBranch,
      });
      return { ok: false, reason: `wrong_parking_branch:${branch}` };
    }
    const statusOut = exec('git status --porcelain', {
      cwd,
      timeout: 10000,
      encoding: 'utf8',
      windowsHide: true,
    }).trim();
    if (statusOut) {
      log.warn('pre_spawn_dirty_refusal', {
        trace_id: traceId,
        cwd,
        reason: 'dirty_worktree',
        status_excerpt: statusOut.slice(0, 200),
      });
      return { ok: false, reason: 'dirty_worktree' };
    }
    return { ok: true };
  } catch (err) {
    log.warn('pre_spawn_git_check_failed', {
      trace_id: traceId,
      cwd,
      error: String(err.message || err).slice(0, 200),
    });
    return { ok: false, reason: `git_check_failed:${String(err.message || err).slice(0, 100)}` };
  }
}

// PRO-334: Post-worker cleanup — stash any uncommitted changes, checkout the
// parking branch, pull latest, and delete the feature branch only if its PR
// is already merged. Injectable execSync for testability.
function cleanupWorktree(cwd, traceId, _deps) {
  const exec = (_deps && _deps.execSync) || execSync;
  const parkingBranch = parkingBranchForCwd(cwd);
  if (!parkingBranch) {
    log.warn('worktree_park_skip', { trace_id: traceId, cwd, reason: 'unrecognized_worktree' });
    return;
  }
  // Defense-in-depth: parkingBranch is derived from cwd (config-controlled)
  // but it gets shell-interpolated into git checkout / git pull below. Apply
  // the same regex check we use for currentBranch so an unsafe character in
  // a misconfigured worktree path can't reach the shell.
  if (!/^[\w./-]+$/.test(parkingBranch)) {
    log.warn('worktree_park_skip', {
      trace_id: traceId,
      cwd,
      branch: parkingBranch,
      reason: 'unsafe_parking_branch_name',
    });
    return;
  }
  try {
    const statusOut = exec('git status --porcelain', {
      cwd,
      timeout: 10000,
      encoding: 'utf8',
      windowsHide: true,
    }).trim();

    let currentBranch = null;
    try {
      currentBranch = exec('git rev-parse --abbrev-ref HEAD', {
        cwd,
        timeout: 10000,
        encoding: 'utf8',
        windowsHide: true,
      }).trim();
    } catch (_e) {
      // detached HEAD or unreachable — leave currentBranch null
    }

    if (statusOut) {
      try {
        exec('git stash push --include-untracked -m "dispatch-cleanup"', {
          cwd,
          timeout: 15000,
          encoding: 'utf8',
          windowsHide: true,
        });
        log.info('worktree_cleanup_stashed', { trace_id: traceId, cwd });
      } catch (stashErr) {
        // Critical (CodeRabbit): if stash fails, abort cleanup. Continuing to
        // `git checkout` would carry the uncommitted local edits onto the
        // `_parking_*` branch, recreating the cross-dispatch contamination
        // this hook exists to prevent.
        log.error('worktree_cleanup_stash_failed_abort', {
          trace_id: traceId,
          cwd,
          error: String(stashErr.message || stashErr).slice(0, 200),
        });
        return;
      }
    }

    exec(`git checkout ${parkingBranch}`, {
      cwd,
      timeout: 15000,
      encoding: 'utf8',
      windowsHide: true,
    });

    try {
      exec(`git pull --ff-only origin ${parkingBranch}`, {
        cwd,
        timeout: 20000,
        encoding: 'utf8',
        windowsHide: true,
      });
    } catch (pullErr) {
      log.warn('worktree_cleanup_pull_failed', {
        trace_id: traceId,
        cwd,
        branch: parkingBranch,
        error: String(pullErr.message || pullErr).slice(0, 200),
      });
    }

    if (currentBranch && !currentBranch.startsWith('_parking_') && currentBranch !== 'HEAD') {
      if (!/^[\w./-]+$/.test(currentBranch)) {
        log.warn('worktree_cleanup_branch_skipped', {
          trace_id: traceId,
          cwd,
          branch: currentBranch,
          reason: 'unsafe_branch_name',
        });
      } else {
        try {
          // Stricter merged-PR check (Codex P1, two iterations):
          //   - Request `headRefName`, `mergedAt`, AND `headRepositoryOwner`
          //     so we can verify the matched PR is in OUR repo (not a fork
          //     with the same branch name).
          //   - `gh pr list --head <branch>` matches by branch name only.
          //     In repos with forks, a fork's PR with the same branch name
          //     can be returned. Even with `headRefName === currentBranch`,
          //     fork PRs share the same head ref name as ours.
          //   - Final guard: filter to PRs where headRefName matches AND
          //     mergedAt is non-null AND headRepositoryOwner.login matches
          //     our local origin's owner. Only then is `git branch -D`
          //     safe.
          const prListOut = exec(
            `gh pr list --head ${currentBranch} --state merged ` +
              `--json number,headRefName,mergedAt,headRepositoryOwner`,
            { cwd, timeout: 15000, encoding: 'utf8', windowsHide: true }
          ).trim();
          const prs = JSON.parse(prListOut || '[]');
          // Resolve our origin owner from the local origin remote so we
          // don't hard-code 'Dreighto'. If the owner can't be resolved,
          // skip the delete (fail-safe — retain branch rather than risk).
          let originOwner = null;
          try {
            const remoteUrl = exec('git config --get remote.origin.url', {
              cwd,
              timeout: 5000,
              encoding: 'utf8',
              windowsHide: true,
            }).trim();
            // Match owner from URLs like:
            //   https://github.com/Owner/repo.git
            //   git@github.com:Owner/repo.git
            const m = remoteUrl.match(/[/:]([^/:]+)\/[^/]+?(\.git)?$/);
            originOwner = m ? m[1] : null;
          } catch (_e) {
            originOwner = null;
          }
          const verifiedMerges =
            Array.isArray(prs) && originOwner
              ? prs.filter(
                  (pr) =>
                    pr &&
                    pr.headRefName === currentBranch &&
                    pr.mergedAt &&
                    pr.headRepositoryOwner &&
                    pr.headRepositoryOwner.login === originOwner
                )
              : [];
          if (verifiedMerges.length > 0) {
            exec(`git branch -D ${currentBranch}`, {
              cwd,
              timeout: 10000,
              encoding: 'utf8',
              windowsHide: true,
            });
            log.info('worktree_cleanup_branch_deleted', {
              trace_id: traceId,
              cwd,
              branch: currentBranch,
              pr_count: verifiedMerges.length,
            });
          } else {
            log.info('worktree_cleanup_branch_retained', {
              trace_id: traceId,
              cwd,
              branch: currentBranch,
              reason: 'no_merged_pr',
            });
          }
        } catch (branchErr) {
          log.warn('worktree_cleanup_branch_check_failed', {
            trace_id: traceId,
            cwd,
            branch: currentBranch,
            error: String(branchErr.message || branchErr).slice(0, 200),
          });
        }
      }
    }

    log.info('worktree_parked', { trace_id: traceId, cwd, parking_branch: parkingBranch });
  } catch (err) {
    log.warn('worktree_cleanup_failed', {
      trace_id: traceId,
      cwd,
      error: String(err.message || err).slice(0, 300),
    });
  }
}

// PRO-342: After a CONFIRMED_WORKING exit, verify that a PR was opened for the
// worker's branch. Fire-and-forget via setImmediate — never blocks the listener
// event loop or delays onDone. Injectable _deps.execFile for testability.
function checkNoPrAsync({ traceId, worker, branch, cwd }, _deps) {
  setImmediate(() => {
    const execFileAsync = (_deps && _deps.execFile) || execFile;
    execFileAsync(
      'git',
      ['config', '--get', 'remote.origin.url'],
      { cwd, timeout: 5000, encoding: 'utf8', windowsHide: true },
      (_err, stdout) => {
        if (_err) {
          log.info('no_pr_check_skipped', {
            trace_id: traceId,
            error: String(_err.message || _err).slice(0, 200),
          });
          return;
        }
        const remoteUrl = stdout.trim();
        const m = remoteUrl.match(/[/:]([^/:]+)\/([^/]+?)(\.git)?$/);
        if (!m) {
          log.info('no_pr_check_skipped', { trace_id: traceId, reason: 'cannot_parse_remote_url' });
          return;
        }
        const targetRepo = `${m[1]}/${m[2]}`;
        execFileAsync(
          'gh',
          [
            'pr',
            'list',
            '--repo',
            targetRepo,
            '--head',
            branch,
            '--json',
            'number',
            '--jq',
            'length',
          ],
          { timeout: 15000, encoding: 'utf8', windowsHide: true },
          (_err2, stdout2) => {
            if (_err2) {
              log.info('no_pr_check_skipped', {
                trace_id: traceId,
                error: String(_err2.message || _err2).slice(0, 200),
              });
              return;
            }
            const countStr = stdout2.trim();
            const count = parseInt(countStr, 10);
            if (isNaN(count)) {
              log.info('no_pr_check_skipped', {
                trace_id: traceId,
                reason: 'malformed_gh_output',
                raw: countStr.slice(0, 100),
              });
            } else if (count === 0) {
              log.warn('worker_no_pr_warning', {
                trace_id: traceId,
                worker,
                branch,
                target_repo: targetRepo,
              });
            }
          }
        );
      }
    );
  });
}

// PRO-233: run `cmd /c <binary> --version` before the real spawn to confirm
// the binary is reachable in this process's environment. Synchronous on
// purpose — the caller is already committed to spawning; this probe adds at
// most 10s and the result goes straight into the structured log.
function probeWorkerBinary(binary, cwd) {
  try {
    const output = execSync(`cmd /c "${binary}" --version`, {
      cwd,
      timeout: 10000,
      encoding: 'utf8',
      windowsHide: true,
      env: { ...process.env },
    });
    return { ok: true, output: (output || '').trim().slice(0, 300) };
  } catch (err) {
    const combined = String(err.stderr || err.stdout || err.message || '').trim();
    return {
      ok: false,
      exit_code: err.status !== null && err.status !== undefined ? err.status : null,
      signal: err.signal || null,
      output: combined.slice(0, 300),
    };
  }
}

function spawnWorker({
  traceId,
  worker,
  promptText,
  timeoutSeconds,
  // Renamed to _useApiKey because the param is accepted from the dispatch
  // payload but not currently wired through to the spawned worker (legacy
  // caller contract). Prefix matches the no-unused-vars argsIgnorePattern.
  // If/when this gets wired, drop the underscore.
  useApiKey: _useApiKey = false,
  model = null,
  thinkingLevel = null,
  toolProfile = 'standard_worker',
  cwd,
  traceLogDir,
  onDone,
}) {
  const workerSpec = spec(worker);
  if (!workerSpec) {
    throw new Error(`worker ${worker} not in allowlist (defensive guard)`);
  }

  fs.mkdirSync(traceLogDir, { recursive: true });

  // PRO-334: defense-in-depth — refuse if worktree is not parked or is dirty
  const parkCheck = verifyWorktreeParked(cwd, traceId);
  if (!parkCheck.ok) {
    throw new Error(`pre_spawn_dirty_refusal: ${parkCheck.reason}`);
  }

  // LOS-10 Step 2 / LOS-13: probe the gateway's /canon-manifest BEFORE spawning.
  // Fail-closed: if the gateway is unreachable, refuse to spawn. Workers that
  // can't fetch canon over HTTP would otherwise spawn with stale or missing
  // rules — silent drift is the failure mode we're preventing. The snapshot_id
  // also gets passed to the worker env (LOGUEOS_CANON_SNAPSHOT_ID) so the
  // worker's completion marker can carry a deterministic pointer to the canon
  // that was in force when the dispatch happened.
  const canonProbe = probeBeforeSpawn(traceId);

  const stdoutPath = path.join(traceLogDir, `${traceId}.stdout.log`);
  const stderrPath = path.join(traceLogDir, `${traceId}.stderr.log`);

  const stdoutFd = fs.openSync(stdoutPath, 'a');
  const stderrFd = fs.openSync(stderrPath, 'a');

  const binary = workerSpec.binaryPath || workerSpec.binary;

  // PRO-233: pre-spawn diagnostics — binary path, cwd, PATH snapshot, version probe.
  const pathDirs = (process.env.PATH || '').split(path.delimiter);
  log.info('spawn_pre_diagnostic', {
    trace_id: traceId,
    worker,
    binary_path: binary,
    binary_raw: workerSpec.binary,
    flags: workerSpec.flags,
    cwd,
    path_dirs_count: pathDirs.length,
    path_first_10: pathDirs.slice(0, 10).join(path.delimiter),
    appdata: process.env.APPDATA || null,
    localappdata: process.env.LOCALAPPDATA || null,
  });

  const probe = probeWorkerBinary(binary, cwd);
  log.info('spawn_version_probe', { trace_id: traceId, binary_path: binary, ...probe });

  // PRO-316: auto-clean gitignored artifacts before worker starts.
  cleanWorktree(cwd, traceId);

  // Prompt is written to a temp file and the file is opened as a read-only
  // file descriptor passed directly to the child as stdio[0]. This sidesteps
  // every cmd-level escaping concern:
  //   * The prompt content never appears in argv, so cmd's %VAR% expansion
  //     and newline-as-terminator behavior can't mutate or truncate it (see
  //     PR #22 Bugbot finding "Prompt passed unescaped to cmd.exe argv").
  //   * No `<` redirect inside a cmd command string, so node's argv escaping
  //     can't mangle it (we hit "The filename, directory name, or volume
  //     label syntax is incorrect" trying to use `cmd /c "<bin> <args> <
  //     <file>"` directly).
  //   * No reliance on `child.stdin.pipe` writes, which empirically don't
  //     reach the worker when `detached: true` is set on Windows.
  // The temp file is unlinked on exit/error.
  const promptFile = path.join(traceLogDir, `${traceId}.prompt.tmp`);
  fs.writeFileSync(promptFile, promptText, 'utf8');

  let promptFd;
  try {
    promptFd = fs.openSync(promptFile, 'r');
  } catch (err) {
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    throw err;
  }

  // Auth: workers use CLAUDE_CODE_OAUTH_TOKEN — a portable OAuth token generated
  // via `claude setup-token` that is NOT DPAPI-encrypted, so it works in Session 0
  // scheduled tasks. ANTHROPIC_API_KEY is always stripped to prevent accidental
  // API billing. MIRU_ROUTING_KEY remains available for Claude Chat's own session.
  const childEnv = { ...process.env };
  delete childEnv.ANTHROPIC_API_KEY;

  // Token isolation: strip all GitHub auth vars from child env before setting
  // the restricted worker token. This covers both the new ROOM_TOKEN_* names
  // and the legacy GITHUB_TOKEN_* names that may still exist during rotation.
  // Any inherited GH_TOKEN is also cleared so we fully control what the worker
  // gets — no accidental operator-level access via an inherited value.
  delete childEnv.ROOM_TOKEN_OPERATOR;
  delete childEnv.GITHUB_TOKEN_WRITE;
  delete childEnv.GITHUB_TOKEN_READ;
  delete childEnv.GH_TOKEN;

  // Set GH_TOKEN so gh CLI authenticates automatically inside the worker.
  // Prefer ROOM_TOKEN_WORKER; fall back to legacy read token during rotation.
  const workerGhToken = process.env.ROOM_TOKEN_WORKER || process.env.GITHUB_TOKEN_READ;
  if (workerGhToken) {
    childEnv.GH_TOKEN = workerGhToken;
  }

  // Gemini CLI trust gate: --skip-trust flag is unreliable in headless
  // environments on some versions. Injecting the env var is the guaranteed
  // alternative per the CLI docs and the error message itself.
  if (worker === 'gemini') {
    childEnv.GEMINI_CLI_TRUST_WORKSPACE = 'true';
  }

  // Expose trace_id to the worker so emit_heartbeat / emit_completion can
  // include it in their JSONL rows. Without this, log correlation across
  // dispatch → worker → marker → bridge is impossible.
  childEnv.MIRU_TRACE_ID = traceId;
  childEnv.MIRU_TOOL_PROFILE = toolProfile;

  // LOS-10 Step 2 / LOS-13: pass the canon snapshot ID to the worker so its
  // completion marker can record a deterministic pointer to the canon that
  // was in force at spawn time. Uses the FUTURE name (LOGUEOS_*) per the
  // Step 6 rename map — new env vars adopt the post-rename style now to
  // avoid a second rename pass at cutover. Existing MIRU_* vars stay until
  // Step 6.
  childEnv.LOGUEOS_CANON_SNAPSHOT_ID = canonProbe.snapshot_id;

  log.info('spawn_auth_debug', {
    trace_id: traceId,
    has_api_key: !!childEnv.ANTHROPIC_API_KEY,
    has_routing_key: !!childEnv.MIRU_ROUTING_KEY,
    has_oauth_token: !!childEnv.CLAUDE_CODE_OAUTH_TOKEN,
  });

  // PRO-265: per-dispatch model and effort flags (claude-code only).
  // thinking_level "extended" maps to --effort max (the Claude CLI effort scale is
  // low/medium/high/xhigh/max; "extended" is the semantic alias used by Claude Chat).
  // For gemini the override flags are not yet wired; values are
  // logged but ignored so callers don't get a silent no-op without a trace.
  const EFFORT_MAP = {
    extended: 'max',
    low: 'low',
    medium: 'medium',
    high: 'high',
    xhigh: 'xhigh',
    max: 'max',
  };
  const extraFlags = [];
  if (worker === 'claude-code') {
    if (model) extraFlags.push('--model', model);
    if (thinkingLevel && EFFORT_MAP[thinkingLevel]) {
      extraFlags.push('--effort', EFFORT_MAP[thinkingLevel]);
    }
    const mcpConfigPath = path.join(cwd, '.mcp.json');
    if (fs.existsSync(mcpConfigPath)) {
      extraFlags.push('--mcp-config', mcpConfigPath, '--strict-mcp-config');
    }
  }

  log.info('spawn_flags', {
    trace_id: traceId,
    worker,
    base_flags: workerSpec.flags,
    extra_flags: extraFlags,
    model_requested: model,
    thinking_level_requested: thinkingLevel,
  });

  // PRO-329: Hermes shadow prediction — fire-and-forget, never blocks spawn.
  predictDispatchAsync({ traceId, worker, promptText });

  let child;
  try {
    // detached:true was empirically incompatible with stdio file fds on this
    // Windows setup -- claude exited 1 with empty stdout/stderr no matter how
    // stdin was wired (pipe, file fd, batch wrapper with `< redirect`).
    // Dropping detached:true makes the worker a normal child of the listener:
    // listener crash will kill mid-flight workers (acceptable Phase 1 behavior
    // per the README, the orphan sweep already handles that case at startup).
    //
    // windowsHide:true sets CREATE_NO_WINDOW on Windows. This is correct for
    // claude-code (no console needed) but breaks gemini: gemini's startup
    // sequence runs conpty_console_list_agent.js which calls AttachConsole().
    // CREATE_NO_WINDOW prevents any console from being allocated, so
    // AttachConsole() fails and gemini exits immediately.
    //
    // The dispatch_listener's startup script (windows/start_dispatch_listener.ps1)
    // hides the PowerShell console window via ShowWindow(hwnd, 0) but leaves the
    // console allocated. Without windowsHide, gemini inherits that hidden console,
    // AttachConsole() succeeds, and no new window appears on the user's desktop.
    const spawnOpts = {
      cwd,
      stdio: [promptFd, stdoutFd, stderrFd],
      env: childEnv,
    };
    if (worker !== 'gemini') {
      spawnOpts.windowsHide = true;
    }
    child = spawn('cmd', ['/c', binary, ...workerSpec.flags, ...extraFlags], spawnOpts);
  } catch (err) {
    fs.closeSync(promptFd);
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    throw err;
  }

  // Parent closes its copy of the fds; the child has inherited them.
  fs.closeSync(promptFd);
  fs.closeSync(stdoutFd);
  fs.closeSync(stderrFd);

  const startedAt = new Date().toISOString();
  log.info('worker_spawned', {
    trace_id: traceId,
    worker,
    pid: child.pid,
    pid_defined: child.pid !== null && child.pid !== undefined,
    timeout_seconds: timeoutSeconds,
    model: model || null,
    thinking_level: thinkingLevel || null,
  });

  if (child.pid === null || child.pid === undefined) {
    log.error('spawn_pid_undefined', {
      trace_id: traceId,
      worker,
      binary_path: binary,
      cwd,
    });
  }

  // Per Node child_process semantics, `error` and `exit` can BOTH fire for the
  // same spawn. Without a guard, both handlers would write a terminal receipt
  // and a DLQ row -- producing duplicate rows for a single trace_id and an
  // overwriting receipt rename. The `finalized` flag guarantees exactly one
  // terminal receipt + at most one DLQ row per spawn even when both events
  // fire (and even when the timeout races with a natural exit).
  //
  // worker_terminal is the single canonical terminal event. It fires exactly
  // once per spawn from whichever handler wins the finalized race.
  let finalized = false;
  let timedOut = false;
  const timer = setTimeout(() => {
    // If the child has already finalized (natural exit / spawn error), skip
    // the kill -- no zombie to terminate, and timedOut would only confuse the
    // already-written receipt's status if the exit handler races with us.
    if (finalized) return;
    timedOut = true;
    log.warn('worker_timeout_kill', {
      trace_id: traceId,
      pid: child.pid,
      timeout_seconds: timeoutSeconds,
    });
    killProcessTree(child);
  }, timeoutSeconds * 1000);
  if (typeof timer.unref === 'function') timer.unref();

  child.on('error', (err) => {
    if (finalized) return;
    finalized = true;
    clearTimeout(timer);
    log.error('worker_spawn_error', { trace_id: traceId, error: err.message });
    const completedAt = new Date().toISOString();
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);
    log.info('worker_terminal', {
      trace_id: traceId,
      worker,
      pid: child.pid,
      exit_code: null,
      signal: null,
      timed_out: false,
      cause: 'spawn_error',
      status: 'FAILED',
      duration_ms: new Date(completedAt) - new Date(startedAt),
      stdout_bytes: 0,
      stderr_bytes: safeStatSize(stderrPath),
    });
    try {
      writeTerminalReceipt({
        traceId,
        worker,
        status: 'FAILED',
        startedAt,
        completedAt,
        exitCode: null,
        stderrTail: stderrTail || err.message,
      });
      writeDlqEntry({
        traceId,
        worker,
        promptPath: null,
        exitCode: null,
        stderrTail: stderrTail || err.message,
        errorClass: 'spawn_failed',
      });
    } catch (writeErr) {
      log.error('finalize_error_path_failed', { trace_id: traceId, error: writeErr.message });
    }
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    cleanupWorktree(cwd, traceId); // PRO-334
    if (typeof onDone === 'function') onDone();
  });

  child.on('exit', (code, signal) => {
    if (finalized) return;
    finalized = true;
    clearTimeout(timer);
    const completedAt = new Date().toISOString();
    const exitCode = code !== null ? code : -1;
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);
    const stdoutTail = readTailRaw(stdoutPath, STDOUT_SCAN_BYTES);

    // --print buffers all stdout until clean exit; a killed process flushes nothing.
    // Timeout → FAILED is intentional — no stdout data to scan.
    // PRO-335: scanStdoutForStatus now returns {status, category, summary} so
    // the worker's diagnostic block flows through to result.json instead of
    // being silently replaced with an empty INCONCLUSIVE.
    let status;
    let summary = '';
    let escalationCategory = null;
    if (timedOut) {
      status = 'FAILED';
      summary = stderrTail || '';
    } else if (exitCode === 0) {
      const scan = scanStdoutForStatus(stdoutTail);
      status = scan.status || 'INCONCLUSIVE';
      summary = scan.summary || '';
      escalationCategory = scan.category;
    } else {
      status = 'FAILED';
      const scan = scanStdoutForStatus(stdoutTail);
      summary = scan.summary || stderrTail || '';
      escalationCategory = scan.category;
    }

    log.info('worker_exit', {
      trace_id: traceId,
      worker,
      pid: child.pid,
      exit_code: exitCode,
      signal,
      status,
      timed_out: timedOut,
    });
    log.info('worker_terminal', {
      trace_id: traceId,
      worker,
      pid: child.pid,
      exit_code: exitCode,
      signal,
      timed_out: timedOut,
      cause: computeTerminalCause(timedOut, exitCode),
      status,
      duration_ms: new Date(completedAt) - new Date(startedAt),
      stdout_bytes: safeStatSize(stdoutPath),
      stderr_bytes: safeStatSize(stderrPath),
    });

    try {
      writeTerminalReceipt({
        traceId,
        worker,
        status,
        startedAt,
        completedAt,
        exitCode,
        stderrTail,
        summary,
        escalationCategory,
      });

      if (status === 'FAILED') {
        const errorClass = timedOut ? 'timeout' : 'spawn_failed';
        writeDlqEntry({
          traceId,
          worker,
          promptPath: null,
          exitCode,
          stderrTail,
          errorClass,
        });
      }
    } catch (writeErr) {
      log.error('finalize_exit_path_failed', { trace_id: traceId, error: writeErr.message });
    }
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }

    // PRO-342: start branch probe fire-and-forget, then clean up immediately
    // so onDone is never gated on the probe. Callback only calls checkNoPrAsync.
    if (status === 'CONFIRMED_WORKING') {
      execFile(
        'git',
        ['branch', '--show-current'],
        { cwd, timeout: 2000, encoding: 'utf8', windowsHide: true },
        (_err, stdout) => {
          const workerBranch = _err ? null : stdout.trim() || null;
          if (workerBranch) checkNoPrAsync({ traceId, worker, branch: workerBranch, cwd });
        }
      );
    }
    cleanupWorktree(cwd, traceId); // PRO-334 — always runs immediately
    if (typeof onDone === 'function') onDone();
  });

  return { pid: child.pid, startedAt };
}

module.exports = {
  spawnWorker,
  readTail,
  readTailRaw,
  scanStdoutForStatus,
  computeTerminalCause,
  TERMINAL_CAUSES,
  parkingBranchForCwd,
  cleanupWorktree,
  cleanWorktree,
  verifyWorktreeParked,
  CLEAN_WORKTREE_SCRIPT,
  checkNoPrAsync,
};
