'use strict';

// Tests for buildMcpConfig — the function that generates every dispatched
// worker's .mcp.json. Covers both branches of the playwright probe and the
// PLAYWRIGHT_MCP_PATH override hierarchy. Stubs fs.existsSync + the project
// logger, restores both after each case to avoid leaking state into sibling
// tests.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const log = require('../src/log');

// Helper: load a fresh mcp_config module so module-level constants like
// PLAYWRIGHT_MCP_PATH re-evaluate against the current env. Without this,
// require() caches the first load and the env-override tests would all see
// whatever the first invocation computed.
function loadMcpConfig() {
  const modPath = require.resolve('../src/mcp_config');
  delete require.cache[modPath];
  return require('../src/mcp_config');
}

test('buildMcpConfig: includes playwright entry when path exists', () => {
  const mod = loadMcpConfig();
  const originalExistsSync = fs.existsSync;
  fs.existsSync = (p) => (p === mod.PLAYWRIGHT_MCP_PATH ? true : originalExistsSync(p));
  try {
    const json = mod.buildMcpConfig();
    const cfg = JSON.parse(json);
    assert.ok(cfg.mcpServers['miru-gateway'], 'miru-gateway entry always present');
    assert.ok(cfg.mcpServers.playwright, 'playwright entry present when path exists');
    assert.equal(cfg.mcpServers.playwright.type, 'stdio');
    assert.equal(cfg.mcpServers.playwright.command, 'node');
    assert.deepEqual(cfg.mcpServers.playwright.args, [mod.PLAYWRIGHT_MCP_PATH]);
  } finally {
    fs.existsSync = originalExistsSync;
  }
});

test('buildMcpConfig: omits playwright + warns when path missing', () => {
  const mod = loadMcpConfig();
  const originalExistsSync = fs.existsSync;
  const originalWarn = log.warn;
  const warnings = [];
  fs.existsSync = (p) => (p === mod.PLAYWRIGHT_MCP_PATH ? false : originalExistsSync(p));
  log.warn = (event, fields) => {
    warnings.push({ event, fields });
  };
  try {
    const json = mod.buildMcpConfig();
    const cfg = JSON.parse(json);
    assert.ok(cfg.mcpServers['miru-gateway'], 'miru-gateway still present');
    assert.equal(
      cfg.mcpServers.playwright,
      undefined,
      'playwright entry omitted when path missing'
    );

    const warning = warnings.find((w) => w.event === 'mcp_playwright_missing');
    assert.ok(warning, 'warning emitted with event mcp_playwright_missing');
    assert.equal(warning.fields.path, mod.PLAYWRIGHT_MCP_PATH);
    assert.match(warning.fields.hint, /npm install -g @playwright\/mcp/);
    assert.match(warning.fields.hint, /restart the dispatch listener/);
  } finally {
    fs.existsSync = originalExistsSync;
    log.warn = originalWarn;
  }
});

test('PLAYWRIGHT_MCP_PATH: honors PLAYWRIGHT_MCP_PATH env override', () => {
  const originalEnv = process.env.PLAYWRIGHT_MCP_PATH;
  process.env.PLAYWRIGHT_MCP_PATH = 'C:\\custom\\path\\to\\mcp\\cli.js';
  try {
    const mod = loadMcpConfig();
    assert.equal(mod.PLAYWRIGHT_MCP_PATH, 'C:\\custom\\path\\to\\mcp\\cli.js');
  } finally {
    if (originalEnv === undefined) {
      delete process.env.PLAYWRIGHT_MCP_PATH;
    } else {
      process.env.PLAYWRIGHT_MCP_PATH = originalEnv;
    }
  }
});

test('PLAYWRIGHT_MCP_PATH: honors NPM_GLOBAL_PREFIX env override', () => {
  const originalPrefix = process.env.NPM_GLOBAL_PREFIX;
  const originalDirect = process.env.PLAYWRIGHT_MCP_PATH;
  delete process.env.PLAYWRIGHT_MCP_PATH; // direct override would win otherwise
  process.env.NPM_GLOBAL_PREFIX = 'D:\\custom-npm-prefix';
  try {
    const mod = loadMcpConfig();
    const expected = path.join(
      'D:\\custom-npm-prefix',
      'node_modules',
      '@playwright',
      'mcp',
      'cli.js'
    );
    assert.equal(mod.PLAYWRIGHT_MCP_PATH, expected);
  } finally {
    if (originalPrefix === undefined) {
      delete process.env.NPM_GLOBAL_PREFIX;
    } else {
      process.env.NPM_GLOBAL_PREFIX = originalPrefix;
    }
    if (originalDirect !== undefined) {
      process.env.PLAYWRIGHT_MCP_PATH = originalDirect;
    }
  }
});

test('PLAYWRIGHT_MCP_PATH: falls back to %APPDATA%/npm when no overrides', () => {
  const originalDirect = process.env.PLAYWRIGHT_MCP_PATH;
  const originalPrefix = process.env.NPM_GLOBAL_PREFIX;
  const originalAppdata = process.env.APPDATA;
  delete process.env.PLAYWRIGHT_MCP_PATH;
  delete process.env.NPM_GLOBAL_PREFIX;
  process.env.APPDATA = 'C:\\Users\\Test\\AppData\\Roaming';
  try {
    const mod = loadMcpConfig();
    const expected = path.join(
      'C:\\Users\\Test\\AppData\\Roaming',
      'npm',
      'node_modules',
      '@playwright',
      'mcp',
      'cli.js'
    );
    assert.equal(mod.PLAYWRIGHT_MCP_PATH, expected);
  } finally {
    if (originalDirect !== undefined) process.env.PLAYWRIGHT_MCP_PATH = originalDirect;
    if (originalPrefix !== undefined) process.env.NPM_GLOBAL_PREFIX = originalPrefix;
    if (originalAppdata === undefined) {
      delete process.env.APPDATA;
    } else {
      process.env.APPDATA = originalAppdata;
    }
  }
});
