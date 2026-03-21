"""Analysis tools — summarize transcripts and run quality reviews.

summarize_transcript: Generate an AI summary with key points and action items.
quality_review: Enqueue a two-pass quality review job (non-blocking).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def handle_summarize_transcript(args: dict, ctx: ToolContext) -> ToolResult:
    """Generate an AI summary of a transcript."""
    transcript_id = args.get("transcript_id", "")

    from persistence.models import Transcript, Segment
    from core.interfaces.ai import ChatMessage, ChatOptions

    # Verify transcript exists
    result = await ctx.db.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    )
    transcript = result.scalar_one_or_none()
    if not transcript:
        return ToolResult(content=f"Transcript '{transcript_id}' not found.")

    # Get segments
    seg_result = await ctx.db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.start_time)
    )
    segments = seg_result.scalars().all()

    if not segments:
        return ToolResult(content="Transcript has no segments to summarize.")

    # Build transcript text
    transcript_text = "\n".join(
        f"[{getattr(s, 'speaker', '') or 'Speaker'}] {s.text}" for s in segments
    )

    # Truncate if too long
    if len(transcript_text) > 8000:
        transcript_text = transcript_text[:8000] + "\n... (truncated)"

    # Call LLM for summary
    if not ctx.ai_service:
        return ToolResult(content="AI service not available for summarization.")

    summary_prompt = (
        "Summarize the following transcript. Include:\n"
        "1. A brief overall summary (2-3 sentences)\n"
        "2. Key points (bullet list)\n"
        "3. Action items (if any)\n"
        "4. Main topics discussed\n\n"
        f"Transcript:\n{transcript_text}"
    )

    try:
        response = await ctx.ai_service.chat(
            [
                ChatMessage(role="system", content="You are a transcript analysis assistant. Be concise and accurate."),
                ChatMessage(role="user", content=summary_prompt),
            ],
            ChatOptions(temperature=0.3, max_tokens=800),
        )
        return ToolResult(content=response.content)
    except Exception as e:
        logger.exception("Summarization failed")
        return ToolResult(content=f"Summarization failed: {e}")


async def handle_quality_review(args: dict, ctx: ToolContext) -> ToolResult:
    """Enqueue a quality review job for a transcript."""
    transcript_id = args.get("transcript_id", "")

    from persistence.models import Transcript
    from services.jobs import job_queue

    # Verify transcript exists
    result = await ctx.db.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    )
    transcript = result.scalar_one_or_none()
    if not transcript:
        return ToolResult(content=f"Transcript '{transcript_id}' not found.")

    # Enqueue the quality review job
    try:
        job_id = await job_queue.enqueue(
            "quality_review",
            {"transcript_id": transcript_id},
        )
        return ToolResult(
            content=f"Quality review has been queued for this transcript (job: {job_id}). "
            "The review will run in the background. You can check the Quality Review panel "
            "in the transcript view for results when it completes."
        )
    except Exception as e:
        logger.exception("Failed to queue quality review")
        return ToolResult(content=f"Failed to queue quality review: {e}")


summarize_transcript_tool = ToolDef(
    name="summarize_transcript",
    description="Generate an AI summary of a transcript with key points, action items, and topics.",
    parameters={
        "type": "object",
        "properties": {
            "transcript_id": {"type": "string", "description": "The transcript ID to summarize"},
        },
        "required": ["transcript_id"],
    },
    handler=handle_summarize_transcript,
    project_scoped=True,
)

quality_review_tool = ToolDef(
    name="quality_review",
    description="Run a quality review to detect transcription errors and propose corrections. Runs in the background.",
    parameters={
        "type": "object",
        "properties": {
            "transcript_id": {"type": "string", "description": "The transcript ID to review"},
        },
        "required": ["transcript_id"],
    },
    handler=handle_quality_review,
    project_scoped=True,
)
