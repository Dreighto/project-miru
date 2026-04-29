#!/usr/bin/env node
// tests/w2/test_w2003f_recency_window.js
//
// PRO-208: Boundary-crossing regression test for w2003f-detect-manual-dispatch
// 24h recency window addition.
//
// PRO-189 pattern: loads jsCode from the live workflow JSON file on disk,
// evals it via vm.Script to confirm it parses, then exercises the algorithm
// directly against the loaded code path — not a clean extracted copy.
//
// Exit 0 on all pass, exit 1 on any fail.

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ── Step 1: Load workflow JSON from disk and extract jsCode ──────────────────
const WORKFLOW_PATH = path.resolve(
  __dirname,
  '../../docker/n8n/workflows/w2_worker_selection_router.json'
);
const workflow = JSON.parse(fs.readFileSync(WORKFLOW_PATH, 'utf8'));
const node = workflow.nodes.find((n) => n.id === 'w2003f-detect-manual-dispatch');
if (!node) throw new Error('w2003f-detect-manual-dispatch node not found in workflow JSON');
const jsCode = node.parameters.jsCode;

// ── Step 2: Confirm the code parses without SyntaxError ─────────────────────
// n8n code nodes use top-level `return`, which is only valid inside a function scope.
// Wrap in a function body for the parse check (mirrors how n8n actually runs the code).
try {
  new vm.Script('(function() {' + jsCode + '})');
  console.log('PARSE CHECK: OK (no SyntaxError)');
} catch (e) {
  console.error('PARSE CHECK: FAIL —', e.message);
  process.exit(1);
}

// ── Step 3: Confirm 24h recency window constants are present ─────────────────
const hasRecencyConst = jsCode.includes('RECENCY_WINDOW_MS') && jsCode.includes('cutoff');
if (!hasRecencyConst) {
  console.error('RECENCY CONSTANTS: FAIL — RECENCY_WINDOW_MS / cutoff not found in jsCode');
  process.exit(1);
}
console.log('RECENCY CONSTANTS: OK');

// ── Step 4: Extract and exercise the dedupe algorithm ───────────────────────
// We build a minimal n8n-like harness to run the jsCode, then test that:
//   (a) old rows (>24h) do NOT block re-emission
//   (b) recent rows (<24h) DO block re-emission
//   (c) rows with no timestamp field are treated as old (skipped, not blocking)
//   (d) fresh intent rows (intent_written_at) block re-emission

const tmp = require('os').tmpdir();
const PENDING_FILE = path.join(tmp, 'pro208_test_pending.jsonl');
const HISTORY_FILE = path.join(tmp, 'pro208_test_history.jsonl');

// Patch PENDING/HISTORY paths in the jsCode to use temp files
function buildCode(pendingFile, historyFile) {
  return jsCode
    .replace("'/miru-data/pending_callbacks.jsonl'", JSON.stringify(pendingFile))
    .replace("'/miru-data/routing_history.jsonl'", JSON.stringify(historyFile));
}

function runNode(pendingRows, historyRows, issueId, workerLabel) {
  // Write temp files
  fs.writeFileSync(
    PENDING_FILE,
    pendingRows.map((r) => JSON.stringify(r)).join('\n') + (pendingRows.length ? '\n' : '')
  );
  fs.writeFileSync(
    HISTORY_FILE,
    historyRows.map((r) => JSON.stringify(r)).join('\n') + (historyRows.length ? '\n' : '')
  );

  const code = buildCode(PENDING_FILE, HISTORY_FILE);

  // Minimal $input.item.json shape
  const inputJson = {
    issue_id: issueId,
    issue_identifier: 'PRO-TEST',
    issue_title: 'Test issue',
    labels: { nodes: [{ name: workerLabel }] },
  };

  const result = { json: null };
  const ctx = {
    $input: { item: { json: inputJson } },
    $env: { TELEGRAM_CALLBACK_SECRET: 'test', TELEGRAM_CHAT_ID: '123' },
    require,
    // n8n `return` in a code node sets the output
    _return: null,
  };

  // Wrap the jsCode so we can capture its `return` statement
  const wrapped = `(function() { ${code} })()`;
  try {
    const out = vm.runInNewContext(wrapped, ctx);
    return out ? out.json : null;
  } catch (e) {
    return { _error: e.message };
  }
}

const NOW = Date.now();
const ISSUE_ID = 'issue-uuid-pro-153';
const LABEL = 'claude-code';

// ── Fixture helpers ──────────────────────────────────────────────────────────
function hoursAgo(h) {
  return new Date(NOW - h * 60 * 60 * 1000).toISOString();
}

// ── Fixtures ─────────────────────────────────────────────────────────────────
const FIXTURES = [
  {
    id: 'A-no-history',
    name: 'empty history → should_emit_dispatch=true',
    run: () => {
      const r = runNode([], [], ISSUE_ID, LABEL);
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'B-old-dispatch-row',
    name: 'dispatch row 25h ago (beyond 24h window) → should emit (PRO-208 core fix)',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'dispatch',
            issue_id: ISSUE_ID,
            send_message_ok: true,
            created_at: hoursAgo(25),
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'C-recent-dispatch-row',
    name: 'dispatch row 2h ago (within 24h window) → should NOT emit',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'dispatch',
            issue_id: ISSUE_ID,
            send_message_ok: true,
            created_at: hoursAgo(2),
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === false && r.has_dispatch_row === true;
    },
  },
  {
    id: 'D-old-decided-outcome',
    name: 'dispatched outcome 26h ago → should emit (PRO-208 core fix)',
    run: () => {
      const r = runNode(
        [],
        [
          {
            task_id: ISSUE_ID,
            outcome: 'dispatched',
            timestamp: hoursAgo(26),
            trace_id: 'trace-old',
          },
        ],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'E-recent-decided-outcome',
    name: 'dispatched outcome 1h ago → should NOT emit',
    run: () => {
      const r = runNode(
        [],
        [
          {
            task_id: ISSUE_ID,
            outcome: 'dispatched',
            timestamp: hoursAgo(1),
            trace_id: 'trace-recent',
          },
        ],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === false && r.has_decided_outcome === true;
    },
  },
  {
    id: 'F-old-intent-row',
    name: 'intent row 25h ago (intent_written_at) → should emit',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'intent',
            issue_id: ISSUE_ID,
            intent_written_at: hoursAgo(25),
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'G-recent-intent-row',
    name: 'intent row 30min ago (intent_written_at) → should NOT emit',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'intent',
            issue_id: ISSUE_ID,
            intent_written_at: hoursAgo(0.5),
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === false && r.has_intent_row === true;
    },
  },
  {
    id: 'H-no-timestamp',
    name: 'dispatch row with no timestamp → treated as old, should emit',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'dispatch',
            issue_id: ISSUE_ID,
            send_message_ok: true,
            // no created_at, no intent_written_at
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'I-old-all-three',
    name: 'all three checks fire but all >24h old → should emit (PRO-153 scenario)',
    run: () => {
      const r = runNode(
        [
          { kind: 'dispatch', issue_id: ISSUE_ID, send_message_ok: true, created_at: hoursAgo(48) },
          { kind: 'intent', issue_id: ISSUE_ID, intent_written_at: hoursAgo(36) },
        ],
        [
          {
            task_id: ISSUE_ID,
            outcome: 'dispatched',
            timestamp: hoursAgo(48),
            trace_id: 'trace-old',
          },
        ],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'J-different-issue',
    name: 'old rows for different issue_id do not affect this ticket',
    run: () => {
      const OTHER = 'issue-uuid-other';
      const r = runNode(
        [{ kind: 'dispatch', issue_id: OTHER, send_message_ok: true, created_at: hoursAgo(1) }],
        [
          {
            task_id: OTHER,
            outcome: 'dispatched',
            timestamp: hoursAgo(1),
            trace_id: 'trace-other',
          },
        ],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
  {
    id: 'K-failed-send-still-emits',
    name: 'dispatch row with send_message_ok=false within 24h → still emits (existing Bugbot rule)',
    run: () => {
      const r = runNode(
        [
          {
            kind: 'dispatch',
            issue_id: ISSUE_ID,
            send_message_ok: false,
            created_at: hoursAgo(1),
          },
        ],
        [],
        ISSUE_ID,
        LABEL
      );
      return r && r.should_emit_dispatch === true;
    },
  },
];

// ── Runner ───────────────────────────────────────────────────────────────────
function main() {
  let passed = 0;
  const results = [];

  for (const f of FIXTURES) {
    let ok = false;
    let err = null;
    try {
      ok = !!f.run();
    } catch (e) {
      err = e.message;
    }
    if (ok) passed++;
    results.push({ id: f.id, name: f.name, ok, err });
  }

  // Clean up temp files
  try {
    fs.unlinkSync(PENDING_FILE);
  } catch (_) {}
  try {
    fs.unlinkSync(HISTORY_FILE);
  } catch (_) {}

  const pad = (s, n) => (String(s) + ' '.repeat(n)).slice(0, n);
  console.log('\n' + pad('id', 28) + pad('result', 8) + 'name');
  console.log('-'.repeat(120));
  for (const r of results) {
    const mark = r.ok ? 'PASS' : 'FAIL';
    console.log(pad(r.id, 28) + pad(mark, 8) + r.name + (r.err ? ` [error: ${r.err}]` : ''));
  }
  console.log('-'.repeat(120));
  console.log(`${passed}/${FIXTURES.length} passed`);
  process.exit(passed === FIXTURES.length ? 0 : 1);
}

main();
