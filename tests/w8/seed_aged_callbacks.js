#!/usr/bin/env node
// tests/w8/seed_aged_callbacks.js
//
// Test harness for PRO-77 — W8 callbacks GC.
// Modes:
//   seed <jsonl-path>     Append 3 aged token-pairs (now-72h) + 2 fresh (now-1h).
//                         Records seeded tokens in a sidecar .w8seed.json.
//   verify <jsonl-path>   Read jsonl back, confirm aged tokens dropped, fresh
//                         tokens kept, and any rows present before seeding are
//                         still preserved.
//
// Pure JS, no n8n runtime. Exit 0 on success, 1 on failure.

'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const SIDECAR_SUFFIX = '.w8seed.json';
const AGED_OFFSET_MS = 72 * 3600 * 1000;
const FRESH_OFFSET_MS = 1 * 3600 * 1000;
const AGED_PAIR_COUNT = 3;
const FRESH_PAIR_COUNT = 2;

function tok() { return crypto.randomBytes(6).toString('hex'); }

function intentRow(token, isoTs) {
  return {
    kind: 'intent',
    token,
    intent_written_at: isoTs,
    trace_id: 'w8-seed-' + token,
    issue_id: 'seed-issue-' + token,
    issue_identifier: 'PRO-W8SEED',
    chosen_worker: 'claude-code',
    confidence: 0.5,
    risk: 'low',
    button_set: 'proposal',
    issue_existing_label_ids: [],
    labels_map: {}
  };
}

function decidedRow(token, isoTs) {
  return {
    kind: 'decided',
    token,
    action: 'a',
    action_label: 'Approve',
    decided_at: isoTs,
    decided_by_user_id: null,
    trace_id: 'w8-seed-' + token,
    task_id: 'seed-issue-' + token,
    task_identifier: 'PRO-W8SEED',
    edit_message_ok: true
  };
}

function readJsonlTokens(filePath) {
  if (!fs.existsSync(filePath)) return new Set();
  const tokens = new Set();
  const raw = fs.readFileSync(filePath, 'utf8');
  for (const line of raw.split('\n')) {
    if (!line) continue;
    try {
      const obj = JSON.parse(line);
      if (obj && obj.token) tokens.add(obj.token);
    } catch (_) { /* ignore malformed */ }
  }
  return tokens;
}

function readJsonlLines(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, 'utf8').split('\n').filter(Boolean);
}

function cmdSeed(filePath) {
  const sidecar = filePath + SIDECAR_SUFFIX;
  if (fs.existsSync(sidecar)) {
    console.error(`[seed] sidecar already exists: ${sidecar}`);
    console.error(`[seed] run \`verify\` or remove the sidecar before re-seeding.`);
    process.exit(1);
  }

  const preTokens = Array.from(readJsonlTokens(filePath));
  const preLines = readJsonlLines(filePath);

  const now = Date.now();
  const agedIso = new Date(now - AGED_OFFSET_MS).toISOString();
  const freshIso = new Date(now - FRESH_OFFSET_MS).toISOString();

  const agedTokens = [];
  const freshTokens = [];
  const appended = [];

  for (let i = 0; i < AGED_PAIR_COUNT; i++) {
    const t = tok();
    agedTokens.push(t);
    appended.push(JSON.stringify(intentRow(t, agedIso)));
    appended.push(JSON.stringify(decidedRow(t, agedIso)));
  }
  for (let i = 0; i < FRESH_PAIR_COUNT; i++) {
    const t = tok();
    freshTokens.push(t);
    appended.push(JSON.stringify(intentRow(t, freshIso)));
    appended.push(JSON.stringify(decidedRow(t, freshIso)));
  }

  fs.appendFileSync(filePath, appended.join('\n') + '\n');

  fs.writeFileSync(sidecar, JSON.stringify({
    seeded_at: new Date().toISOString(),
    file: path.resolve(filePath),
    aged_tokens: agedTokens,
    fresh_tokens: freshTokens,
    pre_seed_tokens: preTokens,
    pre_seed_line_count: preLines.length
  }, null, 2));

  console.log(`[seed] appended ${AGED_PAIR_COUNT} aged pairs + ${FRESH_PAIR_COUNT} fresh pairs to ${filePath}`);
  console.log(`[seed] aged tokens (should be dropped):  ${agedTokens.join(', ')}`);
  console.log(`[seed] fresh tokens (should survive):    ${freshTokens.join(', ')}`);
  console.log(`[seed] sidecar: ${sidecar}`);
}

function cmdVerify(filePath) {
  const sidecar = filePath + SIDECAR_SUFFIX;
  if (!fs.existsSync(sidecar)) {
    console.error(`[verify] no sidecar at ${sidecar} — did you run \`seed\` first?`);
    process.exit(1);
  }
  const meta = JSON.parse(fs.readFileSync(sidecar, 'utf8'));

  const postTokens = readJsonlTokens(filePath);

  const failures = [];

  for (const t of meta.aged_tokens) {
    if (postTokens.has(t)) failures.push(`aged token ${t} should be dropped, still present`);
  }
  for (const t of meta.fresh_tokens) {
    if (!postTokens.has(t)) failures.push(`fresh token ${t} should be kept, missing`);
  }
  for (const t of meta.pre_seed_tokens) {
    if (!postTokens.has(t)) failures.push(`pre-seed token ${t} disappeared (GC dropped it — was it >48h old already?)`);
  }

  console.log(`[verify] file: ${filePath}`);
  console.log(`[verify] aged tokens dropped: ${meta.aged_tokens.filter(t => !postTokens.has(t)).length}/${meta.aged_tokens.length}`);
  console.log(`[verify] fresh tokens kept:   ${meta.fresh_tokens.filter(t => postTokens.has(t)).length}/${meta.fresh_tokens.length}`);
  console.log(`[verify] pre-seed tokens preserved: ${meta.pre_seed_tokens.filter(t => postTokens.has(t)).length}/${meta.pre_seed_tokens.length}`);

  if (failures.length === 0) {
    console.log(`[verify] PASS`);
    fs.unlinkSync(sidecar);
    process.exit(0);
  } else {
    console.error(`[verify] FAIL:`);
    for (const f of failures) console.error(`  - ${f}`);
    console.error(`[verify] sidecar kept at ${sidecar} for debugging`);
    process.exit(1);
  }
}

function main() {
  const [, , cmd, filePath] = process.argv;
  if (!cmd || !filePath) {
    console.error('usage: seed_aged_callbacks.js <seed|verify> <jsonl-path>');
    process.exit(2);
  }
  if (cmd === 'seed') return cmdSeed(filePath);
  if (cmd === 'verify') return cmdVerify(filePath);
  console.error(`unknown command: ${cmd}`);
  process.exit(2);
}

main();
