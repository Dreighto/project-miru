"""
Claude Code CLI handler.

Invokes the Claude Code CLI (installed via npm as `claude.cmd`) in
non-interactive --print mode. Effort is passed as an annotated prefix
in the prompt because the CLI has no native --effort flag.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("miru.dispatcher.handler.claude")

# Claude Code CLI — installed via `npm install -g @anthropic-ai/claude-code`
_CLAUDE_CLI = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"


def handler(job) -> None:
    """Real handler: invokes the Claude Code CLI via subprocess."""
    if not _CLAUDE_CLI.exists():
        job.status = "failed"
        job.output = f"[claude_cli] CLI not found at {_CLAUDE_CLI}"
        log.error("Claude CLI not found at %s", _CLAUDE_CLI)
        return

    # Effort → max-turns hint injected as a comment prefix.
    effort_hint = {"Quick": "brief", "Standard": "standard", "Deep": "thorough"}
    annotated_prompt = (
        f"[effort:{effort_hint.get(job.effort, 'standard')}] {job.prompt}"
    )

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    cmd = [str(_CLAUDE_CLI), "--print", annotated_prompt]
    log.info("Launching Claude CLI for job %s (timeout=%ds)", job.id, timeout)

    # Build env: inherit process env + ensure Git Bash is discoverable.
    cli_env = os.environ.copy()
    if "CLAUDE_CODE_GIT_BASH_PATH" not in cli_env:
        _git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if _git_bash.exists():
            cli_env["CLAUDE_CODE_GIT_BASH_PATH"] = str(_git_bash)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

        # Stream stdout lines into job.output_lines for SSE streaming.
        collected = []
        poll_interval = 0.5
        elapsed = 0.0

        import select as _sel  # noqa: E402
        import io

        # Non-blocking read via thread
        _stdout_lines: list[str] = []
        _read_done = False

        def _reader():
            nonlocal _read_done
            for raw_line in proc.stdout:
                _stdout_lines.append(raw_line)
            _read_done = True

        import threading as _th
        rt = _th.Thread(target=_reader, daemon=True)
        rt.start()

        while elapsed < timeout:
            if job.cancel_event.is_set():
                proc.kill()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + "".join(collected)
                log.info("Job %s cancelled mid-run; Claude CLI killed", job.id)
                return

            # Drain buffered lines
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

        # Drain remaining lines after process exits
        rt.join(timeout=5)
        while _stdout_lines:
            line = _stdout_lines.pop(0)
            collected.append(line)
            if hasattr(job, "output_lines"):
                job.output_lines.append(line.rstrip("\n"))

        stderr_text = proc.stderr.read() or ""
        if proc.returncode == 0:
            job.output = "".join(collected).strip() or "[claude_cli] (no output)"
            job.status = "done"
            log.info("Job %s completed successfully (rc=0)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[claude_cli] exit code {proc.returncode}\n"
                f"stdout: {''.join(collected).strip()}\n"
                f"stderr: {stderr_text.strip()}"
            )
            log.warning(
                "Job %s failed: rc=%d stderr=%s",
                job.id, proc.returncode, stderr_text[:200],
            )

    except FileNotFoundError:
        job.status = "failed"
        job.output = f"[claude_cli] CLI executable not found: {_CLAUDE_CLI}"
        log.error("Claude CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"[claude_cli] unexpected error: {exc}"
        log.exception("Unexpected error in claude handler for job %s", job.id)
