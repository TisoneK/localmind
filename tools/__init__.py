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
import asyncio
from core.models import Intent, ToolResult

_REGISTRY: dict[Intent, dict] = {}


def register_tool(
    intent: Intent,
    fn,
    *,
    description: str,
    cost: float = 0.1,
    latency_ms: int = 500,
    parallelizable: bool = True,   # A3: safe to run concurrently by default
):
    """Register a callable as the handler for an intent, with metadata."""
    _REGISTRY[intent] = {
        "fn": fn,
        "description": description,
        "cost": cost,
        "latency_ms": latency_ms,
        "parallelizable": parallelizable,
    }


async def dispatch(intent: Intent, message: str) -> ToolResult | None:
    """Dispatch to the registered tool for an intent. Returns None if not registered."""
    entry = _REGISTRY.get(intent)
    if not entry:
        return None
    return await entry["fn"](message)


async def dispatch_parallel(intents_and_inputs: list[tuple[Intent, str]]) -> list[ToolResult | None]:
    """A3: Dispatch multiple independent tools concurrently via asyncio.gather.

    Tools flagged parallelizable=False (e.g. code_exec which is stateful) are
    still run, but grouped to run after any parallel batch completes — this
    preserves execution order safety for stateful tools while still allowing
    network-bound tools (web_search, memory_op) to run concurrently.

    Args:
        intents_and_inputs: List of (intent, input_text) pairs.

    Returns:
        List of ToolResult | None in the same order as inputs.
    """
    if not intents_and_inputs:
        return []

    # Split into parallel-safe and sequential
    parallel_idx = []
    sequential_idx = []
    for i, (intent, _) in enumerate(intents_and_inputs):
        entry = _REGISTRY.get(intent)
        if entry and entry.get("parallelizable", True):
            parallel_idx.append(i)
        else:
            sequential_idx.append(i)

    results: list[ToolResult | None] = [None] * len(intents_and_inputs)

    # Run parallelizable tools concurrently
    if parallel_idx:
        tasks = [dispatch(intents_and_inputs[i][0], intents_and_inputs[i][1]) for i in parallel_idx]
        parallel_results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, res in zip(parallel_idx, parallel_results):
            results[idx] = None if isinstance(res, BaseException) else res

    # Run sequential tools one at a time
    for i in sequential_idx:
        try:
            results[i] = await dispatch(intents_and_inputs[i][0], intents_and_inputs[i][1])
        except Exception:
            results[i] = None

    return results


def available_tools() -> list[dict]:
    """Return metadata for all registered tools."""
    return [
        {
            "intent": intent.value,
            "description": meta["description"],
            "cost": meta["cost"],
            "latency_ms": meta["latency_ms"],
            "parallelizable": meta.get("parallelizable", True),
        }
        for intent, meta in _REGISTRY.items()
    ]


# ── Auto-register all built-in tools ─────────────────────────────────────────
# Import order matters: file_reader must be before engine uses parse_file
from tools import file_reader   # noqa: E402, F401 — registers FILE_TASK
from tools import web_search    # noqa: E402, F401 — registers WEB_SEARCH
from tools import code_exec     # noqa: E402, F401 — registers CODE_EXEC
from tools import shell         # noqa: E402, F401 — registers SHELL
from tools import sysinfo       # noqa: E402, F401 — registers SYSINFO (offline: time, date, specs)
from tools import memory_tool   # noqa: E402, F401 — registers MEMORY_OP
from tools import file_writer   # noqa: E402, F401 — FILE_WRITE (secondary intent)
