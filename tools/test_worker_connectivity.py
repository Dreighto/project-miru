"""Worker connectivity test — dispatches directly to the local listener.

Bypasses the MCP gateway (which may run remotely and can't write prompt files
to the local filesystem). Creates prompt files locally, computes HMAC, POSTs
to 127.0.0.1:19100.

Usage: python tools/test_worker_connectivity.py
"""

import contextlib
import hashlib
import hmac
import http.client
import json
import os
import secrets
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = REPO_ROOT / "data" / "n8n_inbox"
TRACE_LOG_DIR = REPO_ROOT / "logs" / "dispatch_listener_traces"

WORKERS = ["claude-code", "gemini", "codex"]
PROMPTS = {
    "claude-code": "Read the file CLAUDE.md and report the first 3 lines of content. Then exit.",
    "gemini": "Read the file CLAUDE.md and report the first 4 lines of content. Then exit.",
    "codex": "Read the file CLAUDE.md and report the first 5 lines of content. Then exit.",
}
# Per-worker dispatch + polling timeouts (seconds).
# Codex uses gpt-5.4 with reasoning effort: high — needs more time to
# initialize MCP servers and produce a response.
TIMEOUTS = {
    "claude-code": 120,
    "gemini": 120,
    "codex": 300,
}

# Load .env
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = val

SECRET = os.environ.get("W4_LISTENER_HMAC_SECRET", "")
if not SECRET:
    print("FATAL: W4_LISTENER_HMAC_SECRET not set in .env or environment")
    sys.exit(1)


def make_trace_id(worker: str) -> str:
    ts = int(time.time() * 1000) & 0xFFFFFFFF
    rnd = secrets.token_hex(4)
    return f"cc-test-{worker}-{ts:08x}-{rnd}"


def dispatch(worker: str) -> dict:
    trace_id = make_trace_id(worker)
    prompt_text = PROMPTS.get(worker, PROMPTS["claude-code"])

    # Write prompt file locally
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = INBOX_DIR / f"{trace_id}.prompt.json"
    prompt_file.write_text(
        json.dumps({"prompt": prompt_text}, ensure_ascii=False), encoding="utf-8"
    )
    prompt_path = f"data/n8n_inbox/{trace_id}.prompt.json"

    timeout_s = TIMEOUTS.get(worker, 120)
    body_dict = {
        "trace_id": trace_id,
        "worker": worker,
        "prompt_path": prompt_path,
        "timeout_seconds": timeout_s,
    }
    body = json.dumps(body_dict, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()

    print(f"\n{'='*60}")
    print(f"DISPATCHING: {worker}")
    print(f"  trace_id:    {trace_id}")
    print(f"  prompt_file: {prompt_file}")
    print(f"  timeout:     {TIMEOUTS.get(worker, 120)}s")

    try:
        conn = http.client.HTTPConnection("127.0.0.1", 19100, timeout=15)
        conn.request(
            "POST",
            "/dispatch",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-W4-Hmac": sig,
            },
        )
        resp = conn.getresponse()
        status = resp.status
        data = resp.read(65536)
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = {"raw": data.decode("utf-8", errors="replace")[:500]}

        print(f"  HTTP {status}: {json.dumps(parsed, indent=2)}")
        return {
            "worker": worker,
            "trace_id": trace_id,
            "http_status": status,
            "response": parsed,
        }
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return {
            "worker": worker,
            "trace_id": trace_id,
            "http_status": None,
            "error": str(exc),
        }


def check_result(trace_id: str, worker: str, timeout: int = 120) -> dict:
    """Poll for a terminal receipt (.result.json) until timeout."""
    result_file = INBOX_DIR / f"{trace_id}.result.json"
    stdout_file = TRACE_LOG_DIR / f"{trace_id}.stdout.log"
    stderr_file = TRACE_LOG_DIR / f"{trace_id}.stderr.log"

    start = time.time()
    print(f"  Waiting for {worker} to complete (max {timeout}s)...")
    last_dot = start
    while time.time() - start < timeout:
        if result_file.exists():
            try:
                receipt = json.loads(result_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                receipt = {"error": "could not parse receipt"}
            # "spawned" is the initial receipt; wait for a terminal status
            if receipt.get("status") == "spawned":
                now = time.time()
                if now - last_dot > 10:
                    print(f"    ... spawned, waiting for terminal status ({int(now - start)}s)")
                    last_dot = now
                time.sleep(2)
                continue
            stdout_text = ""
            stderr_text = ""
            with contextlib.suppress(OSError):
                stdout_text = stdout_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            with contextlib.suppress(OSError):
                stderr_text = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            elapsed = time.time() - start
            print(f"  Completed in {elapsed:.1f}s")
            print(f"  Receipt: {json.dumps(receipt, indent=2)}")
            if stdout_text.strip():
                print(f"  Stdout (last 2000 chars):\n{stdout_text[:500]}")
            if stderr_text.strip():
                print(f"  Stderr (last 2000 chars):\n{stderr_text[:500]}")
            return {
                "worker": worker,
                "trace_id": trace_id,
                "receipt": receipt,
                "elapsed_s": round(elapsed, 1),
                "stdout_preview": stdout_text[:500],
                "stderr_preview": stderr_text[:500],
            }
        now = time.time()
        if now - last_dot > 10:
            print(f"    ... still waiting ({int(now - start)}s elapsed)")
            last_dot = now
        time.sleep(2)

    print(f"  TIMEOUT after {timeout}s — no receipt found")
    return {
        "worker": worker,
        "trace_id": trace_id,
        "receipt": None,
        "elapsed_s": timeout,
        "timed_out": True,
    }


def main():
    print("Worker Connectivity Test — Pre-Phase 3 Gate")
    print(f"Repo root: {REPO_ROOT}")
    print(f"HMAC secret: {'*' * 4}...set")
    print(f"Prompts: {len(PROMPTS)} unique (per-worker to avoid hash collisions)")
    print(f"Timeouts: { {w: f'{t}s' for w, t in TIMEOUTS.items()} }")

    results = []
    for worker in WORKERS:
        dispatch_result = dispatch(worker)
        if dispatch_result.get("http_status") == 202:
            trace_id = dispatch_result["trace_id"]
            t = TIMEOUTS.get(worker, 120)
            check = check_result(trace_id, worker, timeout=t + 10)
            results.append(check)
        else:
            results.append(dispatch_result)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        worker = r["worker"]
        if r.get("timed_out"):
            verdict = "STALL"
        elif r.get("receipt"):
            status = r["receipt"].get("status", "UNKNOWN")
            exit_code = r["receipt"].get("exit_code")
            if status == "INCONCLUSIVE" and exit_code == 0:
                verdict = "PASS"
            else:
                verdict = f"FAIL (status={status}, exit={exit_code})"
        elif r.get("http_status") != 202:
            verdict = f"DISPATCH_FAIL (HTTP {r.get('http_status')})"
        else:
            verdict = "UNKNOWN"
        print(f"  {worker:15s} -> {verdict}")


if __name__ == "__main__":
    main()
