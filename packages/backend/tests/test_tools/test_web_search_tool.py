"""Tests for web_search tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.web_search_tool import web_search_tool, handle_web_search


def make_ctx(**kwargs):
    defaults = dict(
        project_id=None, conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestWebSearchTool:
    def test_tool_def_exists(self):
        assert web_search_tool.name == "web_search"
        assert "search" in web_search_tool.description.lower()

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        from services.web_search import WebSearchResult

        mock_results = [
            WebSearchResult(title="Result 1", url="https://example.com", content="Content 1", relevance_score=0.9),
            WebSearchResult(title="Result 2", url="https://test.com", content="Content 2", relevance_score=0.7),
        ]

        with patch("services.tools.web_search_tool.load_web_search_config") as mock_config, \
             patch("services.tools.web_search_tool.create_search_provider") as mock_provider_factory:
            mock_config.return_value = MagicMock(provider="tavily", api_key="test")
            mock_provider = AsyncMock()
            mock_provider.search.return_value = mock_results
            mock_provider_factory.return_value = mock_provider

            result = await handle_web_search({"query": "AI benchmarks"}, make_ctx())

        assert "Result 1" in result.content
        assert "Result 2" in result.content

    @pytest.mark.asyncio
    async def test_search_no_config_returns_error(self):
        with patch("services.tools.web_search_tool.load_web_search_config", return_value=None):
            result = await handle_web_search({"query": "test"}, make_ctx())

        assert "not configured" in result.content.lower()

    @pytest.mark.asyncio
    async def test_url_extraction(self):
        from services.web_search import WebSearchResult

        mock_results = [
            WebSearchResult(title="Page", url="https://example.com/article", content="Full page content", relevance_score=1.0),
        ]

        with patch("services.tools.web_search_tool.load_web_search_config") as mock_config, \
             patch("services.tools.web_search_tool.create_search_provider") as mock_provider_factory:
            mock_config.return_value = MagicMock()
            mock_provider = AsyncMock()
            mock_provider.extract.return_value = mock_results
            mock_provider_factory.return_value = mock_provider

            result = await handle_web_search(
                {"query": "summarize this", "urls": ["https://example.com/article"]},
                make_ctx(),
            )

        mock_provider.extract.assert_called_once()
        assert "Full page content" in result.content
