"""Tests for highlight_segments and add_note tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.tool_registry import ToolContext
from services.tools.annotation_tools import (
    highlight_segments_tool,
    add_note_tool,
    handle_highlight_segments,
    handle_add_note,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id="proj-1", conversation_id=None,
        recording_ids=["rec-1"], document_ids=[],
        db=AsyncMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestHighlightSegmentsTool:
    def test_tool_def(self):
        assert highlight_segments_tool.name == "highlight_segments"
        assert highlight_segments_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_highlight_segments(self):
        """Should upsert SegmentHighlight rows for each segment."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=3))
        mock_db.commit = AsyncMock()

        ctx = make_ctx(db=mock_db)
        result = await handle_highlight_segments({
            "segment_ids": ["seg-1", "seg-2", "seg-3"],
            "color": "yellow",
        }, ctx)

        assert "highlight" in result.content.lower() or "3" in result.content
        mock_db.execute.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_color(self):
        """Should reject invalid colors."""
        ctx = make_ctx()
        result = await handle_highlight_segments({
            "segment_ids": ["seg-1"],
            "color": "rainbow",
        }, ctx)
        assert "invalid" in result.content.lower() or "color" in result.content.lower()

    @pytest.mark.asyncio
    async def test_no_segment_ids(self):
        """Should reject empty segment list."""
        ctx = make_ctx()
        result = await handle_highlight_segments({
            "segment_ids": [],
            "color": "yellow",
        }, ctx)
        assert "no segment" in result.content.lower()


class TestAddNoteTool:
    def test_tool_def(self):
        assert add_note_tool.name == "add_note"
        assert add_note_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_add_note_to_recording(self):
        """Should create a note anchored to a timestamp."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        ctx = make_ctx(db=mock_db)
        result = await handle_add_note({
            "content": "Important discussion about Q4 targets",
            "recording_id": "rec-1",
            "timestamp": 120.5,
        }, ctx)

        assert "note" in result.content.lower() or "added" in result.content.lower()
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_note_to_document(self):
        """Should create a note anchored to a page."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        ctx = make_ctx(db=mock_db)
        result = await handle_add_note({
            "content": "Key findings on page 5",
            "document_id": "doc-1",
            "page": 5,
        }, ctx)

        assert "note" in result.content.lower() or "added" in result.content.lower()
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_note_missing_content(self):
        """Should require content."""
        ctx = make_ctx()
        result = await handle_add_note({
            "recording_id": "rec-1",
            "timestamp": 0,
        }, ctx)
        assert "content" in result.content.lower() or "required" in result.content.lower()

    @pytest.mark.asyncio
    async def test_add_note_no_anchor(self):
        """Should require either recording_id or document_id."""
        ctx = make_ctx()
        result = await handle_add_note({
            "content": "A floating note",
        }, ctx)
        assert "recording_id" in result.content.lower() or "document_id" in result.content.lower()
