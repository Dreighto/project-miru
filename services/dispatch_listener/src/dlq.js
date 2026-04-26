'use strict';

const fs = require('fs');

const SCHEMA_VERSION = 'v1';
const ERROR_CLASSES = new Set([
  'binary_missing',
  'spawn_failed',
  'timeout',
  'hmac_reject',
  'allowlist_reject',
  'bad_request',
  'listener_restarted',
]);

function writeDlqEntry({ traceId, worker, promptPath, exitCode, stderrTail, errorClass }) {
  const dlqPath = process.env.DISPATCH_DLQ_PATH;
  if (!dlqPath) {
    throw new Error('DISPATCH_DLQ_PATH not set in process env');
  }
  if (!ERROR_CLASSES.has(errorClass)) {
    throw new Error(`unknown error_class "${errorClass}" — refusing to write DLQ row`);
  }

  const row = {
    schema_version: SCHEMA_VERSION,
    trace_id: traceId == null ? null : traceId,
    worker: worker == null ? null : worker,
    prompt_path: promptPath == null ? null : promptPath,
    exit_code: exitCode == null ? null : exitCode,
    stderr_tail: stderrTail == null ? '' : stderrTail,
    error_class: errorClass,
    timestamp: new Date().toISOString(),
  };
  fs.appendFileSync(dlqPath, JSON.stringify(row) + '\n');
}

module.exports = { writeDlqEntry, ERROR_CLASSES };
