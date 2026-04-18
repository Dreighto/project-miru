"""
Gemini CLI handler.

Invokes the Gemini CLI (installed via ``npm install -g @google/gemini-cli``)
in non-interactive ``-p`` mode with ``--yolo`` for auto-approval.

Output is streamed line-by-line via Popen + reader thread for real-time
SSE delivery (instead of waiting for subprocess.run to finish).
"""

from __future__ import annotations

import logging
import os
import re
import re as _re
import shutil
import subprocess
import threading
import time

log = logging.getLogger("miru.dispatcher.handler.gemini")

_REPO_ROOT = r"D:\dev\miru"

APPROVAL_PATTERNS = [
    r"\(y/n\)",
    r"\[y/N\]",
    r"Approve:",
    r"Allow\?",
    r"Proceed\?",
    r"1\.\s+Allow",
]
_APPROVAL_RE = re.compile("|".join(APPROVAL_PATTERNS), re.IGNORECASE)
_GEMINI_NOISE_RE = _re.compile(
    r'(?:'
    r'\[WARN\]\s+Skipping unreadable directory'
    r'|YOLO mode is enabled'
    r'|All tool calls will be automatically approved'
    r'|MCP issues detected\. Run /mcp list'
    r')',
    _re.IGNORECASE,
)


def _find_gemini_cli() -> str | None:
    """Locate the ``gemini`` binary on PATH (or common npm global paths)."""
    found = shutil.which("gemini")
    if found:
        return found
    # Fallback: check the npm global bin directory on Windows
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidate = os.path.join(appdata, "npm", "gemini.cmd")
        if os.path.isfile(candidate):
            return candidate
    return None


def handler(job) -> None:
    """Real handler: invokes the Gemini CLI via Popen + reader thread."""
    cli = _find_gemini_cli()
    if cli is None:
        job.status = "failed"
        job.output = (
            "[gemini_cli] Gemini CLI not found on PATH or in npm globals.\n"
            "Install with: npm install -g @google/gemini-cli"
        )
        log.error("Gemini CLI not found for job %s", job.id)
        return

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    cmd = [cli, "-p", job.prompt, "--yolo"]
    log.info("Launching Gemini CLI for job %s (timeout=%ds)", job.id, timeout)

    # Build env: suppress ANSI codes for clean text output.
    cli_env = os.environ.copy()
    cli_env["NO_COLOR"] = "1"
    cli_env["TERM"] = "dumb"
    cli_env["PYTHONUNBUFFERED"] = "1"

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
            cwd=_REPO_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

        # Store proc on job for stdin injection endpoint access.
        job.proc = proc

        # Close stdin — with --yolo, Gemini never prompts for input;
        # keeping stdin open can cause the process to hang on some platforms.
        try:
            proc.stdin.close()
        except Exception:
            pass

        _stdout_lines: list[str] = []
        _read_done = False

        def _reader():
            nonlocal _read_done
            for raw_line in proc.stdout:
                clean = raw_line.replace('\r', '')
                if not _GEMINI_NOISE_RE.search(clean):
                    _stdout_lines.append(clean)
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
                _kill_gemini_children()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + "".join(collected)
                log.info("Job %s cancelled; Gemini CLI killed", job.id)
                return

            # Drain buffered lines → real-time SSE
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
            _kill_gemini_children()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + "".join(collected)
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        # Drain remaining lines after process exits
        rt.join(timeout=5)
        while _stdout_lines:
            line = _stdout_lines.pop(0)
            collected.append(line)
            if hasattr(job, "output_lines"):
                job.output_lines.append(line.rstrip("\n"))

        if proc.returncode == 0:
            job.output = "".join(collected).strip() or "[gemini_cli] (no output)"
            job.status = "done"
            log.info("Job %s completed successfully (rc=0)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[gemini_cli] exit code {proc.returncode}\n"
                f"output: {''.join(collected).strip()}"
            )
            log.warning("Job %s failed: rc=%d", job.id, proc.returncode)

    except FileNotFoundError:
        job.status = "failed"
        job.output = f"[gemini_cli] CLI executable not found: {cli}"
        log.error("Gemini CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:
        job.status = "failed"
        job.output = f"[gemini_cli] unexpected error: {exc}"
        log.exception("Unexpected error in gemini handler for job %s", job.id)


def _kill_gemini_children():
    """Best-effort kill of any running gemini-cli node.exe processes."""
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-Process -Name node -EA SilentlyContinue "
             "| Where-Object { $_.CommandLine -match 'gemini' } "
             "| Stop-Process -Force -EA SilentlyContinue"],
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass
