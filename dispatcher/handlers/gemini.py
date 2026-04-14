"""
Gemini CLI handler.

Invokes the Gemini CLI (installed via ``npm install -g @google/gemini-cli``)
in non-interactive ``-p`` mode.  Output is captured from stdout.
Environment variables NO_COLOR, TERM=dumb suppress ANSI escape codes.

Strategy: We use ``subprocess.Popen`` with a background reader thread that
drains stdout *character-by-character* into a list, then ``proc.wait()``
in the main thread.  This bypasses Python's 8 KB line-buffer which hangs
when Gemini CLI pipes on Windows.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger("miru.dispatcher.handler.gemini")

_REPO_ROOT = r"D:\dev\tcg-watcher-worktree"


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
    """Real handler: invokes the Gemini CLI via subprocess.run in a thread."""
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

    cmd = [cli, "-p", job.prompt]
    log.info("Launching Gemini CLI for job %s (timeout=%ds)", job.id, timeout)

    # Build env: suppress ANSI codes for clean text output.
    cli_env = os.environ.copy()
    cli_env["NO_COLOR"] = "1"
    cli_env["TERM"] = "dumb"
    cli_env["PYTHONUNBUFFERED"] = "1"

    # Use subprocess.run in a worker thread so main thread can poll cancel.
    result_holder: dict = {}
    run_done = threading.Event()

    def _run_subprocess():
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=_REPO_ROOT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=cli_env,
                timeout=timeout,
            )
            result_holder["stdout"] = r.stdout
            result_holder["stderr"] = r.stderr
            result_holder["returncode"] = r.returncode
        except subprocess.TimeoutExpired as exc:
            result_holder["timeout"] = True
            result_holder["stdout"] = exc.stdout or ""
            result_holder["stderr"] = exc.stderr or ""
        except Exception as exc:
            result_holder["error"] = str(exc)
        finally:
            run_done.set()

    worker = threading.Thread(target=_run_subprocess, daemon=True)
    worker.start()

    # Poll for cancel while subprocess runs.
    while not run_done.is_set():
        if job.cancel_event.is_set():
            # subprocess.run doesn't expose the Popen object, so we
            # can't kill it directly. Use taskkill on child processes.
            log.info("Job %s cancel requested — attempting to kill child processes", job.id)
            _kill_gemini_children()
            run_done.wait(timeout=10)
            job.status = "cancelled"
            job.output = "[cancelled by operator]\n" + result_holder.get("stdout", "").strip()
            log.info("Job %s cancelled", job.id)
            return
        run_done.wait(timeout=1.0)

    # Check results.
    if "error" in result_holder:
        job.status = "failed"
        job.output = f"[gemini_cli] unexpected error: {result_holder['error']}"
        log.error("Gemini handler error for job %s: %s", job.id, result_holder["error"])
        return

    if result_holder.get("timeout"):
        job.status = "failed"
        stdout = result_holder.get("stdout", "")
        job.output = f"[timeout after {timeout}s]\n{stdout.strip()}"
        log.warning("Job %s timed out after %ds", job.id, timeout)
        return

    stdout = result_holder.get("stdout", "")
    stderr = result_holder.get("stderr", "")
    rc = result_holder.get("returncode", -1)

    # Store output lines for SSE streaming
    if stdout and hasattr(job, "output_lines"):
        for line in stdout.splitlines():
            job.output_lines.append(line)

    if rc == 0:
        job.output = stdout.strip() or "[gemini_cli] (no output)"
        job.status = "done"
        log.info("Job %s completed successfully (rc=0)", job.id)
    else:
        job.status = "failed"
        job.output = (
            f"[gemini_cli] exit code {rc}\n"
            f"stdout: {stdout.strip()}\n"
            f"stderr: {stderr.strip()[-500:]}"
        )
        log.warning(
            "Job %s failed: rc=%d stderr=%s",
            job.id, rc, stderr[:200],
        )


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
