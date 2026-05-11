'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { parkingBranchForCwd, cleanupWorktree, verifyWorktreeParked } = require('../src/spawn');

// --- parkingBranchForCwd (pure function, no mock needed) ---

test('parkingBranchForCwd: maps miru-w1 to _parking_w1', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w1'), '_parking_w1');
});

test('parkingBranchForCwd: maps miru-w3 to _parking_w3', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w3'), '_parking_w3');
});

test('parkingBranchForCwd: maps miru-cursor to _parking_cursor', () => {
  assert.equal(parkingBranchForCwd('/dev/miru-cursor'), '_parking_cursor');
});

test('parkingBranchForCwd: returns null for unrecognized path', () => {
  assert.equal(parkingBranchForCwd('/some/other/path'), null);
});

test('parkingBranchForCwd: canonicalizes uppercase suffix to lowercase', () => {
  // Codex P2 finding: Windows paths are case-insensitive, but git branch names
  // are case-sensitive. Lowercasing the suffix prevents D:\dev\MIRU-W1 from
  // producing _parking_W1 (which would mismatch the actual _parking_w1 branch).
  assert.equal(parkingBranchForCwd('D:\\dev\\MIRU-W1'), '_parking_w1');
  assert.equal(parkingBranchForCwd('D:\\dev\\Miru-Cursor'), '_parking_cursor');
});

// Multi-repo support (added 2026-05-09 for LOS team).
// Non-miru worktrees use full-basename parking branches because cross-repo
// collisions are possible (LogueOS-Console-w1 vs LogueOS-Framework-w1 both
// end in -w1 but must map to different parking branches).

test('parkingBranchForCwd: maps LogueOS-Console-w1 to _parking_LogueOS-Console-w1', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console-w1'), '_parking_LogueOS-Console-w1');
});

test('parkingBranchForCwd: preserves casing for non-miru worktree names', () => {
  // Non-miru paths are NOT lowercased — the parking branch matches the worktree
  // basename exactly (the `git worktree add` command was run with the cased name).
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console-w1'), '_parking_LogueOS-Console-w1');
  assert.notEqual(
    parkingBranchForCwd('D:\\dev\\LogueOS-Console-w1'),
    '_parking_logueos-console-w1',
    'must preserve original casing for non-miru worktrees'
  );
});

test('parkingBranchForCwd: still returns null for non-worktree-shaped paths', () => {
  // Pattern guard: only match basenames that look like worktree slots (X-wN).
  assert.equal(parkingBranchForCwd('/some/random/path'), null);
  assert.equal(parkingBranchForCwd('D:\\dev\\not-a-worktree'), null);
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console'), null); // no -wN suffix
});

test('parkingBranchForCwd: handles multi-digit worker numbers (non-legacy slots)', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console-w10'), '_parking_LogueOS-Console-w10');
  // miru-w99 is NOT in the legacy allowlist — falls through to full-basename pattern
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w99'), '_parking_miru-w99');
});

test('parkingBranchForCwd: hypothetical miru-prefixed repo gets full-basename, not legacy short-form', () => {
  // Per CodeRabbit feedback on PR #157: a regex /^miru-/ would misclassify a
  // future repo named miru-tools-w1 as legacy and produce _parking_tools-w1.
  // The explicit LEGACY_MIRU_SLOT_BASENAMES allowlist prevents this — only
  // the 7 known legacy basenames get the short-form, everything else gets
  // full-basename treatment.
  assert.equal(
    parkingBranchForCwd('D:\\dev\\miru-tools-w1'),
    '_parking_miru-tools-w1',
    'miru-tools-w1 must NOT be treated as legacy short-form'
  );
  assert.notEqual(
    parkingBranchForCwd('D:\\dev\\miru-tools-w1'),
    '_parking_tools-w1',
    'misclassification regression guard'
  );
});

test('parkingBranchForCwd: only the exact 7 legacy basenames get short-form', () => {
  // Inventory check: w1..w6 + cursor are the legacy slots. Anything else
  // with miru- prefix is a new repo and gets full-basename.
  for (const slot of ['miru-w1', 'miru-w2', 'miru-w3', 'miru-w4', 'miru-w5', 'miru-w6']) {
    const expected = `_parking_${slot.slice('miru-'.length)}`;
    assert.equal(
      parkingBranchForCwd(`D:\\dev\\${slot}`),
      expected,
      `${slot} should map to ${expected}`
    );
  }
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-cursor'), '_parking_cursor');
});

// --- verifyWorktreeParked ---

test('verifyWorktreeParked: returns ok for parked clean worktree', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_w1\n';
    if (cmd.includes('status --porcelain')) return '';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-verify-ok', {
    execSync: mockExec,
  });
  assert.equal(result.ok, true);
});

test('verifyWorktreeParked: refuses when on a non-parking branch', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return 'feat/pro-330\n';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-verify-branch', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.ok(
    result.reason.includes('wrong_parking_branch'),
    `expected reason to contain 'wrong_parking_branch', got: ${result.reason}`
  );
});

test('verifyWorktreeParked: refuses when on wrong parking branch (slot mismatch)', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_w2\n'; // miru-w1 expects _parking_w1
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-wrong-slot', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.ok(
    result.reason.includes('wrong_parking_branch'),
    `expected reason to contain 'wrong_parking_branch', got: ${result.reason}`
  );
});

test('verifyWorktreeParked: refuses when worktree has untracked files', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_w1\n';
    if (cmd.includes('status --porcelain')) return '?? newfile.js\n';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-untracked', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'dirty_worktree');
});

test('verifyWorktreeParked: refuses when worktree is dirty', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_w1\n';
    if (cmd.includes('status --porcelain')) return ' M somefile.js\n';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-verify-dirty', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'dirty_worktree');
});

test('verifyWorktreeParked: refuses unrecognized worktree path', () => {
  const mockExec = () => {
    throw new Error('should not be called');
  };
  const result = verifyWorktreeParked('/some/unknown/path', 'trace-unknown', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'unrecognized_worktree');
});

test('verifyWorktreeParked: handles git command failure', () => {
  const mockExec = () => {
    throw new Error('git not found');
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-git-fail', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.ok(
    result.reason.startsWith('git_check_failed:'),
    `expected reason to start with 'git_check_failed:', got: ${result.reason}`
  );
});

// --- cleanupWorktree ---

test('cleanupWorktree: skips stash on clean state, checks out parking branch', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-123\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) return '[]';
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w1', 'trace-clean', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git checkout _parking_w1')),
    'should checkout parking branch'
  );
  assert.ok(
    calls.some((c) => c.includes('git pull --ff-only origin _parking_w1')),
    'should pull parking branch'
  );
  assert.ok(!calls.some((c) => c.includes('git stash')), 'should NOT stash on clean state');
});

test('cleanupWorktree: stashes dirty changes before parking', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return ' M dirty.js\n';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-200\n';
    if (cmd.includes('git stash push')) return 'Saved working directory and index state';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) return '[]';
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w2', 'trace-dirty', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git stash push') && c.includes('--include-untracked')),
    'should stash dirty changes with --include-untracked'
  );
  assert.ok(
    calls.some((c) => c.includes('git checkout _parking_w2')),
    'should checkout parking branch after stash'
  );
});

test('cleanupWorktree: deletes feature branch when PR is merged in our repo', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-300\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('git config --get remote.origin.url')) {
      return 'https://github.com/Dreighto/project-miru.git\n';
    }
    // Stricter merged-PR check (Codex P1, two iterations): require
    // headRefName + mergedAt + headRepositoryOwner.login matching our origin.
    if (cmd.includes('gh pr list')) {
      return JSON.stringify([
        {
          number: 99,
          headRefName: 'feat/pro-300',
          mergedAt: '2026-05-09T18:00:00Z',
          headRepositoryOwner: { login: 'Dreighto' },
        },
      ]);
    }
    if (cmd.includes('git branch -D')) return 'Deleted branch feat/pro-300';
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-merged', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git branch -D feat/pro-300')),
    'should force-delete merged branch'
  );
});

test('cleanupWorktree: does NOT delete when matched PR is from a fork (different repo owner)', () => {
  // Codex P1 round-2: even when headRefName matches AND mergedAt is non-null,
  // a fork's PR with the same branch name can be returned. The third guard:
  // headRepositoryOwner.login must equal our local origin's owner.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-305\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('git config --get remote.origin.url')) {
      return 'https://github.com/Dreighto/project-miru.git\n';
    }
    if (cmd.includes('gh pr list')) {
      // Same branch name, exact headRefName, but PR is from a fork.
      return JSON.stringify([
        {
          number: 77,
          headRefName: 'feat/pro-305',
          mergedAt: '2026-05-09T18:00:00Z',
          headRepositoryOwner: { login: 'someone-else' },
        },
      ]);
    }
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-fork-repo', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.includes('git branch -D')),
    'should NOT delete when matched PR is from a fork'
  );
});

test('cleanupWorktree: does NOT delete when origin owner cannot be resolved', () => {
  // Fail-safe: if we can't determine our own origin owner, retain the branch
  // rather than risk deletion based on an ambiguous match.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-307\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('git config --get remote.origin.url')) {
      throw new Error('no origin remote configured');
    }
    if (cmd.includes('gh pr list')) {
      return JSON.stringify([
        {
          number: 88,
          headRefName: 'feat/pro-307',
          mergedAt: '2026-05-09T18:00:00Z',
          headRepositoryOwner: { login: 'Dreighto' },
        },
      ]);
    }
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-no-origin', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.includes('git branch -D')),
    'should NOT delete when origin owner cannot be resolved (fail-safe)'
  );
});

test('cleanupWorktree: aborts cleanup if git stash fails (does not checkout)', () => {
  // Critical (CodeRabbit): if stash fails, cleanup must abort. Continuing to
  // git checkout would carry uncommitted local edits onto the parking branch,
  // recreating the cross-dispatch contamination this hook exists to prevent.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return ' M dirty.js\n';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-stashfail\n';
    if (cmd.includes('git stash push')) {
      throw new Error('fatal: git stash refused');
    }
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w5', 'trace-stash-fail', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.startsWith('git checkout')),
    'should NOT proceed to checkout when stash fails'
  );
});

test('cleanupWorktree: does NOT delete when gh pr list returns wrong headRefName (fork collision)', () => {
  // Codex P1 finding: gh pr list --head <branch> matches by branch name only.
  // In repos with forks, a PR with the same branch name from a fork could be
  // returned as "merged" — deleting our local branch based on someone else's
  // merged PR is destructive. The stricter check filters by exact headRefName.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-310\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) {
      // Same branch name, but actually a different PR (e.g. fork name collision)
      return '[{"number":42,"headRefName":"someone-else/feat/pro-310","mergedAt":"2026-05-09T18:00:00Z"}]';
    }
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-fork-collision', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.includes('git branch -D')),
    'should NOT delete branch when matched PR has different headRefName'
  );
});

test('cleanupWorktree: does NOT delete when matched PR has null mergedAt', () => {
  // Defensive: even if --state merged returns a PR, double-check mergedAt is
  // populated. If the PR record has mergedAt: null for any reason, skip deletion.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-320\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) {
      return '[{"number":50,"headRefName":"feat/pro-320","mergedAt":null}]';
    }
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-null-mergedat', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.includes('git branch -D')),
    'should NOT delete branch when mergedAt is null'
  );
});

test('cleanupWorktree: retains feature branch when PR is not merged', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-400\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) return '[]';
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w4', 'trace-unmerged', { execSync: mockExec });
  assert.ok(
    !calls.some((c) => c.includes('git branch -D')),
    'should NOT delete branch when PR is not merged'
  );
});

// --- Additional edge-case tests for parkingBranchForCwd (PR #157 changes) ---

// POSIX path handling for multi-repo worktrees.
// path.win32.basename is used so the function works on both POSIX and Windows.
test('parkingBranchForCwd: POSIX path with LogueOS worktree produces full-basename', () => {
  assert.equal(parkingBranchForCwd('/home/user/LogueOS-Console-w1'), '_parking_LogueOS-Console-w1');
});

test('parkingBranchForCwd: POSIX path with miru-w1 (legacy) still produces short-form', () => {
  // Even on POSIX, path.win32.basename extracts the last segment correctly.
  assert.equal(parkingBranchForCwd('/home/user/miru-w1'), '_parking_w1');
});

// miru-w7, miru-w8, miru-w9 are NOT in the legacy allowlist — they fall
// through to the full-basename pattern and get _parking_miru-w7 etc.
test('parkingBranchForCwd: miru-w7 is not in legacy allowlist → full-basename', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w7'), '_parking_miru-w7');
  assert.notEqual(
    parkingBranchForCwd('D:\\dev\\miru-w7'),
    '_parking_w7',
    'miru-w7 is not a legacy slot and must not get the short-form'
  );
});

test('parkingBranchForCwd: miru-w8 and miru-w9 are not in legacy allowlist', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w8'), '_parking_miru-w8');
  assert.equal(parkingBranchForCwd('D:\\dev\\miru-w9'), '_parking_miru-w9');
});

// Worktree basenames with dots and underscores are valid per the pattern
// /^[A-Za-z0-9._-]+-w\d+$/i — verify the pattern accepts them.
test('parkingBranchForCwd: basename with dots is accepted by full-basename pattern', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\My.Project-w1'), '_parking_My.Project-w1');
});

test('parkingBranchForCwd: basename with underscores is accepted by full-basename pattern', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\My_Project-w2'), '_parking_My_Project-w2');
});

// Boundary: bare "-w1" basename returns null. The regex
// /^[A-Za-z0-9._-]+-w\d+$/i requires SOMETHING before the "-wN" suffix; the
// chars consumed by [A-Za-z0-9._-]+ can't double as the "-w" of the suffix,
// so "-w1" fails the pattern (no characters left for the prefix after
// reserving "-w1" for the suffix portion). Asserting the concrete `null`
// outcome so a regression that loosened the prefix requirement (e.g.
// allowing zero-length prefix) would be caught.
// Per CodeRabbit feedback on PR #157: the previous OR assertion accepted
// two outcomes — non-deterministic and useless as a regression guard.
test('parkingBranchForCwd: bare "-w1" basename (no real prefix) returns null', () => {
  assert.strictEqual(parkingBranchForCwd('D:\\dev\\-w1'), null);
});

// Numbers-only prefix with -wN suffix — valid per regex.
test('parkingBranchForCwd: numeric prefix basename with -wN is accepted', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\123-w3'), '_parking_123-w3');
});

// Regression: the old regex /^miru-(.+)$/i would match ANY miru- prefix and
// use the capture group for the parking suffix. The allowlist approach means
// the boundary is now a compile-time constant. Confirm all 7 entries individually.
test('parkingBranchForCwd: all 7 legacy basenames map to expected short-form parking branch', () => {
  const legacyMap = {
    'miru-w1': '_parking_w1',
    'miru-w2': '_parking_w2',
    'miru-w3': '_parking_w3',
    'miru-w4': '_parking_w4',
    'miru-w5': '_parking_w5',
    'miru-w6': '_parking_w6',
    'miru-cursor': '_parking_cursor',
  };
  for (const [slot, expected] of Object.entries(legacyMap)) {
    assert.equal(
      parkingBranchForCwd(`/dev/${slot}`),
      expected,
      `${slot} (POSIX path) should map to ${expected}`
    );
    assert.equal(
      parkingBranchForCwd(`D:\\dev\\${slot}`),
      expected,
      `${slot} (Windows path) should map to ${expected}`
    );
  }
});

// Uppercase legacy slots canonicalize to lowercase parking branches.
test('parkingBranchForCwd: MIRU-W6 (all-caps) maps to _parking_w6', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\MIRU-W6'), '_parking_w6');
});

test('parkingBranchForCwd: Miru-W4 (mixed-case) maps to _parking_w4', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\Miru-W4'), '_parking_w4');
});

// Non-legacy miru- names with uppercase should NOT get short-form.
test('parkingBranchForCwd: MIRU-TOOLS-W1 (all-caps non-legacy) gets full-basename', () => {
  // lowercased to "miru-tools-w1" which is not in LEGACY_MIRU_SLOT_BASENAMES,
  // then checked against the pattern with original casing.
  assert.equal(parkingBranchForCwd('D:\\dev\\MIRU-TOOLS-W1'), '_parking_MIRU-TOOLS-W1');
  assert.notEqual(
    parkingBranchForCwd('D:\\dev\\MIRU-TOOLS-W1'),
    '_parking_TOOLS-W1',
    'non-legacy miru- prefix must not produce legacy short-form'
  );
});

// Paths with only a repo name and no -wN suffix remain null (pattern guard).
test('parkingBranchForCwd: LogueOS-Console (no worker suffix) returns null', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console'), null);
});

// A path of just a number like "w1" (no dash) returns null.
test('parkingBranchForCwd: bare "w1" (no leading repo name) returns null', () => {
  assert.equal(parkingBranchForCwd('D:\\dev\\w1'), null);
});

// Cross-repo collision guard: two distinct repos that both have -w1 worktrees
// must produce different parking branches.
test('parkingBranchForCwd: different repos with same worker suffix produce distinct parking branches', () => {
  const consoleResult = parkingBranchForCwd('D:\\dev\\LogueOS-Console-w1');
  const frameworkResult = parkingBranchForCwd('D:\\dev\\LogueOS-Framework-w1');
  assert.notEqual(
    consoleResult,
    frameworkResult,
    'LogueOS-Console-w1 and LogueOS-Framework-w1 must produce distinct parking branches'
  );
  assert.equal(consoleResult, '_parking_LogueOS-Console-w1');
  assert.equal(frameworkResult, '_parking_LogueOS-Framework-w1');
});

// --- LOS-14 layout (D:\dev\worktrees\<repo>\w<N>) ---

test('parkingBranchForCwd: LOS-14 layout LogueOS-Orchestrator\\w1 → _parking_LogueOS-Orchestrator-w1', () => {
  assert.equal(
    parkingBranchForCwd('D:\\dev\\worktrees\\LogueOS-Orchestrator\\w1'),
    '_parking_LogueOS-Orchestrator-w1'
  );
});

test('parkingBranchForCwd: LOS-14 layout multi-digit slot index', () => {
  assert.equal(
    parkingBranchForCwd('D:\\dev\\worktrees\\project-miru\\w12'),
    '_parking_project-miru-w12'
  );
});

test('parkingBranchForCwd: LOS-14 layout preserves repo casing (case-sensitive parent)', () => {
  assert.equal(
    parkingBranchForCwd('D:\\dev\\worktrees\\LogueOS-Console\\w3'),
    '_parking_LogueOS-Console-w3'
  );
});

test('parkingBranchForCwd: LOS-14 layout grandparent must be exactly "worktrees" (case-insensitive)', () => {
  // Mixed case "Worktrees" should still match — the comparison is case-insensitive.
  assert.equal(
    parkingBranchForCwd('D:\\dev\\Worktrees\\LogueOS-Console\\w1'),
    '_parking_LogueOS-Console-w1'
  );
});

test('parkingBranchForCwd: LOS-14 anchor rejects bare D:\\dev\\<repo>\\w1 without worktrees parent', () => {
  // Without the "worktrees" grandparent marker, a path like
  // D:\dev\foo\w1 must NOT match LOS-14. This is the anchor that prevents
  // accidental matches on unrelated paths with parent dirs that happen to
  // look repo-shaped.
  assert.equal(parkingBranchForCwd('D:\\dev\\foo\\w1'), null);
});

test('parkingBranchForCwd: LOS-14 layout rejects bare w1 with non-worktrees parent', () => {
  // Same idea but with a parent that IS a real repo name. Without the
  // worktrees anchor, the function still returns null.
  assert.equal(parkingBranchForCwd('D:\\dev\\LogueOS-Console\\w1'), null);
});

test('parkingBranchForCwd: LOS-14 anchor follows LOGUEOS_WORKTREE_BASE override (CR R1)', () => {
  // CR R1 on PR #187: the LOS-14 anchor used to be hardcoded to 'worktrees',
  // so operators who set LOGUEOS_WORKTREE_BASE to a non-default path
  // (e.g. D:\custom\disp-pool) would silently get null from
  // parkingBranchForCwd because the anchor mismatched. Now the anchor
  // derives from the basename of LOGUEOS_WORKTREE_BASE at module load.
  //
  // To exercise the override, we reload spawn.js with a different env
  // value. Done via require-cache deletion + restore so the test is
  // self-contained.
  const SPAWN_PATH = require.resolve('../src/spawn');
  const originalBase = process.env.LOGUEOS_WORKTREE_BASE;
  const originalCache = require.cache[SPAWN_PATH];
  delete require.cache[SPAWN_PATH];
  try {
    process.env.LOGUEOS_WORKTREE_BASE = 'D:\\custom\\disp-pool';
    // Re-require with the override in effect.
    const { parkingBranchForCwd: reloaded } = require('../src/spawn');
    // Custom path matches the new anchor:
    assert.equal(
      reloaded('D:\\custom\\disp-pool\\LogueOS-Console\\w1'),
      '_parking_LogueOS-Console-w1',
      'custom pool root should produce correct parking branch'
    );
    // Default 'worktrees' path NO LONGER matches because the anchor is
    // now 'disp-pool', not 'worktrees':
    assert.equal(
      reloaded('D:\\dev\\worktrees\\LogueOS-Console\\w1'),
      null,
      'with custom anchor, the default worktrees path should NOT match'
    );
  } finally {
    // Restore original env + module cache so subsequent tests use the
    // default anchor.
    if (originalBase === undefined) {
      delete process.env.LOGUEOS_WORKTREE_BASE;
    } else {
      process.env.LOGUEOS_WORKTREE_BASE = originalBase;
    }
    delete require.cache[SPAWN_PATH];
    if (originalCache) {
      require.cache[SPAWN_PATH] = originalCache;
    } else {
      // Re-require with the original env so the cache is repopulated with
      // the default-anchor module that the rest of the suite expects.
      require('../src/spawn');
    }
  }
});

// --- verifyWorktreeParked with multi-repo (non-miru) worktrees ---

test('verifyWorktreeParked: accepts LogueOS-Console-w1 on its expected parking branch', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_LogueOS-Console-w1\n';
    if (cmd.includes('status --porcelain')) return '';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\LogueOS-Console-w1', 'trace-los-verify-ok', {
    execSync: mockExec,
  });
  assert.equal(result.ok, true);
});

test('verifyWorktreeParked: refuses LogueOS-Console-w1 on wrong parking branch', () => {
  // If a multi-repo worktree has a lowercase branch name but the expected
  // branch preserves the original casing, verifyWorktreeParked should refuse.
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_logueos-console-w1\n'; // wrong casing
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\LogueOS-Console-w1', 'trace-los-wrong-branch', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.ok(
    result.reason.includes('wrong_parking_branch'),
    `expected 'wrong_parking_branch', got: ${result.reason}`
  );
});

test('verifyWorktreeParked: refuses LogueOS-Console-w1 when worktree is dirty', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return '_parking_LogueOS-Console-w1\n';
    if (cmd.includes('status --porcelain')) return 'M  src/index.js\n';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\LogueOS-Console-w1', 'trace-los-dirty', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'dirty_worktree');
});

test('verifyWorktreeParked: treats LogueOS path as unrecognized when it has no -wN suffix', () => {
  // A non-worktree-shaped path must return unrecognized_worktree without
  // calling git at all.
  const mockExec = () => {
    throw new Error('should not be called for unrecognized paths');
  };
  const result = verifyWorktreeParked('D:\\dev\\LogueOS-Console', 'trace-los-no-wn', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'unrecognized_worktree');
});

// --- cleanupWorktree with multi-repo (non-miru) worktrees ---

test('cleanupWorktree: checks out full-basename parking branch for LogueOS worktree', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/los-123\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) return '[]';
    if (cmd.includes('git config --get remote.origin.url')) return '';
    return '';
  };
  cleanupWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-los-cleanup', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git checkout _parking_LogueOS-Console-w1')),
    'should checkout the full-basename parking branch for multi-repo worktrees'
  );
  assert.ok(
    calls.some((c) => c.includes('git pull --ff-only origin _parking_LogueOS-Console-w1')),
    'should pull the full-basename parking branch for multi-repo worktrees'
  );
});

test('cleanupWorktree: skips for non-worktree-shaped LogueOS path (no -wN suffix)', () => {
  // cleanupWorktree internally calls parkingBranchForCwd; if it returns null,
  // cleanup should skip entirely without calling any git commands.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    return '';
  };
  cleanupWorktree('D:\\dev\\LogueOS-Console', 'trace-los-no-wn-cleanup', { execSync: mockExec });
  assert.equal(calls.length, 0, 'should not call any git commands for unrecognized paths');
});

test('cleanupWorktree: deletes merged feature branch for multi-repo worktree', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/los-500\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('git config --get remote.origin.url')) {
      return 'https://github.com/LOS-Org/LogueOS-Console.git\n';
    }
    if (cmd.includes('gh pr list')) {
      return JSON.stringify([
        {
          number: 55,
          headRefName: 'feat/los-500',
          mergedAt: '2026-05-09T20:00:00Z',
          headRepositoryOwner: { login: 'LOS-Org' },
        },
      ]);
    }
    if (cmd.includes('git branch -D')) return 'Deleted branch feat/los-500';
    return '';
  };
  cleanupWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-los-merged', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git branch -D feat/los-500')),
    'should force-delete merged feature branch for multi-repo worktrees'
  );
});
