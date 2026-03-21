"""Tests for project_search and global_search tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext
from services.tools.search_tools import (
    project_search_tool,
    global_search_tool,
    handle_project_search,
    handle_global_search,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id="proj-123", conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(spec=AsyncSession), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestProjectSearchTool:
    def test_tool_def(self):
        assert project_search_tool.name == "project_search"
        assert project_search_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_search_with_project_scope(self):
        """project_search should pass project_id to the search function."""
        mock_results = [
            {"type": "segment", "text": "quarterly revenue discussion", "id": "seg-1", "start_time": 30.0},
        ]

        with patch("services.tools.search_tools._run_search", return_value=mock_results) as mock_search:
            result = await handle_project_search({"query": "quarterly revenue"}, make_ctx())

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        # Should pass project_id="proj-123"
        assert call_args[1].get("project_id") == "proj-123" or (len(call_args[0]) > 2 and call_args[0][2] == "proj-123")
        assert "quarterly revenue" in result.content.lower() or "1 result" in result.content.lower()


class TestGlobalSearchTool:
    def test_tool_def(self):
        assert global_search_tool.name == "global_search"
        assert global_search_tool.project_scoped is False

    @pytest.mark.asyncio
    async def test_search_crosses_projects(self):
        """global_search should NOT filter by project_id."""
        mock_results = []

        with patch("services.tools.search_tools._run_search", return_value=mock_results) as mock_search:
            result = await handle_global_search({"query": "test"}, make_ctx())

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        # Should pass project_id=None (not the ctx project_id)
        assert call_args[1].get("project_id") is None or (len(call_args[0]) > 2 and call_args[0][2] is None)
        assert "no results" in result.content.lower()

    @pytest.mark.asyncio
    async def test_formats_results(self):
        mock_results = [
            {"type": "recording", "title": "Team Meeting", "id": "rec-1"},
            {"type": "document", "title": "Q4 Report", "id": "doc-1"},
        ]

        with patch("services.tools.search_tools._run_search", return_value=mock_results):
            result = await handle_global_search({"query": "Q4"}, make_ctx())

        assert "Team Meeting" in result.content
        assert "Q4 Report" in result.content
