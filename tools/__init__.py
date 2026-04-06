"""
Tool registry and dispatcher.

Each tool declares:
- run: async callable
- description: plain English what it does
- risk: LOW / MEDIUM / HIGH
- intents: which Intent values trigger this tool

The dispatcher is called by the engine with an Intent and the raw message.
"""
from __future__ import annotations
from core.models import Intent, RiskLevel, ToolResult
from tools.base import ToolResult

# Lazy imports — tools only loaded when dispatched
_REGISTRY: dict[str, dict] = {
    Intent.FILE_TASK: {
        "module": "tools.file_reader",
        "description": "Read and parse uploaded files",
        "risk": RiskLevel.LOW,
    },
    Intent.WEB_SEARCH: {
        "module": "tools.web_search",
        "description": "Search the web for current information",
        "risk": RiskLevel.LOW,
    },
    Intent.CODE_EXEC: {
        "module": "tools.code_executor",
        "description": "Execute Python or JavaScript code in a sandbox",
        "risk": RiskLevel.MEDIUM,
    },
    Intent.MEMORY_OP: {
        "module": "tools.memory",
        "description": "Store and recall facts across sessions",
        "risk": RiskLevel.LOW,
    },
    Intent.FILE_WRITE: {
        "module": "tools.file_writer",
        "description": "Create or edit files on disk (requires confirmation)",
        "risk": RiskLevel.HIGH,
    },
}


async def dispatch(intent: Intent, message: str, context: dict = None) -> ToolResult:
    """
    Dispatch to the correct tool for the given intent.

    Args:
        intent: Classified intent from the intent router.
        message: Raw user message (used as fallback query).
        context: Optional extra context dict passed to the tool.

    Returns:
        ToolResult from the dispatched tool.
    """
    import importlib

    context = context or {"message": message}
    entry = _REGISTRY.get(intent)

    if not entry:
        return ToolResult(
            content="",
            risk=RiskLevel.LOW,
            source="dispatch",
        )

    module = importlib.import_module(entry["module"])
    result = await module.run(
        input_data={"query": message, "message": message},
        context=context,
    )
    return result


def list_tools() -> list[dict]:
    """Return all registered tools with metadata. Used by CLI and health endpoint."""
    return [
        {
            "intent": intent.value,
            "description": meta["description"],
            "risk": meta["risk"].value,
            "module": meta["module"],
        }
        for intent, meta in _REGISTRY.items()
    ]
