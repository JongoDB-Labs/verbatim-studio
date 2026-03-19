"""Web search service for AI-augmented chat.

Supports Tavily (primary) and Brave Search (fallback).
Uses hybrid query extraction: heuristic first, LLM fallback.
Includes in-memory TTL cache and centralized config resolution.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# In-memory TTL cache for search results (avoids duplicate API calls)
_search_cache: dict[str, tuple[float, list[WebSearchResult]]] = {}
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX_SIZE = 100

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
    r"\b(from the transcript|in this document|from this)\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s)\"'>]+", re.IGNORECASE)


@dataclass
class SearchQuery:
    """Extracted search query."""

    text: str
    confidence: float = 1.0
    urls: list[str] | None = None  # When set, use extract instead of search


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
    When URLs are detected, sets ``urls`` so callers can use extract
    instead of search.
    """
    # URL detection takes priority — if the user pastes a URL, they want
    # its content regardless of other heuristics.
    urls = _URL_PATTERN.findall(message)
    if urls:
        # Strip trailing punctuation that regex may have captured
        urls = [u.rstrip(".,;:!?)") for u in urls]
        return SearchQuery(text=message, confidence=0.95, urls=urls)

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

    async def extract(self, urls: list[str]) -> list[WebSearchResult]:
        """Extract content from specific URLs via Tavily Extract API."""
        async with httpx.AsyncClient(timeout=max(self._timeout, 15.0)) as client:
            response = await client.post(
                "https://api.tavily.com/extract",
                json={
                    "api_key": self._api_key,
                    "urls": urls,
                    "extract_depth": "basic",
                },
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", []):
            raw = item.get("raw_content", "")
            # Truncate very long pages to keep context manageable
            if len(raw) > 8000:
                raw = raw[:8000] + "\n\n[Content truncated...]"
            results.append(
                WebSearchResult(
                    title=item.get("url", "").split("/")[-1] or item.get("url", ""),
                    url=item.get("url", ""),
                    content=raw,
                    relevance_score=1.0,
                )
            )

        for item in data.get("failed_results", []):
            logger.warning("Tavily extract failed for %s: %s", item.get("url"), item.get("error"))

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


# ── In-memory TTL cache ─────────────────────────────────────────────


def get_cached_results(query: str) -> list[WebSearchResult] | None:
    """Return cached results if present and not expired."""
    entry = _search_cache.get(query)
    if entry is None:
        return None
    ts, results = entry
    if time.time() - ts < _CACHE_TTL:
        logger.debug("Web search cache hit: %s", query)
        return results
    del _search_cache[query]
    return None


def cache_results(query: str, results: list[WebSearchResult]) -> None:
    """Store results in the in-memory cache with TTL."""
    if len(_search_cache) >= _CACHE_MAX_SIZE:
        # Evict oldest half
        by_age = sorted(_search_cache, key=lambda k: _search_cache[k][0])
        for k in by_age[: _CACHE_MAX_SIZE // 2]:
            del _search_cache[k]
    _search_cache[query] = (time.time(), results)


# ── Config resolution (DB → env → default) ─────────────────────────


async def load_web_search_config() -> WebSearchConfig | None:
    """Load web search config from DB, falling back to env/runtime settings.

    Returns None if no API key is configured (avoids wasted HTTP calls).
    """
    from core.config import settings as app_settings

    provider = None
    api_key = None

    # Try DB first (survives server restarts)
    try:
        from sqlalchemy import select

        from persistence.database import get_session_factory
        from persistence.models import Setting
        from services.encryption import decrypt_config

        async with get_session_factory()() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == "web_search_settings")
            )
            setting = result.scalar_one_or_none()

        if setting and setting.value:
            config = decrypt_config(setting.value) if isinstance(setting.value, dict) else {}
            provider = config.get("provider")
            api_key = config.get("api_key")
    except Exception as e:
        logger.debug("Could not load web search settings from DB: %s", e)

    # Fall back to runtime/env settings
    if not provider:
        provider = app_settings.WEB_SEARCH_PROVIDER or "tavily"
    if not api_key:
        api_key = app_settings.WEB_SEARCH_API_KEY

    if not api_key:
        logger.debug("No web search API key configured, skipping search")
        return None

    return WebSearchConfig(provider=provider, api_key=api_key)
