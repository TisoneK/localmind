# Contributing to LocalMind

LocalMind is open-source and contributions are welcome. The architecture is intentionally layered so you can add a new tool, adapter, or surface without touching anything outside your area.

## Three ways to contribute

### 1. Add a new tool

Tools live in `tools/`. Each tool is a self-contained Python module with a single entry function.

**Steps:**

1. Create `tools/my_tool.py`:

```python
from pydantic import BaseModel
from tools.base import ToolResult, ToolRisk

class MyToolInput(BaseModel):
    query: str

async def run(input: MyToolInput, context: dict) -> ToolResult:
    # Your logic here
    result = do_something(input.query)
    return ToolResult(
        content=result,
        risk=ToolRisk.LOW,
        source="my_tool",
    )
```

2. Register in `tools/__init__.py`:

```python
from tools.my_tool import run as my_tool_run

TOOL_REGISTRY = {
    ...
    "my_tool": {
        "run": my_tool_run,
        "description": "What this tool does",
        "risk": ToolRisk.LOW,
        "intent_patterns": ["pattern1", "pattern2"],
    },
}
```

3. Write tests in `tests/test_my_tool.py`.

That's it. The intent router will automatically consider your tool for relevant queries.

### 2. Add a new model adapter

Adapters live in `adapters/`. Each adapter implements the same interface as `adapters/ollama.py`.

**Steps:**

1. Create `adapters/my_runtime.py` implementing `BaseAdapter`:

```python
from adapters.base import BaseAdapter, ChatMessage, StreamChunk

class MyRuntimeAdapter(BaseAdapter):
    async def chat(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[StreamChunk]:
        # Call your runtime's API
        ...
```

2. Register in `adapters/__init__.py`.
3. Set `LOCALMIND_ADAPTER=my_runtime` in `.env`.

No changes to the core engine, tools, or surfaces required.

### 3. Add a new surface

Surfaces call the core engine's `process()` function:

```python
from core.engine import Engine

engine = Engine()

async def handle_user_message(message: str, session_id: str, file_bytes: bytes = None):
    async for chunk in engine.process(message, session_id, file=file_bytes):
        # render chunk however your surface needs
        print(chunk.text, end="", flush=True)
```

The engine has no knowledge of how the response is rendered.

## Code standards

- Python 3.10+, strict type hints
- Pydantic models for all inputs and outputs at component boundaries
- `async`/`await` throughout — no blocking calls in async context
- One pytest test file per module, minimum happy path + one error case
- `ruff` for linting: `ruff check .`
- No LangChain, no framework lock-in

## Pull request process

1. Fork the repo and create a branch: `feat/my-tool-name`
2. Write the code and tests
3. Run `pytest` — all tests must pass
4. Open a PR with a clear description of what the tool/adapter/surface does
5. Keep PRs focused — one thing per PR

## Questions

Open a GitHub issue with the `question` label.
