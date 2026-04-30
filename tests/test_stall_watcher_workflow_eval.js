'use strict';
// Boundary-crossing test for stlw002-find-stalls (PRO-189 / PRO-236).
// Loads docker/n8n/workflows/w-stall-watcher.json from disk, extracts the
// jsCode string from the stlw002-find-stalls node, and evals it in a
// controlled harness — the same boundary n8n crosses when it runs the workflow.
//
// This catches bugs that live in the JSON-to-eval path:
//   Bug A (PRO-189): $getWorkflowStaticData call mangled to string, mutations lost
//   Bug B (PRO-189): literal LF inside a string literal → SyntaxError at eval time
//
// Run with: node tests/test_stall_watcher_workflow_eval.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// ---- load workflow ----

const WF_PATH = path.join(__dirname, '..', 'docker', 'n8n', 'workflows', 'w-stall-watcher.json');
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));
const jsCode = wf.nodes.find((n) => n.id === 'stlw002-find-stalls')?.parameters?.jsCode;
if (!jsCode) throw new Error('stlw002-find-stalls node or jsCode not found in workflow JSON');

// ---- harness ----

const TMP_DIR = path.join(__dirname, '_tmp');
if (!fs.existsSync(TMP_DIR)) fs.mkdirSync(TMP_DIR, { recursive: true });

function makeTmpPath(suffix) {
  return path.join(
    TMP_DIR,
    'stlw002_' + Date.now() + '_' + Math.random().toString(36).slice(2) + suffix + '.jsonl'
  );
}

// Run the stlw002 jsCode in a sandboxed function.
// hbContent: string written to a temp HB file, or null (simulates missing file).
// cpContent: string written to a temp CP file, or null (simulates missing file).
// initState: object (initial static-data values, mutated in place).
// Returns { json: <output json>, state: <the mutated state object> }.
function runSTLW002(hbContent, cpContent, initState) {
  const state = Object.assign({}, initState);
  if (initState.stall_alerted) {
    state.stall_alerted = Object.assign({}, initState.stall_alerted);
  }

  const hbPath = hbContent !== null ? makeTmpPath('_hb') : '/nonexistent/hb_sentinel_' + Date.now();
  const cpPath = cpContent !== null ? makeTmpPath('_cp') : '/nonexistent/cp_sentinel_' + Date.now();

  if (hbContent !== null) fs.writeFileSync(hbPath, hbContent, 'utf8');
  if (cpContent !== null) fs.writeFileSync(cpPath, cpContent, 'utf8');

  // Patch both hardcoded paths. JSON.stringify produces a valid JS string
  // literal so Windows backslashes are properly escaped for the JS engine.
  let patchedCode = jsCode.replace("'/miru-data/cc_heartbeat_log.jsonl'", JSON.stringify(hbPath));
  patchedCode = patchedCode.replace("'/miru-data/cc_completion_log.jsonl'", JSON.stringify(cpPath));

  let output;
  try {
    const fn = new Function('$getWorkflowStaticData', 'require', patchedCode);
    output = fn(() => state, require);
  } finally {
    if (hbContent !== null && fs.existsSync(hbPath)) fs.unlinkSync(hbPath);
    if (cpContent !== null && fs.existsSync(cpPath)) fs.unlinkSync(cpPath);
  }

  return { json: output[0].json, state };
}

// Build a heartbeat row with ts = now - ageSec seconds.
function mkHbRow(workerId, ageSec, ticketId, step, branch, stallSignal) {
  const ts = new Date(Date.now() - ageSec * 1000).toISOString();
  return JSON.stringify({
    ts,
    worker_id: workerId,
    ticket_id: ticketId || null,
    status: 'IN_PROGRESS',
    step: step || null,
    branch: branch || null,
    stall_signal: stallSignal || null,
  });
}

function mkCpRow(ticketId) {
  return JSON.stringify({
    ticket_id: ticketId,
    status: 'CONFIRMED_WORKING',
    timestamp: new Date().toISOString(),
  });
}

// ---- test runner ----

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
    passed++;
  } catch (e) {
    console.log(`  FAIL  ${name}: ${e.message}`);
    failed++;
  }
}

// ---- Bug-specific guard tests (catch PRO-189 regressions) ----

// Guard B: jsCode must parse without SyntaxError (catches literal-newline-in-string bug).
test('Guard B: jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($getWorkflowStaticData,require){' + jsCode + '})');
});

// Guard A: $getWorkflowStaticData must be called and its return value mutated
// (catches mangled-call bug — if staticData is a plain string, stall_alerted is never written).
test('Guard A: $getWorkflowStaticData call mutates state (stall_alerted initialized)', () => {
  const { state } = runSTLW002('', '', {});
  assert.ok(
    state.stall_alerted !== undefined &&
      typeof state.stall_alerted === 'object' &&
      !Array.isArray(state.stall_alerted),
    'stall_alerted must be an object in state — Bug A would leave it absent'
  );
});

// ---- Algorithm correctness tests ----

// Case 1: No heartbeat rows → empty alerts, 0 checked_workers.
test('Case 1: no heartbeat rows → empty alerts, 0 checked_workers', () => {
  const { json } = runSTLW002('', '', {});
  assert.deepStrictEqual(json.stall_alerts, []);
  assert.strictEqual(json.checked_workers, 0);
});

// Case 2: Active heartbeat (30s old) → no stall.
test('Case 2: active heartbeat (30s old) → no stall', () => {
  const hb = mkHbRow('cc-1', 30, 'PRO-001');
  const { json } = runSTLW002(hb + '\n', '', {});
  assert.deepStrictEqual(json.stall_alerts, []);
  assert.strictEqual(json.checked_workers, 1);
});

// Case 3: Stale heartbeat (10min old, no completion) → one alert with correct fields.
test('Case 3: stale heartbeat (10min old, no completion) → one alert', () => {
  const hb = mkHbRow('cc-1', 600, 'PRO-001', 'writing_tests', 'dreighto/pro-001', null);
  const { json, state } = runSTLW002(hb + '\n', '', {});
  assert.strictEqual(json.stall_alerts.length, 1);
  const a = json.stall_alerts[0];
  assert.strictEqual(a.worker_id, 'cc-1');
  assert.strictEqual(a.ticket_id, 'PRO-001');
  assert.strictEqual(a.step, 'writing_tests');
  assert.ok(
    a.age_minutes >= 9 && a.age_minutes <= 11,
    `age_minutes out of range: ${a.age_minutes}`
  );
  assert.ok(
    state.stall_alerted['cc-1:PRO-001'],
    'stall_alerted key must be written to state for dedup'
  );
});

// Case 4: Stale heartbeat but ticket appears in completion log → no alert.
test('Case 4: stale heartbeat + ticket in completion log → no alert', () => {
  const hb = mkHbRow('cc-1', 600, 'PRO-002');
  const cp = mkCpRow('PRO-002');
  const { json } = runSTLW002(hb + '\n', cp + '\n', {});
  assert.deepStrictEqual(json.stall_alerts, []);
});

// Case 5: Dedup — stall already alerted for same worker+ticket within 1hr → no duplicate.
test('Case 5: dedup — recent stall_alerted entry → no duplicate alert', () => {
  const hb = mkHbRow('cc-1', 600, 'PRO-003');
  const initState = { stall_alerted: { 'cc-1:PRO-003': new Date().toISOString() } };
  const { json } = runSTLW002(hb + '\n', '', initState);
  assert.deepStrictEqual(json.stall_alerts, []);
});

// Case 6: Dedup entry older than 1hr is evicted → alert fires again.
test('Case 6: dedup entry >1hr old is evicted → new alert fires', () => {
  const hb = mkHbRow('cc-1', 600, 'PRO-004');
  const oldTs = new Date(Date.now() - 3700 * 1000).toISOString();
  const initState = { stall_alerted: { 'cc-1:PRO-004': oldTs } };
  const { json } = runSTLW002(hb + '\n', '', initState);
  assert.strictEqual(json.stall_alerts.length, 1);
  assert.strictEqual(json.stall_alerts[0].worker_id, 'cc-1');
});

// Case 7: Multiple workers, only one stalled → only stalled worker in alerts.
test('Case 7: multiple workers, only one stalled → correct worker in alerts', () => {
  const activeHb = mkHbRow('cc-active', 30, 'PRO-010');
  const stalledHb = mkHbRow(
    'cc-stalled',
    600,
    'PRO-011',
    'awaiting_bugbot',
    null,
    'awaiting_external: bugbot'
  );
  const hbContent = activeHb + '\n' + stalledHb + '\n';
  const { json } = runSTLW002(hbContent, '', {});
  assert.strictEqual(json.checked_workers, 2);
  assert.strictEqual(json.stall_alerts.length, 1);
  assert.strictEqual(json.stall_alerts[0].worker_id, 'cc-stalled');
  assert.strictEqual(json.stall_alerts[0].ticket_id, 'PRO-011');
  assert.strictEqual(json.stall_alerts[0].stall_signal, 'awaiting_external: bugbot');
});

// Case 8: Latest heartbeat row wins when worker has multiple rows.
// Old row (600s) + fresh row (30s) for same worker → latest is fresh → no stall.
test('Case 8: latest row wins over older rows for same worker_id', () => {
  const oldHb = mkHbRow('cc-1', 600, 'PRO-020');
  const freshHb = mkHbRow('cc-1', 30, 'PRO-020');
  const hbContent = oldHb + '\n' + freshHb + '\n';
  const { json } = runSTLW002(hbContent, '', {});
  assert.deepStrictEqual(json.stall_alerts, []);
  assert.strictEqual(json.checked_workers, 1);
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
