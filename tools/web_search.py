"""
Web Search tool - Three-tier architecture: DuckDuckGo (fast) -> SearXNG (stable) -> Brave (premium).

Registered as Intent.WEB_SEARCH in the tool registry.

Results are formatted as a compact markdown list so the model can
reason over them without a huge token footprint.

Architecture:
- Tier 1: DuckDuckGo - Fast first attempt, no auth required
- Tier 2: SearXNG - Stable core fallback, multi-source aggregation  
- Tier 3: Brave - Premium optional layer, requires API key
"""
from __future__ import annotations
import asyncio
import logging
from core.models import Intent, ToolResult, RiskLevel
from tools import register_tool

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
SNIPPET_MAX = 300


async def _search_ddg(query: str) -> tuple[list[dict], str, str]:
    try:
        from ddgs import DDGS
        safe_query = query.encode("utf-8", errors="replace").decode("utf-8")

        def _run_sync():
            with DDGS() as ddgs:
                return list(ddgs.text(safe_query, max_results=MAX_RESULTS))

        # DDGS is synchronous and blocks the event loop. Run it in a thread
        # with a hard timeout so a slow/hung search never stalls the server.
        results = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _run_sync),
            timeout=12,
        )
        return results, "", ""
    except asyncio.TimeoutError:
        logger.warning("[web_search] DDG timed out after 12s")
        return [], "timeout", "DuckDuckGo search timed out after 12 seconds"
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logger.warning(f"[web_search] DDG failed: {error_msg}")
        return [], "network", f"DuckDuckGo search failed: {error_msg}"


async def _search_searxng(query: str, searxng_url: str = "https://searx.be") -> tuple[list[dict], str, str]:
    """Search using SearXNG instance with proper error handling."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            # SearXNG API format
            params = {
                "q": query,
                "format": "json",
                "engines": "google,duckduckgo,bing,startpage",
                "language": "en",
                "safesearch": "moderate",
                "results": MAX_RESULTS
            }
            r = await client.get(f"{searxng_url}/search", params=params)
            r.raise_for_status()
            data = r.json()
            
            # Extract results from SearXNG response
            results = []
            for item in data.get("results", [])[:MAX_RESULTS]:
                results.append({
                    "title": item.get("title", ""),
                    "href": item.get("url", ""),
                    "body": item.get("content", "")
                })
            
            return results, "", ""
            
    except asyncio.TimeoutError:
        logger.warning(f"[web_search] SearXNG timed out after 15s")
        return [], "timeout", f"SearXNG search timed out after 15 seconds"
    except httpx.ConnectError as e:
        logger.warning(f"[web_search] SearXNG connection failed: {e}")
        return [], "network", f"SearXNG instance unreachable at {searxng_url} - check internet connection"
    except httpx.HTTPStatusError as e:
        logger.warning(f"[web_search] SearXNG HTTP error: {e.response.status_code}")
        if e.response.status_code == 404:
            return [], "network", f"SearXNG instance not found at {searxng_url}"
        elif e.response.status_code >= 500:
            return [], "network", f"SearXNG server error (HTTP {e.response.status_code})"
        else:
            return [], "network", f"SearXNG returned HTTP {e.response.status_code}"
    except Exception as e:
        logger.warning(f"[web_search] SearXNG failed: {repr(e)}")
        error_str = str(e)
        if "timeout" in error_str.lower():
            return [], "timeout", "SearXNG search timed out"
        elif "json" in error_str.lower():
            return [], "network", f"SearXNG returned invalid JSON - instance may be misconfigured"
        else:
            return [], "network", f"SearXNG search error: {error_str}"


async def _search_brave(query: str, api_key: str) -> tuple[list[dict], str, str]:
    try:
        import httpx
        # Pass query directly as string - httpx handles UTF-8 encoding automatically
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": MAX_RESULTS},
                headers={"Accept": "application/json", "x-subscription-token": api_key},
            )
            r.raise_for_status()
            data = r.json()
            results = [
                {"title": w.get("title", ""), "href": w.get("url", ""), "body": w.get("description", "")}
                for w in data.get("web", {}).get("results", [])
            ]
            return results, "", ""
    except asyncio.TimeoutError:
        logger.warning("[web_search] Brave timed out after 10s")
        return [], "timeout", "Brave Search API timed out after 10 seconds"
    except httpx.ConnectError as e:
        logger.warning(f"[web_search] Brave connection failed: {e}")
        return [], "network", f"Brave Search API unreachable - check internet connection"
    except httpx.HTTPStatusError as e:
        logger.warning(f"[web_search] Brave HTTP error: {e.response.status_code}")
        if e.response.status_code == 422:
            return [], "api_key", "Brave Search API key invalid or request format error (HTTP 422)"
        elif e.response.status_code == 401:
            return [], "api_key", "Brave Search API key unauthorized (HTTP 401)"
        else:
            return [], "network", f"Brave Search API returned HTTP {e.response.status_code}"
    except Exception as e:
        logger.warning(f"[web_search] Brave failed: {repr(e)}")
        error_str = str(e)
        if "timeout" in error_str.lower():
            return [], "timeout", "Brave Search API timed out"
        else:
            return [], "network", f"Brave Search API error: {error_str}"


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
    error_type = ""
    error_message = ""
    successful_provider = ""

    # Three-tier search: DDG (fast) -> SearXNG (stable) -> Brave (premium)
    
    # Tier 1: DuckDuckGo - fast first attempt
    logger.info("[web_search] Tier 1: Trying DuckDuckGo")
    results, error_type, error_message = await _search_ddg(query)
    if results:
        successful_provider = "duckduckgo"
        logger.info(f"[web_search] DuckDuckGo succeeded with {len(results)} results")

    # Tier 2: SearXNG - stable core fallback
    if not results:
        logger.info("[web_search] Tier 2: Trying SearXNG")
        searxng_url = getattr(settings, "searxng_url", "https://searx.be")
        results, error_type, error_message = await _search_searxng(query, searxng_url)
        if results:
            successful_provider = "searxng"
            logger.info(f"[web_search] SearXNG succeeded with {len(results)} results")

    # Tier 3: Brave - premium optional layer
    if not results and getattr(settings, "brave_search_api_key", ""):
        logger.info("[web_search] Tier 3: Trying Brave")
        results, error_type, error_message = await _search_brave(query, settings.brave_search_api_key)
        if results:
            successful_provider = "brave"
            logger.info(f"[web_search] Brave succeeded with {len(results)} results")

    # Check if we have results
    if not results:
        logger.warning(f"[web_search] All tiers failed. Final error: {error_message}")
        return ToolResult(
            content=f"All search providers failed. Last error: {error_message}" if error_message else "No results found from any provider.",
            risk=RiskLevel.LOW,
            source="web_search",
            success=False,
            error_type=error_type or "all_tiers_failed",
            error_message=error_message or "All search providers failed",
            metadata={
                "query": query, 
                "result_count": 0, 
                "sources": [],
                "successful_provider": "",
                "attempted_providers": ["duckduckgo", "searxng", "brave" if getattr(settings, "brave_search_api_key", "") else "duckduckgo,searxng"]
            },
        )

    content = _format_results(results)
    source_urls = [r.get("href", r.get("url", "")) for r in results if r.get("href") or r.get("url")]

    logger.info(f"[web_search] Success via {successful_provider}: {len(results)} results")
    return ToolResult(
        content=content,
        risk=RiskLevel.LOW,
        source="web_search",
        success=True,
        error_type="",
        error_message="",
        metadata={
            "query": query, 
            "result_count": len(results), 
            "sources": source_urls[:5],
            "successful_provider": successful_provider,
            "attempted_providers": ["duckduckgo", "searxng", "brave" if getattr(settings, "brave_search_api_key", "") else "duckduckgo,searxng"]
        },
    )


# Register into the tool registry
register_tool(
    Intent.WEB_SEARCH,
    web_search,
    description="Search the web for current information, news, prices, or recent events",
    cost=0.05,
    latency_ms=1500,
)
