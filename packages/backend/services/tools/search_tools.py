"""Search tools — project-scoped and global search.

Wraps the existing search infrastructure to search across
transcripts, documents, OCR text, notes, and conversations.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def _run_search(
    query: str,
    db: AsyncSession,
    project_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Run search across recordings, segments, and documents.

    Returns a list of result dicts.
    """
    from persistence.models import Recording, Segment, Transcript, Document

    results = []
    per_type_limit = max(limit // 3, 3)

    # Search recordings by title
    rec_q = select(Recording).where(Recording.title.ilike(f"%{query}%")).limit(per_type_limit)
    if project_id:
        rec_q = rec_q.where(Recording.project_id == project_id)
    recs = (await db.execute(rec_q)).scalars().all()
    for r in recs:
        results.append({"type": "recording", "title": r.title, "id": str(r.id)})

    # Search segments by text
    seg_q = (
        select(Segment)
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
        .where(Segment.text.ilike(f"%{query}%"))
        .limit(per_type_limit)
    )
    if project_id:
        seg_q = seg_q.where(Recording.project_id == project_id)
    segs = (await db.execute(seg_q)).scalars().all()
    for s in segs:
        results.append({"type": "segment", "text": s.text[:200], "id": str(s.id), "start_time": s.start_time})

    # Search documents
    doc_q = select(Document).where(
        Document.title.ilike(f"%{query}%") | Document.extracted_text.ilike(f"%{query}%")
    ).limit(per_type_limit)
    if project_id:
        doc_q = doc_q.where(Document.project_id == project_id)
    docs = (await db.execute(doc_q)).scalars().all()
    for d in docs:
        results.append({"type": "document", "title": d.title, "id": str(d.id)})

    return results


def _format_results(results: list[dict]) -> str:
    """Format search results into readable text for Max."""
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):\n"]
    for r in results:
        if r["type"] == "recording":
            lines.append(f"- Recording: \"{r['title']}\" (id: {r['id']})")
        elif r["type"] == "segment":
            lines.append(f"- Transcript segment: \"{r['text']}\" (at {r.get('start_time', '?')}s)")
        elif r["type"] == "document":
            lines.append(f"- Document: \"{r['title']}\" (id: {r['id']})")
        else:
            lines.append(f"- {r['type']}: {r.get('title', r.get('text', ''))}")
    return "\n".join(lines)


async def handle_project_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Search within the active project workspace."""
    query = args.get("query", "")
    results = await _run_search(query, ctx.db, project_id=ctx.project_id)
    return ToolResult(content=_format_results(results))


async def handle_global_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Search across all projects."""
    query = args.get("query", "")
    results = await _run_search(query, ctx.db, project_id=None)
    return ToolResult(content=_format_results(results))


project_search_tool = ToolDef(
    name="project_search",
    description="Search transcripts, documents, notes, and chats within the current project workspace.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    handler=handle_project_search,
    project_scoped=True,
)

global_search_tool = ToolDef(
    name="global_search",
    description="Search across ALL projects. Use when the user says 'across all projects' or names a specific different project.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "project_name": {"type": "string", "description": "Optional: search a specific project by name"},
        },
        "required": ["query"],
    },
    handler=handle_global_search,
    project_scoped=False,
)
