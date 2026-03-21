"""Web search tool — provider-agnostic internet search.

Wraps the existing web_search service. Supports Tavily, Brave Search,
SearXNG, and custom self-hosted providers via the provider factory.
"""

from __future__ import annotations

import logging

from services.tool_registry import Artifact, ToolContext, ToolDef, ToolResult
from services.web_search import (
    create_search_provider,
    format_results_for_context,
    load_web_search_config,
    get_cached_results,
    cache_results,
)

logger = logging.getLogger(__name__)


async def handle_web_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Execute a web search or URL extraction."""
    query = args.get("query", "")
    urls = args.get("urls")

    config = await load_web_search_config()
    if not config:
        return ToolResult(content="Web search is not configured. No API key found.")

    # Check cache first
    if not urls:
        cached = get_cached_results(query)
        if cached:
            formatted = format_results_for_context(cached, max_tokens=3000)
            return ToolResult(content=formatted)

    provider = create_search_provider(config)

    try:
        if urls and hasattr(provider, "extract"):
            results = await provider.extract(urls)
        else:
            results = await provider.search(query)
            # Enrich top 3 with full content
            if results and hasattr(provider, "extract"):
                top_urls = [r.url for r in results[:3]]
                try:
                    extracted = await provider.extract(top_urls)
                    extracted_map = {e.url: e.content for e in extracted}
                    for r in results:
                        if r.url in extracted_map:
                            r.content = extracted_map[r.url]
                except Exception as e:
                    logger.debug("Extract enrichment failed: %s", e)

        if results:
            cache_results(query, results)
            formatted = format_results_for_context(results, max_tokens=3000)
            return ToolResult(content=formatted)
        else:
            return ToolResult(content="No results found.")

    except Exception as e:
        logger.exception("Web search failed")
        return ToolResult(content=f"Web search failed: {e}")


web_search_tool = ToolDef(
    name="web_search",
    description="Search the internet for current information. Use when the user asks about current events, recent data, or asks you to look something up. Also extracts content from URLs.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "Optional: specific URLs to extract content from"},
        },
        "required": ["query"],
    },
    handler=handle_web_search,
    project_scoped=False,
)
