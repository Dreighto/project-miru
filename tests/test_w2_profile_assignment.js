#!/usr/bin/env node
// test_w2_profile_assignment.js
// Boundary-crossing test per adopted lesson (PRO-189 retro):
// Load jsCode from the actual workflow JSON and exercise it.
//
// This test:
// 1. Loads w2_worker_selection_router.json from disk
// 2. Extracts w2008a-assign-profile jsCode
// 3. Exercises it against known signal combinations
// 4. Verifies classification outputs

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const W2_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w2_worker_selection_router.json'
);
const CONFIG_PATH = path.join(__dirname, '..', 'data', 'config', 'w2_profile_rules.json');

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error('  FAIL: ' + message);
    failed++;
  } else {
    passed++;
  }
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    console.error(
      '  FAIL: ' +
        message +
        ' — expected ' +
        JSON.stringify(expected) +
        ', got ' +
        JSON.stringify(actual)
    );
    failed++;
  } else {
    passed++;
  }
}

// ==========================================
// Step 1: Load and parse the workflow JSON
// ==========================================
console.log('Loading workflow JSON...');
const w2 = JSON.parse(fs.readFileSync(W2_PATH, 'utf8'));
const node = w2.nodes.find((n) => n.id === 'w2008a-assign-profile');
assert(node, 'w2008a-assign-profile node exists in workflow JSON');
assert(node.parameters && node.parameters.jsCode, 'node has jsCode');

// ==========================================
// Step 2: Parse the jsCode as JS (catches SyntaxError)
// ==========================================
console.log('Parsing jsCode...');
let jsCode;
try {
  jsCode = node.parameters.jsCode;
  new Function(jsCode); // SyntaxError check
  passed++;
  console.log('  jsCode parses OK (' + jsCode.length + ' chars)');
} catch (e) {
  console.error('  FAIL: jsCode SyntaxError — ' + e.message);
  failed++;
  process.exit(1);
}

// ==========================================
// Step 3: Verify config file loads
// ==========================================
console.log('Verifying config file...');
const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
assert(Array.isArray(config.routine_keywords), 'config has routine_keywords array');
assert(Array.isArray(config.blocked_keywords), 'config has blocked_keywords array');
assert(Array.isArray(config.ambiguous_keywords), 'config has ambiguous_keywords array');
assert(config.mode_profile_map, 'config has mode_profile_map');

// ==========================================
// Step 4: Exercise classification logic
// ==========================================
// The jsCode reads from $input.item.json and uses fs.readFileSync + $getWorkflowStaticData.
// We need to create a sandbox that mocks these n8n globals.
console.log('Exercising classification logic...');

// Load the real config so we can inject it via a mock fs
const configContent = fs.readFileSync(CONFIG_PATH, 'utf8');

function runClassifier(signals, risk) {
  // Build the mock data object that $input.item.json would provide
  const inputData = {
    extracted_signals: signals,
    risk: risk || 'low',
  };

  // Mock fs.readFileSync to intercept the Docker-path config read and serve
  // the real config file content. This is the boundary-crossing test: we run
  // the EXACT jsCode from the workflow JSON, but supply the config via mock
  // since /miru-data/ is a Docker bind mount that doesn't exist on the host.
  const mockFs = {
    readFileSync: function (filePath, enc) {
      if (filePath === '/miru-data/config/w2_profile_rules.json') {
        return configContent;
      }
      return fs.readFileSync(filePath, enc);
    },
    existsSync: fs.existsSync,
  };

  const mockRequire = function (mod) {
    if (mod === 'fs') return mockFs;
    return require(mod);
  };

  // Mock the n8n environment
  const sandbox = {
    $input: { item: { json: inputData } },
    $getWorkflowStaticData: function (scope) {
      return {};
    },
    require: mockRequire,
    console: console,
    JSON: JSON,
    Math: Math,
    Date: Date,
    Array: Array,
    Object: Object,
    String: String,
    Error: Error,
    parseInt: parseInt,
    Buffer: Buffer,
    // The code returns { json: {...} }, capture it
    _result: null,
  };

  // The n8n Code node wraps the jsCode in a function that returns { json: {...} }.
  // We need to handle the return statement. Wrap in a function.
  const wrappedCode = `
    _result = (function() {
      ${jsCode}
    })();
  `;

  const script = new vm.Script(wrappedCode);
  const context = vm.createContext(sandbox);
  script.runInContext(context);
  if (!sandbox._result || !sandbox._result.json) {
    console.error('  FAIL: runClassifier returned no result');
    failed++;
    return {};
  }
  return sandbox._result.json;
}

// Test case 1: Bug + "audit this" keywords → routine/drift_executor (keyword precedence)
console.log('\nTest 1: Bug + audit keyword → routine/drift_executor');
let result = runClassifier(
  {
    task_type: 'Bug',
    surface_keywords: ['audit', 'fix the thing'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'routine', 'task_mode should be routine');
  assertEqual(result.suggested_profile, 'drift_executor', 'profile should be drift_executor');
  assert(
    result.profile_rationale && result.profile_rationale.length <= 120,
    'rationale under 120 chars'
  );
}

// Test case 2: Feature + no special keywords → judgment/standard_worker
console.log('\nTest 2: Feature + no keywords → judgment/standard_worker');
result = runClassifier(
  {
    task_type: 'Feature',
    surface_keywords: ['implement button'],
    touches_paths: ['pm/templates/card_detail.html'],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'judgment', 'task_mode should be judgment');
  assertEqual(result.suggested_profile, 'standard_worker', 'profile should be standard_worker');
}

// Test case 3: Unknown type + "investigate" → ambiguous/reviewer
console.log('\nTest 3: Unknown + investigate keyword → ambiguous/reviewer');
result = runClassifier(
  {
    task_type: 'unknown',
    surface_keywords: ['investigate', 'what is happening'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'ambiguous', 'task_mode should be ambiguous');
  assertEqual(result.suggested_profile, 'reviewer', 'profile should be reviewer');
}

// Test case 4: "blocked by PRO-123" → blocked/null
console.log('\nTest 4: Blocked keyword → blocked/null');
result = runClassifier(
  {
    task_type: 'Feature',
    surface_keywords: ['blocked by', 'PRO-123'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'blocked', 'task_mode should be blocked');
  assertEqual(result.suggested_profile, null, 'profile should be null');
}

// Test case 5: Routine keywords + high risk → judgment/standard_worker (risk override)
console.log('\nTest 5: Routine + high risk → judgment/standard_worker (safety override)');
result = runClassifier(
  {
    task_type: 'research',
    surface_keywords: ['audit', 'repo scan'],
    touches_paths: [],
    research_signal: true,
  },
  'high'
);
if (result) {
  assertEqual(result.task_mode, 'judgment', 'task_mode should be judgment (risk override)');
  assertEqual(
    result.suggested_profile,
    'standard_worker',
    'profile should be standard_worker (risk override)'
  );
}

// Test case 6: research type with no keywords → routine/drift_executor (Tier 2)
console.log('\nTest 6: research type, no keywords → routine/drift_executor (Tier 2)');
result = runClassifier(
  {
    task_type: 'research',
    surface_keywords: ['look at code'],
    touches_paths: [],
    research_signal: true,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'routine', 'task_mode should be routine');
  assertEqual(result.suggested_profile, 'drift_executor', 'profile should be drift_executor');
}

// Test case 7: Improvement type → judgment/standard_worker
console.log('\nTest 7: Improvement type → judgment/standard_worker');
result = runClassifier(
  {
    task_type: 'Improvement',
    surface_keywords: ['refactor the module'],
    touches_paths: ['miru_ai/core/ai.py'],
    research_signal: false,
  },
  'medium'
);
if (result) {
  assertEqual(result.task_mode, 'judgment', 'task_mode should be judgment');
  assertEqual(result.suggested_profile, 'standard_worker', 'profile should be standard_worker');
}

// Test case 8: chore type → judgment/standard_worker
console.log('\nTest 8: chore type → judgment/standard_worker');
result = runClassifier(
  {
    task_type: 'chore',
    surface_keywords: ['clean up logs'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'judgment', 'task_mode should be judgment');
  assertEqual(result.suggested_profile, 'standard_worker', 'profile should be standard_worker');
}

// Test case 9: "second opinion" keyword → routine
console.log('\nTest 9: "second opinion" keyword → routine/drift_executor');
result = runClassifier(
  {
    task_type: 'Bug',
    surface_keywords: ['second opinion', 'on this approach'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'routine', 'task_mode should be routine');
  assertEqual(result.suggested_profile, 'drift_executor', 'profile should be drift_executor');
}

// Test case 10: "figure out" keyword → ambiguous
console.log('\nTest 10: "figure out" keyword → ambiguous/reviewer');
result = runClassifier(
  {
    task_type: 'Feature',
    surface_keywords: ['figure out', 'how to implement'],
    touches_paths: [],
    research_signal: false,
  },
  'low'
);
if (result) {
  assertEqual(result.task_mode, 'ambiguous', 'task_mode should be ambiguous');
  assertEqual(result.suggested_profile, 'reviewer', 'profile should be reviewer');
}

// Test case 11: Missing signals → safe default
console.log('\nTest 11: Missing/empty signals → judgment/standard_worker (safe default)');
result = runClassifier({}, 'low');
if (result) {
  assertEqual(result.task_mode, 'judgment', 'task_mode should be judgment (safe default)');
  assertEqual(
    result.suggested_profile,
    'standard_worker',
    'profile should be standard_worker (safe default)'
  );
}

// Test case 12: Config file missing → graceful fallback to judgment/standard_worker
console.log('\nTest 12: Config missing → judgment/standard_worker (graceful fallback)');
{
  // Create a sandbox where fs.readFileSync throws for the config path
  const brokenFs = {
    readFileSync: function (filePath) {
      throw new Error('ENOENT: no such file');
    },
    existsSync: function () {
      return false;
    },
  };
  const brokenRequire = function (mod) {
    if (mod === 'fs') return brokenFs;
    return require(mod);
  };
  const inputData = {
    extracted_signals: { task_type: 'Bug', surface_keywords: ['audit'] },
    risk: 'low',
  };
  const sandbox = {
    $input: { item: { json: inputData } },
    $getWorkflowStaticData: function () {
      return {};
    },
    require: brokenRequire,
    console,
    JSON,
    Math,
    Date,
    Array,
    Object,
    String,
    Error,
    parseInt,
    Buffer,
    _result: null,
  };
  const wrappedCode = `_result = (function() { ${jsCode} })();`;
  const script = new vm.Script(wrappedCode);
  const context = vm.createContext(sandbox);
  script.runInContext(context);
  const r = sandbox._result ? sandbox._result.json : null;
  if (r) {
    assertEqual(r.task_mode, 'judgment', 'config-missing: task_mode should be judgment');
    assertEqual(
      r.suggested_profile,
      'standard_worker',
      'config-missing: profile should be standard_worker'
    );
    assert(
      r.profile_rationale && r.profile_rationale.includes('config read failed'),
      'config-missing: rationale mentions config failure'
    );
  }
}

// ==========================================
// Step 5: Boundary-crossing syntax tests for W4 and W7 (PRO-189 adopted lesson)
// ==========================================
console.log('\n--- W4/W7 Boundary-Crossing Syntax Tests ---');

const W4_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w4-dispatch-button-handler.json'
);
const W7_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w7-telegram-callback-handler.json'
);

// W4: verify all jsCode nodes parse
console.log('\nW4 jsCode syntax check:');
const w4 = JSON.parse(fs.readFileSync(W4_PATH, 'utf8'));
w4.nodes.forEach((n) => {
  if (n.parameters && n.parameters.jsCode) {
    try {
      new Function(n.parameters.jsCode);
      passed++;
    } catch (e) {
      console.error('  FAIL: W4 node ' + n.id + ' SyntaxError — ' + e.message);
      failed++;
    }
  }
});
console.log('  All W4 jsCode nodes parse OK');

// W4: verify w4021-assemble-prompt has plan-only mode
const w4021 = w4.nodes.find((n) => n.id === 'w4021-assemble-prompt');
assert(w4021, 'W4 w4021-assemble-prompt exists');
assert(w4021.parameters.jsCode.includes('PLAN-ONLY MODE'), 'W4 w4021 has plan-only instructions');
assert(w4021.parameters.jsCode.includes('tool_profile'), 'W4 w4021 reads tool_profile');

// W4: verify w4023 includes tool_profile in POST body
const w4023 = w4.nodes.find((n) => n.id === 'w4023-build-listener-request');
assert(w4023, 'W4 w4023-build-listener-request exists');
assert(
  w4023.parameters.jsCode.includes('tool_profile: data.tool_profile'),
  'W4 w4023 includes tool_profile in POST body'
);

// W7: verify all jsCode nodes parse
console.log('\nW7 jsCode syntax check:');
const w7 = JSON.parse(fs.readFileSync(W7_PATH, 'utf8'));
w7.nodes.forEach((n) => {
  if (n.parameters && n.parameters.jsCode) {
    try {
      new Function(n.parameters.jsCode);
      passed++;
    } catch (e) {
      console.error('  FAIL: W7 node ' + n.id + ' SyntaxError — ' + e.message);
      failed++;
    }
  }
});
console.log('  All W7 jsCode nodes parse OK');

// W7: verify w7006 reads profile_override rows
const w7006 = w7.nodes.find((n) => n.id === 'w7006-lookup-pending');
assert(w7006, 'W7 w7006-lookup-pending exists');
assert(
  w7006.parameters.jsCode.includes('profile_override'),
  'W7 w7006 scans for profile_override rows'
);
assert(
  w7006.parameters.jsCode.includes('final_profile_override'),
  'W7 w7006 outputs final_profile_override'
);

// W7: verify w7-determine-dispatch-target uses final_profile_override
const w7dispatch = w7.nodes.find((n) => n.id === 'w7-determine-dispatch-target');
assert(w7dispatch, 'W7 w7-determine-dispatch-target exists');
assert(
  w7dispatch.parameters.jsCode.includes('final_profile_override'),
  'W7 dispatch target uses final_profile_override'
);

// W7: verify w7-store-pending-dispatch includes task_mode
const w7store = w7.nodes.find((n) => n.id === 'w7-store-pending-dispatch');
assert(w7store, 'W7 w7-store-pending-dispatch exists');
assert(w7store.parameters.jsCode.includes('task_mode'), 'W7 dispatch row includes task_mode');

// ==========================================
// Summary
// ==========================================
console.log('\n' + '='.repeat(50));
console.log('Results: ' + passed + ' passed, ' + failed + ' failed');
if (failed > 0) {
  process.exit(1);
} else {
  console.log('All tests passed!');
}
