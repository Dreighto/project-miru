#!/usr/bin/env node
// tests/w7/test_w7_pro126_mutation_body_consistency.js
//
// PRO-126: Boundary-crossing regression test for w7008-build-mutation +
// w7-picker-build-mutation return-shape consistency.
//
// The bug: when build-mutation early-returned an error, it set _build_error
// (string) but left mutation_body_obj undefined. When build-mutation
// succeeded, it set mutation_body_obj but left _build_error undefined. The
// downstream IF (w7008-error-branch / w7-picker-error-branch) tested
// _build_error with operator string.notEmpty + typeValidation: strict.
// Strict-string check on undefined is non-deterministic across n8n
// versions, and could route a no-mutation-body item to the HTTP node,
// producing "undefined" as the JSON body — n8n's HTTP node parser then
// throws `"undefined" is not valid JSON`.
//
// The fix: always set _build_error as a string. Empty string on success,
// non-empty string on error. The IF's strict-string notEmpty now works
// deterministically.
//
// PRO-189 canon: load jsCode from the live workflow JSON file on disk,
// confirm it parses, then exercise the algorithm against that loaded
// code path — not a clean extracted copy.

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WORKFLOW_PATH = path.resolve(
  __dirname,
  '../../docker/n8n/workflows/w7-telegram-callback-handler.json'
);
const workflow = JSON.parse(fs.readFileSync(WORKFLOW_PATH, 'utf8'));

function getNode(name) {
  const node = workflow.nodes.find((n) => n.name === name);
  if (!node) throw new Error(`Node not found: ${name}`);
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

// ── Step 1: Parse checks ────────────────────────────────────────────────────
for (const nodeName of ['w7008-build-mutation', 'w7-picker-build-mutation']) {
  const node = getNode(nodeName);
  const jsCode = node.parameters.jsCode;
  try {
    new vm.Script('(function() {' + jsCode + '})');
    ok(`${nodeName} jsCode parses without SyntaxError`);
  } catch (e) {
    fail(`${nodeName} jsCode parse`, e.message);
  }
}

// ── Step 2: Verify IF nodes still test _build_error string.notEmpty ──────────
// (Defends against the IF being changed without a corresponding build-mutation
// shape change. If the IF is updated later, this test may need updating —
// but at that point the contract changes.)
for (const ifNode of ['w7008-error-branch', 'w7-picker-error-branch']) {
  const node = getNode(ifNode);
  const conditions = node.parameters.conditions.conditions;
  const cond = conditions.find(
    (c) =>
      c.leftValue.includes('_build_error') &&
      c.operator.type === 'string' &&
      c.operator.operation === 'notEmpty'
  );
  if (cond) ok(`${ifNode} still tests _build_error with string.notEmpty`);
  else fail(ifNode, 'expected _build_error string.notEmpty condition');
}

// ── Step 3: Exercise w7008-build-mutation ──────────────────────────────────
function runBuildMutation(nodeName, inputJson) {
  const jsCode = getNode(nodeName).parameters.jsCode;
  const fn = new Function('$input', jsCode);
  return fn({ item: { json: inputJson } });
}

// 3a. Success path (action='a', well-formed input)
{
  const out = runBuildMutation('w7008-build-mutation', {
    issue_id: 'issue-001',
    issue_existing_label_ids: ['label-pending', 'label-other'],
    chosen_worker: 'claude-code',
    confidence: 0.85,
    risk: 'low',
    action: 'a',
    from_user_id: 12345,
    callback_age_seconds: 30,
    trace_id: 'trace-success',
    labels_map: {
      'pending-approval': 'label-pending',
      'claude-code': 'label-cc',
      triage: 'label-triage',
      'manual-intervention-required': 'label-manual',
    },
  });
  const j = out.json;

  if (j._build_error === '') ok('w7008 success: _build_error is empty string');
  else fail('w7008 success', `_build_error not '': ${JSON.stringify(j._build_error)}`);

  if (typeof j._build_error === 'string')
    ok('w7008 success: _build_error type is string (deterministic for IF strict typing)');
  else fail('w7008 success', `_build_error type: ${typeof j._build_error}`);

  if (j.mutation_body_obj && typeof j.mutation_body_obj === 'object')
    ok('w7008 success: mutation_body_obj is a populated object');
  else fail('w7008 success', `mutation_body_obj: ${JSON.stringify(j.mutation_body_obj)}`);

  if (j.mutation_body_obj && j.mutation_body_obj.variables.issueId === 'issue-001')
    ok('w7008 success: mutation_body_obj.variables.issueId carried through');
  else fail('w7008 success', 'issueId not in mutation_body_obj');

  if (j.action_label === 'Approve') ok('w7008 success: action_label = Approve');
  else fail('w7008 success', `action_label: ${j.action_label}`);
}

// 3b. Error path: missing pending-approval label
{
  const out = runBuildMutation('w7008-build-mutation', {
    action: 'a',
    chosen_worker: 'claude-code',
    issue_existing_label_ids: [],
    labels_map: { 'claude-code': 'label-cc' }, // no pending-approval
  });
  const j = out.json;

  if (typeof j._build_error === 'string' && j._build_error.length > 0)
    ok('w7008 missing-pending-approval: _build_error is non-empty string');
  else fail('w7008 missing-pending-approval', `_build_error: ${j._build_error}`);

  if (j._build_error.includes('pending-approval'))
    ok('w7008 missing-pending-approval: _build_error message references the missing label');
  else fail('w7008 missing-pending-approval', `unexpected _build_error: ${j._build_error}`);
}

// 3c. Error path: action='a' but worker label missing
{
  const out = runBuildMutation('w7008-build-mutation', {
    action: 'a',
    chosen_worker: 'gemini',
    issue_existing_label_ids: ['label-pending'],
    labels_map: { 'pending-approval': 'label-pending' }, // no gemini key
  });
  const j = out.json;
  if (j._build_error && j._build_error.includes('gemini'))
    ok('w7008 missing-worker-label: _build_error names the worker');
  else fail('w7008 missing-worker-label', `_build_error: ${j._build_error}`);
}

// 3d. Error path: unknown action code
{
  const out = runBuildMutation('w7008-build-mutation', {
    action: 'z', // unknown
    chosen_worker: 'claude-code',
    issue_existing_label_ids: ['label-pending'],
    labels_map: {
      'pending-approval': 'label-pending',
      'claude-code': 'label-cc',
    },
  });
  const j = out.json;
  if (j._build_error && j._build_error.includes('unknown action code'))
    ok('w7008 unknown-action: _build_error reports unknown action');
  else fail('w7008 unknown-action', `_build_error: ${j._build_error}`);
}

// 3e. Action='t' (triage) — success path with no chosen worker dependency
{
  const out = runBuildMutation('w7008-build-mutation', {
    action: 't',
    chosen_worker: 'claude-code',
    issue_id: 'issue-002',
    issue_existing_label_ids: ['label-pending'],
    labels_map: {
      'pending-approval': 'label-pending',
      triage: 'label-triage',
      'claude-code': 'label-cc',
    },
  });
  const j = out.json;
  if (j._build_error === '' && j.mutation_body_obj)
    ok("w7008 action='t' success: _build_error empty + mutation_body_obj set");
  else
    fail(
      "w7008 action='t'",
      `_build_error=${j._build_error}, mutation_body_obj=${!!j.mutation_body_obj}`
    );
}

// ── Step 4: Exercise w7-picker-build-mutation ─────────────────────────────
// 4a. Success path
{
  const out = runBuildMutation('w7-picker-build-mutation', {
    action: 'c', // claude-code
    chosen_worker: 'cursor',
    issue_id: 'issue-100',
    issue_existing_label_ids: ['label-pending', 'label-cursor'],
    labels_map: {
      'pending-approval': 'label-pending',
      'claude-code': 'label-cc',
      cursor: 'label-cursor',
      codex: 'label-codex',
      gemini: 'label-gemini',
      triage: 'label-triage',
    },
  });
  const j = out.json;

  if (j._build_error === '') ok('picker success: _build_error is empty string');
  else fail('picker success', `_build_error: ${JSON.stringify(j._build_error)}`);

  if (j.mutation_body_obj && j.mutation_body_obj.variables.issueId === 'issue-100')
    ok('picker success: mutation_body_obj populated with issueId');
  else fail('picker success', 'mutation_body_obj wrong');

  if (j.picker_label_name === 'claude-code') ok('picker success: picker_label_name = claude-code');
  else fail('picker success', `picker_label_name: ${j.picker_label_name}`);
}

// 4b. Error: unknown picker action
{
  const out = runBuildMutation('w7-picker-build-mutation', {
    action: 'q', // not in c/u/x/g/T
    chosen_worker: 'cursor',
    labels_map: { 'pending-approval': 'label-pending' },
  });
  const j = out.json;
  if (j._build_error && j._build_error.includes('unknown picker action'))
    ok('picker unknown-action: _build_error reports unknown picker action');
  else fail('picker unknown-action', `_build_error: ${j._build_error}`);
}

// 4c. Error: picker label not in labels_map
{
  const out = runBuildMutation('w7-picker-build-mutation', {
    action: 'g', // gemini, but missing from labels_map
    chosen_worker: 'cursor',
    labels_map: {
      'pending-approval': 'label-pending',
      'claude-code': 'label-cc',
    },
  });
  const j = out.json;
  if (j._build_error && j._build_error.includes('gemini'))
    ok('picker missing-label: _build_error names the missing label');
  else fail('picker missing-label', `_build_error: ${j._build_error}`);
}

// ── Step 5: IF semantics regression — verify the new shapes route correctly ─
// Simulates n8n's `string.notEmpty` operator on the _build_error field.
// Real n8n: notEmpty on a string is true iff string.length > 0.
function nNotEmpty(value) {
  return typeof value === 'string' && value.length > 0;
}

// Success cases route to FALSE (output[1]) → HTTP node
{
  const successOuts = [
    runBuildMutation('w7008-build-mutation', {
      action: 'a',
      chosen_worker: 'claude-code',
      issue_id: 'i1',
      issue_existing_label_ids: ['lp'],
      labels_map: { 'pending-approval': 'lp', 'claude-code': 'lcc' },
    }),
    runBuildMutation('w7-picker-build-mutation', {
      action: 'c',
      chosen_worker: 'cursor',
      issue_id: 'i2',
      issue_existing_label_ids: ['lp'],
      labels_map: {
        'pending-approval': 'lp',
        'claude-code': 'lcc',
        cursor: 'lcu',
        codex: 'lcx',
        gemini: 'lg',
      },
    }),
  ];
  for (const out of successOuts) {
    const routesToError = nNotEmpty(out.json._build_error);
    if (!routesToError && out.json.mutation_body_obj)
      ok('IF semantics: success → FALSE branch (HTTP node) with mutation_body_obj set');
    else
      fail(
        'IF semantics success',
        `routesToError=${routesToError}, has_body=${!!out.json.mutation_body_obj}`
      );
  }
}

// Error cases route to TRUE (output[0]) → error path
{
  const errorOuts = [
    runBuildMutation('w7008-build-mutation', {
      action: 'a',
      chosen_worker: 'claude-code',
      labels_map: {}, // missing pending-approval
    }),
    runBuildMutation('w7-picker-build-mutation', {
      action: 'q', // unknown
      labels_map: {},
    }),
  ];
  for (const out of errorOuts) {
    const routesToError = nNotEmpty(out.json._build_error);
    if (routesToError) ok('IF semantics: error → TRUE branch (error path)');
    else
      fail(
        'IF semantics error',
        `expected TRUE branch, got FALSE; _build_error=${out.json._build_error}`
      );
  }
}

// ── Step 6: HTTP body-expression result on the new shapes ──────────────────
// Simulates n8n's `={{ JSON.stringify($json.mutation_body_obj) }}` expression.
// On the new success shape: produces a valid JSON string. Never "undefined".
{
  const out = runBuildMutation('w7008-build-mutation', {
    action: 'a',
    chosen_worker: 'claude-code',
    issue_id: 'i-final',
    issue_existing_label_ids: ['lp'],
    labels_map: { 'pending-approval': 'lp', 'claude-code': 'lcc' },
  });
  const stringified = JSON.stringify(out.json.mutation_body_obj);
  if (stringified !== undefined && stringified !== 'undefined') {
    try {
      JSON.parse(stringified);
      ok('w7009 HTTP body: JSON.stringify(mutation_body_obj) produces valid JSON');
    } catch (e) {
      fail('w7009 HTTP body', `JSON.parse failed: ${e.message}`);
    }
  } else {
    fail('w7009 HTTP body', `stringified is ${stringified} — original bug would still hit`);
  }
}

console.log(`\n${passed + failed} checks: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
