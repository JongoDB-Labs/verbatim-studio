"""Build a compact project index for AI context injection."""

import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Document, Recording, Transcript

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 200
MAX_KEY_POINTS = 3


async def build_project_index(
    db: AsyncSession,
    project_ids: list[str],
) -> str:
    """Build a compact index of all content in the given projects.

    Returns a structured string with title, date, duration, speaker count,
    and a short summary for each recording and document. Designed to give
    the LLM a "table of contents" without consuming the full context window.
    """
    parts: list[str] = []

    # --- Recordings ---
    rec_query = (
        select(Recording)
        .where(
            Recording.project_id.in_(project_ids),
            Recording.is_archived == False,
        )
        .order_by(Recording.created_at.desc())
    )
    result = await db.execute(rec_query)
    recordings = result.scalars().all()

    if recordings:
        parts.append("## Recordings\n")

    for rec in recordings:
        t_result = await db.execute(
            select(Transcript).where(Transcript.recording_id == rec.id)
        )
        transcript = t_result.scalar_one_or_none()

        line = f"- **{rec.title}**"
        meta = []
        if rec.duration_seconds:
            mins = int(rec.duration_seconds // 60)
            meta.append(f"{mins}m")
        if transcript and transcript.word_count:
            meta.append(f"{transcript.word_count:,} words")
        if rec.created_at:
            meta.append(rec.created_at.strftime("%b %d, %Y"))
        if meta:
            line += f" ({', '.join(meta)})"

        if transcript and transcript.ai_summary:
            summary = transcript.ai_summary
            if isinstance(summary, dict):
                text = summary.get("summary", "")
                if text:
                    truncated = text[:MAX_SUMMARY_CHARS]
                    if len(text) > MAX_SUMMARY_CHARS:
                        truncated = truncated.rsplit(" ", 1)[0] + "..."
                    line += f"\n  {truncated}"

                key_points = summary.get("key_points", [])[:MAX_KEY_POINTS]
                if key_points:
                    for kp in key_points:
                        line += f"\n  - {kp}"

        # Fallback: if no summary, show basic stats
        if not (transcript and transcript.ai_summary):
            if transcript:
                from persistence.models import Segment
                speaker_result = await db.execute(
                    select(func.count(func.distinct(Segment.speaker)))
                    .where(Segment.transcript_id == transcript.id)
                )
                speaker_count = speaker_result.scalar() or 0
                if speaker_count > 0:
                    line += f"\n  {speaker_count} speaker(s), no AI summary generated yet"
            else:
                line += "\n  Not yet transcribed"

        parts.append(line)

    # --- Documents ---
    doc_query = (
        select(Document)
        .where(
            Document.project_id.in_(project_ids),
            Document.is_archived == False,
        )
        .order_by(Document.created_at.desc())
    )
    doc_result = await db.execute(doc_query)
    documents = doc_result.scalars().all()

    if documents:
        parts.append("\n## Documents\n")

    for doc in documents:
        line = f"- **{doc.title}** ({doc.mime_type or 'unknown'}"
        if doc.page_count:
            line += f", {doc.page_count} pages"
        if doc.created_at:
            line += f", {doc.created_at.strftime('%b %d, %Y')}"
        line += ")"

        text = doc.extracted_text or doc.extracted_markdown or ""
        if text:
            preview = text[:MAX_SUMMARY_CHARS].replace("\n", " ").strip()
            if len(text) > MAX_SUMMARY_CHARS:
                preview = preview.rsplit(" ", 1)[0] + "..."
            line += f"\n  {preview}"

        parts.append(line)

    if not parts:
        return ""

    header = f"This project contains {len(recordings)} recording(s) and {len(documents)} document(s).\n\n"
    return header + "\n".join(parts)
