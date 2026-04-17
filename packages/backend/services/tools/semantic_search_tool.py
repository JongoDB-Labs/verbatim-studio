"""Semantic search tool — find content by meaning, not just keywords.

Uses nomic-embed-text-v1.5 embeddings and cosine similarity to find
transcript segments and document chunks that are semantically relevant
to the query, even without exact keyword matches.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def handle_semantic_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Find content by meaning using embedding similarity."""
    query = args.get("query", "")
    limit = min(args.get("limit", 15), 25)

    if not query:
        return ToolResult(content="Please provide a search query.")

    # Check if embedding service is available
    from services.embedding import EmbeddingService, bytes_to_embedding

    embedding_service = EmbeddingService()
    if not embedding_service.is_available():
        return ToolResult(
            content="Semantic search is not available — the embedding model is not installed. "
            "Use the get_context tool with keyword search instead."
        )

    # Embed the query
    try:
        query_embedding = await embedding_service.embed_query(query)
    except Exception as e:
        logger.warning("Failed to embed query: %s", e)
        return ToolResult(content=f"Failed to generate query embedding: {e}")

    from persistence.models import (
        Document,
        DocumentEmbedding,
        Recording,
        Segment,
        SegmentEmbedding,
        Transcript,
    )

    parts: list[str] = []

    # --- Semantic search over transcript segments ---
    seg_query = (
        select(
            SegmentEmbedding,
            Segment,
            Recording.title.label("recording_title"),
        )
        .join(Segment, SegmentEmbedding.segment_id == Segment.id)
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
        .where(Recording.is_archived == False)
    )
    if ctx.project_id:
        seg_query = seg_query.where(Recording.project_id == ctx.project_id)

    seg_result = await ctx.db.execute(seg_query)
    seg_rows = seg_result.all()

    scored_segments = []
    for seg_emb, segment, rec_title in seg_rows:
        emb = bytes_to_embedding(seg_emb.embedding)
        score = _cosine_similarity(query_embedding, emb)
        if score >= 0.3:
            scored_segments.append((score, segment, rec_title))

    scored_segments.sort(key=lambda x: x[0], reverse=True)

    for score, segment, rec_title in scored_segments[:limit]:
        speaker = f"[{segment.speaker}] " if segment.speaker else ""
        time = _format_time(segment.start_time) if segment.start_time else ""
        parts.append(
            f"[{rec_title} @ {time}] {speaker}{segment.text} (relevance: {score:.0%})"
        )

    # --- Semantic search over document chunks ---
    doc_query = (
        select(DocumentEmbedding, Document.title)
        .join(Document, DocumentEmbedding.document_id == Document.id)
        .where(Document.is_archived == False)
    )
    if ctx.project_id:
        doc_query = doc_query.where(Document.project_id == ctx.project_id)

    doc_result = await ctx.db.execute(doc_query)
    doc_rows = doc_result.all()

    scored_docs = []
    for doc_emb, doc_title in doc_rows:
        emb = bytes_to_embedding(doc_emb.embedding)
        score = _cosine_similarity(query_embedding, emb)
        if score >= 0.3:
            chunk_text = doc_emb.chunk_text or ""
            scored_docs.append((score, doc_title, chunk_text[:800]))

    scored_docs.sort(key=lambda x: x[0], reverse=True)

    for score, doc_title, chunk_text in scored_docs[:5]:
        parts.append(
            f"[Document: {doc_title}] (relevance: {score:.0%})\n{chunk_text}"
        )

    if not parts:
        return ToolResult(
            content="No semantically similar content found for that query. "
            "Try rephrasing or use get_context for exact keyword matching."
        )

    return ToolResult(
        content=f"Found {len(parts)} semantically relevant result(s):\n\n"
        + "\n\n---\n\n".join(parts)
    )


semantic_search_tool = ToolDef(
    name="semantic_search",
    description=(
        "Search the active project by meaning, not just keywords. "
        "Finds transcript segments and document passages that are semantically "
        "similar to your query — e.g., searching 'budget concerns' will find "
        "segments about 'cost overruns' or 'financial risk'. More powerful than "
        "get_context for finding relevant content when you don't know the exact words used."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what to find",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 15, max 25)",
            },
        },
        "required": ["query"],
    },
    handler=handle_semantic_search,
    project_scoped=True,
)
