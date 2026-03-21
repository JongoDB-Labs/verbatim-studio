"""Context retrieval tool — pull content from the active project.

Lets Max proactively find relevant transcripts and documents
without requiring the user to manually attach them.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def handle_get_context(args: dict, ctx: ToolContext) -> ToolResult:
    """Pull relevant content from the active project."""
    query = args.get("query", "")
    content_types = args.get("content_types", ["transcript", "document"])

    from persistence.models import Segment, Transcript, Recording, Document

    parts = []

    # Search transcript segments
    if "transcript" in content_types:
        seg_q = (
            select(Segment)
            .join(Transcript, Segment.transcript_id == Transcript.id)
            .join(Recording, Transcript.recording_id == Recording.id)
            .where(Segment.text.ilike(f"%{query}%"))
            .limit(10)
        )
        if ctx.project_id:
            seg_q = seg_q.where(Recording.project_id == ctx.project_id)
        segs = (await ctx.db.execute(seg_q)).scalars().all()
        for s in segs:
            parts.append(f"[Transcript segment at {s.start_time:.1f}s]: {s.text}")

    # Search documents
    if "document" in content_types:
        doc_q = select(Document).where(
            Document.extracted_text.ilike(f"%{query}%") | Document.title.ilike(f"%{query}%")
        ).limit(5)
        if ctx.project_id:
            doc_q = doc_q.where(Document.project_id == ctx.project_id)
        docs = (await ctx.db.execute(doc_q)).scalars().all()
        for d in docs:
            text_preview = (d.extracted_text or "")[:500]
            parts.append(f"[Document: {d.title}]: {text_preview}")

    if not parts:
        return ToolResult(content="No relevant content found in the current project for that query.")

    return ToolResult(content=f"Found {len(parts)} relevant item(s):\n\n" + "\n\n".join(parts))


context_tool = ToolDef(
    name="get_context",
    description="Pull relevant content from the active project. Use when you need more context to answer a question about the user's transcripts or documents.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
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
