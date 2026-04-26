'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const log = require('./log');
const { spec } = require('./allowlist');
const { writeTerminalReceipt } = require('./receipt');
const { writeDlqEntry } = require('./dlq');

const STDERR_TAIL_BYTES = 4096;

function readTail(filePath, maxBytes) {
  try {
    const stat = fs.statSync(filePath);
    const size = stat.size;
    if (size === 0) return '';
    const start = Math.max(0, size - maxBytes);
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      const text = buf.toString('utf8');
      const lines = text.split(/\r?\n/);
      return lines.slice(-20).join('\n');
    } finally {
      fs.closeSync(fd);
    }
  } catch (_e) {
    return '';
  }
}

function spawnWorker({ traceId, worker, promptText, timeoutSeconds, cwd, traceLogDir }) {
  const workerSpec = spec(worker);
  if (!workerSpec) {
    throw new Error(`worker ${worker} not in allowlist (defensive guard)`);
  }

  fs.mkdirSync(traceLogDir, { recursive: true });
  const stdoutPath = path.join(traceLogDir, `${traceId}.stdout.log`);
  const stderrPath = path.join(traceLogDir, `${traceId}.stderr.log`);

  const stdoutFd = fs.openSync(stdoutPath, 'a');
  const stderrFd = fs.openSync(stderrPath, 'a');

  const binary = workerSpec.binaryPath || workerSpec.binary;
  const argv = ['/c', binary, ...workerSpec.flags, promptText];

  let child;
  try {
    child = spawn('cmd', argv, {
      cwd,
      detached: true,
      windowsHide: true,
      stdio: ['ignore', stdoutFd, stderrFd],
      env: { ...process.env },
    });
  } catch (err) {
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    throw err;
  }

  fs.closeSync(stdoutFd);
  fs.closeSync(stderrFd);

  child.unref();

  const startedAt = new Date().toISOString();
  log.info('worker_spawned', {
    trace_id: traceId,
    worker,
    pid: child.pid,
    timeout_seconds: timeoutSeconds,
  });

  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    log.warn('worker_timeout_kill', {
      trace_id: traceId,
      pid: child.pid,
      timeout_seconds: timeoutSeconds,
    });
    try {
      child.kill('SIGTERM');
    } catch (_e) {
      // already exited
    }
  }, timeoutSeconds * 1000);
  if (typeof timer.unref === 'function') timer.unref();

  child.on('error', (err) => {
    clearTimeout(timer);
    log.error('worker_spawn_error', { trace_id: traceId, error: err.message });
    const completedAt = new Date().toISOString();
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);
    writeTerminalReceipt({
      traceId,
      worker,
      status: 'FAILED',
      startedAt,
      completedAt,
      exitCode: null,
      stderrTail: stderrTail || err.message,
    });
    writeDlqEntry({
      traceId,
      worker,
      promptPath: null,
      exitCode: null,
      stderrTail: stderrTail || err.message,
      errorClass: 'spawn_failed',
    });
  });

  child.on('exit', (code, signal) => {
    clearTimeout(timer);
    const completedAt = new Date().toISOString();
    const exitCode = code !== null ? code : -1;
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);

    let status;
    if (timedOut) {
      status = 'FAILED';
    } else if (exitCode === 0) {
      status = 'INCONCLUSIVE';
    } else {
      status = 'FAILED';
    }

    log.info('worker_exit', {
      trace_id: traceId,
      worker,
      pid: child.pid,
      exit_code: exitCode,
      signal,
      status,
      timed_out: timedOut,
    });

    writeTerminalReceipt({
      traceId,
      worker,
      status,
      startedAt,
      completedAt,
      exitCode,
      stderrTail,
    });

    if (status === 'FAILED') {
      const errorClass = timedOut ? 'timeout' : 'spawn_failed';
      writeDlqEntry({
        traceId,
        worker,
        promptPath: null,
        exitCode,
        stderrTail,
        errorClass,
      });
    }
  });

  return { pid: child.pid, startedAt };
}

module.exports = { spawnWorker, readTail };
