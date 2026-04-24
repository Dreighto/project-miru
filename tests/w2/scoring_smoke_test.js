#!/usr/bin/env node
// tests/w2/scoring_smoke_test.js
//
// Standalone smoke test for W2 scoring widening (PRO-60). No n8n dependency.
// The scoreTask() function below is a verbatim copy of the logic that lives
// in the w2007-score-workers Code node of docker/n8n/workflows/
// w2_worker_selection_router.json. If you change scoring, change both.
//
// Run:  node tests/w2/scoring_smoke_test.js
// Exit: 0 on all pass, 1 on any fail. Prints per-fixture verdict + summary.

const fs = require('fs');
const path = require('path');

const RULES_PATH = path.resolve(__dirname, '..', '..', 'data', 'config', 'w2_routing_rules.json');
const TIE_EPSILON = 0.01;
const PRIORITY = { 'claude-code': 0, 'codex': 1, 'cursor': 2, 'gemini': 3 };
const WORKERS = ['claude-code', 'codex', 'cursor', 'gemini'];

// Alias widening keyed by lowercase canon string. Any alias substring hit in
// the haystack counts as a canon hit. Canon literals remain implicit matches.
// Aliases are intentionally tight — prefer a compound phrase over a bare
// English word to avoid false positives. When in doubt, keep it long.
const CANON_ALIASES = {
  // cursor.best_for
  'rapid ui iteration': ['ui iteration', 'ui tweak', 'iterate ui', 'mockup', 'prototype ui'],
  'html/css/svelte':    ['html/css', 'html mockup', 'css mockup', 'tailwind', 'svelte component', '.svelte', '.html', '.css'],
  'live phone testing': ['phone test', 'mobile preview', 'pwa preview', 'device test'],

  // claude-code.best_for
  'careful implementation':  ['careful implementation', 'surgical edit', 'careful refactor'],
  'architecture-sensitive':  ['architecture-sensitive', 'cross-service', 'multi-module', 'architectural change', 'architectural refactor'],
  'multi-step exec':         ['multi-step', 'pipeline change', 'orchestration change'],

  // codex.best_for
  'technical repo work':     ['repo cleanup', 'repo chore', 'toolchain', 'build config', 'ci config'],
  'analysis-heavy coding':   ['analysis-heavy', 'heavy analysis', 'static analysis', 'complex logic'],
  'multi-file implement':    ['multi-file', 'across files', 'several files', 'multi-file refactor'],

  // gemini.best_for
  'audit':             ['repo audit', 'code audit', 'logging audit', 'pattern audit', 'inventory of', 'survey of'],
  'schema reads':      ['schema read', 'db schema read', 'inspect schema', 'data model inspect'],
  'alternate framing': ['alternate framing', 'reframe', 'different angle'],
  'repo scan':         ['repo scan', 'scan repo', 'codebase scan', 'grep across'],
  'second-opinion':    ['second opinion', 'second-opinion', 'sanity check', 'double-check'],

  // hard_no_go — kept tight (false no-go is worse than false go)
  'pure ui iteration':      ['pure ui iteration', 'ui-only change'],
  'read-only audits':       ['read-only audit', 'no-write review', 'dry run audit'],
  'interactive ui builds':  ['interactive ui build'],
  'direct db execution':    ['direct db write', 'write to card_catalog', 'drop table', 'sqlite3 '],
  'backend architecture':   ['backend architecture refactor', 'service rearchitect'],
  'risky refactors':        ['risky refactor', 'hot path refactor'],
  'editing code or templates': ['edit code file', 'edit template file']
};

function scoreTask(signals, title, description, rules) {
  const haystack = [
    signals.task_type || '',
    ...((signals.surface_keywords) || []),
    ...((signals.touches_paths) || []),
    title || '',
    description || ''
  ].join(' ').toLowerCase();

  function phraseHits(phrase) {
    const canon = phrase.toLowerCase();
    if (haystack.indexOf(canon) >= 0) return true;
    const aliases = CANON_ALIASES[canon] || [];
    return aliases.some(a => haystack.indexOf(a) >= 0);
  }

  const scored = WORKERS.map(worker => {
    const cfg = rules[worker] || { best_for: [], hard_no_go: [] };
    const bestForHits = (cfg.best_for || []).filter(phraseHits);
    const noGoHits = (cfg.hard_no_go || []).filter(phraseHits);
    let score = 0.5;
    if (noGoHits.length > 0) {
      score = 0.0;
    } else {
      const capped = Math.min(4, bestForHits.length);
      score = 0.5 + 0.15 * capped;
    }
    score = Math.max(0.0, Math.min(1.0, score));
    const reasoning = noGoHits.length > 0
      ? ('hard no-go: ' + noGoHits.slice(0, 2).join(', '))
      : (bestForHits.length > 0
          ? ('best-for: ' + bestForHits.slice(0, 3).join(', '))
          : 'baseline');
    return { worker, score: Math.round(score * 1000) / 1000, reasoning };
  });

  scored.sort((a, b) => {
    if (Math.abs(a.score - b.score) < TIE_EPSILON) return PRIORITY[a.worker] - PRIORITY[b.worker];
    return b.score - a.score;
  });

  const top = scored[0], second = scored[1];
  const gap = top.score - second.score;
  const margin = top.score - 0.5;
  let confidence;
  if (top.score < 0.55) confidence = 0.0;
  else {
    const base = Math.min(1.0, 0.3 * (gap / 0.5) + 0.7 * (margin / 0.5) + 0.5);
    confidence = (margin < 0.15) ? Math.min(0.50, base) : base;
  }
  confidence = Math.round(confidence * 1000) / 1000;
  return { ranked_candidates: scored, chosen_worker: top.worker, confidence };
}

// Risk classifier mirror from w2008-classify-risk. Trimmed to what fixtures need.
function classifyRisk(signals, priority, description) {
  const paths = signals.touches_paths || [];
  const keywords = signals.surface_keywords || [];
  const desc = (description || '').toLowerCase();
  const RISK_HIGH_PATHS = /card_catalog\.db|migrations\/|schema|\.env|secrets\//i;
  const RISK_HIGH_KEYWORDS = ['auth','secrets','db schema','rate limit','migrate','drop','delete production','purge','truncate','drop table'];
  const LOW_PATHS = /^(docs\/|tests\/|craft guides\/)/;
  const LOW_TYPES = ['chore','design','docs'];
  const wordCount = desc.trim().split(/\s+/).filter(Boolean).length;
  const PROD_KEYWORDS = ['route','endpoint','database','query','sql','migration','auth','credential','secret','api key','production','deploy','rollout','backfill','reindex'];

  const pathHighHit = paths.some(p => RISK_HIGH_PATHS.test(p)) || RISK_HIGH_PATHS.test(desc);
  const keywordHighHits = RISK_HIGH_KEYWORDS.filter(kw => keywords.indexOf(kw) >= 0 || desc.indexOf(kw) >= 0);
  let risk;
  if (pathHighHit || keywordHighHits.length > 0) risk = 'high';
  else {
    const allPathsLow = paths.length === 0 || paths.every(p => LOW_PATHS.test(p));
    const lowType = LOW_TYPES.indexOf(signals.task_type) >= 0;
    const shortDesc = wordCount <= 200;
    const noProdKw = !PROD_KEYWORDS.some(kw => keywords.indexOf(kw) >= 0 || desc.indexOf(kw) >= 0);
    risk = (allPathsLow && lowType && shortDesc && noProdKw) ? 'low' : 'medium';
  }
  const BUMP = { low:'medium', medium:'high', high:'high' };
  if (priority === 1) risk = BUMP[risk];
  return risk;
}

// Research short-circuit (from w2006-extract-signals + w2006a branch).
function researchSignal(labels, combinedText) {
  const RESEARCH_PHRASES = ['find examples of','what do other','compare','pattern reference','how do others','spec reading','rfc reading','inspiration','alternate framing','second opinion','second-opinion'];
  if ((labels || []).includes('research')) return true;
  const t = (combinedText || '').toLowerCase();
  return RESEARCH_PHRASES.some(p => t.indexOf(p) >= 0);
}

// --------------------------- FIXTURES ---------------------------
// Each fixture provides signals mirroring what w2006 would extract from real
// issue text. expect.worker can be 'triage' to mean confidence < 0.75.
const FIXTURES = [
  {
    id: 1,
    name: 'HTML mockup — single PM card tile',
    title: 'HTML mockup — single PM card tile',
    description: 'Static HTML mockup of one card tile using the Rosinante color palette. CSS only, no JS. Saved at docs/mockups/card-tile-mockup.html.',
    signals: { task_type: 'design', surface_keywords: ['ui','html/css'], touches_paths: ['docs/mockups/card-tile-mockup.html'], research_signal: false },
    priority: 3, labels: ['design','Improvement'],
    expect: { worker: 'cursor', minConf: 0.75, risk: 'low' }
  },
  {
    id: 2,
    name: 'Refactor ingestion pipeline across miru_ai/',
    title: 'Refactor ingestion pipeline',
    description: 'Multi-file refactor of the Miru AI ingestion pipeline. Architectural change that touches miru_ai/ingestion/ and miru_ai/workers/. Careful implementation required.',
    signals: { task_type: 'Improvement', surface_keywords: ['refactor','multi-file','architectural','architecture'], touches_paths: ['miru_ai/ingestion/','miru_ai/workers/'], research_signal: false },
    priority: 3, labels: ['Improvement'],
    expect: { worker: 'claude-code', minConf: 0.75, risk: 'medium' }
  },
  {
    id: 3,
    name: 'Fix typo in docs/pm/00_PRINCIPLES.md',
    title: 'Fix typo in principles doc',
    description: 'One-line typo fix in docs/pm/00_PRINCIPLES.md.',
    signals: { task_type: 'chore', surface_keywords: [], touches_paths: ['docs/pm/00_PRINCIPLES.md'], research_signal: false },
    priority: 4, labels: ['chore'],
    expect: { worker: 'any', minConf: 0, risk: 'low' }
  },
  {
    id: 4,
    name: 'Migrate card_catalog schema — add rarity column',
    title: 'Add rarity column to card_catalog schema',
    description: 'DB schema migration: add a rarity column to card_catalog.db. Write migration SQL, backfill, update ingestion to populate. Multi-step execution across migrations/ and miru_ai/ingestion/.',
    signals: { task_type: 'Improvement', surface_keywords: ['migrate','db schema','schema','sql','multi-step','backfill'], touches_paths: ['card_catalog.db','migrations/0003_rarity.sql','miru_ai/ingestion/'], research_signal: false },
    priority: 2, labels: ['Improvement'],
    expect: { worker: 'claude-code', minConf: 0.75, risk: 'high' }
  },
  {
    id: 5,
    name: 'Clean up spike artifacts in data/',
    title: 'Clean up spike files in data/',
    description: 'Remove data/spike_ntfy_log.jsonl, data/spike_telegram_workflow.json, data/spike_workflow.json and add to .gitignore. Tidy-up chore, no logic changes.',
    signals: { task_type: 'chore', surface_keywords: [], touches_paths: ['data/spike_ntfy_log.jsonl','data/spike_telegram_workflow.json','data/spike_workflow.json','data/'], research_signal: false },
    priority: 4, labels: ['chore'],
    expect: { worker: 'triage', maxConf: 0.749, risk: 'medium' }
  },
  {
    id: 6,
    name: 'Compare how other TCG trackers handle deck import (research label)',
    title: 'Survey: how do other TCG trackers handle deck import?',
    description: 'Compare 3-4 other TCG deck tracker tools and document their deck import UX. No code. Pattern reference for Miru deck builder.',
    signals: { task_type: 'research', surface_keywords: [], touches_paths: [], research_signal: true },
    priority: 4, labels: ['research'],
    expect: { researchShortCircuit: true }
  },
  {
    id: 7,
    name: 'Second opinion — is this auth flow sane? (research phrase, no label)',
    title: 'Second opinion on session token flow',
    description: 'I want an alternate framing on the session token handoff in pm/. Second opinion only; no code changes.',
    signals: { task_type: 'unknown', surface_keywords: [], touches_paths: ['pm/'], research_signal: true },
    priority: 4, labels: ['Improvement'],
    expect: { researchShortCircuit: true }
  },
  {
    id: 8,
    name: 'Repo-wide audit of logging patterns',
    title: 'Repo audit of logging patterns',
    description: 'Repo audit: inventory of how logging is done across miru_ai/, pm/, dispatcher/. Scan repo and produce a summary. No code changes.',
    signals: { task_type: 'chore', surface_keywords: ['audit','repo scan'], touches_paths: ['miru_ai/','pm/','dispatcher/'], research_signal: false },
    priority: 4, labels: ['chore'],
    expect: { worker: 'gemini', minConf: 0.65, risk: 'medium' }
  }
];

function run() {
  const rules = JSON.parse(fs.readFileSync(RULES_PATH, 'utf8'));
  const results = [];
  let passCount = 0;
  for (const f of FIXTURES) {
    let pass = true;
    const reasons = [];
    let result;
    if (f.expect.researchShortCircuit) {
      const combined = (f.title + '\n' + f.description);
      const detected = researchSignal(f.labels, combined);
      if (!detected) { pass = false; reasons.push('research signal not detected'); }
      result = { research_signal: detected };
    } else {
      const s = scoreTask(f.signals, f.title, f.description, rules);
      const risk = classifyRisk(f.signals, f.priority, f.description);
      result = { ...s, risk };
      if (f.expect.worker === 'triage') {
        if (s.confidence > f.expect.maxConf) { pass = false; reasons.push(`expected triage (conf<=${f.expect.maxConf}) got ${s.confidence}`); }
      } else if (f.expect.worker && f.expect.worker !== 'any') {
        if (s.chosen_worker !== f.expect.worker) { pass = false; reasons.push(`expected ${f.expect.worker} got ${s.chosen_worker}`); }
        if (typeof f.expect.minConf === 'number' && s.confidence < f.expect.minConf) { pass = false; reasons.push(`conf ${s.confidence} < ${f.expect.minConf}`); }
      }
      if (f.expect.risk && risk !== f.expect.risk) { pass = false; reasons.push(`risk expected ${f.expect.risk} got ${risk}`); }
    }
    results.push({ fixture: f, result, pass, reasons });
    if (pass) passCount++;
    const mark = pass ? 'PASS' : 'FAIL';
    console.log(`[${mark}] #${f.id} ${f.name}`);
    console.log('       result: ' + JSON.stringify(result));
    if (!pass) console.log('       reasons: ' + reasons.join('; '));
  }
  console.log(`\nSummary: ${passCount}/${FIXTURES.length} passed`);
  if (passCount < FIXTURES.length) process.exit(1);
}

if (require.main === module) run();
module.exports = { scoreTask, classifyRisk, researchSignal, CANON_ALIASES, FIXTURES };
