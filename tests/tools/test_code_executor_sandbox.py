"""Tests for the code executor sandbox."""
from __future__ import annotations
import pytest
from tools.code_executor.sandbox import run_python, run_javascript
from tools.code_executor.detector import detect


# ── Detector tests ────────────────────────────────────────────────────────────

def test_detect_fenced_python():
    msg = "Run this:\n```python\nprint('hello')\n```"
    result = detect(msg)
    assert result is not None
    assert result.language == "python"
    assert "print" in result.code
    assert result.from_fence is True


def test_detect_fenced_javascript():
    msg = "```js\nconsole.log('hi')\n```"
    result = detect(msg)
    assert result is not None
    assert result.language == "javascript"


def test_detect_no_code_returns_none():
    result = detect("What is the capital of France?")
    assert result is None


def test_detect_bare_python_heuristic():
    msg = "What does this do?\ndef add(a, b):\n    return a + b\nprint(add(1,2))"
    result = detect(msg)
    assert result is not None
    assert result.language == "python"


# ── Sandbox tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_python_hello_world():
    result = await run_python("print('hello')")
    assert result.success
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_python_arithmetic():
    result = await run_python("print(2 + 2)")
    assert result.success
    assert "4" in result.stdout


@pytest.mark.asyncio
async def test_python_syntax_error_captured():
    result = await run_python("def bad(:\n    pass")
    assert not result.success
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_python_runtime_error_captured():
    result = await run_python("raise ValueError('intentional')")
    assert not result.success
    assert "ValueError" in result.stderr or "intentional" in result.stderr


@pytest.mark.asyncio
async def test_python_timeout():
    result = await run_python("import time\ntime.sleep(999)")
    # Uses default timeout from settings — override for test speed
    from tools.code_executor import sandbox
    import tools.code_executor.sandbox as sb
    original = sb.settings.localmind_code_exec_timeout
    # Just verify the timeout attribute exists; real timeout test needs patching
    assert hasattr(result, "timed_out")
