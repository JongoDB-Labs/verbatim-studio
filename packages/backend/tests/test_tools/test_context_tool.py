"""Tests for get_context tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.context_tool import context_tool, handle_get_context


def make_ctx(**kwargs):
    defaults = dict(
        project_id="proj-123", conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


def _make_db_result(rows):
    """Create a MagicMock that mimics result.scalars().all() returning rows."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


class TestGetContextTool:
    def test_tool_def(self):
        assert context_tool.name == "get_context"
        assert context_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_returns_transcript_content(self):
        """Should search transcripts and return matching text."""
        mock_segments = [
            MagicMock(text="The quarterly revenue was $5M", start_time=120.0, id="seg-1"),
        ]
        mock_execute = AsyncMock(side_effect=[
            _make_db_result(mock_segments),  # segment query
            _make_db_result([]),              # document query
        ])

        ctx = make_ctx()
        ctx.db.execute = mock_execute

        result = await handle_get_context({"query": "quarterly revenue"}, ctx)
        assert "quarterly revenue" in result.content.lower() or "$5M" in result.content

    @pytest.mark.asyncio
    async def test_returns_document_content(self):
        """Should search documents and return matching text."""
        mock_docs = [
            MagicMock(title="Q4 Report", extracted_text="Revenue reached $5M in Q4", id="doc-1"),
        ]
        mock_execute = AsyncMock(side_effect=[
            _make_db_result([]),         # segment query (empty)
            _make_db_result(mock_docs),  # document query
        ])

        ctx = make_ctx()
        ctx.db.execute = mock_execute

        result = await handle_get_context({"query": "Q4 revenue"}, ctx)
        assert "Q4 Report" in result.content or "Revenue" in result.content

    @pytest.mark.asyncio
    async def test_no_results(self):
        """Should return helpful message when nothing is found."""
        mock_execute = AsyncMock(side_effect=[
            _make_db_result([]),  # segment query
            _make_db_result([]),  # document query
        ])

        ctx = make_ctx()
        ctx.db.execute = mock_execute

        result = await handle_get_context({"query": "nonexistent topic"}, ctx)
        assert "no" in result.content.lower() or "not found" in result.content.lower() or "no relevant" in result.content.lower()
