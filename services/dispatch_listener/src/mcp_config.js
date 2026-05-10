'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');

const GATEWAY_PORT = 18766;

// Playwright MCP server lives in the operator's global npm install. Path is
// constructed from %APPDATA% so it works for any logged-in user without
// hardcoding "Dreighto". If the layout ever changes (e.g. pnpm/yarn global
// dir), update here. We probe Test-Path equivalent below before writing the
// config so a missing playwright install fails loudly at dispatch time
// instead of silently dropping the tool.
const PLAYWRIGHT_MCP_PATH = path.join(
  process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
  'npm',
  'node_modules',
  '@playwright',
  'mcp',
  'cli.js'
);

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
  }

  return JSON.stringify({ mcpServers }, null, 2);
}

function writeMcpConfig(worktreePath) {
  const dest = path.join(worktreePath, '.mcp.json');
  fs.writeFileSync(dest, buildMcpConfig(), 'utf8');
}

module.exports = { writeMcpConfig, buildMcpConfig, PLAYWRIGHT_MCP_PATH };
