"""End-to-end provider tests for web search.

Mocks HTTP calls to verify the full flow:
  query extraction → config resolution → cache → provider HTTP call → response parsing → formatting

Tests each provider's request shape, response parsing, error handling, and the
3-layer cache hierarchy (in-memory → DB → live API).
"""
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.web_search import (
    BraveSearchProvider,
    TavilySearchProvider,
    WebSearchConfig,
    WebSearchResult,
    cache_results,
    create_search_provider,
    extract_search_query,
    format_results_for_context,
    get_cached_results,
    _search_cache,
)


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create an httpx.Response with a request attached (needed for raise_for_status)."""
    request = httpx.Request("GET", "https://mock.test")
    return httpx.Response(status_code, json=json_data, request=request)


def _make_mock_client(method: str, response: httpx.Response, capture: dict | None = None):
    """Create a mock httpx.AsyncClient that captures calls and returns a response.

    Args:
        method: "post" or "get"
        response: the httpx.Response to return
        capture: optional dict to capture the call args
    """
    mock_client = AsyncMock()

    async def handler(*args, **kwargs):
        if capture is not None:
            capture["args"] = args
            capture["kwargs"] = kwargs
        return response

    setattr(mock_client, method, AsyncMock(side_effect=handler))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── Tavily Provider ─────────────────────────────────────────────────


class TestTavilyProviderHTTP:
    """Verify Tavily request shape and response parsing."""

    @pytest.fixture
    def tavily_response_data(self):
        return {
            "results": [
                {
                    "title": "AI Benchmarks 2026",
                    "url": "https://example.com/ai-benchmarks",
                    "content": "The latest AI benchmarks show significant improvements in reasoning.",
                    "score": 0.95,
                },
                {
                    "title": "ML Progress Report",
                    "url": "https://example.com/ml-report",
                    "content": "Machine learning models have surpassed human performance on several tasks.",
                    "score": 0.82,
                },
            ]
        }

    @pytest.mark.asyncio
    async def test_tavily_sends_correct_request(self, tavily_response_data):
        """Verify Tavily POST body has required fields."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-test-key-123", max_results=3)
        provider = TavilySearchProvider(config)

        capture = {}
        mock_client = _make_mock_client("post", _mock_response(200, tavily_response_data), capture)

        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("latest AI benchmarks")

        # Verify request shape
        call_kwargs = capture["kwargs"]
        assert "https://api.tavily.com/search" in capture["args"]
        req_body = call_kwargs["json"]
        assert req_body["api_key"] == "tvly-test-key-123"
        assert req_body["query"] == "latest AI benchmarks"
        assert req_body["max_results"] == 3
        assert req_body["include_answer"] is False
        assert req_body["search_depth"] == "basic"

    @pytest.mark.asyncio
    async def test_tavily_parses_response_correctly(self, tavily_response_data):
        """Verify Tavily response is parsed into WebSearchResult objects."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-key")
        provider = TavilySearchProvider(config)

        mock_client = _make_mock_client("post", _mock_response(200, tavily_response_data))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("AI benchmarks")

        assert len(results) == 2
        assert results[0].title == "AI Benchmarks 2026"
        assert results[0].url == "https://example.com/ai-benchmarks"
        assert "reasoning" in results[0].content
        assert results[0].relevance_score == 0.95
        assert results[1].relevance_score == 0.82

    @pytest.mark.asyncio
    async def test_tavily_handles_empty_results(self):
        """Tavily returns empty list when no results found."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-key")
        provider = TavilySearchProvider(config)

        mock_client = _make_mock_client("post", _mock_response(200, {"results": []}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("xyzzy nonexistent topic 12345")

        assert results == []

    @pytest.mark.asyncio
    async def test_tavily_handles_missing_fields(self):
        """Tavily gracefully handles results with missing optional fields."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-key")
        provider = TavilySearchProvider(config)

        mock_client = _make_mock_client("post", _mock_response(200, {
            "results": [{"title": "Partial", "url": "https://p.com"}]
        }))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("test")

        assert len(results) == 1
        assert results[0].title == "Partial"
        assert results[0].content == ""
        assert results[0].relevance_score == 0.0

    @pytest.mark.asyncio
    async def test_tavily_raises_on_http_error(self):
        """Tavily raises on non-200 status (e.g. 401 unauthorized)."""
        config = WebSearchConfig(provider="tavily", api_key="invalid-key")
        provider = TavilySearchProvider(config)

        mock_client = _make_mock_client("post", _mock_response(401, {"error": "Invalid API key"}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await provider.search("test query")

    @pytest.mark.asyncio
    async def test_tavily_raises_on_timeout(self):
        """Tavily raises on connection timeout."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-key", timeout_s=0.1)
        provider = TavilySearchProvider(config)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("Connection timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.ReadTimeout):
                await provider.search("test query")


# ── Brave Provider ──────────────────────────────────────────────────


class TestBraveProviderHTTP:
    """Verify Brave Search request shape and response parsing."""

    @pytest.fixture
    def brave_response_data(self):
        return {
            "web": {
                "results": [
                    {
                        "title": "Quantum Computing News",
                        "url": "https://example.com/quantum",
                        "description": "Breakthrough in quantum error correction announced today.",
                    },
                    {
                        "title": "Tech Trends 2026",
                        "url": "https://example.com/trends",
                        "description": "Top technology trends shaping the industry this year.",
                    },
                ]
            }
        }

    @pytest.mark.asyncio
    async def test_brave_sends_correct_request(self, brave_response_data):
        """Verify Brave GET params and headers."""
        config = WebSearchConfig(provider="brave", api_key="BSA-test-key-456", max_results=4)
        provider = BraveSearchProvider(config)

        capture = {}
        mock_client = _make_mock_client("get", _mock_response(200, brave_response_data), capture)

        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("quantum computing news")

        call_kwargs = capture["kwargs"]
        assert "https://api.search.brave.com/res/v1/web/search" in capture["args"]
        assert call_kwargs["params"]["q"] == "quantum computing news"
        assert call_kwargs["params"]["count"] == 4
        assert call_kwargs["headers"]["X-Subscription-Token"] == "BSA-test-key-456"
        assert call_kwargs["headers"]["Accept"] == "application/json"

    @pytest.mark.asyncio
    async def test_brave_parses_response_correctly(self, brave_response_data):
        """Verify Brave response is parsed into WebSearchResult objects."""
        config = WebSearchConfig(provider="brave", api_key="BSA-key")
        provider = BraveSearchProvider(config)

        mock_client = _make_mock_client("get", _mock_response(200, brave_response_data))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("quantum computing")

        assert len(results) == 2
        assert results[0].title == "Quantum Computing News"
        assert results[0].url == "https://example.com/quantum"
        assert "quantum error correction" in results[0].content
        # Brave assigns fixed 0.5 relevance (no score from API)
        assert results[0].relevance_score == 0.5
        assert results[1].relevance_score == 0.5

    @pytest.mark.asyncio
    async def test_brave_handles_empty_results(self):
        """Brave returns empty list when no results found."""
        config = WebSearchConfig(provider="brave", api_key="BSA-key")
        provider = BraveSearchProvider(config)

        mock_client = _make_mock_client("get", _mock_response(200, {"web": {"results": []}}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("xyzzy nonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_brave_handles_missing_web_key(self):
        """Brave gracefully handles response without 'web' key."""
        config = WebSearchConfig(provider="brave", api_key="BSA-key")
        provider = BraveSearchProvider(config)

        mock_client = _make_mock_client("get", _mock_response(200, {}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_brave_raises_on_http_error(self):
        """Brave raises on 429 rate limit."""
        config = WebSearchConfig(provider="brave", api_key="BSA-key")
        provider = BraveSearchProvider(config)

        mock_client = _make_mock_client("get", _mock_response(429, {"error": "Rate limit exceeded"}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await provider.search("test")

    @pytest.mark.asyncio
    async def test_brave_raises_on_timeout(self):
        """Brave raises on connection timeout."""
        config = WebSearchConfig(provider="brave", api_key="BSA-key")
        provider = BraveSearchProvider(config)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("Connection timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.ReadTimeout):
                await provider.search("test")


# ── Provider Factory ────────────────────────────────────────────────


class TestProviderFactoryIntegration:
    """Test factory creates correct provider and it works end-to-end."""

    @pytest.mark.asyncio
    async def test_factory_tavily_end_to_end(self):
        """Factory → Tavily → mock HTTP → parsed results."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-e2e")
        provider = create_search_provider(config)
        assert isinstance(provider, TavilySearchProvider)

        mock_client = _make_mock_client("post", _mock_response(200, {
            "results": [{"title": "E2E", "url": "https://e2e.com", "content": "works", "score": 0.99}]
        }))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("end to end test")

        assert len(results) == 1
        assert results[0].title == "E2E"
        assert results[0].relevance_score == 0.99

    @pytest.mark.asyncio
    async def test_factory_brave_end_to_end(self):
        """Factory → Brave → mock HTTP → parsed results."""
        config = WebSearchConfig(provider="brave", api_key="BSA-e2e")
        provider = create_search_provider(config)
        assert isinstance(provider, BraveSearchProvider)

        mock_client = _make_mock_client("get", _mock_response(200, {
            "web": {"results": [{"title": "BraveE2E", "url": "https://b.com", "description": "brave works"}]}
        }))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search("brave end to end")

        assert len(results) == 1
        assert results[0].title == "BraveE2E"


# ── Full Flow: Extract → Search → Format ───────────────────────────


class TestFullSearchFlow:
    """Test the complete pipeline: extraction → provider → formatting."""

    def setup_method(self):
        _search_cache.clear()

    @pytest.mark.asyncio
    async def test_full_tavily_flow(self):
        """User message → extract query → Tavily search → formatted context."""
        # Step 1: Extract query
        query = extract_search_query("What are the latest AI benchmarks?")
        assert query is not None
        assert query.confidence >= 0.6

        # Step 2: Search via Tavily
        config = WebSearchConfig(provider="tavily", api_key="tvly-flow-test")
        provider = create_search_provider(config)

        mock_data = {
            "results": [
                {"title": "MMLU Scores 2026", "url": "https://ai.com/mmlu", "content": "GPT-5 achieves 95% on MMLU.", "score": 0.93},
                {"title": "HumanEval Update", "url": "https://ai.com/humaneval", "content": "Code generation at 90% pass rate.", "score": 0.85},
            ]
        }
        mock_client = _make_mock_client("post", _mock_response(200, mock_data))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search(query.text)

        assert len(results) == 2

        # Step 3: Format for context
        formatted = format_results_for_context(results, max_tokens=1000)
        assert "MMLU Scores 2026" in formatted
        assert "HumanEval Update" in formatted
        assert "GPT-5" in formatted
        # Higher relevance should appear first
        assert formatted.index("MMLU") < formatted.index("HumanEval")

    @pytest.mark.asyncio
    async def test_full_brave_flow(self):
        """User message → extract query → Brave search → formatted context."""
        query = extract_search_query("Search for quantum computing advances")
        assert query is not None
        assert "quantum computing" in query.text

        config = WebSearchConfig(provider="brave", api_key="BSA-flow-test")
        provider = create_search_provider(config)

        mock_data = {
            "web": {
                "results": [
                    {"title": "Quantum Leap", "url": "https://q.com/leap", "description": "100-qubit quantum computer achieves supremacy."},
                ]
            }
        }
        mock_client = _make_mock_client("get", _mock_response(200, mock_data))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await provider.search(query.text)

        formatted = format_results_for_context(results, max_tokens=500)
        assert "Quantum Leap" in formatted
        assert "100-qubit" in formatted

    @pytest.mark.asyncio
    async def test_non_search_message_skips_everything(self):
        """Messages without search intent should not trigger any API calls."""
        query = extract_search_query("Summarize the key points from the transcript")
        assert query is None
        # No provider call needed — pipeline stops at extraction

    @pytest.mark.asyncio
    async def test_cache_prevents_duplicate_api_calls(self):
        """Second identical query should hit cache, not API."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-cache-test")
        provider = create_search_provider(config)

        call_count = 0
        mock_data = {"results": [{"title": "Cached", "url": "https://c.com", "content": "data", "score": 0.9}]}

        async def counting_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response(200, mock_data)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=counting_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            # First call — hits API
            results1 = await provider.search("test query for caching")
            cache_results("test query for caching", results1)

            # Second call — should hit cache
            cached = get_cached_results("test query for caching")
            assert cached is not None

        assert call_count == 1  # Only one API call
        assert len(cached) == 1
        assert cached[0].title == "Cached"

    @pytest.mark.asyncio
    async def test_api_error_does_not_crash(self):
        """Search API errors raise but are caught by the caller in ai.py."""
        config = WebSearchConfig(provider="tavily", api_key="tvly-error-test")
        provider = create_search_provider(config)

        mock_client = _make_mock_client("post", _mock_response(500, {"error": "Internal server error"}))
        with patch("services.web_search.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await provider.search("test")
            assert exc_info.value.response.status_code == 500

    @pytest.mark.asyncio
    async def test_formatted_output_respects_token_budget(self):
        """Results exceeding token budget are truncated, not dropped."""
        # Create results with lots of content
        results = [
            WebSearchResult(title=f"Result {i}", url=f"https://r{i}.com",
                          content="x" * 500, relevance_score=1.0 - i * 0.1)
            for i in range(10)
        ]
        formatted = format_results_for_context(results, max_tokens=100)
        # Should fit within roughly 100 tokens (~300 chars)
        assert len(formatted) < 600


# ── Config Guard ────────────────────────────────────────────────────


class TestConfigGuard:
    """Test that empty/missing API keys prevent wasted HTTP calls."""

    def test_empty_api_key_is_falsy(self):
        """Empty string API key should be treated as missing."""
        config = WebSearchConfig(provider="tavily", api_key="")
        assert not config.api_key

    def test_provider_default_is_tavily(self):
        """Default provider should be Tavily."""
        config = WebSearchConfig(api_key="test")
        assert config.provider == "tavily"

    def test_unknown_provider_falls_back_to_tavily(self):
        """Unknown provider string defaults to Tavily via factory."""
        config = WebSearchConfig(provider="nonexistent", api_key="test")
        provider = create_search_provider(config)
        assert isinstance(provider, TavilySearchProvider)
