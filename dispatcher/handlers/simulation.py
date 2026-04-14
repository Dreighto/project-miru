"""
Simulation handler — echoes the prompt after a short cancellable sleep.
Used as the fallback when no real executor is available.
"""

from __future__ import annotations

import time


def handler(job) -> None:
    """Simulated worker. Sleeps 3 s with cancel checks, then echoes the prompt."""
    total_sleep = 3.0
    step = 0.25
    elapsed = 0.0
    while elapsed < total_sleep:
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.output = f"[cancelled mid-run]\nPrompt: {job.prompt}"
            return
        time.sleep(step)
        elapsed += step
    job.output = (
        f"[simulated {job.model} / {job.effort}]\n"
        f"Prompt echoed:\n{job.prompt}"
    )
    job.status = "done"
