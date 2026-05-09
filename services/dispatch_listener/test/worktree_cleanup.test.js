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

test('verifyWorktreeParked: refuses when not on parking branch', () => {
  const mockExec = (cmd) => {
    if (cmd.includes('rev-parse')) return 'feat/pro-330\n';
    throw new Error(`unexpected cmd: ${cmd}`);
  };
  const result = verifyWorktreeParked('D:\\dev\\miru-w1', 'trace-verify-branch', {
    execSync: mockExec,
  });
  assert.equal(result.ok, false);
  assert.ok(
    result.reason.includes('not_on_parking_branch'),
    `expected reason to contain 'not_on_parking_branch', got: ${result.reason}`
  );
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
    calls.some((c) => c.includes('git stash push')),
    'should stash dirty changes'
  );
  assert.ok(
    calls.some((c) => c.includes('git checkout _parking_w2')),
    'should checkout parking branch after stash'
  );
});

test('cleanupWorktree: deletes feature branch when PR is merged', () => {
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    if (cmd.includes('status --porcelain')) return '';
    if (cmd.includes('rev-parse --abbrev-ref')) return 'feat/pro-300\n';
    if (cmd.startsWith('git checkout')) return '';
    if (cmd.startsWith('git pull')) return '';
    if (cmd.includes('gh pr list')) return '[{"number":99}]';
    if (cmd.includes('git branch -D')) return 'Deleted branch feat/pro-300';
    return '';
  };
  cleanupWorktree('D:\\dev\\miru-w3', 'trace-merged', { execSync: mockExec });
  assert.ok(
    calls.some((c) => c.includes('git branch -D feat/pro-300')),
    'should force-delete merged branch'
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
