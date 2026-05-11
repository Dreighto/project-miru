'use strict';

// Tests for services/dispatch_listener/src/canon_probe.js — LOS-10 Step 2.
//
// Coverage:
// - Happy path: gateway returns valid manifest → probeBeforeSpawn returns
//   { snapshot_id, file_count } and emits canon_snapshot_recorded log.
// - Gateway unreachable (curl non-zero exit) → throws canon_probe_failed,
//   emits worker_spawn_refused_canon_unavailable log.
// - Empty response body → throws.
// - Malformed JSON → throws.
// - Manifest missing ok=true → throws.
// - Manifest missing canon_snapshot_id → throws.
//
// Tests use an injectable execFile stub instead of spinning up a real HTTP
// server. Faster + more reliable than the gateway-up-or-test-fails pattern.

const { test } = require('node:test');
const assert = require('node:assert/strict');

const canonProbe = require('../src/canon_probe');
const log = require('../src/log');

function withCapturedLogs(fn) {
  const logs = [];
  const originals = {
    info: log.info,
    warn: log.warn,
  };
  log.info = (event, data) => logs.push({ level: 'info', event, data });
  log.warn = (event, data) => logs.push({ level: 'warn', event, data });
  try {
    fn(logs);
  } finally {
    log.info = originals.info;
    log.warn = originals.warn;
  }
}

test('probeBeforeSpawn: happy path returns snapshot_id and emits canon_snapshot_recorded', () => {
  const fakeManifest = {
    ok: true,
    canon_snapshot_id: 'a'.repeat(64),
    file_count: 42,
    files: {},
  };
  const stubExecFile = () => JSON.stringify(fakeManifest);
  withCapturedLogs((logs) => {
    const result = canonProbe.probeBeforeSpawn('trace-test-happy', { execFile: stubExecFile });
    assert.equal(result.snapshot_id, fakeManifest.canon_snapshot_id);
    assert.equal(result.file_count, 42);
    const evt = logs.find((l) => l.event === 'canon_snapshot_recorded');
    assert.ok(evt, 'canon_snapshot_recorded event emitted');
    assert.equal(evt.data.trace_id, 'trace-test-happy');
    assert.equal(evt.data.canon_snapshot_id, fakeManifest.canon_snapshot_id);
    assert.equal(evt.data.file_count, 42);
  });
});

test('probeBeforeSpawn: curl failure throws + emits worker_spawn_refused_canon_unavailable', () => {
  const stubExecFile = () => {
    const err = new Error('curl: (7) Failed to connect to 127.0.0.1 port 18766');
    err.stderr = 'curl: (7) Failed to connect';
    throw err;
  };
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-unreachable', { execFile: stubExecFile }),
      /canon_probe_failed/
    );
    const evt = logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable');
    assert.ok(evt, 'worker_spawn_refused_canon_unavailable event emitted');
    assert.equal(evt.data.trace_id, 'trace-test-unreachable');
    assert.match(evt.data.error, /canon_probe_failed/);
  });
});

test('probeBeforeSpawn: empty response body throws', () => {
  const stubExecFile = () => '';
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-empty', { execFile: stubExecFile }),
      /canon_probe_failed.*empty response/
    );
    assert.ok(logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'));
  });
});

test('probeBeforeSpawn: malformed JSON throws', () => {
  const stubExecFile = () => 'not-json{{{';
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-badjson', { execFile: stubExecFile }),
      /canon_probe_failed.*malformed JSON/
    );
    assert.ok(logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'));
  });
});

test('probeBeforeSpawn: missing ok=true throws', () => {
  const stubExecFile = () => JSON.stringify({ canon_snapshot_id: 'a'.repeat(64), file_count: 42 });
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-no-ok', { execFile: stubExecFile }),
      /canon_probe_failed.*missing ok=true/
    );
    assert.ok(logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'));
  });
});

test('probeBeforeSpawn: missing canon_snapshot_id throws', () => {
  const stubExecFile = () => JSON.stringify({ ok: true, file_count: 42 });
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-no-snap', { execFile: stubExecFile }),
      /canon_probe_failed.*missing canon_snapshot_id/
    );
    assert.ok(logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'));
  });
});

test('probeBeforeSpawn: non-string canon_snapshot_id throws', () => {
  const stubExecFile = () => JSON.stringify({ ok: true, canon_snapshot_id: 12345, file_count: 42 });
  withCapturedLogs((logs) => {
    assert.throws(
      () => canonProbe.probeBeforeSpawn('trace-test-bad-snap', { execFile: stubExecFile }),
      /canon_probe_failed.*missing canon_snapshot_id/
    );
    assert.ok(logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'));
  });
});

test('probeBeforeSpawn: malformed-string snapshot_id throws (CR R1: format check)', () => {
  // A short or non-hex string passes the "is string" check but indicates the
  // gateway is misconfigured or compromised. The format guard (64-char
  // lowercase hex) must reject it before the spawn proceeds. CodeRabbit R1.
  const cases = [
    { id: 'bad-id', label: 'short+nonhex' },
    { id: 'abc', label: 'tooshort' },
    { id: 'A'.repeat(64), label: 'uppercase' },
    { id: 'g'.repeat(64), label: 'right-length-nonhex' },
    { id: 'a'.repeat(63), label: 'one-short' },
    { id: 'a'.repeat(65), label: 'one-long' },
  ];
  for (const { id, label } of cases) {
    const stubExecFile = () => JSON.stringify({ ok: true, canon_snapshot_id: id, file_count: 42 });
    withCapturedLogs((logs) => {
      assert.throws(
        () =>
          canonProbe.probeBeforeSpawn(`trace-test-malformed-${label}`, {
            execFile: stubExecFile,
          }),
        /canon_probe_failed.*64-char lowercase hex/,
        `should reject ${label}: ${id}`
      );
      assert.ok(
        logs.find((l) => l.event === 'worker_spawn_refused_canon_unavailable'),
        `should emit refusal log for ${label}`
      );
    });
  }
});

test('probeCanonManifestSync: respects custom url + timeout', () => {
  let capturedArgs = null;
  const stubExecFile = (_bin, args) => {
    capturedArgs = args;
    return JSON.stringify({ ok: true, canon_snapshot_id: 'b'.repeat(64), file_count: 1 });
  };
  const result = canonProbe.probeCanonManifestSync({
    url: 'http://10.0.0.5:9999/canon-manifest',
    timeoutS: 3,
    execFile: stubExecFile,
  });
  assert.equal(result.canon_snapshot_id, 'b'.repeat(64));
  assert.ok(capturedArgs.includes('http://10.0.0.5:9999/canon-manifest'));
  assert.ok(capturedArgs.includes('--max-time'));
  assert.ok(capturedArgs.includes('3'));
});

test('getCanonManifestUrl: builds default URL from env or fallbacks', () => {
  const url = canonProbe.getCanonManifestUrl();
  assert.match(url, /^http:\/\/[^/]+\/canon-manifest$/);
});
