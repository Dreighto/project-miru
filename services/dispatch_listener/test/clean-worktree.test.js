'use strict';

// PRO-338: cleanWorktree must invoke clean_worktree.py via absolute path
// from REPO_ROOT (not from the worker's cwd) so multi-repo worktrees that
// don't carry tools/ on their own filesystem (LogueOS-Console, etc.) still
// get the cleanup pass.
//
// Round 2 (CodeRabbit Major on PR #160): switched from execSync(cmd) to
// execFileSync(file, args[]) to eliminate shell-injection risk on the cwd
// interpolation. Mocks now receive (file, args, opts) instead of (cmd, opts).

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

test('cleanWorktree invokes execFileSync with python + [script, --cwd, worker_path]', () => {
  // Capture the file + args so we can assert on them. execFileSync sig:
  //   (file, args, opts) — file is the binary, args is an array, opts is options.
  const calls = [];
  const mockExecFile = (file, args, _opts) => {
    calls.push({ file, args });
    return '{"cleaned":[],"skipped":[],"errors":[]}\nCLEAN: nothing to remove\n';
  };

  const result = cleanWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-pro338-1', {
    execFileSync: mockExecFile,
  });

  assert.equal(result.ok, true);
  assert.equal(calls.length, 1, 'expected exactly one execFileSync call');

  const { file, args } = calls[0];
  assert.equal(file, 'python', 'binary should be python');
  // args[0] = absolute script path; args[1] = '--cwd'; args[2] = worker cwd.
  assert.equal(args[0], CLEAN_WORKTREE_SCRIPT, 'first arg should be absolute script path');
  assert.equal(args[1], '--cwd', 'second arg should be the --cwd flag');
  assert.equal(args[2], 'D:\\dev\\LogueOS-Console-w1', 'third arg should be the worker cwd');
  // Three args, no more — defends against a future change that accidentally
  // appends extra positional args (could mask a bug).
  assert.equal(args.length, 3, `expected exactly 3 args, got ${args.length}`);
});

test('cleanWorktree does NOT pass cwd to execFileSync (script reads --cwd from argv)', () => {
  // Regression guard: previously cleanWorktree set cwd=<worker_cwd> on the
  // execSync opts, which is what made it look for tools/ inside the worker.
  // After PRO-338 the script is invoked via absolute path + --cwd, so no
  // execFileSync cwd should be set.
  let capturedOpts = null;
  const mockExecFile = (_file, _args, opts) => {
    capturedOpts = opts;
    return '';
  };

  cleanWorktree('D:\\dev\\miru-w1', 'trace-pro338-2', { execFileSync: mockExecFile });

  assert.ok(capturedOpts, 'mock should have captured opts');
  assert.equal(
    capturedOpts.cwd,
    undefined,
    `execFileSync opts.cwd should be undefined; got ${capturedOpts.cwd}`
  );
  // Other opts should still be sane.
  assert.equal(capturedOpts.timeout, 15000);
  assert.equal(capturedOpts.encoding, 'utf8');
  assert.equal(capturedOpts.windowsHide, true);
});

test('cleanWorktree returns ok:false with stderr when script fails', () => {
  const mockExecFile = () => {
    const err = new Error('Command failed: python ... clean_worktree.py');
    err.stderr = 'CLEAN_ERROR: --cwd path is not a directory: D:\\bogus';
    throw err;
  };

  const result = cleanWorktree('D:\\bogus', 'trace-pro338-fail', {
    execFileSync: mockExecFile,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /CLEAN_ERROR/);
});

test('cleanWorktree handles missing python interpreter gracefully', () => {
  // Edge case: if python isn't on PATH, execFileSync throws an ENOENT-shaped
  // error. The wrapper must surface it as ok:false rather than letting the
  // exception bubble up and crash the spawn flow.
  const mockExecFile = () => {
    const err = new Error('spawn python ENOENT');
    err.code = 'ENOENT';
    throw err;
  };

  const result = cleanWorktree('D:\\dev\\miru-w1', 'trace-pro338-no-python', {
    execFileSync: mockExecFile,
  });

  assert.equal(result.ok, false);
  assert.ok(result.error.length > 0, 'should propagate the error message');
});

test('cleanWorktree works for non-miru worktrees (the PRO-338 win condition)', () => {
  // The whole point of PRO-338: a LogueOS-Console-w1 dispatch should be able
  // to invoke cleanWorktree without the listener log filling with
  // "no such file" warnings. Sanity-check the happy path with a non-miru cwd.
  const calls = [];
  const mockExecFile = (_file, args) => {
    calls.push(args);
    return 'CLEAN: nothing to remove';
  };

  const result = cleanWorktree('D:\\dev\\LogueOS-Console-w1', 'trace-pro338-los', {
    execFileSync: mockExecFile,
  });

  assert.equal(result.ok, true);
  assert.ok(
    calls[0].includes('D:\\dev\\LogueOS-Console-w1'),
    `worker cwd should appear in argv, got: ${JSON.stringify(calls[0])}`
  );
});

test('cleanWorktree coerces non-string cwd to string for execFileSync argv', () => {
  // Defensive: spawn.js wraps cwd in String() before passing to execFileSync.
  // A future caller bug that passed e.g. a Buffer or PathLike won't crash
  // execFileSync's argv encoding (which strictly requires string args).
  const calls = [];
  const mockExecFile = (_file, args) => {
    calls.push(args);
    return '';
  };

  // Pass an object with a custom toString; without String() coercion in
  // cleanWorktree, execFileSync would reject the non-string argv entry.
  const cwdLike = { toString: () => 'D:\\dev\\miru-w2' };
  const result = cleanWorktree(cwdLike, 'trace-pro338-coerce', {
    execFileSync: mockExecFile,
  });

  assert.equal(result.ok, true);
  assert.equal(typeof calls[0][2], 'string', 'third argv entry must be a string');
  assert.equal(calls[0][2], 'D:\\dev\\miru-w2');
});
