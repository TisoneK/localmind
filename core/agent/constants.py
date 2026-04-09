"""
Agent constants — all tunable values in one place.

Changing a value here affects the whole agent package;
nothing else needs to be edited.
"""
from core.models import Intent

# ── Iteration budget ──────────────────────────────────────────────────────────

MAX_ITERATIONS: int = 2
"""Maximum think→act→observe cycles before forcing a final answer."""

CLARIFICATION_THRESHOLD: float = 0.45
"""If classifier confidence is below this, ask the user to clarify instead of acting."""

# ── Context window protection ─────────────────────────────────────────────────

OBS_LOG_MAX_CHARS: int = 100
"""
Max characters kept per observation entry in the running log.
The *full* observation is still stored in AgentStep for trace/debug.
Extreme reduction tuned for local LLMs with small context windows.
"""

# ── Tool retry config ─────────────────────────────────────────────────────────

TOOL_MAX_RETRIES: int = 2
"""How many times to retry a failing tool call before giving up."""

TOOL_RETRY_BASE_DELAY: float = 0.5
"""Seconds before the first retry; doubles each subsequent attempt."""

# ── Web-search result limits ──────────────────────────────────────────────────

WEB_SEARCH_MAX_CHARS_PER_RESULT: int = 100
"""Truncation limit per individual search result entry."""

WEB_SEARCH_MAX_RESULTS: int = 1
"""Maximum number of search results injected into context."""

# ── Intent routing ────────────────────────────────────────────────────────────

AGENT_INTENTS: frozenset[Intent] = frozenset({
    Intent.WEB_SEARCH,
    Intent.CODE_EXEC,
    Intent.FILE_WRITE,
    Intent.MEMORY_OP,
    # Intent.SHELL     — handled directly by engine (prevents hallucination)
    # Intent.FILE_TASK — handled directly by engine (prevents hallucination)
    # Intent.SYSINFO   — instant offline tool, no loop needed
})
"""Intents that are routed through the agent loop rather than direct dispatch."""

# -- Agent tool allowlist ----------------------------------------------------

AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "code_exec",
    "file_write",
    "file_task",
    "shell",
    "memory_op",
    "sysinfo",
})
"""Tool names the agent loop is permitted to call via <action> blocks.

Any tool name the LLM generates that is not in this set is rejected before
dispatch with a clear observation message, preventing hallucinated tool names
from producing confusing registry-miss errors.
"""
