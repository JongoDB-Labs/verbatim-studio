"""Tests for generate_document and export_transcript tools."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.document_tools import (
    generate_document_tool,
    export_transcript_tool,
    handle_generate_document,
    handle_export_transcript,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id=None, conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestGenerateDocumentTool:
    def test_tool_def(self):
        assert generate_document_tool.name == "generate_document"
        assert generate_document_tool.project_scoped is False

    @pytest.mark.asyncio
    async def test_generate_pdf(self, tmp_path):
        """Should generate a PDF file with sections."""
        with patch("services.tools.document_tools._GENERATED_DIR", str(tmp_path)):
            result = await handle_generate_document({
                "title": "Q4 Report",
                "format": "pdf",
                "sections": [
                    {"heading": "Summary", "content": "Revenue grew 20%."},
                    {"heading": "Details", "content": "Total: $5M in Q4."},
                ],
            }, make_ctx())

        assert len(result.artifacts) == 1
        assert result.artifacts[0].type == "file_download"
        assert result.artifacts[0].data["filename"].endswith(".pdf")
        # Verify the file was actually created
        filepath = os.path.join(str(tmp_path), result.artifacts[0].data["filename"])
        assert os.path.exists(filepath)

    @pytest.mark.asyncio
    async def test_generate_docx(self, tmp_path):
        """Should generate a DOCX file."""
        with patch("services.tools.document_tools._GENERATED_DIR", str(tmp_path)):
            result = await handle_generate_document({
                "title": "Meeting Notes",
                "format": "docx",
                "sections": [
                    {"heading": "Action Items", "content": "1. Follow up with team\n2. Review proposal"},
                ],
            }, make_ctx())

        assert len(result.artifacts) == 1
        assert result.artifacts[0].data["filename"].endswith(".docx")

    @pytest.mark.asyncio
    async def test_missing_sections_returns_error(self):
        """Should return error if sections are missing."""
        result = await handle_generate_document({
            "title": "Empty",
            "format": "pdf",
        }, make_ctx())
        assert "section" in result.content.lower() or "error" in result.content.lower() or len(result.artifacts) == 0


class TestExportTranscriptTool:
    def test_tool_def(self):
        assert export_transcript_tool.name == "export_transcript"
        assert export_transcript_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_export_txt(self, tmp_path):
        """Should export a transcript as TXT."""
        mock_segments = [
            MagicMock(text="Hello world", start_time=0.0, end_time=2.0, speaker="Speaker 1"),
            MagicMock(text="How are you", start_time=2.0, end_time=4.0, speaker="Speaker 2"),
        ]
        mock_transcript = MagicMock(id="t-1")
        mock_transcript.recording = MagicMock(title="Test Recording")

        mock_db = AsyncMock()
        # First execute: get transcript
        # Second execute: get segments
        transcript_result = MagicMock()
        transcript_result.scalar_one_or_none.return_value = mock_transcript

        segment_result = MagicMock()
        segment_result.scalars.return_value.all.return_value = mock_segments

        mock_db.execute = AsyncMock(side_effect=[transcript_result, segment_result])

        ctx = make_ctx(db=mock_db)

        with patch("services.tools.document_tools._GENERATED_DIR", str(tmp_path)):
            result = await handle_export_transcript({
                "transcript_id": "t-1",
                "format": "txt",
            }, ctx)

        assert len(result.artifacts) == 1
        assert result.artifacts[0].data["filename"].endswith(".txt")

    @pytest.mark.asyncio
    async def test_transcript_not_found(self):
        """Should return error if transcript doesn't exist."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_export_transcript({
            "transcript_id": "nonexistent",
            "format": "txt",
        }, ctx)

        assert "not found" in result.content.lower()
        assert len(result.artifacts) == 0
