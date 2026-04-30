#!/usr/bin/env node
// tests/w7/test_w7_pro214_stale_remint.js
//
// PRO-214: Boundary-crossing regression test for w7 stale manual-label
// callback re-mint flow (w7-stale-lookup, w7-stale-mint-fresh).
//
// PRO-189 pattern: loads jsCode from the live workflow JSON file on disk,
// evals it via vm.Script to confirm it parses, then exercises the algorithm
// against that loaded code path — not a clean extracted copy.
//
// Exit 0 on all pass, exit 1 on any fail.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

// ── Step 1: Load workflow JSON from disk ─────────────────────────────────────
const WORKFLOW_PATH = path.resolve(
  __dirname,
  '../../docker/n8n/workflows/w7-telegram-callback-handler.json'
);
const workflow = JSON.parse(fs.readFileSync(WORKFLOW_PATH, 'utf8'));

function getNode(name) {
  const node = workflow.nodes.find((n) => n.name === name);
  if (!node) throw new Error(`Node not found in workflow JSON: ${name}`);
  return node;
}

let passed = 0;
let failed = 0;

function ok(label) {
  console.log(`PASS: ${label}`);
  passed++;
}

function fail(label, msg) {
  console.error(`FAIL: ${label} — ${msg}`);
  failed++;
}

// ── Step 2: Parse checks for all new jsCode nodes ───────────────────────────
for (const nodeName of ['w7-stale-lookup', 'w7-stale-mint-fresh']) {
  const node = getNode(nodeName);
  const jsCode = node.parameters.jsCode;
  try {
    new vm.Script('(function() {' + jsCode + '})');
    ok(`${nodeName} jsCode parses without SyntaxError`);
  } catch (e) {
    fail(`${nodeName} jsCode parse`, e.message);
  }
}

// ── Step 3: w7-check-stale-dispatch IF conditions ───────────────────────────
{
  const node = getNode('w7-check-stale-dispatch');
  const conditions = node.parameters.conditions.conditions;

  const hasStaleCheck = conditions.some(
    (c) => c.leftValue.includes('reject_reason') && c.rightValue === 'older than 10 min'
  );
  const hasActionCheck = conditions.some(
    (c) => c.leftValue.includes('action') && c.rightValue === 'd'
  );
  const isAnd = node.parameters.conditions.combinator === 'and';

  if (hasStaleCheck) ok('w7-check-stale-dispatch: reject_reason contains check present');
  else
    fail('w7-check-stale-dispatch', 'missing reject_reason contains "older than 10 min" condition');

  if (hasActionCheck) ok("w7-check-stale-dispatch: action === 'd' check present");
  else fail('w7-check-stale-dispatch', "missing action === 'd' condition");

  if (isAnd) ok('w7-check-stale-dispatch: combinator is AND');
  else
    fail(
      'w7-check-stale-dispatch',
      `combinator should be 'and', got '${node.parameters.conditions.combinator}'`
    );
}

// ── Step 4: w7-check-stale-dispatch FALSE branch → w7-noop-rejected ─────────
{
  const falseOutput = workflow.connections['w7-check-stale-dispatch'].main[1];
  const dest = falseOutput && falseOutput[0] && falseOutput[0].node;
  if (dest === 'w7-noop-rejected')
    ok('FALSE branch routes to w7-noop-rejected (non-stale silently noop)');
  else fail('w7-check-stale-dispatch FALSE branch', `expected w7-noop-rejected, got ${dest}`);
}

// ── Step 5: w7005-validate-branch FALSE → w7-check-stale-dispatch ───────────
{
  const falseOutput = workflow.connections['w7005-validate-branch'].main[1];
  const dest = falseOutput && falseOutput[0] && falseOutput[0].node;
  if (dest === 'w7-check-stale-dispatch')
    ok('w7005-validate-branch FALSE now routes to w7-check-stale-dispatch');
  else fail('w7005-validate-branch FALSE branch', `expected w7-check-stale-dispatch, got ${dest}`);
}

// ── Step 6: Exercise w7-stale-lookup algorithm ───────────────────────────────
{
  const node = getNode('w7-stale-lookup');
  const jsCode = node.parameters.jsCode;

  // Set up temp pending_callbacks file with a dispatch row
  const tmpDir = os.tmpdir();
  const tmpFile = path.join(tmpDir, `test_pending_${Date.now()}.jsonl`);
  const targetToken = 'abc123def456';
  const dispatchRow = {
    schema_version: 'v1',
    kind: 'dispatch',
    token: targetToken,
    trace_id: 'trace-abc',
    worker: 'claude-code',
    flow: 'manual-label',
    issue_id: 'issue-001',
    issue_identifier: 'PRO-214',
    issue_url: 'https://linear.app/project-miru/issue/PRO-214',
    dispatch_chat_id: 8460649671,
    dispatch_message_id: 233,
    dispatch_callback_data: targetToken + 'd' + '00000000' + '00000000' + '0'.repeat(32),
    prompt_path: 'data/n8n_inbox/trace-abc.json',
    status: 'awaiting',
    created_at: '2026-04-30T03:15:14.557Z',
    send_message_ok: true,
    manual_label: true,
    triaged_first: false,
  };
  // Also add a non-manual-label row with same token (should be skipped)
  const nonManualRow = { kind: 'intent', token: targetToken, manual_label: false };
  fs.writeFileSync(
    tmpFile,
    JSON.stringify(nonManualRow) + '\n' + JSON.stringify(dispatchRow) + '\n'
  );

  // Build harness context mirroring n8n Code node environment
  const inputJson = {
    token: targetToken,
    action: 'd',
    message_text: '🏷️ Manually labeled: claude-code\n<i>PRO-214</i>',
    reject_reason: 'callback older than 10 min (age=3600s)',
  };

  // Substitute /miru-data/pending_callbacks.jsonl path with tmp file in jsCode
  const patchedCode = jsCode.replace(
    "'/miru-data/pending_callbacks.jsonl'",
    JSON.stringify(tmpFile)
  );

  let result;
  try {
    const fn = new Function('$input', 'require', patchedCode);
    result = fn({ item: { json: inputJson } }, require);
  } catch (e) {
    fail('w7-stale-lookup execution', e.message);
    result = null;
  }

  if (result) {
    const j = result.json;
    if (j.dispatch_chat_id === 8460649671) ok('w7-stale-lookup: dispatch_chat_id matches');
    else fail('w7-stale-lookup', `dispatch_chat_id mismatch: ${j.dispatch_chat_id}`);

    if (j.dispatch_message_id === 233) ok('w7-stale-lookup: dispatch_message_id matches');
    else fail('w7-stale-lookup', `dispatch_message_id mismatch: ${j.dispatch_message_id}`);

    if (j.original_worker === 'claude-code') ok('w7-stale-lookup: original_worker populated');
    else fail('w7-stale-lookup', `original_worker: ${j.original_worker}`);

    if (j.original_issue_identifier === 'PRO-214')
      ok('w7-stale-lookup: original_issue_identifier populated');
    else fail('w7-stale-lookup', `original_issue_identifier: ${j.original_issue_identifier}`);

    if (j.telegram_edit_body && j.telegram_edit_body.reply_markup.inline_keyboard.length === 0)
      ok('w7-stale-lookup: telegram_edit_body removes inline_keyboard');
    else fail('w7-stale-lookup', 'telegram_edit_body keyboard not cleared');

    if (j.telegram_edit_body && j.telegram_edit_body.text.includes('⚠️ Approval expired'))
      ok('w7-stale-lookup: expiry notice in edit body text');
    else fail('w7-stale-lookup', 'expiry notice missing from edit body text');
  }

  fs.unlinkSync(tmpFile);

  // Test: token not found → should throw
  const tmpFile2 = path.join(tmpDir, `test_pending2_${Date.now()}.jsonl`);
  fs.writeFileSync(tmpFile2, '{"kind":"dispatch","token":"other","manual_label":true}\n');
  const patchedCode2 = jsCode.replace(
    "'/miru-data/pending_callbacks.jsonl'",
    JSON.stringify(tmpFile2)
  );
  try {
    const fn2 = new Function('$input', 'require', patchedCode2);
    fn2({ item: { json: { token: 'notfound', message_text: '' } } }, require);
    fail('w7-stale-lookup missing token', 'should have thrown but did not');
  } catch (e) {
    if (e.message.includes('no manual-label dispatch row found'))
      ok('w7-stale-lookup: throws when token not found');
    else fail('w7-stale-lookup missing token', `unexpected error: ${e.message}`);
  }
  fs.unlinkSync(tmpFile2);
}

// ── Step 7: Exercise w7-stale-mint-fresh algorithm ──────────────────────────
{
  const node = getNode('w7-stale-mint-fresh');
  const jsCode = node.parameters.jsCode;

  const ANCHOR_UNIX = 1767225600;
  const tmpDir = os.tmpdir();
  const tmpFile = path.join(tmpDir, `test_remint_${Date.now()}.jsonl`);

  const inputJson = {
    token: 'expire000001',
    action: 'd',
    dispatch_chat_id: 8460649671,
    dispatch_message_id: 233,
    original_worker: 'claude-code',
    original_issue_id: 'issue-001',
    original_issue_identifier: 'PRO-214',
    original_issue_url: 'https://linear.app/project-miru/issue/PRO-214',
    original_trace_id: 'trace-abc',
    original_triaged_first: false,
    dispatch_row: { prompt_path: 'data/n8n_inbox/trace-abc.json' },
  };

  const SECRET = 'x'.repeat(32);
  const CHAT_ID = '8460649671';

  // Patch PENDING path, $env
  const patchedCode = jsCode.replace(
    "'/miru-data/pending_callbacks.jsonl'",
    JSON.stringify(tmpFile)
  );

  let result;
  try {
    const fn = new Function('$input', '$env', 'require', patchedCode);
    result = fn(
      { item: { json: inputJson } },
      { TELEGRAM_CALLBACK_SECRET: SECRET, TELEGRAM_CHAT_ID: CHAT_ID },
      require
    );
  } catch (e) {
    fail('w7-stale-mint-fresh execution', e.message);
    result = null;
  }

  if (result) {
    const j = result.json;

    // fresh_token should be 12 hex chars
    if (j.fresh_token && /^[0-9a-f]{12}$/.test(j.fresh_token))
      ok('w7-stale-mint-fresh: fresh_token is 12-char hex');
    else fail('w7-stale-mint-fresh', `fresh_token format wrong: ${j.fresh_token}`);

    // fresh_callback_data should be 61 chars
    if (j.fresh_callback_data && j.fresh_callback_data.length === 61)
      ok('w7-stale-mint-fresh: fresh_callback_data is 61 chars');
    else
      fail(
        'w7-stale-mint-fresh',
        `fresh_callback_data length: ${j.fresh_callback_data && j.fresh_callback_data.length}`
      );

    // action byte should be 'd'
    if (j.fresh_callback_data && j.fresh_callback_data[12] === 'd')
      ok("w7-stale-mint-fresh: action byte is 'd'");
    else
      fail(
        'w7-stale-mint-fresh',
        `action byte: ${j.fresh_callback_data && j.fresh_callback_data[12]}`
      );

    // Verify HMAC with correct anchor
    if (j.fresh_callback_data) {
      const cb = j.fresh_callback_data;
      const token = cb.slice(0, 12);
      const action = cb.slice(12, 13);
      const nonce = cb.slice(13, 21);
      const ts_hex = cb.slice(21, 29);
      const hmac_given = cb.slice(29, 61);
      const payload = token + action + nonce + ts_hex;
      const expected = crypto
        .createHmac('sha256', SECRET)
        .update(payload)
        .digest('hex')
        .slice(0, 32);
      if (expected === hmac_given)
        ok('w7-stale-mint-fresh: HMAC verifies with correct secret + anchor');
      else fail('w7-stale-mint-fresh', 'HMAC verification failed');

      // ts_hex should decode to within last 10 min
      const ts_minutes = parseInt(ts_hex, 16);
      const ts_unix = ANCHOR_UNIX + ts_minutes * 60;
      const age = Math.floor(Date.now() / 1000) - ts_unix;
      if (age >= 0 && age < 120) ok(`w7-stale-mint-fresh: fresh ts_hex is current (age=${age}s)`);
      else fail('w7-stale-mint-fresh', `fresh ts_hex age out of range: ${age}s`);
    }

    // telegram_send_body should have inline_keyboard with 1 button
    const kb = j.telegram_send_body && j.telegram_send_body.reply_markup.inline_keyboard;
    if (kb && kb.length === 1 && kb[0].length === 1)
      ok('w7-stale-mint-fresh: telegram_send_body has 1-button keyboard');
    else fail('w7-stale-mint-fresh', `keyboard shape wrong: ${JSON.stringify(kb)}`);

    // pending_callbacks row written
    const written = fs.readFileSync(tmpFile, 'utf8').trim().split('\n').filter(Boolean);
    if (written.length === 1) {
      const row = JSON.parse(written[0]);
      if (row.kind === 'dispatch' && row.manual_label === true)
        ok(
          'w7-stale-mint-fresh: pending_callbacks row written with kind=dispatch + manual_label=true'
        );
      else fail('w7-stale-mint-fresh', `row kind/manual_label wrong: ${JSON.stringify(row)}`);

      if (row.reminted_from_token === inputJson.token)
        ok('w7-stale-mint-fresh: reminted_from_token links to original expired token');
      else fail('w7-stale-mint-fresh', `reminted_from_token: ${row.reminted_from_token}`);

      if (row.issue_identifier === 'PRO-214')
        ok('w7-stale-mint-fresh: issue_identifier preserved in new row');
      else fail('w7-stale-mint-fresh', `issue_identifier: ${row.issue_identifier}`);
    } else {
      fail('w7-stale-mint-fresh', `expected 1 row in pending_callbacks, got ${written.length}`);
    }
  }

  try {
    fs.unlinkSync(tmpFile);
  } catch (_) {}
}

// ── Step 8: Chain connection integrity ──────────────────────────────────────
{
  const chain = [
    ['w7-check-stale-dispatch', 0, 'w7-stale-lookup'],
    ['w7-stale-lookup', 0, 'w7-stale-edit-expired'],
    ['w7-stale-edit-expired', 0, 'w7-stale-mint-fresh'],
    ['w7-stale-mint-fresh', 0, 'w7-stale-send-fresh'],
  ];
  for (const [src, outputIdx, expectedDest] of chain) {
    const output = workflow.connections[src] && workflow.connections[src].main[outputIdx];
    const dest = output && output[0] && output[0].node;
    if (dest === expectedDest) ok(`chain: ${src} output[${outputIdx}] → ${expectedDest}`);
    else fail(`chain: ${src}`, `expected ${expectedDest}, got ${dest}`);
  }
}

// ── Summary ──────────────────────────────────────────────────────────────────
console.log(`\n${passed + failed} checks: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
