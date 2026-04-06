"""
Web Search Tool — retrieves live search results and injects them as context.

Provider strategy:
- DuckDuckGo: no API key required, good for general queries
- Brave Search: higher quality, requires BRAVE_SEARCH_API_KEY in .env

Results are trimmed to fit within a reasonable token budget before injection.
The model is instructed to cite its sources in the response.
"""
from __future__ import annotations
import logging
from core.models import RiskLevel
from core.config import settings
from tools.base import ToolResult

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
MAX_SNIPPET_CHARS = 500  # per result


def _format_results(results: list[dict]) -> str:
    """Format search results into a clean text block for context injection."""
    lines = ["Search results:\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", r.get("url", ""))
        snippet = r.get("body", r.get("description", ""))[:MAX_SNIPPET_CHARS]
        lines.append(f"[{i}] {title}")
        lines.append(f"    URL: {url}")
        lines.append(f"    {snippet}")
        lines.append("")
    return "\n".join(lines)


async def _search_duckduckgo(query: str) -> list[dict]:
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=MAX_RESULTS))
    return results


async def _search_brave(query: str) -> list[dict]:
    import httpx
    api_key = settings.brave_search_api_key
    if not api_key:
        raise ValueError("BRAVE_SEARCH_API_KEY not set")
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": MAX_RESULTS},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        data = response.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in data.get("web", {}).get("results", [])
        ]


async def run(input_data: dict, context: dict) -> ToolResult:
    """
    Run a web search and return formatted results.

    Args:
        input_data: Dict with key 'query'.
        context: Engine context dict (unused currently).

    Returns:
        ToolResult with formatted search results as content.
    """
    query = input_data.get("query", context.get("message", ""))
    if not query:
        return ToolResult(
            content="No search query provided.",
            risk=RiskLevel.LOW,
            source="web_search",
        )

    provider = settings.localmind_search_provider
    logger.info(f"Web search [{provider}]: {query}")

    try:
        if provider == "brave" and settings.brave_search_api_key:
            results = await _search_brave(query)
        else:
            results = await _search_duckduckgo(query)

        if not results:
            return ToolResult(
                content=f"No results found for: {query}",
                risk=RiskLevel.LOW,
                source="web_search",
            )

        formatted = _format_results(results)
        sources = [r.get("href", r.get("url", "")) for r in results]

        return ToolResult(
            content=formatted,
            risk=RiskLevel.LOW,
            source="web_search",
            metadata={"query": query, "provider": provider, "result_count": len(results)},
        )

    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return ToolResult(
            content=f"Web search failed: {str(e)}. Answering from training knowledge only.",
            risk=RiskLevel.LOW,
            source="web_search",
        )
