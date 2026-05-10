'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { checkNoPrAsync } = require('../src/spawn');

// checkNoPrAsync is fire-and-forget (setImmediate). Tests use a Promise wrapper
// to await the deferred execution.

function runCheck(args, mockExecFileSync) {
  return new Promise((resolve) => {
    checkNoPrAsync(args, { execFileSync: mockExecFileSync });
    // setImmediate fires after the current tick; resolve after it runs
    setImmediate(resolve);
  });
}

test('checkNoPrAsync: warns when PR count is 0', async () => {
  const warnings = [];
  const mockExecFileSync = (bin, argv) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      return 'https://github.com/Dreighto/project-miru.git\n';
    }
    if (bin === 'gh' && argv.includes('pr')) {
      return '0\n';
    }
    throw new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`);
  };

  // Patch log.warn to capture calls
  require('../src/spawn');
  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-no-pr', worker: 'gemini', branch: 'feat/los-3', cwd: 'D:\\dev\\miru-w1' },
      mockExecFileSync
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
  const mockExecFileSync = (bin, argv) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      return 'https://github.com/Dreighto/project-miru.git\n';
    }
    if (bin === 'gh' && argv.includes('pr')) {
      return '1\n';
    }
    throw new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`);
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-has-pr', worker: 'gemini', branch: 'feat/los-4', cwd: 'D:\\dev\\miru-w1' },
      mockExecFileSync
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no worker_no_pr_warning');
});

test('checkNoPrAsync: silently skips when gh throws', async () => {
  const warnings = [];
  const mockExecFileSync = (bin, argv) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      return 'https://github.com/Dreighto/project-miru.git\n';
    }
    if (bin === 'gh' && argv.includes('pr')) {
      throw new Error('gh: command not found');
    }
    throw new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`);
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
      mockExecFileSync
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no worker_no_pr_warning on gh error');
});

test('checkNoPrAsync: silently skips when remote URL cannot be parsed', async () => {
  const warnings = [];
  const mockExecFileSync = (bin, argv) => {
    if (bin === 'git' && argv.includes('remote.origin.url')) {
      return 'not-a-valid-url\n';
    }
    throw new Error(`unexpected cmd: ${bin} ${argv.join(' ')}`);
  };

  const log = require('../src/log');
  const original = log.warn;
  log.warn = (event, data) => {
    warnings.push({ event, data });
  };
  try {
    await runCheck(
      { traceId: 'trace-bad-url', worker: 'gemini', branch: 'feat/los-6', cwd: 'D:\\dev\\miru-w1' },
      mockExecFileSync
    );
  } finally {
    log.warn = original;
  }

  const warning = warnings.find((w) => w.event === 'worker_no_pr_warning');
  assert.equal(warning, undefined, 'expected no warning for unparseable remote URL');
});
