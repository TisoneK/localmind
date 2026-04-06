"""
Tool registry and dispatch layer.

Tools are registered with metadata (description, cost, latency) so the engine
can score and select them intelligently rather than routing by hard-coded intent.
"""
from __future__ import annotations
from core.models import Intent, ToolResult

# Registry populated by register_tool()
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
    """Dispatch to the registered tool for an intent. Returns None if no tool registered."""
    entry = _REGISTRY.get(intent)
    if not entry:
        return None
    return await entry["fn"](message)


def available_tools() -> list[dict]:
    """Return metadata for all registered tools (for model router / tool scoring)."""
    return [
        {
            "intent": intent.value,
            "description": meta["description"],
            "cost": meta["cost"],
            "latency_ms": meta["latency_ms"],
        }
        for intent, meta in _REGISTRY.items()
    ]
