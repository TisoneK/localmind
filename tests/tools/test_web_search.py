"""Tests for the web search tool."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from tools.web_search import run, _format_results


def test_format_results_basic():
    results = [
        {"title": "Test", "href": "https://example.com", "body": "Some snippet"},
    ]
    formatted = _format_results(results)
    assert "[1]" in formatted
    assert "Test" in formatted
    assert "example.com" in formatted
    assert "Some snippet" in formatted


def test_format_results_empty():
    formatted = _format_results([])
    assert "Search results" in formatted


@pytest.mark.asyncio
async def test_run_returns_tool_result_on_success():
    mock_results = [
        {"title": "Result", "href": "https://test.com", "body": "Body text"},
    ]
    with patch("tools.web_search._search_duckduckgo", new_callable=AsyncMock, return_value=mock_results):
        result = await run({"query": "test query"}, {})

    assert result.source == "web_search"
    assert "Result" in result.content
    assert result.risk.value == "low"


@pytest.mark.asyncio
async def test_run_no_query_returns_error():
    result = await run({}, {})
    assert "No search query" in result.content


@pytest.mark.asyncio
async def test_run_search_exception_returns_graceful_error():
    with patch("tools.web_search._search_duckduckgo", side_effect=Exception("network error")):
        result = await run({"query": "test"}, {})
    assert "failed" in result.content.lower()
    assert result.source == "web_search"
