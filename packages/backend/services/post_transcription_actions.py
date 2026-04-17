"""Post-transcription automation framework.

Runs configurable actions after a recording is transcribed — e.g. auto-summarize,
auto-export.  Actions are isolated: a failure in one never blocks the others.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select

from persistence.database import get_session_factory
from persistence.models import Setting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class PostTranscriptionAction(ABC):
    """Base class for all post-transcription actions."""

    name: str = "base"

    @abstractmethod
    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        """Return True if this action should execute."""
        ...

    @abstractmethod
    async def execute(self, recording_id: str, transcript_id: str, **kwargs) -> Any:
        """Execute the action.  May return an arbitrary result dict."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_post_transcription_settings() -> dict:
    """Load the ``post_transcription`` setting from the DB (returns {} on miss)."""
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting.value).where(Setting.key == "post_transcription")
        )
        row = result.scalar_one_or_none()
        return row if isinstance(row, dict) else {}


# ---------------------------------------------------------------------------
# Concrete actions
# ---------------------------------------------------------------------------


class AutoSummarizeAction(PostTranscriptionAction):
    """Enqueue a summarization job when the user has opted-in."""

    name = "auto_summarize"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        settings = await _get_post_transcription_settings()
        return bool(settings.get("auto_summarize", False))

    async def execute(self, recording_id: str, transcript_id: str, **kwargs) -> Any:
        from services.jobs import job_queue

        job_id = await job_queue.enqueue("summarize", {"transcript_id": transcript_id})
        logger.info(
            "AutoSummarizeAction: queued summarize job %s for transcript %s",
            job_id,
            transcript_id,
        )
        return {"job_id": job_id}


class AutoExportAction(PostTranscriptionAction):
    """Log (and eventually trigger) an automatic export after transcription."""

    name = "auto_export"

    async def should_run(self, recording_id: str, transcript_id: str, **kwargs) -> bool:
        settings = await _get_post_transcription_settings()
        export_cfg = settings.get("auto_export", {})
        return bool(export_cfg.get("enabled", False))

    async def execute(self, recording_id: str, transcript_id: str, **kwargs) -> Any:
        settings = await _get_post_transcription_settings()
        export_cfg = settings.get("auto_export", {})
        fmt = export_cfg.get("format", "txt")
        destination = export_cfg.get("destination", "")
        logger.info(
            "AutoExportAction: would export transcript %s as %s to %s",
            transcript_id,
            fmt,
            destination,
        )
        return {"format": fmt, "destination": destination}


# ---------------------------------------------------------------------------
# Default action list
# ---------------------------------------------------------------------------

DEFAULT_ACTIONS: list[PostTranscriptionAction] = [
    AutoSummarizeAction(),
    AutoExportAction(),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_post_transcription_actions(
    recording_id: str,
    transcript_id: str,
    *,
    actions: list[PostTranscriptionAction] | None = None,
) -> list[dict]:
    """Run all registered post-transcription actions.

    Parameters
    ----------
    recording_id:
        The recording that was just transcribed.
    transcript_id:
        The newly-created transcript.
    actions:
        Override the default action list (mainly useful for tests).

    Returns
    -------
    list[dict]
        One entry per action: ``{"action": name, "status": ..., ...}``.
    """
    if actions is None:
        actions = DEFAULT_ACTIONS

    results: list[dict] = []

    for action in actions:
        entry: dict[str, Any] = {"action": action.name}
        try:
            should = await action.should_run(
                recording_id=recording_id,
                transcript_id=transcript_id,
            )
            if not should:
                entry["status"] = "skipped"
                results.append(entry)
                continue

            result = await action.execute(
                recording_id=recording_id,
                transcript_id=transcript_id,
            )
            entry["status"] = "completed"
            entry["result"] = result
        except Exception as exc:
            logger.warning(
                "Post-transcription action '%s' failed: %s",
                action.name,
                exc,
                exc_info=True,
            )
            entry["status"] = "failed"
            entry["error"] = str(exc)

        results.append(entry)

    return results
