"""
Tool registry and dispatch layer.

Tools self-register by importing their module. This __init__ imports all
built-in tools so they're registered at startup — no manual wiring needed.

To add a new tool:
    1. Create tools/my_tool.py
    2. Call register_tool(Intent.MY_INTENT, my_fn, description="...", cost=0.1, latency_ms=500)
    3. Import it here

The registry is then available to the engine, agent loop, and tool scorer.
"""
from __future__ import annotations
from core.models import Intent, ToolResult

_REGISTRY: dict[Intent, dict] = {}


def register_tool(intent: Intent, fn, *, description: str, cost: float = 0.1, latency_ms: int = 500):
    """Register a callable as the handler for an intent, with metadata."""
    _REGISTRY[intent] = {
        "fn": fn,
        "description": description,
        "cost": cost,
        "latency_ms": latency_ms,
    }


async def dispatch(intent: Intent, message: str) -> ToolResult | None:
    """Dispatch to the registered tool for an intent. Returns None if not registered."""
    entry = _REGISTRY.get(intent)
    if not entry:
        return None
    return await entry["fn"](message)


def available_tools() -> list[dict]:
    """Return metadata for all registered tools."""
    return [
        {
            "intent": intent.value,
            "description": meta["description"],
            "cost": meta["cost"],
            "latency_ms": meta["latency_ms"],
        }
        for intent, meta in _REGISTRY.items()
    ]


# ── Auto-register all built-in tools ─────────────────────────────────────────
# Import order matters: file_reader must be before engine uses parse_file
from tools import file_reader   # noqa: E402, F401 — registers FILE_TASK
from tools import web_search    # noqa: E402, F401 — registers WEB_SEARCH
from tools import code_exec     # noqa: E402, F401 — registers CODE_EXEC
from tools import memory_tool   # noqa: E402, F401 — registers MEMORY_OP
from tools import file_writer   # noqa: E402, F401 — FILE_WRITE (secondary intent)
