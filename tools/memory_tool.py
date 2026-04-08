"""
Memory tool — handles explicit memory operations.

For MEMORY_OP intent:
- "remember X" → stores X
- "what do you know about me" → lists all facts
- "forget X" → deletes X

The MemoryComposer handles passive retrieval during every turn.
This tool handles explicit user-initiated memory operations.

Registered as Intent.MEMORY_OP in the tool registry.
"""
from __future__ import annotations
import logging
import re

from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

_FORGET_PAT = re.compile(r"\b(forget|delete|remove)\b.{0,40}", re.IGNORECASE)
_LIST_PAT = re.compile(
    r"\b(what do you (know|remember)|list (all |my )?facts|show (me )?memory|recall all)\b",
    re.IGNORECASE,
)
_STORE_PAT = re.compile(
    r"^\s*(remember|note|store|keep in mind|don't forget)\s*(that\s*)?",
    re.IGNORECASE,
)


async def memory_op(message: str) -> ToolResult:
    from storage.vector import VectorStore
    store = VectorStore()

    # ── List all facts ────────────────────────────────────────────────────
    if _LIST_PAT.search(message):
        facts = await store.list_all_with_metadata()
        if not facts:
            return ToolResult(
                content="No facts stored in memory yet.",
                risk=RiskLevel.LOW,
                source="memory",
            )
        lines = []
        for f in facts:
            mtype = f.get("memory_type", "?")
            imp = float(f.get("importance", 0.5))
            lines.append(f"- [{mtype} | importance={imp:.1f}] {f['fact']}")
        content = f"**Stored memory ({len(facts)} facts):**\n" + "\n".join(lines)
        return ToolResult(content=content, risk=RiskLevel.LOW, source="memory",
                          metadata={"fact_count": len(facts)})

    # ── Forget a fact ─────────────────────────────────────────────────────
    if _FORGET_PAT.search(message):
        # Extract what to forget — everything after the trigger word
        fact = re.sub(r"^\s*(forget|delete|remove)\b\s*", "", message, flags=re.IGNORECASE).strip()
        if fact:
            success = await store.forget(fact)
            if success:
                return ToolResult(
                    content=f"Removed from memory: *{fact}*",
                    risk=RiskLevel.LOW,
                    source="memory",
                )
        return ToolResult(
            content="Couldn't find that specific fact to remove. Try listing memory first.",
            risk=RiskLevel.LOW,
            source="memory",
        )

    # ── Store a fact ──────────────────────────────────────────────────────
    fact = _STORE_PAT.sub("", message).strip()
    if not fact:
        return ToolResult(
            content="I didn't catch what you'd like me to remember. Try: 'remember that I prefer Python'",
            risk=RiskLevel.LOW,
            source="memory",
        )

    # Infer importance from wording
    importance = 0.8 if any(w in message.lower() for w in ["always", "never", "prefer", "important", "critical"]) else 0.5

    # Use the canonical session_id so facts are retrievable in the same session scope.
    # Falls back to a named bucket for CLI/direct tool calls without a session.
    effective_session_id = "explicit_memory_op"
    success = await store.store(
        fact=fact,
        session_id=effective_session_id,
        source="user",
        extra_metadata={"memory_type": "semantic", "importance": str(importance), "access_count": "0"},
    )

    if success:
        return ToolResult(
            content=f"Remembered: *{fact}*",
            risk=RiskLevel.LOW,
            source="memory",
            metadata={"fact": fact, "importance": importance},
        )
    return ToolResult(
        content="Memory storage is unavailable (chromadb may not be installed).",
        risk=RiskLevel.LOW,
        source="memory",
    )


# Register
register_tool(
    Intent.MEMORY_OP,
    memory_op,
    description="Store, recall, or delete facts from persistent semantic memory",
    cost=0.01,
    latency_ms=200,
)
