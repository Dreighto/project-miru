'use strict';

const fs = require('fs');
const path = require('path');
const { spawn, execSync } = require('child_process');

const log = require('./log');
const { spec } = require('./allowlist');
const { writeTerminalReceipt } = require('./receipt');
const { writeDlqEntry } = require('./dlq');

const STDERR_TAIL_BYTES = 4096;
const STDOUT_SCAN_BYTES = 8192;

// Scan stdout tail for terminal status markers emitted by workers.
// Workers print "STATUS: CONFIRMED WORKING" or "CONFIRMED_WORKING" in stdout.
// Returns 'CONFIRMED_WORKING' if found, null otherwise.
function readTailRaw(filePath, maxBytes) {
  try {
    const stat = fs.statSync(filePath);
    const size = stat.size;
    if (size === 0) return '';
    const start = Math.max(0, size - maxBytes);
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      return buf.toString('utf8');
    } finally {
      fs.closeSync(fd);
    }
  } catch (_e) {
    return '';
  }
}

function scanStdoutForStatus(stdoutTail) {
  if (!stdoutTail) return null;
  if (/STATUS:\s*\bCONFIRMED[\s_]WORKING\b/i.test(stdoutTail)) return 'CONFIRMED_WORKING';
  if (/\bCONFIRMED_WORKING\b/i.test(stdoutTail)) return 'CONFIRMED_WORKING';
  return null;
}

function killProcessTree(child) {
  if (!child || !child.pid) return;

  if (process.platform === 'win32') {
    try {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      killer.on('error', () => {
        try {
          child.kill('SIGTERM');
        } catch (_e) {
          // already exited
        }
      });
      killer.unref();
      return;
    } catch (_e) {
      // fall through to the direct child fallback below
    }
  }

  try {
    if (process.platform !== 'win32') {
      process.kill(-child.pid, 'SIGTERM');
    } else {
      child.kill('SIGTERM');
    }
  } catch (_e) {
    // already exited
  }
}

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

// PRO-316: run `python tools/clean_worktree.py` before spawning to remove
// known-safe gitignored artifacts (test-results/, playwright-report/, etc.)
// that would fail the worker's worktree cleanliness gate.
function cleanWorktree(cwd, traceId) {
  try {
    const output = execSync('python tools/clean_worktree.py', {
      cwd,
      timeout: 15000,
      encoding: 'utf8',
      windowsHide: true,
    });
    log.info('worktree_auto_clean', {
      trace_id: traceId,
      output: (output || '').trim().slice(0, 500),
    });
    return { ok: true };
  } catch (err) {
    const stderr = String(err.stderr || err.message || '').trim();
    log.warn('worktree_auto_clean_failed', { trace_id: traceId, stderr: stderr.slice(0, 500) });
    return { ok: false, error: stderr.slice(0, 300) };
  }
}

// PRO-233: run `cmd /c <binary> --version` before the real spawn to confirm
// the binary is reachable in this process's environment. Synchronous on
// purpose — the caller is already committed to spawning; this probe adds at
// most 10s and the result goes straight into the structured log.
function probeWorkerBinary(binary, cwd) {
  try {
    const output = execSync(`cmd /c "${binary}" --version`, {
      cwd,
      timeout: 10000,
      encoding: 'utf8',
      windowsHide: true,
      env: { ...process.env },
    });
    return { ok: true, output: (output || '').trim().slice(0, 300) };
  } catch (err) {
    const combined = String(err.stderr || err.stdout || err.message || '').trim();
    return {
      ok: false,
      exit_code: err.status != null ? err.status : null,
      signal: err.signal || null,
      output: combined.slice(0, 300),
    };
  }
}

function spawnWorker({
  traceId,
  worker,
  promptText,
  timeoutSeconds,
  useApiKey = false,
  model = null,
  thinkingLevel = null,
  toolProfile = 'standard_worker',
  cwd,
  traceLogDir,
  onDone,
}) {
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

  // PRO-233: pre-spawn diagnostics — binary path, cwd, PATH snapshot, version probe.
  const pathDirs = (process.env.PATH || '').split(path.delimiter);
  log.info('spawn_pre_diagnostic', {
    trace_id: traceId,
    worker,
    binary_path: binary,
    binary_raw: workerSpec.binary,
    flags: workerSpec.flags,
    cwd,
    path_dirs_count: pathDirs.length,
    path_first_10: pathDirs.slice(0, 10).join(path.delimiter),
    appdata: process.env.APPDATA || null,
    localappdata: process.env.LOCALAPPDATA || null,
  });

  const probe = probeWorkerBinary(binary, cwd);
  log.info('spawn_version_probe', { trace_id: traceId, binary_path: binary, ...probe });

  // PRO-316: auto-clean gitignored artifacts before worker starts.
  cleanWorktree(cwd, traceId);

  // Prompt is written to a temp file and the file is opened as a read-only
  // file descriptor passed directly to the child as stdio[0]. This sidesteps
  // every cmd-level escaping concern:
  //   * The prompt content never appears in argv, so cmd's %VAR% expansion
  //     and newline-as-terminator behavior can't mutate or truncate it (see
  //     PR #22 Bugbot finding "Prompt passed unescaped to cmd.exe argv").
  //   * No `<` redirect inside a cmd command string, so node's argv escaping
  //     can't mangle it (we hit "The filename, directory name, or volume
  //     label syntax is incorrect" trying to use `cmd /c "<bin> <args> <
  //     <file>"` directly).
  //   * No reliance on `child.stdin.pipe` writes, which empirically don't
  //     reach the worker when `detached: true` is set on Windows.
  // The temp file is unlinked on exit/error.
  const promptFile = path.join(traceLogDir, `${traceId}.prompt.tmp`);
  fs.writeFileSync(promptFile, promptText, 'utf8');

  let promptFd;
  try {
    promptFd = fs.openSync(promptFile, 'r');
  } catch (err) {
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    throw err;
  }

  // Auth: workers use CLAUDE_CODE_OAUTH_TOKEN — a portable OAuth token generated
  // via `claude setup-token` that is NOT DPAPI-encrypted, so it works in Session 0
  // scheduled tasks. ANTHROPIC_API_KEY is always stripped to prevent accidental
  // API billing. MIRU_ROUTING_KEY remains available for Claude Chat's own session.
  const childEnv = { ...process.env };
  delete childEnv.ANTHROPIC_API_KEY;

  // Token isolation: strip all GitHub auth vars from child env before setting
  // the restricted worker token. This covers both the new ROOM_TOKEN_* names
  // and the legacy GITHUB_TOKEN_* names that may still exist during rotation.
  // Any inherited GH_TOKEN is also cleared so we fully control what the worker
  // gets — no accidental operator-level access via an inherited value.
  delete childEnv.ROOM_TOKEN_OPERATOR;
  delete childEnv.GITHUB_TOKEN_WRITE;
  delete childEnv.GITHUB_TOKEN_READ;
  delete childEnv.GH_TOKEN;

  // Set GH_TOKEN so gh CLI authenticates automatically inside the worker.
  // Prefer ROOM_TOKEN_WORKER; fall back to legacy read token during rotation.
  const workerGhToken = process.env.ROOM_TOKEN_WORKER || process.env.GITHUB_TOKEN_READ;
  if (workerGhToken) {
    childEnv.GH_TOKEN = workerGhToken;
  }

  // Gemini CLI trust gate: --skip-trust flag is unreliable in headless
  // environments on some versions. Injecting the env var is the guaranteed
  // alternative per the CLI docs and the error message itself.
  if (worker === 'gemini') {
    childEnv.GEMINI_CLI_TRUST_WORKSPACE = 'true';
  }

  // Expose trace_id to the worker so emit_heartbeat / emit_completion can
  // include it in their JSONL rows. Without this, log correlation across
  // dispatch → worker → marker → bridge is impossible.
  childEnv.MIRU_TRACE_ID = traceId;
  childEnv.MIRU_TOOL_PROFILE = toolProfile;

  log.info('spawn_auth_debug', {
    trace_id: traceId,
    has_api_key: !!childEnv.ANTHROPIC_API_KEY,
    has_routing_key: !!childEnv.MIRU_ROUTING_KEY,
    has_oauth_token: !!childEnv.CLAUDE_CODE_OAUTH_TOKEN,
  });

  // PRO-265: per-dispatch model and effort flags (claude-code only).
  // thinking_level "extended" maps to --effort max (the Claude CLI effort scale is
  // low/medium/high/xhigh/max; "extended" is the semantic alias used by Claude Chat).
  // For gemini the override flags are not yet wired; values are
  // logged but ignored so callers don't get a silent no-op without a trace.
  const EFFORT_MAP = {
    extended: 'max',
    low: 'low',
    medium: 'medium',
    high: 'high',
    xhigh: 'xhigh',
    max: 'max',
  };
  const extraFlags = [];
  if (worker === 'claude-code') {
    if (model) extraFlags.push('--model', model);
    if (thinkingLevel && EFFORT_MAP[thinkingLevel]) {
      extraFlags.push('--effort', EFFORT_MAP[thinkingLevel]);
    }
    const mcpConfigPath = path.join(cwd, '.mcp.json');
    if (fs.existsSync(mcpConfigPath)) {
      extraFlags.push('--mcp-config', mcpConfigPath, '--strict-mcp-config');
    }
  }

  log.info('spawn_flags', {
    trace_id: traceId,
    worker,
    base_flags: workerSpec.flags,
    extra_flags: extraFlags,
    model_requested: model,
    thinking_level_requested: thinkingLevel,
  });

  let child;
  try {
    // detached:true was empirically incompatible with stdio file fds on this
    // Windows setup -- claude exited 1 with empty stdout/stderr no matter how
    // stdin was wired (pipe, file fd, batch wrapper with `< redirect`).
    // Dropping detached:true makes the worker a normal child of the listener:
    // listener crash will kill mid-flight workers (acceptable Phase 1 behavior
    // per the README, the orphan sweep already handles that case at startup).
    //
    // windowsHide:true sets CREATE_NO_WINDOW on Windows. This is correct for
    // claude-code (no console needed) but breaks gemini: gemini's startup
    // sequence runs conpty_console_list_agent.js which calls AttachConsole().
    // CREATE_NO_WINDOW prevents any console from being allocated, so
    // AttachConsole() fails and gemini exits immediately.
    //
    // The dispatch_listener's startup script (windows/start_dispatch_listener.ps1)
    // hides the PowerShell console window via ShowWindow(hwnd, 0) but leaves the
    // console allocated. Without windowsHide, gemini inherits that hidden console,
    // AttachConsole() succeeds, and no new window appears on the user's desktop.
    const spawnOpts = {
      cwd,
      stdio: [promptFd, stdoutFd, stderrFd],
      env: childEnv,
    };
    if (worker !== 'gemini') {
      spawnOpts.windowsHide = true;
    }
    child = spawn('cmd', ['/c', binary, ...workerSpec.flags, ...extraFlags], spawnOpts);
  } catch (err) {
    fs.closeSync(promptFd);
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    throw err;
  }

  // Parent closes its copy of the fds; the child has inherited them.
  fs.closeSync(promptFd);
  fs.closeSync(stdoutFd);
  fs.closeSync(stderrFd);

  const startedAt = new Date().toISOString();
  log.info('worker_spawned', {
    trace_id: traceId,
    worker,
    pid: child.pid,
    pid_defined: child.pid != null,
    timeout_seconds: timeoutSeconds,
    model: model || null,
    thinking_level: thinkingLevel || null,
  });

  if (child.pid == null) {
    log.error('spawn_pid_undefined', {
      trace_id: traceId,
      worker,
      binary_path: binary,
      cwd,
    });
  }

  // Per Node child_process semantics, `error` and `exit` can BOTH fire for the
  // same spawn. Without a guard, both handlers would write a terminal receipt
  // and a DLQ row -- producing duplicate rows for a single trace_id and an
  // overwriting receipt rename. The `finalized` flag guarantees exactly one
  // terminal receipt + at most one DLQ row per spawn even when both events
  // fire (and even when the timeout races with a natural exit).
  let finalized = false;
  let timedOut = false;
  const timer = setTimeout(() => {
    // If the child has already finalized (natural exit / spawn error), skip
    // the kill -- no zombie to terminate, and timedOut would only confuse the
    // already-written receipt's status if the exit handler races with us.
    if (finalized) return;
    timedOut = true;
    log.warn('worker_timeout_kill', {
      trace_id: traceId,
      pid: child.pid,
      timeout_seconds: timeoutSeconds,
    });
    killProcessTree(child);
  }, timeoutSeconds * 1000);
  if (typeof timer.unref === 'function') timer.unref();

  child.on('error', (err) => {
    if (finalized) return;
    finalized = true;
    clearTimeout(timer);
    log.error('worker_spawn_error', { trace_id: traceId, error: err.message });
    const completedAt = new Date().toISOString();
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);
    try {
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
    } catch (writeErr) {
      log.error('finalize_error_path_failed', { trace_id: traceId, error: writeErr.message });
    }
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    if (typeof onDone === 'function') onDone();
  });

  child.on('exit', (code, signal) => {
    if (finalized) return;
    finalized = true;
    clearTimeout(timer);
    const completedAt = new Date().toISOString();
    const exitCode = code !== null ? code : -1;
    const stderrTail = readTail(stderrPath, STDERR_TAIL_BYTES);
    const stdoutTail = readTailRaw(stdoutPath, STDOUT_SCAN_BYTES);

    let status;
    if (timedOut) {
      status = 'FAILED';
    } else if (exitCode === 0) {
      status = scanStdoutForStatus(stdoutTail) || 'INCONCLUSIVE';
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

    try {
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
    } catch (writeErr) {
      log.error('finalize_exit_path_failed', { trace_id: traceId, error: writeErr.message });
    }
    try {
      fs.unlinkSync(promptFile);
    } catch (_e) {
      /* best effort */
    }
    if (typeof onDone === 'function') onDone();
  });

  return { pid: child.pid, startedAt };
}

module.exports = { spawnWorker, readTail, readTailRaw, scanStdoutForStatus };
