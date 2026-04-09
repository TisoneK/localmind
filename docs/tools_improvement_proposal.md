# Tools Improvement Proposal

**Scope:** Targeted fixes and lightweight additions to the existing tools layer.  
**Principle:** Work with the current architecture, not against it. The registry pattern, `ToolResult`, and `dispatch_with_retry` are all solid — don't replace them.

---

## What to fix first (bugs / security)

### 1. Path traversal in `file_writer._safe_output_path`

**Problem:** `filename` is taken from user input and appended to the allowed path without sanitisation. A filename of `../../.bashrc` writes outside the safe directory.

```python
# Current — unsafe
return home / filename

# Fix — resolve and verify
filepath = (home / filename).resolve()
if not str(filepath).startswith(str(home.resolve())):
    raise ValueError(f"Path traversal attempt: {filename}")
return filepath
```

---

### 2. `safety_gate.py` over-blocks legitimate requests

**Problem:** Vocabulary-level pattern matching produces constant false positives.  
"How do I crack a walnut?", "what's the legal drug policy in Kenya?", "I need to steal a few minutes" — all blocked.

**Fix:** Replace the broad blocklist with a tiny set of genuinely unambiguous harm patterns, and rely on the LLM's own refusal capability for everything else.

```python
# Replace the current blocked_patterns list with:
_HARD_BLOCKS = [
    r'\b(csam|child pornography)\b',
    r'\b(make a bomb|build a bomb|synthesize [a-z]+ explosive)\b',
]
```

Everything else falls through to the model. That's the right division of labour.

---

### 3. Web search result handler signals success before confirming file write

**Problem:** In `agent/loop.py`, the `__RETURN__` sentinel is yielded before the file write is confirmed, so "Search complete! Results saved to..." can appear even when saving failed.

```python
# Current order (wrong)
yield StreamChunk(text=immediate_response, done=False)
yield StreamChunk(text="", done=True, error="__RETURN__")   # exits even if write failed

# Fix: gate the success message on write result
if file_failed or not file_result:
    # fall through — let agent loop continue with search results in context
    return

yield StreamChunk(text=immediate_response, done=False)
yield StreamChunk(text="", done=True, error="__RETURN__")
```

---

### 4. WEB_SEARCH intent fires on non-search phrases

**Problem:** The bare `today` / `this week` patterns in `intent_router.py` match statements, not questions.

```python
# Current — too broad
r'\b(today|this week|this month)\b',

# Fix — require an information-seeking signal nearby
r'\b(what|how|who|when|where|news|update).{0,30}\b(today|this week|this month)\b',
r'\b(today|this week|this month).{0,30}\b(news|update|price|score|result)\b',
```

---

## What to add (lightweight, < 100 lines each)

### 5. Pre-execution payload validation

**Problem:** Tools currently receive bad inputs and surface errors to the user (e.g. "No executable Python code found"). These failures should be caught before dispatch.

**Add to `tools/__init__.py`:**

```python
# Pre-execution validators — called by dispatch() before handing off to the tool fn
_VALIDATORS: dict[Intent, Callable[[str], str | None]] = {}

def register_validator(intent: Intent, fn: Callable[[str], str | None]) -> None:
    """Register a pre-dispatch validator. fn returns an error string or None."""
    _VALIDATORS[intent] = fn

async def dispatch(intent: Intent, message: str) -> ToolResult | None:
    # Validate first
    validator = _VALIDATORS.get(intent)
    if validator:
        error = validator(message)
        if error:
            return ToolResult(content=error, risk=RiskLevel.LOW, source="validator")
    # Then dispatch as before
    entry = _REGISTRY.get(intent)
    ...
```

**Example validator in `code_exec.py`:**

```python
def _validate_code_exec(message: str) -> str | None:
    if not _CODE_FENCE.search(message):
        return "Please provide your code in a ```python``` block so I can run it."
    return None

register_validator(Intent.CODE_EXEC, _validate_code_exec)
```

This eliminates a class of user-facing errors with ~15 lines per tool.

---

### 6. Intent→tool guard in the agent loop

**Problem:** The agent can call any tool string it invents. An LLM hallucinating `tool: delete_files` won't find a registry entry but will log a confusing error.

**Add a small allowlist to `agent/constants.py`:**

```python
# Tools the agent loop is permitted to call
AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "code_exec",
    "file_write",
    "file_task",
    "shell",
    "memory_op",
    "sysinfo",
})
```

**Check in `_handle_action` before dispatch:**

```python
if tool_name not in AGENT_ALLOWED_TOOLS:
    observation = f"[Tool '{tool_name}' is not available. Available: {', '.join(sorted(AGENT_ALLOWED_TOOLS))}]"
    # update trace, continue loop
```

---

### 7. Promote `code_executor/` pattern to `file_operations/`

`file_reader.py` and `file_writer.py` are growing (parser dispatch, permission gates, chunk logic). When either exceeds ~250 lines, extract into a package following the existing `code_executor/` pattern — no new conventions needed, just the same shape:

```
tools/
  file_operations/
    __init__.py     # re-exports, registers intents
    reader.py       # parse_file() + format-specific parsers
    writer.py       # write_file(), write_response(), path safety
```

This is the one structural change worth making, and only when the files justify it.

---

## What not to do

| Proposal element | Reason to skip |
|---|---|
| `capabilities/` / `primitives/` nesting per domain | 4-level hierarchy for logic that fits in 1 file |
| `BaseTool` ABC with `intents` + `dependencies` properties | `register_tool(Intent.X, fn)` already does this job |
| `ExecutionEngine` class wrapping `dispatch()` | Adds an indirection layer with no new behaviour |
| `ToolBus` with `MAX_DEPTH` cycle detection | Tool-to-tool calls already work; formalising the bus doesn't remove the coupling |
| Full rewrite migration plan (5 weeks) | The existing code is production-quality; migrate only what's broken |

---

## Migration order

1. **This week** — fixes 1–4 (bugs, security, false positives). No API changes.
2. **Next sprint** — add validators (#5) and the agent tool allowlist (#6) as each tool is touched anyway.
3. **When file_reader/writer hit ~250 lines** — extract to `file_operations/` package (#7).

Total new code: ~120 lines across existing files. No new packages, no new abstractions.
