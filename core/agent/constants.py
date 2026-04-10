"""
Agent constants — all tunable values in one place.

Changing a value here affects the whole agent package;
nothing else needs to be edited.
"""
from core.models import Intent

# ── Iteration budget ──────────────────────────────────────────────────────────

MAX_ITERATIONS: int = 3
"""Maximum think→act→observe cycles before forcing a final answer."""

CLARIFICATION_THRESHOLD: float = 0.45
"""If classifier confidence is below this, ask the user to clarify instead of acting."""

# ── Context window protection ─────────────────────────────────────────────────

OBS_LOG_MAX_CHARS: int = 800
"""
Max characters kept per observation entry in the running log.
The *full* observation is still stored in AgentStep for trace/debug.
800 chars keeps enough signal for the LLM to reason without flooding small
context windows. Tuned for local models (4k–8k context windows).
"""

# ── Tool retry config ─────────────────────────────────────────────────────────

TOOL_MAX_RETRIES: int = 1
"""
How many times to retry a failing tool call before giving up.
Reduced from 2 to 1: a single retry catches transient I/O errors without
burning 4× the latency on genuinely broken inputs (bad path, bad code, etc.).
"""

TOOL_RETRY_BASE_DELAY: float = 0.3
"""Seconds before the first retry; doubles each subsequent attempt."""

# ── File operation timeouts ───────────────────────────────────────────────────

FILE_OP_TIMEOUT_SECONDS: float = 10.0
"""
Hard timeout for file read/write operations.
Prevents stalls on network mounts, slow disks, or huge files.
"""

FILE_READ_MAX_BYTES: int = 512 * 1024  # 512 KB
"""
Maximum bytes read from a single file before chunking.
Files larger than this are read in streaming chunks rather than slurped whole.
Prevents OOM and context overflow on large source files or logs.
"""

SHELL_OP_TIMEOUT_SECONDS: float = 20.0
"""
Default timeout for shell commands (overrides config if lower).
Separate from code_exec timeout — shell ops like `find` and `grep` on
large trees can stall; this provides a hard ceiling.
"""

# ── Web-search result limits ──────────────────────────────────────────────────

WEB_SEARCH_MAX_CHARS_PER_RESULT: int = 400
"""Truncation limit per individual search result entry."""

WEB_SEARCH_MAX_RESULTS: int = 3
"""Maximum number of search results injected into context."""

# ── Agent response streaming ──────────────────────────────────────────────────

AGENT_THINKING_MIN_CHARS: int = 20
"""
Minimum characters in a sanitized thought before streaming it to the UI.
Filters out sub-threshold fragments like '*...*' or whitespace-only thinking
display that pollute the stream without adding signal.
"""

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
