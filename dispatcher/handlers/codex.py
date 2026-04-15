"""
OpenAI Codex CLI handler.

Invokes the Codex CLI (installed via npm install -g @openai/codex)
using the non-interactive `exec` subcommand.  Output is captured
line by line via Popen + reader thread for real-time SSE streaming.

Flags used:
  exec          -- non-interactive subcommand (no TUI, no stdin wait)
  --full-auto   -- sets --sandbox workspace-write (sandboxed auto)
  --color never -- suppresses ANSI colour codes in output

Requires: OPENAI_API_KEY in .env
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("miru.dispatcher.handler.codex")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_CODEX_NOISE_RE = re.compile(
    r'(?:'
    r'Reading additional input from stdin'
    r'|OpenAI Codex v\d'
    r'|^-{4,}$'
    r'|^workdir:'
    r'|^model:'
    r'|^provider:'
    r'|^approval:'
    r'|^sandbox:'
    r'|^reasoning effort:'
    r'|^reasoning summaries:'
    r'|^session id:'
    r'|^tokens used$'
    r'|^\d{1,3},\d{3}$'
    r'|ERROR rmcp::transport'
    r'|worker quit with fatal'
    r'|Unexpected content type'
    r'|AuthRequired'
    r'|huggingface\.co'
    r'|send initialized notification'
    r')',
    re.IGNORECASE | re.MULTILINE,
)

APPROVAL_PATTERNS = [
    r"\(y/n\)",
    r"\[y/N\]",
    r"Approve:",
    r"Allow\?",
    r"Proceed\?",
    r"1\.\s+Allow",
]
_APPROVAL_RE = re.compile("|".join(APPROVAL_PATTERNS), re.IGNORECASE)


def _find_codex_cli() -> str | None:
    """Locate the codex binary on PATH or common npm global paths."""
    import shutil
    found = shutil.which("codex")
    if found:
        return found
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidate = os.path.join(appdata, "npm", "codex.cmd")
        if os.path.isfile(candidate):
            return candidate
    return None


def handler(job) -> None:
    """Real handler: invokes OpenAI Codex CLI via subprocess."""
    cli = _find_codex_cli()
    if cli is None:
        job.status = "failed"
        job.output = (
            "[codex_cli] Codex CLI not found on PATH or in npm globals.\n"
            "Install with: npm install -g @openai/codex"
        )
        log.error("Codex CLI not found for job %s", job.id)
        return

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        job.status = "failed"
        job.output = "[codex_cli] OPENAI_API_KEY not set in environment"
        log.error("OPENAI_API_KEY missing for job %s", job.id)
        return

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    cmd = [cli, "exec", "--full-auto", "--color", "never", job.prompt]
    log.info("Launching Codex CLI for job %s (timeout=%ds)", job.id, timeout)

    cli_env = os.environ.copy()
    cli_env["OPENAI_API_KEY"] = openai_key
    cli_env["NO_COLOR"] = "1"
    cli_env["TERM"] = "dumb"

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(_REPO_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

        # Store proc on job for stdin injection endpoint access.
        job.proc = proc
        # Close stdin immediately — codex exec is non-interactive; keeping
        # the pipe open causes it to block waiting for a stdin EOF.
        if proc.stdin:
            proc.stdin.close()

        _stdout_lines: list[str] = []
        _read_done = False

        def _reader():
            nonlocal _read_done
            for raw_line in proc.stdout:
                clean = raw_line.replace('\r', '')
                stripped_clean = clean.strip()
                # Append only if non-empty, not a separator, and not noise
                if stripped_clean and stripped_clean != '--------' and not _CODEX_NOISE_RE.search(clean):
                    _stdout_lines.append(clean)
                # Approval check runs regardless of noise filter
                stripped = raw_line.strip()
                if _APPROVAL_RE.search(stripped):
                    try:
                        from task_dispatcher import ApprovalBridge
                        bridge = ApprovalBridge(timeout_seconds=600)
                        reply = bridge.ask(job, stripped)
                        if reply == "review":
                            reply = "n"
                        if reply and proc.stdin:
                            proc.stdin.write(reply + "\n")
                            proc.stdin.flush()
                    except Exception as bridge_exc:
                        log.warning("ApprovalBridge error: %s", bridge_exc)
            _read_done = True

        rt = threading.Thread(target=_reader, daemon=True)
        rt.start()

        collected = []
        poll_interval = 0.5
        elapsed = 0.0

        while elapsed < timeout:
            if job.cancel_event.is_set():
                proc.kill()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + "".join(collected)
                log.info("Job %s cancelled; Codex CLI killed", job.id)
                return

            while _stdout_lines:
                line = _stdout_lines.pop(0)
                collected.append(line)
                if hasattr(job, "output_lines"):
                    job.output_lines.append(line.rstrip("\n"))

            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            proc.kill()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + "".join(collected)
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        rt.join(timeout=5)
        while _stdout_lines:
            line = _stdout_lines.pop(0)
            collected.append(line)
            if hasattr(job, "output_lines"):
                job.output_lines.append(line.rstrip("\n"))

        if proc.returncode == 0:
            job.output = "".join(collected).strip() or "[codex_cli] (no output)"
            job.status = "done"
            log.info("Job %s completed successfully (rc=0)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[codex_cli] exit code {proc.returncode}\n"
                f"output: {''.join(collected).strip()}"
            )
            log.warning("Job %s failed: rc=%d", job.id, proc.returncode)

    except FileNotFoundError:
        job.status = "failed"
        job.output = f"[codex_cli] CLI executable not found: {cli}"
        log.error("Codex CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:
        job.status = "failed"
        job.output = f"[codex_cli] unexpected error: {exc}"
        log.exception("Unexpected error in codex handler for job %s", job.id)
