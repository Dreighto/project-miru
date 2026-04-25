#!/usr/bin/env node
// tests/w2/dedupe_smoke_test.js
//
// Regression suite for PRO-65 — W2 dedupe guard no-op bug.
// Pure JS re-implementation of:
//   (1) w2001a-linear-poll exclude-label filter (mirrors Linear's labels.every.name.nin)
//   (2) w2003a-dedupe-guard (terminal outcome list + 5-min window, task_id === issue_id)
// Runs standalone — does NOT touch n8n or read workflow JSON.
//
// Exit 0 on all pass, exit 1 on any fail. Prints table.

'use strict';

// Canon from w2001a-linear-poll exclude list
const POLL_EXCLUDE = [
  'claude-code', 'cursor', 'codex', 'gemini',
  'intake-draft', 'triage', 'research', 'pending-approval', 'test-w2'
];

// Canon from w2003a-dedupe-guard
// MUST stay in sync with w2003a-dedupe-guard's terminal list in production W2 workflow JSON.
const TERMINAL_OUTCOMES = ['success', 'fail', 'inconclusive', 'halted', 'triage', 'apply-failed', 'callback-decided', 'picker-decided', 'dispatched', 'skipped-dedupe'];
const WINDOW_MS = 5 * 60 * 1000;

// (1) Poll filter: Linear semantics are `labels.every.name.nin: exclude` —
// keep issue iff EVERY label is NOT in the exclude list. An issue with zero
// labels passes trivially (vacuously true).
function applyPollExclude(issue, exclude = POLL_EXCLUDE) {
  const labels = issue.labels || [];
  return labels.every(l => !exclude.includes(l));
}

// (2) Dedupe guard: walk history newest-first, stop past window,
// block on first task_id match with non-terminal outcome.
function dedupeGuard(task_id, historyRows, nowMs, windowMs = WINDOW_MS) {
  let should_proceed = true;
  let dedupe_reason = '';
  for (let i = historyRows.length - 1; i >= 0; i--) {
    const row = historyRows[i];
    const ts = new Date(row.timestamp).getTime();
    if (isNaN(ts)) continue;
    if (nowMs - ts > windowMs) break;
    if (row.task_id === task_id) {
      if (!TERMINAL_OUTCOMES.includes(row.outcome)) {
        should_proceed = false;
        dedupe_reason = `recent non-terminal row found (outcome=${row.outcome}, trace_id=${row.trace_id})`;
        break;
      }
    }
  }
  return { should_proceed, dedupe_reason };
}

// ---------------------------------------------------------------------------
// Fixtures

const NOW = Date.parse('2026-04-24T21:00:00Z');
const TASK = 'issue-uuid-pro-64';
const OTHER_TASK = 'issue-uuid-pro-99';

function row(offsetSec, outcome, task_id = TASK, trace_id = 'trace-x') {
  return {
    timestamp: new Date(NOW - offsetSec * 1000).toISOString(),
    task_id,
    outcome,
    trace_id
  };
}

const FIXTURES = [
  // --- Poll filter fixtures ---
  {
    id: 'A',
    name: 'pending-approval label excludes issue from poll',
    run: () => applyPollExclude({ labels: ['pending-approval'] }) === false
  },
  {
    id: 'B',
    name: 'worker label (cursor) excludes issue from poll',
    run: () => applyPollExclude({ labels: ['cursor'] }) === false
  },
  {
    id: 'B2',
    name: 'unlabeled issue passes poll filter',
    run: () => applyPollExclude({ labels: [] }) === true
  },
  {
    id: 'B3',
    name: 'benign label (Bug) alone passes poll filter',
    run: () => applyPollExclude({ labels: ['Bug'] }) === true
  },
  {
    id: 'B4',
    name: 'mixed labels — any excluded label filters issue out',
    run: () => applyPollExclude({ labels: ['Bug', 'cursor'] }) === false
  },

  // --- Dedupe-guard fixtures ---
  {
    id: 'C',
    name: 'dispatched 2 min ago does NOT block (terminal)',
    run: () => {
      const history = [row(120, 'dispatched', TASK, 'trace-1')];
      const r = dedupeGuard(TASK, history, NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'D',
    name: 'dispatched 6 min ago does NOT block (past window)',
    run: () => {
      const history = [row(360, 'dispatched', TASK, 'trace-1')];
      const r = dedupeGuard(TASK, history, NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'C-pending',
    name: 'pending 1 min ago blocks (non-terminal)',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'pending')], NOW);
      return r.should_proceed === false;
    }
  },
  {
    id: 'C-callback-decided',
    name: 'callback-decided 1 min ago does NOT block (terminal)',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'callback-decided')], NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'C-picker-decided',
    name: 'picker-decided 1 min ago does NOT block (terminal, PRO-80 Phase A)',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'picker-decided')], NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'D-triage',
    name: 'triage 1 min ago does NOT block (terminal)',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'triage')], NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'D-success',
    name: 'success 1 min ago does NOT block (terminal)',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'success')], NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'D-other-task',
    name: 'non-terminal row for different task_id does not affect this one',
    run: () => {
      const r = dedupeGuard(TASK, [row(60, 'dispatched', OTHER_TASK)], NOW);
      return r.should_proceed === true;
    }
  },
  {
    id: 'C-pro64-timeline',
    name: 'PRO-64 live timeline: pending + dispatched + callback-decided all within 2 min → block',
    run: () => {
      const history = [
        row(183, 'pending',          TASK, 'trace-25cd'),
        row(181, 'dispatched',       TASK, 'trace-25cd'),
        row(98,  'callback-decided', TASK, 'trace-25cd')
      ];
      const r = dedupeGuard(TASK, history, NOW);
      return r.should_proceed === false && /callback-decided|dispatched|pending/.test(r.dedupe_reason);
    }
  },
  {
    id: 'D-empty-history',
    name: 'empty history allows proceed',
    run: () => dedupeGuard(TASK, [], NOW).should_proceed === true
  }
];

// ---------------------------------------------------------------------------
// Runner

function main() {
  let passed = 0;
  const results = [];
  for (const f of FIXTURES) {
    let ok = false;
    let err = null;
    try { ok = !!f.run(); } catch (e) { err = e.message; }
    if (ok) passed++;
    results.push({ id: f.id, name: f.name, ok, err });
  }

  // print table
  const pad = (s, n) => (s + ' '.repeat(n)).slice(0, n);
  console.log(pad('id', 22) + pad('result', 8) + 'name');
  console.log('-'.repeat(100));
  for (const r of results) {
    const mark = r.ok ? 'PASS' : 'FAIL';
    console.log(pad(r.id, 22) + pad(mark, 8) + r.name + (r.err ? ` [error: ${r.err}]` : ''));
  }
  console.log('-'.repeat(100));
  console.log(`${passed}/${FIXTURES.length} passed`);
  process.exit(passed === FIXTURES.length ? 0 : 1);
}

if (require.main === module) main();

module.exports = { applyPollExclude, dedupeGuard, POLL_EXCLUDE, TERMINAL_OUTCOMES, WINDOW_MS, FIXTURES };
