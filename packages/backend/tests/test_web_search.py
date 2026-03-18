"""Tests for web search service."""
import pytest
from services.web_search import (
    WebSearchResult,
    SearchQuery,
    extract_search_query,
    format_results_for_context,
)


class TestQueryExtraction:
    """Test hybrid query extraction."""

    def test_extracts_from_temporal_query(self):
        query = extract_search_query("What are the latest features in Python 3.13?")
        assert query is not None
        assert "Python 3.13" in query.text or "latest features" in query.text

    def test_extracts_from_explicit_search(self):
        query = extract_search_query("Search for IBM Granite model benchmarks")
        assert query is not None
        assert "Granite" in query.text

    def test_returns_none_for_non_search_intent(self):
        query = extract_search_query("Summarize this transcript")
        assert query is None

    def test_returns_none_for_simple_greeting(self):
        query = extract_search_query("Hello!")
        assert query is None


class TestResultFormatting:
    """Test formatting search results for LLM context."""

    def test_formats_results_with_sources(self):
        results = [
            WebSearchResult(
                title="Test Article",
                url="https://example.com/article",
                content="This is the article content about testing.",
                relevance_score=0.95,
            ),
        ]
        formatted = format_results_for_context(results, max_tokens=500)
        assert "Test Article" in formatted
        assert "example.com" in formatted
        assert "article content" in formatted

    def test_truncates_to_fit_budget(self):
        results = [
            WebSearchResult(
                title=f"Article {i}",
                url=f"https://example.com/{i}",
                content="x" * 1000,
                relevance_score=0.9 - i * 0.1,
            )
            for i in range(10)
        ]
        formatted = format_results_for_context(results, max_tokens=200)
        # Should have truncated — rough estimate: 200 tokens ≈ 600 chars
        assert len(formatted) < 5000
