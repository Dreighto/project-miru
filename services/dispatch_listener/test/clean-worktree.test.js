'use strict';

// PRO-338: cleanWorktree must invoke clean_worktree.py via absolute path
// from REPO_ROOT (not from the worker's cwd) so multi-repo worktrees that
// don't carry tools/ on their own filesystem (LogueOS-Console, etc.) still
// get the cleanup pass. These tests inject a mock execSync to verify the
// command shape without actually running python.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { cleanWorktree, CLEAN_WORKTREE_SCRIPT } = require('../src/spawn');

test('CLEAN_WORKTREE_SCRIPT resolves to project-miru/tools/clean_worktree.py', () => {
  // Sanity: the export should be an absolute path that ends with the expected
  // script. If the relative-to-__dirname computation in spawn.js drifts, this
  // catches it before the listener log fills with "no such file" warnings.
  assert.ok(path.isAbsolute(CLEAN_WORKTREE_SCRIPT), 'must be absolute');
  assert.match(
    CLEAN_WORKTREE_SCRIPT,
    /[\\/]tools[\\/]clean_worktree\.py$/,
    `expected to end with tools/clean_worktree.py, got ${CLEAN_WORKTREE_SCRIPT}`
  );
});

test('cleanWorktree invokes python with --cwd <worker_path> using absolute script path', () => {
  // Capture the command string so we can assert on it.
  const calls = [];
  const mockExec = (cmd, _opts) => {
    calls.push({ cmd });
    return '{"cleaned":[],"skipped":[],"errors":[]}\nCLEAN: nothing to remove\n';
  };

  const result = cleanWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-pro338-1', {
    execSync: mockExec,
  });

  assert.equal(result.ok, true);
  assert.equal(calls.length, 1, 'expected exactly one execSync call');

  const cmd = calls[0].cmd;
  // The command must reference the absolute script path, not "tools/clean_worktree.py".
  assert.ok(
    cmd.includes(CLEAN_WORKTREE_SCRIPT),
    `expected command to include absolute script path, got: ${cmd}`
  );
  // The command must pass --cwd with the worker's worktree.
  assert.ok(
    cmd.includes('--cwd') && cmd.includes('D:\\dev\\LogueOS-Console-w1'),
    `expected --cwd <worker_path> in command, got: ${cmd}`
  );
});

test('cleanWorktree does NOT pass cwd to execSync (script reads --cwd from argv)', () => {
  // Regression guard: previously cleanWorktree set cwd=<worker_cwd> on the
  // execSync opts, which is what made it look for tools/ inside the worker.
  // After PRO-338 the script is invoked via absolute path, so no execSync
  // cwd should be set — let exec resolve `python` from PATH wherever it is.
  let capturedOpts = null;
  const mockExec = (_cmd, opts) => {
    capturedOpts = opts;
    return '';
  };

  cleanWorktree('D:\\dev\\miru-w1', 'trace-pro338-2', { execSync: mockExec });

  assert.ok(capturedOpts, 'mock should have captured opts');
  assert.equal(
    capturedOpts.cwd,
    undefined,
    `execSync opts.cwd should be undefined; got ${capturedOpts.cwd}`
  );
  // Other opts should still be sane.
  assert.equal(capturedOpts.timeout, 15000);
  assert.equal(capturedOpts.encoding, 'utf8');
  assert.equal(capturedOpts.windowsHide, true);
});

test('cleanWorktree returns ok:false with stderr when script fails', () => {
  const mockExec = () => {
    const err = new Error('Command failed: python ... clean_worktree.py');
    err.stderr = 'CLEAN_ERROR: --cwd path is not a directory: D:\\bogus';
    throw err;
  };

  const result = cleanWorktree('D:\\bogus', 'trace-pro338-fail', { execSync: mockExec });

  assert.equal(result.ok, false);
  assert.match(result.error, /CLEAN_ERROR/);
});

test('cleanWorktree handles missing python interpreter gracefully', () => {
  // Edge case: if python isn't on PATH at all, execSync throws an ENOENT-shaped
  // error. The wrapper must surface it as ok:false rather than letting the
  // exception bubble up and crash the spawn flow.
  const mockExec = () => {
    const err = new Error('spawn python ENOENT');
    err.code = 'ENOENT';
    throw err;
  };

  const result = cleanWorktree('D:\\dev\\miru-w1', 'trace-pro338-no-python', {
    execSync: mockExec,
  });

  assert.equal(result.ok, false);
  assert.ok(result.error.length > 0, 'should propagate the error message');
});

test('cleanWorktree works for non-miru worktrees (the PRO-338 win condition)', () => {
  // The whole point of PRO-338: a LogueOS-Console-w1 dispatch should be able
  // to invoke cleanWorktree without the listener log filling with
  // "no such file" warnings. Sanity-check the happy path with a non-miru cwd.
  const calls = [];
  const mockExec = (cmd) => {
    calls.push(cmd);
    return 'CLEAN: nothing to remove';
  };

  const result = cleanWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-pro338-los', {
    execSync: mockExec,
  });

  assert.equal(result.ok, true);
  assert.ok(
    calls[0].includes('LogueOS-Console-w1'),
    `worker cwd should appear in --cwd arg, got: ${calls[0]}`
  );
});
