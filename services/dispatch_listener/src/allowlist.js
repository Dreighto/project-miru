'use strict';

const fs = require('fs');
const path = require('path');

// Flags here intentionally do NOT include the prompt as a positional argv
// element. The prompt is written to the child's stdin in spawn.js -- this
// sidesteps cmd.exe's argv parsing of %VAR% expansion and embedded newlines
// that would mutate or truncate multi-line / env-string-containing prompts.
// See PR #22 Bugbot finding "Prompt passed unescaped to cmd.exe argv".
//
// Stdin behavior verified empirically (2026-04-26):
//   * claude --print --dangerously-skip-permissions  -- reads prompt from stdin
//   * gemini -p "" --yolo                            -- stdin appended to (empty) -p; --yolo auto-approves all tool actions (equivalent of --dangerously-skip-permissions)
//   * codex exec -                                   -- explicit `-` reads stdin
const ALLOWLIST_DEF = Object.freeze({
  'claude-code': {
    binary: 'claude.cmd',
    flags: ['--print', '--dangerously-skip-permissions'],
  },
  gemini: { binary: 'gemini.cmd', flags: ['-p', '', '--yolo', '--skip-trust'] },
  // -c overrides applied before `exec` (global flags must precede the subcommand):
  //   model/effort: use gpt-4o + medium for automated dispatch (faster than the
  //     interactive gpt-5.4/high config; interactive sessions are unaffected).
  //   mcp_servers.justtcg.enabled=false: disable the HTTP SSE MCP server that
  //     triggers an rmcp transport fatal during MCP init (missing-content-type
  //     on the initialized notification). JustTCG is not needed for code tasks.
  codex: {
    binary: 'codex.cmd',
    flags: [
      '-c',
      'model="gpt-4o"',
      '-c',
      'model_reasoning_effort="medium"',
      '-c',
      'mcp_servers.justtcg.enabled=false',
      'exec',
      '-',
    ],
  },
});

// Some Windows installations virtualize %APPDATA%\npm into an AppContainer
// "real" path under %LOCALAPPDATA%\Packages\<package>\LocalCache\Roaming\npm.
// Under interactive logon the redirect is transparent; under S4U / LocalSystem
// the redirect doesn't activate and `fs.statSync` against the canonical
// %APPDATA% path returns ENOENT for selectively-redirected files.
// We fall back to scanning every Packages\*\LocalCache\Roaming\npm directory
// so resolution works in both contexts. See README "Deployment" section.
function appContainerNpmDirs() {
  if (!process.env.LOCALAPPDATA) return [];
  const packagesRoot = path.join(process.env.LOCALAPPDATA, 'Packages');
  let entries;
  try {
    entries = fs.readdirSync(packagesRoot, { withFileTypes: true });
  } catch (_e) {
    return [];
  }
  const dirs = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const candidate = path.join(packagesRoot, entry.name, 'LocalCache', 'Roaming', 'npm');
    try {
      const s = fs.statSync(candidate);
      if (s.isDirectory()) dirs.push(candidate);
    } catch (_e) {
      // package without a LocalCache\Roaming\npm — ignore
    }
  }
  return dirs;
}

function candidateDirs() {
  const dirs = [];
  if (process.env.APPDATA) {
    dirs.push(path.join(process.env.APPDATA, 'npm'));
  }
  for (const d of appContainerNpmDirs()) {
    dirs.push(d);
  }
  for (const d of (process.env.PATH || '').split(path.delimiter)) {
    if (d) dirs.push(d);
  }
  return dirs;
}

function resolveBinary(filename) {
  for (const dir of candidateDirs()) {
    const candidate = path.join(dir, filename);
    try {
      fs.statSync(candidate);
      return candidate;
    } catch (err) {
      if (err.code !== 'ENOENT') {
        // Surface unusual errors (EACCES, EBUSY, EPERM, ENOTDIR, …) — useful when
        // a file appears present in the directory listing but stat fails for the
        // service account (LocalSystem, etc.).
        try {
          const entries = fs.readdirSync(dir);
          const present = entries.includes(filename);
          // eslint-disable-next-line no-console
          console.error(
            JSON.stringify({
              ts: new Date().toISOString(),
              level: 'warn',
              msg: 'resolve_stat_unusual',
              candidate,
              code: err.code,
              message: err.message,
              present_in_dir: present,
            })
          );
        } catch (_e2) {
          // ignore
        }
      }
    }
  }
  return null;
}

function buildResolvedAllowlist() {
  const resolved = {};
  const missing = [];
  const debug = [];
  for (const [worker, def] of Object.entries(ALLOWLIST_DEF)) {
    const absolutePath = resolveBinary(def.binary);
    if (absolutePath) {
      resolved[worker] = { binaryPath: absolutePath, binary: def.binary, flags: [...def.flags] };
    } else {
      const checked = candidateDirs().map((d) => path.join(d, def.binary));
      debug.push({ worker, binary: def.binary, checked });
      missing.push({ worker, binary: def.binary });
    }
  }
  return { resolved, missing, debug };
}

const {
  resolved: ALLOWLIST,
  missing: MISSING_BINARIES,
  debug: MISSING_DEBUG,
} = buildResolvedAllowlist();

function isAllowed(worker) {
  return Object.prototype.hasOwnProperty.call(ALLOWLIST, worker);
}

function spec(worker) {
  return ALLOWLIST[worker] || null;
}

module.exports = { ALLOWLIST, MISSING_BINARIES, MISSING_DEBUG, isAllowed, spec };
