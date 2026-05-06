"""
Handler registry for the Miru Task Dispatcher.

Each handler module exposes a single ``handler(job) -> None`` callable.
The HANDLER_MAP maps model names (as accepted by the API) to their handler.
Models not in the map fall back to the simulation handler.
"""

from .claude import handler as claude_handler
from .gemini import handler as gemini_handler
from .ollama import handler as ollama_handler
from .simulation import handler as simulation_handler

HANDLER_MAP = {
    "Ollama": ollama_handler,  # local  — Ollama HTTP API
    "Claude": claude_handler,  # real   — Claude Code CLI
    "Gemini": gemini_handler,  # real   — Gemini CLI (Google)
    "Simulation": simulation_handler,  # dry-run / unknown-model fallback
}


def get_handler(model: str):
    """Return the handler callable for *model*, defaulting to simulation."""
    return HANDLER_MAP.get(model, simulation_handler)


def resolve_executor_mode(handler) -> str:
    """Return the executor_mode tag for a given handler identity."""
    if handler is simulation_handler:
        return "simulated"
    if handler is ollama_handler:
        return "local"
    return "real"
