'use strict';
// Boundary-crossing test for parent_watcher_poll.json (PRO-189 lesson).
// Load the JS as it lives in the workflow JSON, eval it, then confirm
// it parses without SyntaxError. This catches deploy-time mangling.
//
// Run: node tests/test_parent_watcher_poll_workflow_eval.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const WORKFLOW = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'parent_watcher_poll.json'
);

let testsRun = 0;
let testsPassed = 0;
function test(name, fn) {
  testsRun += 1;
  try {
    fn();
    testsPassed += 1;
    console.log(`  PASS  ${name}`);
  } catch (err) {
    console.error(`  FAIL  ${name}`);
    console.error(`         ${err.message}`);
    if (err.stack) console.error(err.stack.split('\n').slice(1, 4).join('\n'));
  }
}

const wf = JSON.parse(fs.readFileSync(WORKFLOW, 'utf8'));

function getNodeJsCode(nodeId) {
  const node = wf.nodes.find((n) => n.id === nodeId);
  if (!node) throw new Error(`node ${nodeId} not found in workflow`);
  return node.parameters.jsCode;
}

function buildCodeRunner(jsCode) {
  return new Function('require', '$input', '$env', '$getWorkflowStaticData', `${jsCode}`);
}

// ─── Test 1: pwp003-parse-json jsCode parses ───────────────────────────────
test('pwp003-parse-json jsCode parses without SyntaxError', () => {
  const code = getNodeJsCode('pwp003-parse-json');
  buildCodeRunner(code);
  assert.ok(code.length > 50, 'code should be substantial');
});

// ─── Test 2: pwp005-append-log jsCode parses ───────────────────────────────
test('pwp005-append-log jsCode parses without SyntaxError', () => {
  const code = getNodeJsCode('pwp005-append-log');
  buildCodeRunner(code);
  assert.ok(code.length > 50, 'code should be substantial');
});

// ─── Test 3: pwp003 parse logic with valid input ────────────────────────────
test('pwp003 parses valid parent_watcher.py JSON output', () => {
  const code = getNodeJsCode('pwp003-parse-json');
  const mockInput = {
    item: {
      json: {
        exitCode: 0,
        stdout: JSON.stringify({
          actions: [
            {
              parent: 'PRO-100',
              current_state: 'In Progress',
              proposed_state: 'Done',
              comment: 'All done',
              applied: true,
            },
          ],
          dry_run: false,
        }),
        stderr: '',
      },
    },
  };

  const runner = buildCodeRunner(code);
  const result = runner(require, mockInput, {}, () => ({}));
  assert.strictEqual(result.json.action_count, 1);
  assert.strictEqual(result.json.dry_run, false);
});

// ─── Test 4: pwp003 throws on non-zero exit code ───────────────────────────
test('pwp003 throws on non-zero exit code', () => {
  const code = getNodeJsCode('pwp003-parse-json');
  const mockInput = {
    item: {
      json: {
        exitCode: 1,
        stdout: '',
        stderr: 'some error',
      },
    },
  };

  const runner = buildCodeRunner(code);
  assert.throws(() => runner(require, mockInput, {}, () => ({})), /failed/i);
});

// ─── Test 5: pwp003 throws on empty stdout ──────────────────────────────────
test('pwp003 throws on empty stdout with exit 0', () => {
  const code = getNodeJsCode('pwp003-parse-json');
  const mockInput = {
    item: {
      json: {
        exitCode: 0,
        stdout: '',
        stderr: '',
      },
    },
  };

  const runner = buildCodeRunner(code);
  assert.throws(() => runner(require, mockInput, {}, () => ({})), /empty/i);
});

// ─── Summary ────────────────────────────────────────────────────────────────
console.log(`\n${testsPassed}/${testsRun} tests passed`);
if (testsPassed < testsRun) process.exit(1);
