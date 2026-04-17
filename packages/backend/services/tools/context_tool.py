"""Context retrieval tool — pull content from the active project.

Lets Max proactively find relevant transcripts and documents
without requiring the user to manually attach them.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def handle_get_context(args: dict, ctx: ToolContext) -> ToolResult:
    """Pull relevant content from the active project."""
    query = args.get("query", "")
    recording_title = args.get("recording_title", "")
    content_types = args.get("content_types", ["transcript", "document"])

    from persistence.models import Segment, Transcript, Recording, Document

    parts = []

    if "transcript" in content_types:
        seg_q = (
            select(Segment, Recording.title)
            .join(Transcript, Segment.transcript_id == Transcript.id)
            .join(Recording, Transcript.recording_id == Recording.id)
            .where(Recording.is_archived == False)
        )

        if recording_title:
            seg_q = seg_q.where(Recording.title.ilike(f"%{recording_title}%"))
        if query:
            seg_q = seg_q.where(Segment.text.ilike(f"%{query}%"))

        seg_q = seg_q.order_by(Segment.start_time).limit(20)

        if ctx.project_id:
            seg_q = seg_q.where(Recording.project_id == ctx.project_id)

        rows = (await ctx.db.execute(seg_q)).all()
        for seg, rec_title in rows:
            speaker = f"[{seg.speaker}] " if seg.speaker else ""
            time = _format_time(seg.start_time) if seg.start_time else ""
            parts.append(
                f"[{rec_title} @ {time}] {speaker}{seg.text}"
            )

    if "document" in content_types:
        doc_q = select(Document).where(Document.is_archived == False)
        if query:
            doc_q = doc_q.where(
                or_(
                    Document.extracted_text.ilike(f"%{query}%"),
                    Document.title.ilike(f"%{query}%"),
                )
            )
        doc_q = doc_q.limit(5)
        if ctx.project_id:
            doc_q = doc_q.where(Document.project_id == ctx.project_id)
        docs = (await ctx.db.execute(doc_q)).scalars().all()
        for d in docs:
            text_preview = (d.extracted_text or d.extracted_markdown or "")[:1000]
            parts.append(f"[Document: {d.title}]\n{text_preview}")

    if not parts:
        return ToolResult(
            content="No relevant content found in the current project for that query. "
            "Try different search terms or check if the project has transcripts."
        )

    return ToolResult(
        content=f"Found {len(parts)} result(s):\n\n" + "\n\n---\n\n".join(parts)
    )


context_tool = ToolDef(
    name="get_context",
    description=(
        "Retrieve specific content from the active project's transcripts and documents. "
        "Use this when you need the actual text from a recording or document — "
        "the project index only shows summaries. You can search by keyword, "
        "or filter by recording title to get segments from a specific transcript."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to find in transcript segments or documents",
            },
            "recording_title": {
                "type": "string",
                "description": "Optional: filter to segments from a specific recording by title (partial match)",
            },
            "content_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types to search: 'transcript', 'document'. Defaults to both.",
            },
        },
        "required": ["query"],
    },
    handler=handle_get_context,
    project_scoped=True,
)
