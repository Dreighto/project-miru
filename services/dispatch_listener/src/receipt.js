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

function writePlaceholderReceipt({ inboxDir, traceId, worker, startedAt, promptHash }) {
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
    // Optional. Set when the listener has computed sha256(prompt).slice(0,16)
    // so a future dispatch with a different trace_id but the same prompt
    // content can be deduped via findInFlightByPromptHash. Null is fine for
    // older receipts that predate the prompt-hash idempotency feature.
    prompt_hash: promptHash || null,
  };

  const fd = fs.openSync(target, 'wx');
  try {
    fs.writeSync(fd, JSON.stringify(placeholder, null, 2));
  } finally {
    fs.closeSync(fd);
  }
}

// Ticket B3 — prompt-hash idempotency.
// Scan the inbox for any in-flight (status='spawned') receipt whose prompt_hash
// matches and whose started_at is within `windowSeconds` of now. Returns the
// matching trace_id, or null if no match.
//
// Used by index.js after the trace_id idempotency check to catch the case
// where recovery_router (or a buggy caller) re-dispatches the same prompt
// with a fresh trace_id while the original is still running.
function findInFlightByPromptHash(inboxDir, promptHash, windowSeconds = 600) {
  if (!promptHash) return null;
  let entries;
  try {
    entries = fs.readdirSync(inboxDir);
  } catch (_e) {
    return null;
  }
  const cutoff = Date.now() - windowSeconds * 1000;
  for (const name of entries) {
    if (!name.endsWith('.result.json')) continue;
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(path.join(inboxDir, name), 'utf8'));
    } catch (_e) {
      continue;
    }
    if (!parsed || parsed.status !== 'spawned') continue;
    if (parsed.prompt_hash !== promptHash) continue;
    const startedAtMs = Date.parse(parsed.started_at || '');
    if (!Number.isFinite(startedAtMs)) continue;
    if (startedAtMs < cutoff) continue;
    return parsed.trace_id || null;
  }
  return null;
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
  findInFlightByPromptHash,
};
