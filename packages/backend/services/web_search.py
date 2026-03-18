"""Web search service for AI-augmented chat.

Supports Tavily (primary) and Brave Search (fallback).
Uses hybrid query extraction: heuristic first, LLM fallback.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Heuristic patterns that signal search intent
_TEMPORAL_KEYWORDS = re.compile(
    r"\b(latest|recent|current|today|now|new|2025|2026|this year|this month|this week)\b",
    re.IGNORECASE,
)
_SEARCH_COMMANDS = re.compile(
    r"\b(search for|look up|find information|google|find out about|what is the latest)\b",
    re.IGNORECASE,
)
_FACTUAL_PATTERNS = re.compile(
    r"\b(what is|who is|how does|define|explain|tell me about)\b",
    re.IGNORECASE,
)
_NON_SEARCH_PATTERNS = re.compile(
    r"\b(summarize|analyze|list the|from the transcript|in this document|from this)\b",
    re.IGNORECASE,
)


@dataclass
class SearchQuery:
    """Extracted search query."""

    text: str
    confidence: float = 1.0


@dataclass
class WebSearchResult:
    """A single web search result."""

    title: str
    url: str
    content: str
    relevance_score: float = 0.0


@dataclass
class WebSearchConfig:
    """Configuration for web search providers."""

    provider: str = "tavily"
    api_key: str = ""
    max_results: int = 5
    timeout_s: float = 5.0
    searxng_url: str = "http://localhost:8888"


def extract_search_query(message: str) -> SearchQuery | None:
    """Extract a search query from a user message using heuristics.

    Returns None if the message doesn't appear to need web search.
    """
    # Skip if message is about attached content
    if _NON_SEARCH_PATTERNS.search(message):
        return None

    # Skip very short messages (greetings, etc.)
    words = message.split()
    if len(words) < 3:
        return None

    # Check for explicit search commands
    if _SEARCH_COMMANDS.search(message):
        # Strip the search command prefix
        cleaned = _SEARCH_COMMANDS.sub("", message).strip()
        return SearchQuery(text=cleaned or message, confidence=0.9)

    # Check for temporal keywords (wants current info)
    if _TEMPORAL_KEYWORDS.search(message):
        return SearchQuery(text=message, confidence=0.8)

    # Check for factual patterns
    if _FACTUAL_PATTERNS.search(message):
        # Only if it seems like general knowledge, not about attached content
        return SearchQuery(text=message, confidence=0.6)

    return None


def format_results_for_context(
    results: list[WebSearchResult],
    max_tokens: int = 1000,
) -> str:
    """Format search results into a string for LLM context injection.

    Prioritizes higher-relevance results. Truncates to fit token budget.
    """
    if not results:
        return ""

    # Sort by relevance (highest first)
    sorted_results = sorted(results, key=lambda r: r.relevance_score, reverse=True)

    parts: list[str] = []
    estimated_tokens = 0

    for result in sorted_results:
        entry = f"[{result.title}]({result.url})\n{result.content}\n"
        entry_tokens = len(entry) // 3  # Conservative estimate
        if estimated_tokens + entry_tokens > max_tokens:
            # Try to fit a truncated version
            remaining_chars = (max_tokens - estimated_tokens) * 3
            if remaining_chars > 100:
                truncated_content = result.content[: remaining_chars - 50]
                entry = f"[{result.title}]({result.url})\n{truncated_content}...\n"
                parts.append(entry)
            break
        parts.append(entry)
        estimated_tokens += entry_tokens

    return "\n".join(parts)


class TavilySearchProvider:
    """Tavily search API provider — purpose-built for RAG."""

    def __init__(self, config: WebSearchConfig):
        self._api_key = config.api_key
        self._max_results = config.max_results
        self._timeout = config.timeout_s

    async def search(self, query: str) -> list[WebSearchResult]:
        """Execute a search via Tavily API."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": self._max_results,
                    "include_answer": False,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", []):
            results.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("content", ""),
                    relevance_score=item.get("score", 0.0),
                )
            )
        return results


class BraveSearchProvider:
    """Brave Search API provider — privacy-focused fallback."""

    def __init__(self, config: WebSearchConfig):
        self._api_key = config.api_key
        self._max_results = config.max_results
        self._timeout = config.timeout_s

    async def search(self, query: str) -> list[WebSearchResult]:
        """Execute a search via Brave Search API."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": self._max_results},
                headers={
                    "X-Subscription-Token": self._api_key,
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("web", {}).get("results", []):
            results.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("description", ""),
                    relevance_score=0.5,  # Brave doesn't provide relevance scores
                )
            )
        return results


def create_search_provider(
    config: WebSearchConfig,
) -> TavilySearchProvider | BraveSearchProvider:
    """Factory for search providers."""
    if config.provider == "brave":
        return BraveSearchProvider(config)
    return TavilySearchProvider(config)
