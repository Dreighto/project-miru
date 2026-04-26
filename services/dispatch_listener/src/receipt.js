'use strict';

const fs = require('fs');
const path = require('path');

const SCHEMA_VERSION = 'v1';
const TERMINAL_STATES = new Set(['CONFIRMED_WORKING', 'INCONCLUSIVE', 'FAILED']);

function receiptPath(inboxDir, traceId) {
  return path.join(inboxDir, `${traceId}.result.json`);
}

function tryReadReceipt(inboxDir, traceId) {
  const p = receiptPath(inboxDir, traceId);
  try {
    const txt = fs.readFileSync(p, 'utf8');
    return JSON.parse(txt);
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
}

function writePlaceholderReceipt({ inboxDir, traceId, worker, startedAt }) {
  const target = receiptPath(inboxDir, traceId);
  const placeholder = {
    schema_version: SCHEMA_VERSION,
    trace_id: traceId,
    worker,
    status: 'spawned',
    summary: '',
    files_touched: [],
    exit_code: null,
    stderr_tail: '',
    started_at: startedAt,
    completed_at: null,
  };

  const fd = fs.openSync(target, 'wx');
  try {
    fs.writeSync(fd, JSON.stringify(placeholder, null, 2));
  } finally {
    fs.closeSync(fd);
  }
}

function writeTerminalReceipt({
  traceId,
  worker,
  status,
  startedAt,
  completedAt,
  exitCode,
  stderrTail,
}) {
  if (!TERMINAL_STATES.has(status)) {
    throw new Error(`refusing to write non-terminal status "${status}" via writeTerminalReceipt`);
  }
  const inboxDir = process.env.DISPATCH_INBOX_DIR;
  if (!inboxDir) {
    throw new Error('DISPATCH_INBOX_DIR not set in process env');
  }

  const target = receiptPath(inboxDir, traceId);
  const tmp = `${target}.tmp`;
  const final = {
    schema_version: SCHEMA_VERSION,
    trace_id: traceId,
    worker,
    status,
    summary: '',
    files_touched: [],
    exit_code: exitCode,
    stderr_tail: stderrTail || '',
    started_at: startedAt,
    completed_at: completedAt,
  };
  fs.writeFileSync(tmp, JSON.stringify(final, null, 2));
  fs.renameSync(tmp, target);
}

module.exports = {
  SCHEMA_VERSION,
  TERMINAL_STATES,
  receiptPath,
  tryReadReceipt,
  writePlaceholderReceipt,
  writeTerminalReceipt,
};
