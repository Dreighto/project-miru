'use strict';

// LOS-10 Step 2 / LOS-13: synchronous probe of the gateway's /canon-manifest
// endpoint before spawning a worker. If the gateway is unreachable or returns
// a malformed manifest, the spawn is REFUSED (fail-closed semantics).
//
// Why sync: the existing spawnWorker() is synchronous, called from the HTTP
// dispatch handler with no await chain. Making it async would ripple through
// every caller + the existing test suite. execFileSync on curl is the
// project's existing pattern for synchronous HTTP from Node on Windows.
//
// Naming note (LOS-10 Step 6 rename): the gateway is currently named
// 'miru-gateway' (port 18766). At cutover Step 6, it renames to
// 'logueos-gateway' and the env vars move from MIRU_* to LOGUEOS_*. The
// env var introduced here (LOGUEOS_CANON_SNAPSHOT_ID) uses the FUTURE
// name on purpose — new env vars adopt the post-rename style immediately
// to avoid a second rename pass.

const { execFileSync } = require('child_process');

const log = require('./log');

const DEFAULT_GATEWAY_PORT = process.env.MIRU_MCP_GATEWAY_PORT || '18766';
const DEFAULT_GATEWAY_HOST = process.env.MIRU_MCP_GATEWAY_HOST || '127.0.0.1';
const DEFAULT_TIMEOUT_S = 5;

function getCanonManifestUrl() {
  return `http://${DEFAULT_GATEWAY_HOST}:${DEFAULT_GATEWAY_PORT}/canon-manifest`;
}

/**
 * Synchronously fetch the canon manifest from the gateway. Returns the
 * parsed manifest object on success; throws on any failure (gateway down,
 * non-200, malformed JSON, missing canon_snapshot_id).
 *
 * Callers should catch and translate the throw into a fail-closed refusal
 * to spawn. The error message is suitable for logging + surfacing to the
 * dispatch_worker MCP tool caller so the operator sees what failed.
 *
 * Injectable execFile for testability: in production the curl path is used;
 * tests inject a stub that returns whatever bytes they want without touching
 * the real network.
 */
function probeCanonManifestSync(opts = {}) {
  const url = opts.url || getCanonManifestUrl();
  const timeoutS = opts.timeoutS || DEFAULT_TIMEOUT_S;
  const execFile = opts.execFile || execFileSync;

  let stdout;
  try {
    // curl flags:
    //   -s: silent (no progress bar to stderr)
    //   -S: still show errors when -s is on
    //   --max-time: hard timeout to keep spawn flow fast
    //   --fail-with-body: non-2xx exits non-zero AND prints the body so we
    //     can include the gateway's error payload in our exception
    stdout = execFile('curl', ['-sS', '--max-time', String(timeoutS), '--fail-with-body', url], {
      encoding: 'utf8',
      timeout: (timeoutS + 1) * 1000,
      windowsHide: true,
    });
  } catch (err) {
    const stderrSnippet = String(err.stderr || err.message || '').slice(0, 300);
    throw new Error(
      `canon_probe_failed: curl to ${url} failed within ${timeoutS}s — ${stderrSnippet}`
    );
  }

  if (!stdout || !stdout.trim()) {
    throw new Error(`canon_probe_failed: empty response from ${url}`);
  }

  let manifest;
  try {
    manifest = JSON.parse(stdout);
  } catch (err) {
    throw new Error(
      `canon_probe_failed: malformed JSON from ${url} — ${err.message}: ${stdout.slice(0, 200)}`
    );
  }

  if (!manifest || manifest.ok !== true) {
    throw new Error(
      `canon_probe_failed: response missing ok=true from ${url} — body: ${stdout.slice(0, 200)}`
    );
  }

  if (!manifest.canon_snapshot_id || typeof manifest.canon_snapshot_id !== 'string') {
    throw new Error(
      `canon_probe_failed: response missing canon_snapshot_id from ${url} — body: ${stdout.slice(0, 200)}`
    );
  }

  return manifest;
}

/**
 * Called from spawn flow. Returns { snapshot_id, file_count } on success.
 * Throws on failure with a clear error message including the trace_id.
 * Also emits a structured log event so the failure is correlatable.
 */
function probeBeforeSpawn(traceId, opts = {}) {
  try {
    const manifest = probeCanonManifestSync(opts);
    log.info('canon_snapshot_recorded', {
      trace_id: traceId,
      canon_snapshot_id: manifest.canon_snapshot_id,
      file_count: manifest.file_count,
      gateway_url: opts.url || getCanonManifestUrl(),
    });
    return {
      snapshot_id: manifest.canon_snapshot_id,
      file_count: manifest.file_count,
    };
  } catch (err) {
    log.warn('worker_spawn_refused_canon_unavailable', {
      trace_id: traceId,
      gateway_url: opts.url || getCanonManifestUrl(),
      error: String(err.message || err).slice(0, 500),
    });
    // Re-throw so the caller fails the spawn. The caller is responsible for
    // translating this into the HTTP response (503 with structured error).
    throw err;
  }
}

module.exports = {
  probeBeforeSpawn,
  probeCanonManifestSync,
  getCanonManifestUrl,
};
