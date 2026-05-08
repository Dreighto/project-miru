# Codex Ticket — Localhost-bind `full_operator` MCP Profile

This is the locked-design ticket for the first DGAS Tier 1 task. Hand to
Codex after it confirms the briefing read-back. Lifted from PXY's review.

```text
LINEAR TICKET — DGAS Tier 1: localhost-bind full_operator MCP profile

Title: Reject full_operator MCP requests from non-localhost origins

Project: Miru Orchestration / Autonomy
Project ID: 2ba0133d-6f39-41a6-9846-9566e7c895ec
Type: Improvement
Priority: 2 (High)
Tier: CC-merge (single-file edit, follows known canon-lesson pattern)

============================================================================
WHY THIS MATTERS (Operator-facing summary)
============================================================================

Today the MCP gateway treats "no profile header" as full_operator (unrestricted
access). This is intentional for the operator's local CLI session, but it
creates a hole: any HTTP request to the gateway that omits or spoofs the
X-Miru-Tool-Profile header gets full unrestricted tool access. Tomorrow's
worker bug, n8n misconfiguration, or remote tunnel inherits that trust.

The fix: when a request is full_operator (explicit OR via missing header) AND
arrives over HTTP, require it to originate from 127.0.0.1 / ::1. STDIO sessions
(the operator's local CLI) bypass this middleware entirely and are unaffected.

Three independent reviews (CC, Gemini Deep Research, Perplexity Deep Research)
flagged this as a top-3 deterministic-governance gap. PXY specifically
identified it as the widest blast-radius gap per line of fix.

============================================================================
SCOPE
============================================================================

Single change: extend `_ProfileExtractor` ASGI middleware in
`tools/miru_mcp_gateway/server.py` to enforce a localhost-origin check when
the resolved profile is `full_operator`.

============================================================================
LOCKED DESIGN
============================================================================

In tools/miru_mcp_gateway/server.py, the relevant block today is lines 157-182
(class _ProfileExtractor). The relevant excerpt:

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = scope.get("headers", [])
            profile = "full_operator"
            trace_id = ""
            for name, value in headers:
                lower = name.lower() if isinstance(name, bytes) else name.encode().lower()
                if lower == b"x-miru-tool-profile":
                    profile = value.decode("utf-8") if isinstance(value, bytes) else value
                elif lower == b"x-miru-trace-id":
                    trace_id = value.decode("utf-8") if isinstance(value, bytes) else value
            tok_p = current_profile.set(profile)
            ...

Change required:

After resolving `profile` and BEFORE setting the contextvar, add a localhost
check that fires only when `profile == "full_operator"`. If the client origin
is not localhost, return an HTTP 403 ASGI response with body matching the
existing McpError JSON shape:

    {"error": "full_operator_local_only",
     "message": "full_operator requires a local origin (127.0.0.1 or ::1)",
     "remote_addr": "<actual addr>"}

ASGI scope details:
- scope["client"] is typically a tuple (host: str, port: int) when the request
  has an identifiable peer. It may be None for some test transports.
- Acceptable hosts: "127.0.0.1", "::1", "localhost". Explicitly NOT acceptable:
  "0.0.0.0" (a request bound to the wildcard is not the same as one originating
  from localhost), or any other address.
- The X-Forwarded-For header MUST NOT be trusted for this check. Reverse
  proxies are not part of the threat model; we want the actual TCP peer.

Suggested implementation shape (do not copy verbatim — adapt to the file
style and the existing async/ASGI patterns):

    _LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

    def _is_local_origin(scope) -> bool:
        client = scope.get("client")
        if not client:
            # Defensive: if the transport doesn't expose a peer, treat it as
            # non-local. Operator's STDIO session does NOT pass through this
            # ASGI middleware — only HTTP transport does.
            return False
        host = client[0] if isinstance(client, (list, tuple)) and len(client) >= 1 else None
        if not isinstance(host, str):
            return False
        return host in _LOCAL_HOSTS

Then inside __call__, after resolving `profile` but before `current_profile.set`:

    if profile == "full_operator" and not _is_local_origin(scope):
        await self._send_403(scope, send)
        return

Where _send_403 emits an ASGI response body (the gateway already has examples
of this pattern — find one and follow it; if none exist, the standard pattern
is two messages: http.response.start with status=403, then http.response.body
with the JSON-encoded error).

The existing default of profile="full_operator" when no header is present is
PRESERVED. The new check applies after that default is set, so:
- HTTP request with no header from 127.0.0.1 -> profile stays full_operator,
  passes the localhost check, normal flow.
- HTTP request with no header from any other address -> rejected with 403.
- HTTP request with X-Miru-Tool-Profile: full_operator from 127.0.0.1 -> same
  as above, passes.
- HTTP request with X-Miru-Tool-Profile: drift_executor from any address ->
  unaffected (the localhost check only fires for full_operator).

============================================================================
DON'T-TOUCH LIST
============================================================================

- tools/miru_mcp_gateway/profiles.py - DO NOT change profile definitions or
  is_allowed() logic. The fix is purely at the request-entry layer.
- tools/miru_mcp_gateway/_context.py - DO NOT change the ContextVar default.
  The default of full_operator stays for STDIO transport (operator's CLI).
- tools/miru_mcp_gateway/gateway_security.py - DO NOT touch the wrap_tool_entry
  enforcement. That's the per-tool gate, not the entry gate.
- Any other category-specific tool module (telegram_tools.py, dispatch_tools.py,
  etc.) - out of scope.

============================================================================
DONE-WHEN
============================================================================

1. New helper function _is_local_origin(scope) (or equivalent) added to
   server.py, defined ABOVE the _ProfileExtractor class so the class can call it.
2. _ProfileExtractor.__call__ checks the localhost binding ONLY when
   profile == "full_operator" and ONLY for HTTP transport. Other profiles
   (drift_executor, reviewer, standard_worker, vp_ops, unknown) are unaffected
   by this check.
3. STDIO transport (scope.get("type") != "http") is unaffected. The operator's
   CLI session must continue to work without changes.
4. Non-localhost full_operator requests get a 403 ASGI response with the JSON
   error body specified above. The body must include the actual remote_addr
   (redacted via _redact.redact() if appropriate, but at minimum present so
   the operator can debug a misconfigured caller).
5. New test file tests/test_full_operator_localhost_bind.py with at least
   these cases (mirror the style of tests/test_phase3_denial.py):
     a. HTTP request with no header from 127.0.0.1 -> profile resolves to
        full_operator, request proceeds.
     b. HTTP request with no header from "192.168.1.50" -> rejected with 403,
        body contains "full_operator_local_only".
     c. HTTP request with X-Miru-Tool-Profile: full_operator from 127.0.0.1
        -> proceeds.
     d. HTTP request with X-Miru-Tool-Profile: full_operator from "10.0.0.5"
        -> rejected with 403.
     e. HTTP request with X-Miru-Tool-Profile: drift_executor from
        "10.0.0.5" -> proceeds normally (drift_executor is allowed from any
        origin; only full_operator is localhost-bound).
     f. HTTP request with X-Miru-Tool-Profile: full_operator from "::1"
        (IPv6 localhost) -> proceeds.
     g. STDIO scope (type != "http") with default full_operator -> proceeds.
        (Test by passing a scope with type="lifespan" or similar; the
        middleware should pass through without invoking the localhost check.)
6. All existing tests in tests/test_phase3_denial.py still pass (this is a
   regression check — the existing profile enforcement should be unchanged).
7. Pre-commit run --files server.py test_full_operator_localhost_bind.py
   passes (ruff, ruff-format, prettier-where-applicable).
8. Manual smoke test documented in PR description: confirm STDIO session still
   works (run a tool from CC's local session and confirm it succeeds).

============================================================================
INVESTIGATION STEPS (if anything is unclear)
============================================================================

If you cannot find an existing _send_403-style helper in the gateway:
1. Look at how stdio_mcp.McpError is raised today in gateway_security.py
   wrap_tool_entry. That's the pattern for STDIO transport errors.
2. For HTTP transport, the gateway uses ASGI directly. Search server.py for
   "http.response.start" or "http.response.body" - those are the standard
   ASGI message types.
3. If no example exists, the minimal pattern is:
       await send({
           "type": "http.response.start",
           "status": 403,
           "headers": [(b"content-type", b"application/json")],
       })
       await send({
           "type": "http.response.body",
           "body": json.dumps({...}).encode("utf-8"),
       })

If scope.get("client") behavior is unclear in the test transport: check the
existing test fixtures in tests/test_phase3_denial.py - they use a mock
config (_make_cfg) but bypass the ASGI middleware entirely by setting the
ContextVar directly. For this ticket, you'll need to invoke the middleware
itself, not just the wrap_tool_entry. Pattern: instantiate _ProfileExtractor
with a mock app, build a synthetic scope dict, call await middleware(scope,
receive, send) and capture send messages.

If the existing test harness doesn't support ASGI middleware testing: it's
acceptable to use Starlette's TestClient or a small async test helper. Either
is fine; preserve the unittest style of the existing test file.

If you find the existing default of profile="full_operator" debatable AFTER
the localhost check (i.e., should it default to drift_executor instead?):
LEAVE THE DEFAULT AS-IS. That's a separate operator decision (the canonical
"Token of Presence" pattern is in the DGAS synthesis as a Tier 3 follow-up).

============================================================================
COMPLETION CONTRACT
============================================================================

When done:
1. Open a PR. Title: "DGAS Tier 1: localhost-bind full_operator gateway profile"
2. PR description must reference this ticket and the synthesis doc at
   data/peer_reviews/2026-05-08_dgas_three_way_synthesis.md (item under
   Consensus #2).
3. Emit a completion marker via tools/emit_completion.py with:
     - status: CONFIRMED_WORKING (after CI passes and you confirm the smoke test)
     - test_evidence: "<passed>/<total> tests pass" (use real numbers; the new
       test file should add at least 7 cases per Done-When #5)
     - files_touched: list including server.py and the new test file
4. Do NOT self-merge. Operator reviews and merges.

Operator-facing completion message format (per AGENTS.md Operator
Communication Standard):

    What happened: full_operator MCP profile is now blocked from non-localhost
                   HTTP requests; STDIO and local HTTP unaffected
    Does it work: Yes - <X>/<X> tests pass, smoke test confirms operator's
                  CLI session still works
    What you need to do: Review and merge PR #<N>; the gap CC, GMI, PXY all
                         flagged as widest blast-radius is now closed

============================================================================
ESCALATION
============================================================================

If you hit any of these, STOP and emit STATUS: ESCALATE: <category>:

- The existing _ProfileExtractor doesn't behave the way this ticket assumes
  (e.g., the file has been refactored since the synthesis was written) ->
  ESCALATE: DESIGN_CHANGE
- The localhost check materially breaks existing tests beyond
  test_phase3_denial.py -> ESCALATE: SCOPE_EXPANSION
- You discover that 127.0.0.1 is NOT the actual operator's loopback in some
  ROOM-specific config (e.g., the gateway binds to a different interface) ->
  ESCALATE: HUMAN-REQUIRED with the specific config you found

============================================================================
DEFERRED TO A FOLLOW-UP TICKET (NOT THIS ONE)
============================================================================

- "Token of Presence" pattern (short-lived bearer tokens for remote operator
  access via Tailscale). This is a separate Tier 3 ticket per the synthesis.
  The localhost bind is the simple now-version; TOP is the more elaborate
  later-version.
- Logging the rejected origin to the audit log. If this is trivial to add
  in the same change (e.g., the existing audit infrastructure is reachable
  from the middleware), include it. If it requires plumbing audit through
  the middleware where it isn't today, leave it for a follow-up.
- Per-profile localhost binding for other profiles (e.g., binding vp_ops to
  localhost too). Out of scope; only full_operator gets this treatment now.
```
