"""Tests for post-transcription automation framework."""

import pytest

from services.post_transcription_actions import (
    AutoExportAction,
    AutoSummarizeAction,
    PostTranscriptionAction,
    run_post_transcription_actions,
)


# ---------------------------------------------------------------------------
# Helpers — mock actions for testing
# ---------------------------------------------------------------------------


class AlwaysRunAction(PostTranscriptionAction):
    """Action that always runs and succeeds."""

    name = "always_run"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        return True

    async def execute(self, recording_id: str, transcript_id: str, **kwargs):
        return {"done": True}


class NeverRunAction(PostTranscriptionAction):
    """Action whose should_run returns False."""

    name = "never_run"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        return False

    async def execute(self, recording_id: str, transcript_id: str, **kwargs):
        return {"done": True}


class FailAction(PostTranscriptionAction):
    """Action that raises during execute."""

    name = "fail_action"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        return True

    async def execute(self, recording_id: str, transcript_id: str, **kwargs):
        raise RuntimeError("intentional failure")


class ShouldRunFailAction(PostTranscriptionAction):
    """Action that raises during should_run."""

    name = "should_run_fail"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        raise RuntimeError("should_run exploded")

    async def execute(self, recording_id: str, transcript_id: str, **kwargs):
        return {"done": True}


# ---------------------------------------------------------------------------
# Tests — run_post_transcription_actions
# ---------------------------------------------------------------------------


class TestRunPostTranscriptionActions:
    """Tests for the action runner."""

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        results = await run_post_transcription_actions("r1", "t1", actions=[])
        assert results == []

    @pytest.mark.asyncio
    async def test_completed_action(self):
        results = await run_post_transcription_actions(
            "r1", "t1", actions=[AlwaysRunAction()]
        )
        assert len(results) == 1
        assert results[0]["action"] == "always_run"
        assert results[0]["status"] == "completed"
        assert results[0]["result"] == {"done": True}

    @pytest.mark.asyncio
    async def test_skipped_action(self):
        results = await run_post_transcription_actions(
            "r1", "t1", actions=[NeverRunAction()]
        )
        assert len(results) == 1
        assert results[0]["action"] == "never_run"
        assert results[0]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_failed_action(self):
        results = await run_post_transcription_actions(
            "r1", "t1", actions=[FailAction()]
        )
        assert len(results) == 1
        assert results[0]["action"] == "fail_action"
        assert results[0]["status"] == "failed"
        assert "intentional failure" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_failure_does_not_block_others(self):
        """A failing action must not prevent subsequent actions from running."""
        results = await run_post_transcription_actions(
            "r1",
            "t1",
            actions=[FailAction(), AlwaysRunAction()],
        )
        assert len(results) == 2
        assert results[0]["action"] == "fail_action"
        assert results[0]["status"] == "failed"
        assert results[1]["action"] == "always_run"
        assert results[1]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_should_run_exception_records_failed(self):
        """If should_run itself raises, action is marked failed, not skipped."""
        results = await run_post_transcription_actions(
            "r1", "t1", actions=[ShouldRunFailAction()]
        )
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "should_run exploded" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_mixed_actions(self):
        """Run a mix of skipped, completed, and failed actions."""
        results = await run_post_transcription_actions(
            "r1",
            "t1",
            actions=[NeverRunAction(), AlwaysRunAction(), FailAction()],
        )
        statuses = [r["status"] for r in results]
        assert statuses == ["skipped", "completed", "failed"]


# ---------------------------------------------------------------------------
# Tests — individual action classes (unit)
# ---------------------------------------------------------------------------


class TestAutoSummarizeAction:
    """Unit tests for AutoSummarizeAction.should_run logic."""

    def test_name(self):
        assert AutoSummarizeAction().name == "auto_summarize"


class TestAutoExportAction:
    """Unit tests for AutoExportAction.should_run logic."""

    def test_name(self):
        assert AutoExportAction().name == "auto_export"
