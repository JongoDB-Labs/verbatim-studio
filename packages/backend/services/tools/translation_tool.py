"""Translation tool — translate transcript text to a target language using the local LLM."""

from __future__ import annotations

import logging

from sqlalchemy import select

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = [
    "Spanish", "French", "German", "Italian", "Portuguese", "Dutch",
    "Russian", "Chinese", "Japanese", "Korean", "Arabic", "Hindi",
    "Polish", "Turkish", "Vietnamese", "Thai", "Swedish", "Danish",
    "Finnish", "Norwegian", "Czech", "Romanian", "Hungarian", "Greek",
]


async def handle_translate_transcript(args: dict, ctx: ToolContext) -> ToolResult:
    """Translate a transcript to a target language via the local LLM."""
    transcript_id = args.get("transcript_id", "")
    target_language = args.get("target_language", "")

    if not target_language:
        lang_list = ", ".join(SUPPORTED_LANGUAGES)
        return ToolResult(
            content=f"Please specify a target language. Supported languages: {lang_list}"
        )

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
        return ToolResult(content="Transcript has no segments to translate.")

    # Build transcript text
    transcript_text = "\n".join(
        f"[{getattr(s, 'speaker', '') or 'Speaker'}] {s.text}" for s in segments
    )

    # Truncate if too long
    if len(transcript_text) > 6000:
        transcript_text = transcript_text[:6000] + "\n... (truncated)"

    if not ctx.ai_service:
        return ToolResult(content="AI service not available for translation.")

    translation_prompt = (
        f"Translate the following transcript to {target_language}.\n"
        "Preserve all speaker labels in square brackets exactly as they appear.\n\n"
        f"Transcript:\n{transcript_text}"
    )

    try:
        response = await ctx.ai_service.chat(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "You are a professional translator. Translate the text accurately, "
                        "preserving the original meaning, tone, and speaker labels. "
                        "Do not add commentary or notes — output only the translation."
                    ),
                ),
                ChatMessage(role="user", content=translation_prompt),
            ],
            ChatOptions(temperature=0.2, max_tokens=2048),
        )
        return ToolResult(
            content=f"**Translation to {target_language}:**\n\n{response.content}"
        )
    except Exception as e:
        logger.exception("Translation failed")
        return ToolResult(content=f"Translation failed: {e}")


translate_transcript_tool = ToolDef(
    name="translate_transcript",
    description="Translate a transcript to a different language.",
    parameters={
        "type": "object",
        "properties": {
            "transcript_id": {
                "type": "string",
                "description": "The transcript ID to translate",
            },
            "target_language": {
                "type": "string",
                "description": "Target language for translation (e.g. Spanish, French, German)",
            },
        },
        "required": ["transcript_id", "target_language"],
    },
    handler=handle_translate_transcript,
    project_scoped=True,
)
