'use strict';
// Boundary-crossing test for w2006-extract-signals (PRO-189 pattern).
// Loads the workflow JSON from disk, extracts the jsCode, and evals it
// to verify that "Do NOT modify" paths are excluded from touches_paths.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const WF_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w2_worker_selection_router.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));
const node = wf.nodes.find((n) => n.name === 'w2006-extract-signals');
if (!node) throw new Error('w2006-extract-signals node not found');
const jsCode = node.parameters?.jsCode || node.parameters?.code;
if (!jsCode) throw new Error('jsCode not found in w2006-extract-signals');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  PASS: ${name}`);
  } catch (e) {
    failed++;
    console.error(`  FAIL: ${name} -- ${e.message}`);
  }
}

function runExtractor(description, title = 'Test ticket') {
  const $input = {
    item: {
      json: {
        data: {
          issue: {
            title,
            description,
            priority: 2,
            labels: { nodes: [{ name: 'Bug', id: 'lbl-1' }] },
          },
          issueLabels: { nodes: [{ name: 'Bug', id: 'lbl-1' }] },
        },
      },
    },
  };
  const $ = (nodeName) => ({
    item: {
      json: {
        issue_id: 'test-id',
        issue_identifier: 'PRO-999',
        trace_id: 'test-trace',
        shadow_mode: false,
        intake_source: 'test',
      },
    },
  });
  const fn = new Function('$input', '$', jsCode);
  return fn($input, $);
}

console.log('test_w2_signal_extraction_eval.js');

// Guard: jsCode must parse without SyntaxError (wrapped in function body since it uses return)
test('jsCode parses without SyntaxError', () => {
  new vm.Script(`(function($input, $){ ${jsCode} })`);
});

// Core fix: paths in "Do NOT modify" lines must be excluded
test('Do NOT modify paths are excluded from touches_paths', () => {
  const result = runExtractor(
    'Fix the bug in tools/emit_completion.py.\n\nDo NOT modify: tools/miru_mcp_gateway/profiles.py'
  );
  const paths = result.json.extracted_signals.touches_paths;
  assert.ok(paths.includes('tools/emit_completion.py'), 'should include the real target path');
  assert.ok(
    !paths.includes('tools/miru_mcp_gateway/profiles.py'),
    'should exclude the do-not-modify path'
  );
});

test("Don't touch paths are excluded", () => {
  const result = runExtractor("Update pm/app.py\n\nDon't touch: miru_ai/core/engine.py");
  const paths = result.json.extracted_signals.touches_paths;
  assert.ok(paths.includes('pm/app.py'), 'should include target');
  assert.ok(!paths.includes('miru_ai/core/engine.py'), 'should exclude dont-touch path');
});

test('Avoid modifying paths are excluded', () => {
  const result = runExtractor('Edit tests/test_foo.py\n\nAvoid modifying: shared/utils.py');
  const paths = result.json.extracted_signals.touches_paths;
  assert.ok(paths.includes('tests/test_foo.py'), 'should include target');
  assert.ok(!paths.includes('shared/utils.py'), 'should exclude avoid-modifying path');
});

// Paths NOT in exclusion lines should still be extracted
test('Normal paths are still extracted', () => {
  const result = runExtractor(
    'This ticket touches tools/check_kill_switch.py and windows/restart_pm.ps1'
  );
  const paths = result.json.extracted_signals.touches_paths;
  assert.ok(paths.includes('tools/check_kill_switch.py'));
  assert.ok(paths.includes('windows/restart_pm.ps1'));
});

// Directory patterns should also be filtered
test('Directory refs in do-not-modify lines are excluded', () => {
  const result = runExtractor(
    'Work in pm/ directory.\n\nDo NOT modify: docker/n8n/workflows/ files'
  );
  const paths = result.json.extracted_signals.touches_paths;
  assert.ok(
    paths.some((p) => p.startsWith('pm/')),
    'should include pm/'
  );
  assert.ok(
    !paths.some((p) => p.startsWith('docker/')),
    'should exclude docker/ from do-not-modify line'
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
