'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

// predict.js reads env vars lazily (inside functions), so we can set them
// before the first call and the module will pick up the test values.

let mockServer;
let mockPort;
let tempDir;
let basePredictionsPath;
// Snapshot the inherited env so teardown restores rather than blanket-deletes.
// This prevents the test suite from clobbering values that other suites in the
// same process may rely on.
let savedOllamaUrl;
let savedPredictionsPath;
let savedModel;

before(async () => {
  savedOllamaUrl = process.env.MIRU_HERMES_OLLAMA_URL;
  savedPredictionsPath = process.env.MIRU_HERMES_PREDICTIONS_PATH;
  savedModel = process.env.MIRU_HERMES_MODEL;

  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-predict-test-'));
  basePredictionsPath = path.join(tempDir, 'hermes_predictions.jsonl');

  // Mock Ollama server: returns a valid prediction JSON string in message.content
  mockServer = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const prediction = JSON.stringify({
        worker: 'claude-code',
        confidence: 'high',
        risk: 'low',
        rationale: 'Mock prediction for unit test.',
      });
      const response = {
        model: body.model,
        message: { role: 'assistant', content: prediction },
        done: true,
      };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(response));
    });
  });

  await new Promise((resolve) => {
    mockServer.listen(0, '127.0.0.1', () => {
      mockPort = mockServer.address().port;
      resolve();
    });
  });

  process.env.MIRU_HERMES_OLLAMA_URL = `http://127.0.0.1:${mockPort}/api/chat`;
  process.env.MIRU_HERMES_PREDICTIONS_PATH = basePredictionsPath;
  process.env.MIRU_HERMES_MODEL = 'qwen2.5:7b';
});

after(async () => {
  await new Promise((resolve) => mockServer.close(resolve));
  try {
    fs.rmSync(tempDir, { recursive: true, force: true });
  } catch (_e) {
    /* best effort */
  }
  // Restore originals; only delete if the var was unset before this suite.
  if (savedOllamaUrl === undefined) delete process.env.MIRU_HERMES_OLLAMA_URL;
  else process.env.MIRU_HERMES_OLLAMA_URL = savedOllamaUrl;
  if (savedPredictionsPath === undefined) delete process.env.MIRU_HERMES_PREDICTIONS_PATH;
  else process.env.MIRU_HERMES_PREDICTIONS_PATH = savedPredictionsPath;
  if (savedModel === undefined) delete process.env.MIRU_HERMES_MODEL;
  else process.env.MIRU_HERMES_MODEL = savedModel;
});

const { predictDispatch, predictDispatchAsync, PREDICTION_SOURCE } = require('../src/predict');

test('predictDispatch writes one valid row to hermes_predictions.jsonl', async () => {
  await predictDispatch({
    traceId: 'cc-pro329-test-00000001',
    worker: 'claude-code',
    promptText: 'Implement PRO-329: Add Hermes shadow prediction at worker spawn time.',
  });

  assert.ok(fs.existsSync(basePredictionsPath), 'predictions file must exist after call');

  const lines = fs.readFileSync(basePredictionsPath, 'utf8').trim().split('\n');
  assert.equal(lines.length, 1, 'exactly one row should be appended');

  const row = JSON.parse(lines[0]);
  assert.equal(row.schema_version, '1');
  assert.equal(row.trace_id, 'cc-pro329-test-00000001');
  assert.equal(row.worker_dispatched, 'claude-code');
  assert.equal(row.prediction_source, PREDICTION_SOURCE);
  assert.equal(row.qwen_model, 'qwen2.5:7b');
  assert.ok(
    typeof row.prompt_hash === 'string' && row.prompt_hash.length === 16,
    'prompt_hash should be 16-char hex'
  );
  assert.ok(
    typeof row.timestamp === 'string' && row.timestamp.endsWith('Z'),
    'timestamp should be UTC ISO-8601'
  );
  assert.ok(row.qwen_prediction !== null, 'qwen_prediction must be populated on success');
  assert.equal(row.qwen_prediction.worker, 'claude-code');
  assert.equal(row.qwen_prediction.confidence, 'high');
  assert.equal(row.qwen_prediction.risk, 'low');
  assert.ok(typeof row.qwen_prediction.rationale === 'string');
  assert.ok(typeof row.qwen_latency_ms === 'number' && row.qwen_latency_ms >= 0);
  assert.equal(row.ollama_error, null);
});

test('predictDispatch appends (does not overwrite) on second call', async () => {
  // Self-contained: reset state, then exercise the append path with two
  // calls inside this test. Avoids order-dependence on the prior test.
  if (fs.existsSync(basePredictionsPath)) fs.rmSync(basePredictionsPath);

  await predictDispatch({
    traceId: 'cc-pro329-test-00000001b',
    worker: 'claude-code',
    promptText: 'First dispatch for append-only test.',
  });

  await predictDispatch({
    traceId: 'cc-pro329-test-00000002',
    worker: 'gemini',
    promptText: 'Second dispatch for append-only test.',
  });

  const lines = fs.readFileSync(basePredictionsPath, 'utf8').trim().split('\n');
  assert.equal(lines.length, 2, 'second call must append, not overwrite');

  const row2 = JSON.parse(lines[1]);
  assert.equal(row2.trace_id, 'cc-pro329-test-00000002');
  assert.equal(row2.worker_dispatched, 'gemini');
});

test('predictDispatch logs ollama_error and still writes row when Ollama is unreachable', async () => {
  const errPath = path.join(tempDir, 'hermes_err.jsonl');
  const savedUrl = process.env.MIRU_HERMES_OLLAMA_URL;
  const savedPath = process.env.MIRU_HERMES_PREDICTIONS_PATH;

  // Point at a port that is not listening
  process.env.MIRU_HERMES_OLLAMA_URL = 'http://127.0.0.1:19999/api/chat';
  process.env.MIRU_HERMES_PREDICTIONS_PATH = errPath;

  try {
    await predictDispatch({
      traceId: 'cc-pro329-test-error',
      worker: 'gemini',
      promptText: 'Task that will hit an unreachable Ollama.',
    });
  } finally {
    process.env.MIRU_HERMES_OLLAMA_URL = savedUrl;
    process.env.MIRU_HERMES_PREDICTIONS_PATH = savedPath;
  }

  assert.ok(fs.existsSync(errPath), 'predictions file must still be created on Ollama error');
  const row = JSON.parse(fs.readFileSync(errPath, 'utf8').trim());
  assert.equal(row.trace_id, 'cc-pro329-test-error');
  assert.equal(row.qwen_prediction, null, 'prediction must be null when Ollama is unreachable');
  assert.ok(
    typeof row.ollama_error === 'string' && row.ollama_error.length > 0,
    'ollama_error must be a non-empty string'
  );
});

test('predictDispatchAsync does not throw and is truly fire-and-forget', async () => {
  // Should not throw synchronously or reject
  let threw = false;
  try {
    predictDispatchAsync({
      traceId: 'cc-pro329-async-test',
      worker: 'claude-code',
      promptText: 'Testing async fire-and-forget wrapper.',
    });
  } catch (_e) {
    threw = true;
  }
  assert.equal(threw, false, 'predictDispatchAsync must not throw synchronously');

  // Brief pause so the async work can settle before the test runner tears down
  await new Promise((resolve) => setTimeout(resolve, 200));
});
