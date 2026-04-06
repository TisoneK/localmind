"""Tests for the code executor tool."""
import pytest
from tools.code_executor.detector import detect
from tools.code_executor.sandbox import run_python, run_javascript


# ── Detector tests ─────────────────────────────────────────────────────────

def test_detect_fenced_python():
    msg = "What does this do?\n```python\nprint('hello')\n```"
    result = detect(msg)
    assert result is not None
    assert result.language == "python"
    assert "print" in result.code
    assert result.from_fence is True


def test_detect_fenced_javascript():
    msg = "Run this:\n```js\nconsole.log('hi')\n```"
    result = detect(msg)
    assert result is not None
    assert result.language == "javascript"


def test_detect_fenced_no_lang():
    msg = "```\nprint('hello')\n```"
    result = detect(msg)
    assert result is not None
    assert result.language == "python"  # default


def test_detect_unfenced_python():
    msg = "what does print('hello') output?"
    result = detect(msg)
    assert result is not None
    assert result.language == "python"


def test_detect_no_code():
    result = detect("What is the capital of France?")
    assert result is None


# ── Sandbox tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_python_simple():
    result = await run_python("print('hello localmind')")
    assert result.success
    assert "hello localmind" in result.stdout


@pytest.mark.asyncio
async def test_run_python_arithmetic():
    result = await run_python("print(2 + 2)")
    assert result.success
    assert "4" in result.stdout


@pytest.mark.asyncio
async def test_run_python_syntax_error():
    result = await run_python("def foo(:\n    pass")
    assert not result.success
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_run_python_runtime_error():
    result = await run_python("raise ValueError('test error')")
    assert not result.success
    assert "ValueError" in result.stderr or "test error" in result.stderr


@pytest.mark.asyncio
async def test_run_python_timeout(monkeypatch):
    from core import config
    monkeypatch.setattr(config.settings, "localmind_code_exec_timeout", 1)
    result = await run_python("import time; time.sleep(10)")
    assert result.timed_out


@pytest.mark.asyncio
async def test_run_python_no_output():
    result = await run_python("x = 1 + 1")
    assert result.success
    assert result.stdout.strip() == ""
