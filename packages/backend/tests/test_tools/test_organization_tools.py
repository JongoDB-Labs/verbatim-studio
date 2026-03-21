"""Tests for organization tools — create_project, tag_recordings, get_recording_info, system_status."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.organization_tools import (
    create_project_tool,
    tag_recordings_tool,
    get_recording_info_tool,
    system_status_tool,
    handle_create_project,
    handle_tag_recordings,
    handle_get_recording_info,
    handle_system_status,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id="proj-1", conversation_id=None,
        recording_ids=["rec-1"], document_ids=[],
        db=AsyncMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


# ── create_project ────────────────────────────────────────────────────


class TestCreateProjectTool:
    def test_tool_def(self):
        assert create_project_tool.name == "create_project"
        assert create_project_tool.project_scoped is False

    @pytest.mark.asyncio
    async def test_create_project(self):
        """Should create a project and return its name and id."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        ctx = make_ctx(db=mock_db)
        result = await handle_create_project(
            {"name": "My New Project", "description": "A test project"}, ctx
        )

        assert "My New Project" in result.content
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_project_missing_name(self):
        """Should reject when name is missing."""
        ctx = make_ctx()
        result = await handle_create_project({}, ctx)
        assert "name" in result.content.lower() or "required" in result.content.lower()


# ── tag_recordings ────────────────────────────────────────────────────


class TestTagRecordingsTool:
    def test_tool_def(self):
        assert tag_recordings_tool.name == "tag_recordings"
        assert tag_recordings_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_tag_recordings(self):
        """Should create tags if needed and assign to recordings."""
        mock_db = AsyncMock()

        # First execute: look up tag "important" -> not found
        mock_tag_result_empty = MagicMock()
        mock_tag_result_empty.scalar_one_or_none.return_value = None

        # Second execute: look up tag "urgent" -> not found
        mock_tag_result_empty2 = MagicMock()
        mock_tag_result_empty2.scalar_one_or_none.return_value = None

        # Third execute: look up recording "rec-1" -> found
        mock_recording = MagicMock()
        mock_recording.id = "rec-1"
        mock_recording.tags = []
        mock_rec_result = MagicMock()
        mock_rec_result.scalar_one_or_none.return_value = mock_recording

        # Fourth execute: look up recording "rec-2" -> found
        mock_recording2 = MagicMock()
        mock_recording2.id = "rec-2"
        mock_recording2.tags = []
        mock_rec_result2 = MagicMock()
        mock_rec_result2.scalar_one_or_none.return_value = mock_recording2

        mock_db.execute = AsyncMock(
            side_effect=[
                mock_tag_result_empty,
                mock_tag_result_empty2,
                mock_rec_result,
                mock_rec_result2,
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        ctx = make_ctx(db=mock_db)
        result = await handle_tag_recordings(
            {"recording_ids": ["rec-1", "rec-2"], "tag_names": ["important", "urgent"]},
            ctx,
        )

        assert "tagged" in result.content.lower() or "tag" in result.content.lower()

    @pytest.mark.asyncio
    async def test_tag_recordings_empty_ids(self):
        """Should reject empty recording_ids."""
        ctx = make_ctx()
        result = await handle_tag_recordings(
            {"recording_ids": [], "tag_names": ["important"]}, ctx
        )
        assert "recording" in result.content.lower()

    @pytest.mark.asyncio
    async def test_tag_recordings_empty_tags(self):
        """Should reject empty tag_names."""
        ctx = make_ctx()
        result = await handle_tag_recordings(
            {"recording_ids": ["rec-1"], "tag_names": []}, ctx
        )
        assert "tag" in result.content.lower()


# ── get_recording_info ────────────────────────────────────────────────


class TestGetRecordingInfoTool:
    def test_tool_def(self):
        assert get_recording_info_tool.name == "get_recording_info"
        assert get_recording_info_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_get_specific_recording(self):
        """Should return detailed info for a specific recording."""
        mock_recording = MagicMock()
        mock_recording.id = "rec-1"
        mock_recording.title = "Team Meeting"
        mock_recording.duration_seconds = 3600.0
        mock_recording.status = "completed"
        mock_recording.file_size = 52428800
        mock_recording.created_at = MagicMock()
        mock_recording.created_at.isoformat.return_value = "2026-01-15T10:00:00"
        mock_recording.tags = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_recording

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_get_recording_info({"recording_id": "rec-1"}, ctx)

        assert "Team Meeting" in result.content
        assert "rec-1" in result.content

    @pytest.mark.asyncio
    async def test_list_recent_recordings(self):
        """Should list recent recordings when no recording_id given."""
        mock_rec1 = MagicMock()
        mock_rec1.id = "rec-1"
        mock_rec1.title = "Meeting A"
        mock_rec1.status = "completed"

        mock_rec2 = MagicMock()
        mock_rec2.id = "rec-2"
        mock_rec2.title = "Meeting B"
        mock_rec2.status = "pending"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_rec1, mock_rec2]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_get_recording_info({}, ctx)

        assert "Meeting A" in result.content
        assert "Meeting B" in result.content

    @pytest.mark.asyncio
    async def test_recording_not_found(self):
        """Should handle recording not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        ctx = make_ctx(db=mock_db)
        result = await handle_get_recording_info({"recording_id": "nonexistent"}, ctx)

        assert "not found" in result.content.lower()


# ── system_status ─────────────────────────────────────────────────────


class TestSystemStatusTool:
    def test_tool_def(self):
        assert system_status_tool.name == "system_status"
        assert system_status_tool.project_scoped is False

    @pytest.mark.asyncio
    async def test_system_status(self):
        """Should return counts for recordings, transcripts, and projects."""
        mock_count_rec = MagicMock()
        mock_count_rec.scalar.return_value = 15

        mock_count_trans = MagicMock()
        mock_count_trans.scalar.return_value = 12

        mock_count_proj = MagicMock()
        mock_count_proj.scalar.return_value = 3

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            side_effect=[mock_count_rec, mock_count_trans, mock_count_proj]
        )

        ctx = make_ctx(db=mock_db)
        result = await handle_system_status({}, ctx)

        assert "15" in result.content
        assert "12" in result.content
        assert "3" in result.content
