'use strict';

// Integration test: verify that POST /dispatch returns 503 when both worktree
// slots are already leased. The test mounts a minimal express route that uses
// the real worktree module (same in-process instance), pre-leases both slots,
// then sends a request and asserts the 503 response and JSON body.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const {
  leaseSlot,
  releaseSlot,
  WORKTREE_SLOTS,
  WORKTREE_POOLS,
  DEFAULT_TARGET_REPO,
  _leases,
} = require('../src/worktree');

// Helper: clear the in-memory lease map. The worktree module is a singleton;
// other test files (e.g. worktree_cleanup.test.js, worktree.test.js) can leave
// behind lease entries that pollute these tests when the full suite runs.
// Run the same _clearAllLeases pattern used in worktree.test.js so this file
// starts from a known clean state regardless of test execution order.
function _clearAllLeases() {
  for (const slot of WORKTREE_SLOTS) _leases.delete(slot);
}

function makeTestServer() {
  const app = express();
  app.use(express.json());
  app.post('/dispatch', (req, res) => {
    const slotPath = leaseSlot('integration-test-trace', 'claude-code');
    if (slotPath === null) {
      return res.status(503).json({ error: 'no_worktree_available' });
    }
    releaseSlot(slotPath);
    return res.status(202).json({ status: 'spawned' });
  });
  return http.createServer(app);
}

function post(port, path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
      },
      (res) => {
        let buf = '';
        res.on('data', (chunk) => {
          buf += chunk;
        });
        res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(buf) }));
      }
    );
    req.on('error', reject);
    req.end(data);
  });
}

test('returns 503 with no_worktree_available when all slots are leased', async () => {
  _clearAllLeases();
  // Pre-lease the default-pool capacity. The dispatch endpoint defaults to
  // target_repo=project-miru when the payload omits it (this test's payload
  // is empty), so filling that pool exhausts what /dispatch will try.
  const defaultPool = WORKTREE_POOLS[DEFAULT_TARGET_REPO];
  const preLeased = defaultPool.map((_unused, i) => leaseSlot(`pre-lease-${i}`, 'claude-code'));
  assert.ok(preLeased.every(Boolean), 'all pre-leases should succeed');

  const server = makeTestServer();
  await new Promise((resolve) => {
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();

  try {
    const resp = await post(port, '/dispatch', {});
    assert.strictEqual(resp.status, 503);
    assert.strictEqual(resp.body.error, 'no_worktree_available');
  } finally {
    await new Promise((resolve) => {
      server.close(resolve);
    });
    for (const slot of preLeased) releaseSlot(slot);
  }
});

test('returns 202 when a slot is available', async () => {
  _clearAllLeases();
  const server = makeTestServer();
  await new Promise((resolve) => {
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();

  try {
    const resp = await post(port, '/dispatch', {});
    assert.strictEqual(resp.status, 202);
  } finally {
    await new Promise((resolve) => {
      server.close(resolve);
    });
  }
});
