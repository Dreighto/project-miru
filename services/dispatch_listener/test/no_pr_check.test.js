'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { checkNoPrAsync } = require('../src/spawn');

// checkNoPrAsync is fire-and-forget (setImmediate). The function now uses async
// execFile internally; the mock must call the callback to drive the logic.
// setImmediate(resolve) fires after the scheduled setImmediate in checkNoPrAsync,
// which works because mock callbacks are synchronous — all work completes before
// the resolve setImmediate fires.

function runCheck(args, mockExecFile) {
  return new Promise((resolve) => {
    checkNoPrAsync(args, { execFile: mockExecFile });
    // setImmediate fires after the current tick; resolve after it runs
    setImmediate(resolve);
  });
}

test('checkNoPrAsync: warns when PR count is 0', async () => {
  const warnings = [];
  const mockExecFile = (bin, argv, _opts, callback) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      callback(null, 'https://github.com/Dreighto/project-miru.git\n');
      return;
    }
    if (bin === 'gh' && argv.includes('pr')) {
      callback(null, '0\n');
      return;
    }
    callback(new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`));
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-no-pr', worker: 'gemini', branch: 'feat/los-3', cwd: 'D:\\dev\\miru-w1' },
      mockExecFile
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.ok(warning, 'expected worker_no_pr_warning to be logged');
  assert.equal(warning.data.branch, 'feat/los-3');
  assert.equal(warning.data.target_repo, 'Dreighto/project-miru');
  assert.equal(warning.data.worker, 'gemini');
});

test('checkNoPrAsync: no warning when PR exists', async () => {
  const warnings = [];
  const mockExecFile = (bin, argv, _opts, callback) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      callback(null, 'https://github.com/Dreighto/project-miru.git\n');
      return;
    }
    if (bin === 'gh' && argv.includes('pr')) {
      callback(null, '1\n');
      return;
    }
    callback(new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`));
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-has-pr', worker: 'gemini', branch: 'feat/los-4', cwd: 'D:\\dev\\miru-w1' },
      mockExecFile
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no worker_no_pr_warning');
});

test('checkNoPrAsync: silently skips when gh throws', async () => {
  const warnings = [];
  const mockExecFile = (bin, argv, _opts, callback) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      callback(null, 'https://github.com/Dreighto/project-miru.git\n');
      return;
    }
    if (bin === 'gh' && argv.includes('pr')) {
      callback(new Error('gh: command not found'));
      return;
    }
    callback(new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`));
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      {
        traceId: 'trace-gh-error',
        worker: 'gemini',
        branch: 'feat/los-5',
        cwd: 'D:\\dev\\miru-w1',
      },
      mockExecFile
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no worker_no_pr_warning on gh error');
});

test('checkNoPrAsync: silently skips when remote URL cannot be parsed', async () => {
  const warnings = [];
  const mockExecFile = (bin, argv, _opts, callback) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      callback(null, 'not-a-valid-url\n');
      return;
    }
    callback(new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`));
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-bad-url', worker: 'gemini', branch: 'feat/los-6', cwd: 'D:\\dev\\miru-w1' },
      mockExecFile
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no warning for unparseable remote URL');
});
