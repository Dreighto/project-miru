'use strict';

// Integration test: verify that POST /dispatch returns 503 when both worktree
// slots are already leased. The test mounts a minimal express route that uses
// the real worktree module (same in-process instance), pre-leases both slots,
// then sends a request and asserts the 503 response and JSON body.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const { leaseSlot, releaseSlot } = require('../src/worktree');

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

test('returns 503 with no_worktree_available when both slots are leased', async (t) => {
  const slot1 = leaseSlot('pre-lease-1', 'claude-code');
  const slot2 = leaseSlot('pre-lease-2', 'claude-code');
  assert.ok(slot1, 'pre-lease slot1 should succeed');
  assert.ok(slot2, 'pre-lease slot2 should succeed');

  const server = makeTestServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();

  try {
    const resp = await post(port, '/dispatch', {});
    assert.strictEqual(resp.status, 503);
    assert.strictEqual(resp.body.error, 'no_worktree_available');
  } finally {
    await new Promise((resolve) => server.close(resolve));
    releaseSlot(slot1);
    releaseSlot(slot2);
  }
});

test('returns 202 when a slot is available', async (t) => {
  const server = makeTestServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();

  try {
    const resp = await post(port, '/dispatch', {});
    assert.strictEqual(resp.status, 202);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
