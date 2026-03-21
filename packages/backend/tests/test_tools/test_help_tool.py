"""Tests for app_help tool."""

import pytest
from services.tools.help_tool import help_tool, handle_app_help, HELP_SECTIONS
from services.tool_registry import ToolContext
from unittest.mock import MagicMock


def make_ctx(**kwargs):
    defaults = dict(
        project_id=None, conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestAppHelpTool:
    def test_tool_def(self):
        assert help_tool.name == "app_help"
        assert help_tool.project_scoped is False

    def test_help_sections_exist(self):
        """Should have multiple distinct sections."""
        assert len(HELP_SECTIONS) >= 5
        assert "navigation" in HELP_SECTIONS
        assert "shortcuts" in HELP_SECTIONS

    @pytest.mark.asyncio
    async def test_specific_topic(self):
        """Requesting a specific topic returns only that section."""
        result = await handle_app_help({"topic": "shortcuts"}, make_ctx())
        assert "Space" in result.content or "Play/Pause" in result.content
        # Should NOT contain unrelated sections
        assert "Cloud (OAuth)" not in result.content

    @pytest.mark.asyncio
    async def test_no_topic_returns_overview(self):
        """No topic returns a list of available topics."""
        result = await handle_app_help({}, make_ctx())
        assert "navigation" in result.content.lower()
        assert "shortcuts" in result.content.lower()

    @pytest.mark.asyncio
    async def test_unknown_topic_returns_all(self):
        """Unknown topic returns best-effort match or full content."""
        result = await handle_app_help({"topic": "xyz_nonexistent"}, make_ctx())
        assert len(result.content) > 50  # Should return something useful

    @pytest.mark.asyncio
    async def test_partial_topic_match(self):
        """Should match partial topic names."""
        result = await handle_app_help({"topic": "export"}, make_ctx())
        assert "Export" in result.content or "TXT" in result.content
