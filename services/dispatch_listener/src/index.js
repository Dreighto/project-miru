'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const express = require('express');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
require('dotenv').config({ path: path.join(REPO_ROOT, '.env') });

const log = require('./log');
const { verifyHmac } = require('./hmac');
const { isAllowed, ALLOWLIST, MISSING_BINARIES, MISSING_DEBUG } = require('./allowlist');
const {
  tryReadReceipt,
  writePlaceholderReceipt,
  writeTerminalReceipt,
  findInFlightByPromptHash,
} = require('./receipt');
const { writeDlqEntry } = require('./dlq');
const { spawnWorker } = require('./spawn');
const {
  leaseSlot,
  updateLeasePid,
  releaseSlot,
  getLeaseByTraceId,
  listKnownRepos,
  DEFAULT_TARGET_REPO,
} = require('./worktree');
const { writeMcpConfig } = require('./mcp_config');

const PORT = 19100;
const BIND_HOST = '127.0.0.1';
const SECRET = process.env.W4_LISTENER_HMAC_SECRET;
const INBOX_DIR = path.join(REPO_ROOT, 'data', 'n8n_inbox');
const DLQ_PATH = path.join(REPO_ROOT, 'data', 'dispatch_dlq.jsonl');
const TRACE_LOG_DIR = path.join(REPO_ROOT, 'logs', 'dispatch_listener_traces');

const TIMEOUT_MIN = 1;
const TIMEOUT_MAX = 1800;
const TIMEOUT_DEFAULT = 600;
const MAX_BODY_BYTES = 65536;
const TRACE_ID_RE = /^[a-zA-Z0-9_-]{6,128}$/;

// Ticket B3 — prompt-hash idempotency window. Receipts older than this are
// ignored when checking for duplicate prompts (matches the typical worker
// runtime; longer would falsely block legitimate re-runs of the same prompt
// later, shorter would miss recovery_router's re-dispatch case).
const PROMPT_HASH_WINDOW_SECONDS = 600;

if (!SECRET) {
  log.fatal('startup_missing_secret', { msg: 'W4_LISTENER_HMAC_SECRET not in process env' });
  process.exit(2);
}
if (MISSING_BINARIES.length > 0) {
  log.fatal('startup_missing_binaries', { missing: MISSING_BINARIES, checked: MISSING_DEBUG });
  process.exit(3);
}
log.info('startup_allowlist_resolved', {
  resolved: Object.fromEntries(Object.entries(ALLOWLIST).map(([k, v]) => [k, v.binaryPath])),
});
process.env.DISPATCH_INBOX_DIR = INBOX_DIR;
process.env.DISPATCH_DLQ_PATH = DLQ_PATH;

fs.mkdirSync(INBOX_DIR, { recursive: true });
fs.mkdirSync(TRACE_LOG_DIR, { recursive: true });
fs.mkdirSync(path.dirname(DLQ_PATH), { recursive: true });

function sweepOrphanedReceipts() {
  let entries;
  try {
    entries = fs.readdirSync(INBOX_DIR);
  } catch (err) {
    log.warn('orphan_sweep_readdir_failed', { error: err.message });
    return;
  }
  const oneHourAgo = Date.now() - 60 * 60 * 1000;
  for (const name of entries) {
    if (!name.endsWith('.result.json')) continue;
    const traceId = name.slice(0, -'.result.json'.length);
    const full = path.join(INBOX_DIR, name);
    let stat;
    try {
      stat = fs.statSync(full);
    } catch {
      continue;
    }
    if (stat.mtimeMs > oneHourAgo) continue;
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(full, 'utf8'));
    } catch {
      continue;
    }
    if (parsed && parsed.status === 'spawned') {
      log.warn('orphan_receipt_dlq', { trace_id: traceId });
      try {
        writeTerminalReceipt({
          traceId,
          worker: parsed.worker || 'unknown',
          status: 'FAILED',
          startedAt: parsed.started_at || new Date().toISOString(),
          completedAt: new Date().toISOString(),
          exitCode: null,
          stderrTail: 'listener restarted while child was in flight',
        });
        writeDlqEntry({
          traceId,
          worker: parsed.worker || 'unknown',
          promptPath: null,
          exitCode: null,
          stderrTail: 'listener restarted while child was in flight',
          errorClass: 'listener_restarted',
        });
        const orphanSlot = getLeaseByTraceId(traceId);
        if (orphanSlot) releaseSlot(orphanSlot);
      } catch (err) {
        log.error('orphan_receipt_dlq_failed', { trace_id: traceId, error: err.message });
      }
    }
  }
}

const app = express();
app.disable('x-powered-by');

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', listener: 'dispatch_listener', port: PORT });
});

app.post(
  '/dispatch',
  express.raw({ type: '*/*', limit: MAX_BODY_BYTES, inflate: false }),
  (req, res) => {
    const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    const provided = String(req.headers['x-w4-hmac'] || '');

    if (!verifyHmac(raw, provided, SECRET)) {
      log.warn('hmac_reject', { body_bytes: raw.length });
      try {
        writeDlqEntry({
          traceId: null,
          worker: null,
          promptPath: null,
          exitCode: null,
          stderrTail: `hmac_reject body_bytes=${raw.length}`,
          errorClass: 'hmac_reject',
        });
      } catch (err) {
        log.error('dlq_write_failed', { error: err.message });
      }
      return res.status(401).json({ error: 'hmac_reject' });
    }

    let payload;
    try {
      payload = JSON.parse(raw.toString('utf8'));
    } catch (_err) {
      log.warn('bad_request_json_parse');
      try {
        writeDlqEntry({
          traceId: null,
          worker: null,
          promptPath: null,
          exitCode: null,
          stderrTail: 'json_parse_failed',
          errorClass: 'bad_request',
        });
      } catch (err) {
        log.error('dlq_write_failed', { error: err.message });
      }
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_json' });
    }

    const {
      trace_id: traceId,
      worker,
      prompt_path: promptPath,
      use_api_key: useApiKey,
      model,
      thinking_level: thinkingLevel,
      tool_profile: toolProfile,
      target_repo: targetRepoRaw,
    } = payload || {};
    // target_repo defaults to project-miru for backward compat with pre-2026-05-09
    // dispatchers. Workers landing in non-default repos must pass it explicitly.
    const targetRepo =
      typeof targetRepoRaw === 'string' && targetRepoRaw.trim()
        ? targetRepoRaw.trim()
        : DEFAULT_TARGET_REPO;
    let timeoutSeconds = (payload && payload.timeout_seconds) || TIMEOUT_DEFAULT;

    if (typeof traceId !== 'string' || !TRACE_ID_RE.test(traceId)) {
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_trace_id' });
    }
    if (typeof worker !== 'string') {
      return res.status(400).json({ error: 'bad_request', reason: 'missing_worker' });
    }
    if (typeof promptPath !== 'string' || promptPath.length === 0) {
      return res.status(400).json({ error: 'bad_request', reason: 'missing_prompt_path' });
    }
    if (typeof timeoutSeconds !== 'number' || !Number.isFinite(timeoutSeconds)) {
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_timeout' });
    }
    timeoutSeconds = Math.floor(timeoutSeconds);
    if (timeoutSeconds < TIMEOUT_MIN || timeoutSeconds > TIMEOUT_MAX) {
      return res.status(400).json({ error: 'bad_request', reason: 'timeout_out_of_range' });
    }
    if (model !== undefined && (typeof model !== 'string' || model.trim() === '')) {
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_model' });
    }
    if (
      thinkingLevel !== undefined &&
      (typeof thinkingLevel !== 'string' || thinkingLevel.trim() === '')
    ) {
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_thinking_level' });
    }
    if (
      toolProfile !== undefined &&
      (typeof toolProfile !== 'string' || !/^[a-z_]{3,30}$/.test(toolProfile))
    ) {
      return res.status(400).json({ error: 'bad_request', reason: 'invalid_tool_profile' });
    }

    // Validate target_repo against the configured worktree pools.
    // Defends against typos (e.g. "logueos-console" vs "LogueOS-Console") that
    // would otherwise silently fail at lease time with a hard-to-debug 503.
    const knownRepos = listKnownRepos();
    if (!knownRepos.includes(targetRepo)) {
      log.warn('unknown_target_repo', {
        trace_id: traceId,
        target_repo: targetRepo,
        known: knownRepos,
      });
      return res.status(400).json({
        error: 'bad_request',
        reason: 'unknown_target_repo',
        target_repo: targetRepo,
        known_repos: knownRepos,
      });
    }

    if (!isAllowed(worker)) {
      log.warn('allowlist_reject', { trace_id: traceId, worker });
      try {
        writeDlqEntry({
          traceId,
          worker,
          promptPath,
          exitCode: null,
          stderrTail: `worker "${worker}" not in allowlist`,
          errorClass: 'allowlist_reject',
        });
      } catch (err) {
        log.error('dlq_write_failed', { error: err.message });
      }
      return res.status(403).json({ error: 'worker_not_allowlisted' });
    }

    if (tryReadReceipt(INBOX_DIR, traceId)) {
      log.info('already_dispatched', { trace_id: traceId });
      return res.status(409).json({ error: 'already_dispatched' });
    }

    const resolvedProfile =
      typeof toolProfile === 'string' && toolProfile.trim()
        ? toolProfile.trim()
        : 'standard_worker';

    const slotPath = leaseSlot(traceId, worker, targetRepo);
    if (slotPath === null) {
      log.warn('no_worktree_available', { trace_id: traceId, worker, target_repo: targetRepo });
      try {
        writeDlqEntry({
          traceId,
          worker,
          promptPath,
          exitCode: null,
          stderrTail: `no worktree slot available in pool: ${targetRepo}`,
          errorClass: 'no_worktree_available',
        });
      } catch (err) {
        log.error('dlq_write_failed', { error: err.message });
      }
      return res.status(503).json({ error: 'no_worktree_available', target_repo: targetRepo });
    }

    try {
      writeMcpConfig(slotPath);
    } catch (err) {
      releaseSlot(slotPath);
      log.error('mcp_config_write_failed', {
        trace_id: traceId,
        slot: slotPath,
        error: err.message,
      });
      return res.status(500).json({ error: 'spawn_failed', reason: 'mcp_config_write_failed' });
    }

    const promptAbs = path.isAbsolute(promptPath) ? promptPath : path.join(REPO_ROOT, promptPath);
    let promptText;
    try {
      // Renamed from `raw` to `promptRaw` to avoid shadowing the outer `raw`
      // that holds the request body for HMAC verification (see top of handler).
      const promptRaw = fs.readFileSync(promptAbs, 'utf8').replace(/^﻿/, '');
      const promptDoc = JSON.parse(promptRaw);
      if (typeof promptDoc.prompt !== 'string' || promptDoc.prompt.length === 0) {
        throw new Error('prompt field missing or empty');
      }
      promptText = promptDoc.prompt;
    } catch (err) {
      releaseSlot(slotPath);
      log.warn('prompt_read_failed', { trace_id: traceId, error: err.message });
      try {
        writeDlqEntry({
          traceId,
          worker,
          promptPath,
          exitCode: null,
          stderrTail: `prompt_read_failed: ${err.message}`,
          errorClass: 'bad_request',
        });
      } catch (err2) {
        log.error('dlq_write_failed', { error: err2.message });
      }
      return res.status(400).json({ error: 'bad_request', reason: 'prompt_unreadable' });
    }

    // Ticket B3 — prompt-hash idempotency. trace_id idempotency (above) only
    // catches re-POSTs of the same trace_id. Recovery_router mints a fresh
    // `recovery-X-Y-Z` trace_id when re-dispatching a stalled worker, which
    // would slip through that check and run the same prompt twice. Hash the
    // prompt body and reject if any in-flight receipt has the same hash.
    const promptHash = crypto.createHash('sha256').update(promptText).digest('hex').slice(0, 16);
    const inFlightTrace = findInFlightByPromptHash(
      INBOX_DIR,
      promptHash,
      PROMPT_HASH_WINDOW_SECONDS
    );
    if (inFlightTrace) {
      releaseSlot(slotPath);
      log.warn('duplicate_prompt_in_flight', {
        trace_id: traceId,
        existing_trace_id: inFlightTrace,
        prompt_hash: promptHash,
      });
      try {
        writeDlqEntry({
          traceId,
          worker,
          promptPath,
          exitCode: null,
          stderrTail: `duplicate_prompt_in_flight: existing_trace_id=${inFlightTrace} hash=${promptHash}`,
          errorClass: 'duplicate_prompt',
        });
      } catch (err) {
        log.error('dlq_write_failed', { error: err.message });
      }
      return res.status(409).json({
        error: 'duplicate_prompt_in_flight',
        existing_trace_id: inFlightTrace,
      });
    }

    const startedAt = new Date().toISOString();
    try {
      writePlaceholderReceipt({
        inboxDir: INBOX_DIR,
        traceId,
        worker,
        startedAt,
        promptHash,
      });
    } catch (err) {
      releaseSlot(slotPath);
      if (err.code === 'EEXIST') {
        log.info('already_dispatched_placeholder_race', { trace_id: traceId });
        return res.status(409).json({ error: 'already_dispatched' });
      }
      log.error('placeholder_write_failed', { trace_id: traceId, error: err.message });
      return res.status(500).json({ error: 'spawn_failed', reason: 'placeholder_write_failed' });
    }

    let result;
    try {
      result = spawnWorker({
        traceId,
        worker,
        promptText,
        timeoutSeconds,
        useApiKey: useApiKey === true,
        model: typeof model === 'string' && model.trim() ? model.trim() : null,
        thinkingLevel:
          typeof thinkingLevel === 'string' && thinkingLevel.trim() ? thinkingLevel.trim() : null,
        toolProfile: resolvedProfile,
        cwd: slotPath,
        traceLogDir: TRACE_LOG_DIR,
        onDone: () => releaseSlot(slotPath),
      });
    } catch (err) {
      releaseSlot(slotPath);
      log.error('spawn_failed', { trace_id: traceId, error: err.message });
      try {
        writeTerminalReceipt({
          traceId,
          worker,
          status: 'FAILED',
          startedAt,
          completedAt: new Date().toISOString(),
          exitCode: null,
          stderrTail: err.message,
        });
        writeDlqEntry({
          traceId,
          worker,
          promptPath,
          exitCode: null,
          stderrTail: err.message,
          errorClass: err.code === 'ENOENT' ? 'binary_missing' : 'spawn_failed',
        });
      } catch (innerErr) {
        log.error('post_spawn_failure_cleanup_failed', {
          trace_id: traceId,
          error: innerErr.message,
        });
      }
      return res.status(500).json({ error: 'spawn_failed', reason: err.message });
    }

    updateLeasePid(slotPath, result.pid);

    return res.status(202).json({
      trace_id: traceId,
      status: 'spawned',
      spawned_at: result.startedAt,
    });
  }
);

app.use((_req, res) => res.status(404).json({ error: 'not_found' }));

const server = app.listen(PORT, BIND_HOST, () => {
  log.info('listener_listening', { host: BIND_HOST, port: PORT, repo_root: REPO_ROOT });
  sweepOrphanedReceipts();
});

function shutdown(signal) {
  log.info('shutdown_begin', { signal });
  server.close(() => {
    log.info('shutdown_complete');
    process.exit(0);
  });
  setTimeout(() => process.exit(0), 5000).unref();
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
