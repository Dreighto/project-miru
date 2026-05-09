'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');

const log = require('./log');

const PREDICTION_SOURCE = 'hermes_shadow_v1';
const HERMES_TIMEOUT_MS = 30_000;
const REPO_ROOT = process.env.MIRU_REPO_ROOT || path.resolve(__dirname, '../../..');

// JSON schema for Qwen's structured prediction response.
// Passed as `format` to Ollama — this build silently ignores options.grammar
// but honours format= (confirmed 2026-05-06 in gatekeeper smoke test).
const PREDICTION_SCHEMA = {
  type: 'object',
  required: ['worker', 'confidence', 'risk', 'rationale'],
  properties: {
    worker: { type: 'string', enum: ['claude-code', 'gemini', 'both', 'none'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    risk: { type: 'string', enum: ['high', 'medium', 'low'] },
    rationale: { type: 'string' },
  },
};

// Lazy env-var readers so tests can set vars before calling functions.
function getOllamaUrl() {
  return process.env.MIRU_HERMES_OLLAMA_URL || 'http://127.0.0.1:11434/api/chat';
}
function getPredictionsPath() {
  return (
    process.env.MIRU_HERMES_PREDICTIONS_PATH ||
    path.join(REPO_ROOT, 'data', 'hermes_predictions.jsonl')
  );
}
function getModel() {
  return process.env.MIRU_HERMES_MODEL || 'qwen2.5:7b';
}

function sha256Prefix(str) {
  return crypto.createHash('sha256').update(str, 'utf8').digest('hex').slice(0, 16);
}

function buildUserMessage(worker, promptExcerpt) {
  return (
    'You are a routing advisor for Project Miru, a multi-worker AI orchestration system.\n\n' +
    'Given the dispatch request below, predict the optimal worker assignment.\n\n' +
    'Workers:\n' +
    '- claude-code: Python, multi-file refactoring, tests, verification, complex reasoning.\n' +
    '- gemini: Large-context reads, cross-file analysis, architecture review.\n\n' +
    'Dispatch request:\n' +
    `- worker_requested: ${worker}\n` +
    `- prompt_excerpt: ${promptExcerpt}\n\n` +
    'Emit a JSON object with fields: worker (claude-code|gemini|both|none), ' +
    'confidence (high|medium|low), risk (high|medium|low), rationale (one sentence).'
  );
}

function callOllama(ollamaUrl, model, userMessage) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(
      JSON.stringify({
        model,
        messages: [{ role: 'user', content: userMessage }],
        stream: false,
        format: PREDICTION_SCHEMA,
      }),
      'utf8'
    );

    let urlObj;
    try {
      urlObj = new URL(ollamaUrl);
    } catch (_e) {
      return reject(new Error(`invalid_ollama_url: ${ollamaUrl}`));
    }

    const transport = urlObj.protocol === 'https:' ? https : http;
    // Use protocol default port (80/443) when URL omits one. Ollama's default
    // 11434 lives in the default URL string, so this branch only fires for
    // arbitrary override URLs where the proxy/protocol default is correct.
    const defaultPort = urlObj.protocol === 'https:' ? 443 : 80;
    const reqOptions = {
      hostname: urlObj.hostname,
      port: urlObj.port ? Number(urlObj.port) : defaultPort,
      path: urlObj.pathname + (urlObj.search || ''),
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': body.length,
      },
    };
    // Forward URL-embedded credentials (e.g. https://user:pass@proxy/...) so
    // override URLs that front Ollama with basic-auth proxies work as-typed.
    if (urlObj.username) {
      reqOptions.auth = `${urlObj.username}:${urlObj.password || ''}`;
    }

    const req = transport.request(reqOptions, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        try {
          resolve(JSON.parse(raw));
        } catch (_e) {
          reject(new Error(`ollama_json_parse_failed: ${raw.slice(0, 200)}`));
        }
      });
      res.on('error', reject);
    });

    req.setTimeout(HERMES_TIMEOUT_MS, () => {
      req.destroy(new Error('qwen_timeout'));
    });

    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

function appendRow(predictionsPath, row) {
  const dir = path.dirname(predictionsPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.appendFileSync(predictionsPath, JSON.stringify(row) + '\n', 'utf8');
}

async function predictDispatch({ traceId, worker, promptText }) {
  if (!traceId || !worker || typeof promptText !== 'string' || promptText.trim() === '') {
    log.warn('hermes_predict_invalid_params', {
      trace_id: traceId || null,
      worker: worker || null,
      prompt_type: typeof promptText,
    });
    return;
  }

  const startMs = Date.now();
  const model = getModel();
  const ollamaUrl = getOllamaUrl();
  const predictionsPath = getPredictionsPath();

  const promptHash = sha256Prefix(promptText);
  const promptExcerpt = promptText.slice(0, 300).replace(/\s+/g, ' ').trim();
  const timestamp = new Date().toISOString().replace('+00:00', 'Z');

  let qwenPrediction = null;
  let ollamaError = null;
  let qwenLatencyMs = null;

  try {
    const userMessage = buildUserMessage(worker, promptExcerpt);
    const ollamaResp = await callOllama(ollamaUrl, model, userMessage);
    qwenLatencyMs = Date.now() - startMs;

    // Ollama /api/chat response: { message: { role, content }, ... }
    // When format schema is used, content is a JSON string.
    const rawContent = ollamaResp?.message?.content;
    if (typeof rawContent === 'string') {
      try {
        qwenPrediction = JSON.parse(rawContent);
      } catch (_e) {
        ollamaError = `content_not_json: ${rawContent.slice(0, 100)}`;
      }
    } else if (rawContent !== null && rawContent !== undefined && typeof rawContent === 'object') {
      qwenPrediction = rawContent;
    } else {
      ollamaError = `unexpected_content_type: ${typeof rawContent}`;
    }
  } catch (err) {
    qwenLatencyMs = Date.now() - startMs;
    ollamaError = err.message || String(err);
    log.warn('hermes_predict_failed', { trace_id: traceId, error: ollamaError });
  }

  const row = {
    schema_version: '1',
    trace_id: traceId,
    timestamp,
    worker_dispatched: worker,
    prompt_hash: promptHash,
    qwen_model: model,
    qwen_prediction: qwenPrediction,
    qwen_latency_ms: qwenLatencyMs,
    prediction_source: PREDICTION_SOURCE,
    ollama_error: ollamaError,
  };

  try {
    appendRow(predictionsPath, row);
  } catch (err) {
    log.error('hermes_predict_append_failed', { trace_id: traceId, error: err.message });
    return;
  }

  log.info('hermes_predict_logged', {
    trace_id: traceId,
    worker_dispatched: worker,
    worker_predicted: qwenPrediction?.worker ?? null,
    qwen_latency_ms: qwenLatencyMs,
    ollama_error: ollamaError ?? null,
  });
}

// Fire-and-forget wrapper — never rejects, never blocks the caller.
// Defers via setImmediate so even the synchronous prologue of predictDispatch
// (param validation, hash, env reads — everything before the first `await`)
// runs off the caller's stack. The spawn path must not pay any cost for
// shadow prediction, even sub-millisecond.
function predictDispatchAsync(params) {
  setImmediate(() => {
    predictDispatch(params).catch((err) => {
      log.error('hermes_predict_unhandled', {
        trace_id: params?.traceId,
        error: err?.message || String(err),
      });
    });
  });
}

module.exports = { predictDispatch, predictDispatchAsync, PREDICTION_SOURCE };
