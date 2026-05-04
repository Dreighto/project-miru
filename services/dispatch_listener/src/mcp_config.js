'use strict';

const fs = require('fs');
const path = require('path');

const GATEWAY_PORT = 18766;

const MCP_CONFIG = JSON.stringify(
  {
    mcpServers: {
      'miru-gateway': {
        type: 'http',
        url: `http://127.0.0.1:${GATEWAY_PORT}/mcp`,
        headers: {
          'X-Miru-Tool-Profile': '${MIRU_TOOL_PROFILE}',
          'X-Miru-Trace-Id': '${MIRU_TRACE_ID}',
        },
      },
    },
  },
  null,
  2
);

function writeMcpConfig(worktreePath) {
  const dest = path.join(worktreePath, '.mcp.json');
  fs.writeFileSync(dest, MCP_CONFIG, 'utf8');
}

module.exports = { writeMcpConfig };
