"""
Web Search tool — DuckDuckGo (no API key required) with Brave fallback.

Registered as Intent.WEB_SEARCH in the tool registry.

Results are formatted as a compact markdown list so the model can
reason over them without a huge token footprint.
"""
from __future__ import annotations
import logging
from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
SNIPPET_MAX = 300


async def _search_ddg(query: str) -> list[dict]:
    try:
        from ddgs import DDGS
        safe_query = query.encode("utf-8", errors="replace").decode("utf-8")
        with DDGS() as ddgs:
            results = list(ddgs.text(safe_query, max_results=MAX_RESULTS))
        return results
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logger.warning(f"[web_search] DDG failed: {error_msg}")
        return []


async def _search_brave(query: str, api_key: str) -> list[dict]:
    try:
        import httpx
        import urllib.parse
        safe_query = urllib.parse.quote(query.encode("utf-8", errors="replace").decode("utf-8"))
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": MAX_RESULTS},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            r.raise_for_status()
            data = r.json()
            return [
                {"title": w.get("title", ""), "href": w.get("url", ""), "body": w.get("description", "")}
                for w in data.get("web", {}).get("results", [])
            ]
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logger.warning(f"[web_search] Brave failed: {error_msg}")
        return []


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        url = r.get("href", r.get("url", "")).strip()
        body = r.get("body", r.get("description", "")).strip()
        if len(body) > SNIPPET_MAX:
            body = body[:SNIPPET_MAX] + "…"
        # Ensure proper Unicode handling
        title = title.encode('utf-8', errors='ignore').decode('utf-8')
        url = url.encode('utf-8', errors='ignore').decode('utf-8')
        body = body.encode('utf-8', errors='ignore').decode('utf-8')
        lines.append(f"{i}. **{title}**\n   {url}\n   {body}")
    return "\n\n".join(lines)


async def web_search(query: str) -> ToolResult:
    from core.config import settings

    results = []

    # Try Brave first if API key is set
    if getattr(settings, "brave_search_api_key", ""):
        results = await _search_brave(query, settings.brave_search_api_key)

    # Fall back to DuckDuckGo
    if not results:
        results = await _search_ddg(query)

    content = _format_results(results)
    source_urls = [r.get("href", r.get("url", "")) for r in results if r.get("href") or r.get("url")]

    return ToolResult(
        content=content,
        risk=RiskLevel.LOW,
        source="web_search",
        metadata={"query": query, "result_count": len(results), "sources": source_urls[:5]},
    )


# Register into the tool registry
register_tool(
    Intent.WEB_SEARCH,
    web_search,
    description="Search the web for current information, news, prices, or recent events",
    cost=0.05,
    latency_ms=1500,
)
