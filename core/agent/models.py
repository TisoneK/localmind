"""
Agent data models — pure dataclasses, no business logic.

Kept separate so that engine.py, tests, and UI serialization layers
can import AgentStep / AgentTrace without pulling in the full loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentStep:
    """Record of a single iteration inside the agent loop."""

    iteration: int
    thought: str
    tool_name: Optional[str]
    tool_input: Optional[str]
    observation: Optional[str]
    reflection: Optional[str] = None
    tool_failed: bool = False
    retry_count: int = 0
    latency_ms: int = 0


@dataclass
class AgentTrace:
    """Accumulated trace for a complete agent run."""

    steps: list[AgentStep] = field(default_factory=list)
    final_response: str = ""
    iterations_used: int = 0
    hit_limit: bool = False
    clarification_issued: bool = False

    def summary(self) -> str:
        """Human-readable one-liner for logs."""
        return (
            f"iters={self.iterations_used} steps={len(self.steps)} "
            f"hit_limit={self.hit_limit} clarified={self.clarification_issued}"
        )
