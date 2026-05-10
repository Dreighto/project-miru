'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const log = require('./log');

const GATEWAY_PORT = 18766;

// Playwright MCP server discovery (CodeRabbit R2):
// 1. PLAYWRIGHT_MCP_PATH env var — explicit override, used as-is.
// 2. NPM_GLOBAL_PREFIX env var — points at a custom npm prefix dir, we append
//    the standard suffix.
// 3. Default — %APPDATA%/npm (the Windows winget Node.js + global install
//    layout the operator runs today). homedir fallback for non-Windows or
//    weird env layouts.
// This handles version managers (volta, nvm-windows, fnm) and pnpm/yarn
// global dirs without hardcoding the operator's path.
function resolvePlaywrightMcpPath() {
  if (process.env.PLAYWRIGHT_MCP_PATH) {
    return process.env.PLAYWRIGHT_MCP_PATH;
  }
  const npmPrefix =
    process.env.NPM_GLOBAL_PREFIX ||
    path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'npm');
  return path.join(npmPrefix, 'node_modules', '@playwright', 'mcp', 'cli.js');
}

const PLAYWRIGHT_MCP_PATH = resolvePlaywrightMcpPath();

function buildMcpConfig() {
  const mcpServers = {
    'miru-gateway': {
      type: 'http',
      url: `http://127.0.0.1:${GATEWAY_PORT}/mcp`,
      headers: {
        'X-Miru-Tool-Profile': '${MIRU_TOOL_PROFILE}',
        'X-Miru-Trace-Id': '${MIRU_TRACE_ID}',
      },
    },
  };

  // Add Playwright MCP only if installed. Operator's request 2026-05-10:
  // gemini failed LOS-7 first try because it lacked Playwright MCP and
  // thrashed for 22 min trying to npm-install it in the worktree. Now both
  // claude-code AND gemini lanes get Playwright via the dispatch listener's
  // generated .mcp.json, satisfying the canon's mandatory iPhone gate.
  // Stdio MCP servers do NOT pass through the X-Miru-Tool-Profile filtering
  // that the http gateway uses — Playwright is granted to all workers
  // unconditionally. Acceptable: Playwright tools are sandboxed to a
  // browser context and don't expose filesystem/network beyond what the
  // worker shell already has.
  if (fs.existsSync(PLAYWRIGHT_MCP_PATH)) {
    mcpServers.playwright = {
      type: 'stdio',
      command: 'node',
      args: [PLAYWRIGHT_MCP_PATH],
      env: {},
    };
  } else {
    // CodeRabbit R1: don't silently drop the entry. CodeRabbit R2: use the
    // project logger (writes structured JSON to stdout) instead of
    // console.warn (which writes to stderr and would land in the wrong log
    // file). Now lands in dispatch_listener_stdout.log next to other warn
    // lines, queryable by msg='mcp_playwright_missing'.
    log.warn('mcp_playwright_missing', {
      path: PLAYWRIGHT_MCP_PATH,
      hint: 'Install via `npm install -g @playwright/mcp` and restart the dispatch listener. Workers will spawn but the iPhone gate will fail.',
    });
  }

  return JSON.stringify({ mcpServers }, null, 2);
}

function writeMcpConfig(worktreePath) {
  const dest = path.join(worktreePath, '.mcp.json');
  fs.writeFileSync(dest, buildMcpConfig(), 'utf8');
}

module.exports = { writeMcpConfig, buildMcpConfig, PLAYWRIGHT_MCP_PATH };
