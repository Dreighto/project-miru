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
