"""Tests for summarize_transcript and quality_review tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.analysis_tools import (
    summarize_transcript_tool,
    quality_review_tool,
    handle_summarize_transcript,
    handle_quality_review,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id=None, conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=MagicMock(),
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestSummarizeTranscriptTool:
    def test_tool_def(self):
        assert summarize_transcript_tool.name == "summarize_transcript"
        assert summarize_transcript_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_summarize_success(self):
        """Should summarize the transcript and return key points."""
        mock_transcript = MagicMock(id="t-1")
        mock_segments = [
            MagicMock(text="We discussed quarterly revenue.", start_time=0.0, end_time=3.0, speaker="Speaker A"),
            MagicMock(text="Revenue grew by 20% this quarter.", start_time=3.0, end_time=6.0, speaker="Speaker B"),
        ]

        mock_db = AsyncMock()
        # First: get transcript
        transcript_result = MagicMock()
        transcript_result.scalar_one_or_none.return_value = mock_transcript
        # Second: get segments
        segment_result = MagicMock()
        segment_result.scalars.return_value.all.return_value = mock_segments

        mock_db.execute = AsyncMock(side_effect=[transcript_result, segment_result])

        mock_ai = MagicMock()
        mock_ai.chat = AsyncMock(return_value=MagicMock(
            content="Summary: Revenue discussion. Key points: 20% growth."
        ))

        ctx = make_ctx(db=mock_db, ai_service=mock_ai)

        result = await handle_summarize_transcript({"transcript_id": "t-1"}, ctx)
        assert "summary" in result.content.lower() or "revenue" in result.content.lower()
        mock_ai.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_transcript_not_found(self):
        """Should return error if transcript doesn't exist."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_summarize_transcript({"transcript_id": "bad-id"}, ctx)
        assert "not found" in result.content.lower()


class TestQualityReviewTool:
    def test_tool_def(self):
        assert quality_review_tool.name == "quality_review"
        assert quality_review_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_enqueue_review(self):
        """Should enqueue a quality review job and return job info."""
        mock_transcript = MagicMock(id="t-1")
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_transcript
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)

        with patch("services.jobs.job_queue") as mock_queue:
            mock_queue.enqueue = AsyncMock(return_value="job-123")
            result = await handle_quality_review({"transcript_id": "t-1"}, ctx)

        assert "review" in result.content.lower() or "quality" in result.content.lower()

    @pytest.mark.asyncio
    async def test_transcript_not_found(self):
        """Should return error if transcript doesn't exist."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_quality_review({"transcript_id": "bad-id"}, ctx)
        assert "not found" in result.content.lower()
