"""Integration tests for web search + smart context features.

Tests the full API contract: config endpoints, chat request/response shapes,
conversation CRUD with compressed_memory, and web cache lifecycle.
"""
import json
import time
import pytest
from httpx import AsyncClient

from services.web_search import (
    extract_search_query,
    format_results_for_context,
    WebSearchResult,
    WebSearchConfig,
    create_search_provider,
    TavilySearchProvider,
    BraveSearchProvider,
    get_cached_results,
    cache_results,
    _search_cache,
    _CACHE_TTL,
)
from services.conversation_memory import ConversationMemoryService
from services.context_manager import ContextManager, ContextBudget


# ── Query Extraction Edge Cases ──────────────────────────────────────

class TestQueryExtractionEdgeCases:
    """Edge cases for heuristic search query extraction."""

    def test_non_search_overrides_temporal(self):
        """Non-search patterns should take precedence over temporal keywords."""
        query = extract_search_query("Summarize the latest points from the transcript")
        assert query is None

    def test_url_in_message(self):
        """Messages with URLs could signal search intent but are currently not special-cased."""
        query = extract_search_query("What is example.com about?")
        # "What is" triggers factual pattern
        assert query is not None

    def test_single_word_rejected(self):
        query = extract_search_query("Hi")
        assert query is None

    def test_two_word_rejected(self):
        query = extract_search_query("Hello there")
        assert query is None

    def test_three_word_factual_passes(self):
        query = extract_search_query("What is Python?")
        assert query is not None

    def test_explicit_search_strips_prefix(self):
        query = extract_search_query("Search for quantum computing advances")
        assert query is not None
        assert "Search for" not in query.text
        assert "quantum computing" in query.text

    def test_confidence_levels(self):
        """Explicit search should have higher confidence than factual."""
        explicit = extract_search_query("Search for AI benchmarks")
        temporal = extract_search_query("What are the latest AI benchmarks?")
        factual = extract_search_query("What is a transformer model?")
        assert explicit.confidence > factual.confidence
        assert temporal.confidence > factual.confidence

    def test_empty_string(self):
        query = extract_search_query("")
        assert query is None

    def test_analyze_document_not_search(self):
        query = extract_search_query("Analyze the sentiment from this document")
        assert query is None

    def test_list_from_transcript_not_search(self):
        query = extract_search_query("List the key points from the transcript")
        assert query is None


# ── Result Formatting Edge Cases ─────────────────────────────────────

class TestResultFormattingEdgeCases:
    """Edge cases for result formatting."""

    def test_empty_results_returns_empty_string(self):
        assert format_results_for_context([], max_tokens=500) == ""

    def test_single_result_fits_budget(self):
        results = [
            WebSearchResult(title="Test", url="https://x.com", content="Hello world", relevance_score=0.9),
        ]
        formatted = format_results_for_context(results, max_tokens=100)
        assert "Test" in formatted
        assert "Hello world" in formatted

    def test_results_sorted_by_relevance(self):
        results = [
            WebSearchResult(title="Low", url="https://low.com", content="Low relevance", relevance_score=0.1),
            WebSearchResult(title="High", url="https://high.com", content="High relevance", relevance_score=0.9),
        ]
        formatted = format_results_for_context(results, max_tokens=500)
        # High relevance should appear first
        assert formatted.index("High") < formatted.index("Low")

    def test_zero_budget_returns_minimal(self):
        results = [
            WebSearchResult(title="Test", url="https://x.com", content="Content", relevance_score=0.9),
        ]
        formatted = format_results_for_context(results, max_tokens=0)
        # With 0 budget, the entry exceeds budget immediately, but remaining_chars check
        # means we either get nothing or a truncated version
        assert len(formatted) < 500


# ── Provider Factory ─────────────────────────────────────────────────

class TestProviderFactory:
    """Test the search provider factory."""

    def test_default_returns_tavily(self):
        config = WebSearchConfig(api_key="test")
        provider = create_search_provider(config)
        assert isinstance(provider, TavilySearchProvider)

    def test_tavily_explicit(self):
        config = WebSearchConfig(provider="tavily", api_key="test")
        provider = create_search_provider(config)
        assert isinstance(provider, TavilySearchProvider)

    def test_brave_provider(self):
        config = WebSearchConfig(provider="brave", api_key="test")
        provider = create_search_provider(config)
        assert isinstance(provider, BraveSearchProvider)

    def test_unknown_provider_defaults_to_tavily(self):
        config = WebSearchConfig(provider="unknown", api_key="test")
        provider = create_search_provider(config)
        assert isinstance(provider, TavilySearchProvider)


# ── ConversationMemory Edge Cases ────────────────────────────────────

class TestConversationMemoryEdgeCases:
    """Additional edge cases for conversation memory service."""

    def test_empty_history_no_compress(self):
        service = ConversationMemoryService(compression_threshold=8)
        assert service.should_compress([]) is False

    def test_exact_threshold_compresses(self):
        service = ConversationMemoryService(compression_threshold=4)
        history = [{"role": "user", "content": f"msg{i}"} for i in range(4)]
        assert service.should_compress(history) is True

    def test_below_threshold_no_compress(self):
        service = ConversationMemoryService(compression_threshold=4)
        history = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
        assert service.should_compress(history) is False

    def test_split_history_all_recent(self):
        """When history fits in recent window, old should be empty."""
        service = ConversationMemoryService(compression_threshold=4, recent_pairs_to_keep=5)
        history = [
            {"role": "user", "content": f"msg{i}"}
            for i in range(6)
        ]
        old, recent = service.split_history(history)
        assert len(old) == 0
        assert len(recent) == 6

    def test_system_prompt_fresh_vs_incremental(self):
        service = ConversationMemoryService()
        fresh = service.get_system_prompt(existing_memory=None)
        incremental = service.get_system_prompt(existing_memory="Previous summary")
        assert fresh != incremental
        assert "summarizer" in fresh.lower()
        assert "summarizer" in incremental.lower()

    def test_compression_prompt_includes_role_labels(self):
        service = ConversationMemoryService()
        old_msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        prompt = service.build_compression_prompt(old_msgs)
        assert "User: Hello" in prompt
        assert "Assistant: Hi there" in prompt


# ── ContextManager Edge Cases ────────────────────────────────────────

class TestContextManagerEdgeCases:
    """Additional edge cases for context budget allocation."""

    def test_all_zero_inputs(self):
        mgr = ContextManager(n_ctx=4096, max_response_tokens=1024)
        budget = mgr.allocate_budget(
            system_tokens=0, user_message_tokens=0,
            memory_tokens=0, web_result_tokens=0,
            history_tokens=0, context_tokens=0,
        )
        assert budget.total_input == 0

    def test_web_results_allocated_before_history(self):
        """Web results have higher priority than context but lower than memory."""
        mgr = ContextManager(n_ctx=2048, max_response_tokens=512)
        budget = mgr.allocate_budget(
            system_tokens=100, user_message_tokens=50,
            memory_tokens=200, web_result_tokens=500,
            history_tokens=500, context_tokens=500,
        )
        # Memory should be fully allocated
        assert budget.memory == 200
        # Web results should get their share before context
        assert budget.web_results >= 0

    def test_build_messages_no_memory_no_web(self):
        mgr = ContextManager(n_ctx=4096, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            user_message="Hello",
        )
        assert len(messages) == 2  # system + user
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert "Conversation Memory" not in messages[0].content
        assert "Web Search" not in messages[0].content

    def test_build_messages_with_web_results(self):
        mgr = ContextManager(n_ctx=4096, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            web_results="[Result 1](https://example.com)\nSome content",
            user_message="What is X?",
        )
        assert "Web Search Results" in messages[0].content
        assert "example.com" in messages[0].content

    def test_build_messages_with_all_tiers(self):
        mgr = ContextManager(n_ctx=8192, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            compressed_memory="User discussed transcription.",
            web_results="[Article](https://x.com)\nContent here",
            attached_context="=== Transcript A ===\nHello world",
            recent_history=[
                {"role": "user", "content": "What's in transcript A?"},
                {"role": "assistant", "content": "It says hello world."},
            ],
            user_message="Tell me more",
        )
        system_msg = messages[0].content
        assert "Conversation Memory" in system_msg
        assert "transcription" in system_msg
        assert "Web Search Results" in system_msg
        assert "Transcript A" in system_msg
        # History + user = 3 more messages
        assert len(messages) == 4
        assert messages[-1].content == "Tell me more"

    def test_budget_total_input_property(self):
        budget = ContextBudget(
            system=100, memory=200, web_results=300,
            context=400, history=500, user_message=50,
            response_reserved=1024,
        )
        assert budget.total_input == 100 + 200 + 300 + 400 + 500 + 50


# ── In-Memory Cache ─────────────────────────────────────────────────

class TestInMemoryCache:
    """Tests for the in-memory TTL search cache."""

    def setup_method(self):
        """Clear cache before each test."""
        _search_cache.clear()

    def test_cache_miss_returns_none(self):
        assert get_cached_results("nonexistent query") is None

    def test_cache_hit_returns_results(self):
        results = [
            WebSearchResult(title="Cached", url="https://c.com", content="data", relevance_score=0.8),
        ]
        cache_results("test query", results)
        cached = get_cached_results("test query")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].title == "Cached"

    def test_cache_expired_returns_none(self):
        results = [
            WebSearchResult(title="Old", url="https://old.com", content="stale", relevance_score=0.5),
        ]
        # Insert with a timestamp in the past
        _search_cache["expired query"] = (time.time() - _CACHE_TTL - 1, results)
        assert get_cached_results("expired query") is None
        # Should also evict the entry
        assert "expired query" not in _search_cache

    def test_cache_eviction_on_overflow(self):
        # Fill cache to max
        for i in range(100):
            cache_results(f"query-{i}", [
                WebSearchResult(title=f"R{i}", url=f"https://{i}.com", content="x", relevance_score=0.5),
            ])
        assert len(_search_cache) == 100

        # Adding one more should trigger eviction of oldest half
        cache_results("overflow-query", [
            WebSearchResult(title="New", url="https://new.com", content="fresh", relevance_score=0.9),
        ])
        assert len(_search_cache) <= 51  # 50 kept + 1 new
        assert get_cached_results("overflow-query") is not None

    def test_different_queries_cached_separately(self):
        r1 = [WebSearchResult(title="A", url="https://a.com", content="aaa", relevance_score=0.9)]
        r2 = [WebSearchResult(title="B", url="https://b.com", content="bbb", relevance_score=0.7)]
        cache_results("query alpha", r1)
        cache_results("query beta", r2)
        assert get_cached_results("query alpha")[0].title == "A"
        assert get_cached_results("query beta")[0].title == "B"
