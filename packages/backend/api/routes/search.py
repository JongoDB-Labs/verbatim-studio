"""Search API endpoints."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_active_project_id
from api.routes.sync import broadcast
from persistence import get_db
from persistence.models import (
    Conversation,
    ConversationMessage,
    Document,
    DocumentEmbedding,
    Note,
    Recording,
    SearchHistory,
    Segment,
    SegmentEmbedding,
    Transcript,
)
from services.embedding import bytes_to_embedding, embedding_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


class SearchResultSegment(BaseModel):
    """A segment matching the search query."""

    id: str
    segment_index: int
    speaker: str | None
    start_time: float
    end_time: float
    text: str
    confidence: float | None
    # Parent info
    transcript_id: str
    recording_id: str
    recording_title: str

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    """Search results response."""

    query: str
    results: list[SearchResultSegment]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/segments", response_model=SearchResponse)
async def search_segments(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Search query")],
    transcript_id: Annotated[str | None, Query(description="Limit to specific transcript")] = None,
    recording_id: Annotated[str | None, Query(description="Limit to specific recording")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 20,
) -> SearchResponse:
    """Search across all transcript segments.

    Full-text search across segment text content. Returns matching segments
    with their parent transcript and recording information.

    Args:
        db: Database session.
        q: Search query string.
        transcript_id: Optional filter to specific transcript.
        recording_id: Optional filter to specific recording.
        page: Page number (1-indexed).
        page_size: Number of results per page.

    Returns:
        Search results with pagination info.
    """
    # Build base query joining segments with transcripts and recordings
    base_query = (
        select(
            Segment,
            Transcript.recording_id,
            Recording.title.label("recording_title"),
        )
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
    )

    # Add search filter (case-insensitive)
    search_filter = Segment.text.ilike(f"%{q}%")
    base_query = base_query.where(search_filter)

    # Add optional filters
    if transcript_id:
        base_query = base_query.where(Segment.transcript_id == transcript_id)
    if recording_id:
        base_query = base_query.where(Transcript.recording_id == recording_id)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination and ordering
    query = (
        base_query
        .order_by(Recording.created_at.desc(), Segment.start_time)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    rows = result.all()

    # Build response
    results = []
    for row in rows:
        segment = row[0]
        rec_id = row[1]
        rec_title = row[2]

        results.append(
            SearchResultSegment(
                id=segment.id,
                segment_index=segment.segment_index,
                speaker=segment.speaker,
                start_time=segment.start_time,
                end_time=segment.end_time,
                text=segment.text,
                confidence=segment.confidence,
                transcript_id=segment.transcript_id,
                recording_id=rec_id,
                recording_title=rec_title,
            )
        )

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

    return SearchResponse(
        query=q,
        results=results,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


class SearchResultDocumentChunk(BaseModel):
    """A document chunk matching the search query."""

    id: str
    chunk_index: int
    chunk_text: str
    chunk_metadata: dict | None
    # Parent info
    document_id: str
    document_title: str
    document_filename: str
    document_mime_type: str

    class Config:
        from_attributes = True


class DocumentSearchResponse(BaseModel):
    """Document search results response."""

    query: str
    results: list[SearchResultDocumentChunk]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/documents", response_model=DocumentSearchResponse)
async def search_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Search query")],
    document_id: Annotated[str | None, Query(description="Limit to specific document")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 20,
) -> DocumentSearchResponse:
    """Search across all document content (including OCR results).

    Full-text search across document chunks. Returns matching chunks
    with their parent document information.

    Args:
        db: Database session.
        q: Search query string.
        document_id: Optional filter to specific document.
        page: Page number (1-indexed).
        page_size: Number of results per page.

    Returns:
        Search results with pagination info.
    """
    # Build base query joining document embeddings with documents
    base_query = (
        select(
            DocumentEmbedding,
            Document.title.label("document_title"),
            Document.filename.label("document_filename"),
            Document.mime_type.label("document_mime_type"),
        )
        .join(Document, DocumentEmbedding.document_id == Document.id)
        .where(Document.status == "completed")
    )

    # Add search filter (case-insensitive)
    search_filter = DocumentEmbedding.chunk_text.ilike(f"%{q}%")
    base_query = base_query.where(search_filter)

    # Add optional filters
    if document_id:
        base_query = base_query.where(DocumentEmbedding.document_id == document_id)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination and ordering
    query = (
        base_query
        .order_by(Document.created_at.desc(), DocumentEmbedding.chunk_index)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    rows = result.all()

    # Build response
    results = []
    for row in rows:
        chunk = row[0]
        doc_title = row[1]
        doc_filename = row[2]
        doc_mime_type = row[3]

        results.append(
            SearchResultDocumentChunk(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                chunk_metadata=chunk.chunk_metadata,
                document_id=chunk.document_id,
                document_title=doc_title,
                document_filename=doc_filename,
                document_mime_type=doc_mime_type,
            )
        )

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

    return DocumentSearchResponse(
        query=q,
        results=results,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def _semantic_search(
    db: AsyncSession,
    query_embedding: list[float],
    limit: int,
    exclude_ids: set[str],
    project_id: str | None = None,
) -> list["GlobalSearchResult"]:
    """Perform semantic search using embeddings.

    Args:
        db: Database session.
        query_embedding: The embedded query vector.
        limit: Maximum results.
        exclude_ids: Segment IDs to exclude (already found by keyword).
        project_id: Optional project ID to scope results to.

    Returns:
        List of semantic search results.
    """
    # Get all embeddings (for small datasets, in-memory similarity is fast enough)
    # For larger datasets, use sqlite-vec virtual table
    query = (
        select(
            SegmentEmbedding,
            Segment,
            Transcript.recording_id,
            Recording.title.label("recording_title"),
            Recording.created_at.label("recording_created_at"),
        )
        .join(Segment, SegmentEmbedding.segment_id == Segment.id)
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
    )
    if project_id:
        query = query.where(Recording.project_id == project_id)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return []

    # Calculate cosine similarity for each
    import math

    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # Score all embeddings
    scored = []
    for row in rows:
        seg_emb = row[0]
        segment = row[1]
        rec_id = row[2]
        rec_title = row[3]
        rec_created = row[4]

        if segment.id in exclude_ids:
            continue

        emb = bytes_to_embedding(seg_emb.embedding)
        score = cosine_similarity(query_embedding, emb)
        scored.append((score, segment, rec_id, rec_title, rec_created))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top_results = scored[:limit]

    # Convert to GlobalSearchResult
    results = []
    for score, segment, rec_id, rec_title, rec_created in top_results:
        # Only include if similarity is above threshold
        if score < 0.3:
            continue
        results.append(
            GlobalSearchResult(
                type="segment",
                id=segment.id,
                title=None,
                text=segment.text,
                recording_id=rec_id,
                recording_title=rec_title,
                start_time=segment.start_time,
                end_time=segment.end_time,
                created_at=rec_created,
                match_type="semantic",
            )
        )

    return results


async def _semantic_search_documents(
    db: AsyncSession,
    query_embedding: list[float],
    limit: int,
    exclude_ids: set[str],
    project_id: str | None = None,
) -> list["GlobalSearchResult"]:
    """Perform semantic search on document embeddings.

    Args:
        db: Database session.
        query_embedding: The embedded query vector.
        limit: Maximum results.
        exclude_ids: Document chunk IDs to exclude (already found by keyword).
        project_id: Optional project ID to scope results to.

    Returns:
        List of semantic search results for documents.
    """
    # Get all document embeddings
    query = (
        select(
            DocumentEmbedding,
            Document.title.label("document_title"),
            Document.filename.label("document_filename"),
            Document.created_at.label("document_created_at"),
        )
        .join(Document, DocumentEmbedding.document_id == Document.id)
        .where(Document.status == "completed")
    )
    if project_id:
        query = query.where(Document.project_id == project_id)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return []

    # Calculate cosine similarity for each
    import math

    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # Score all embeddings
    scored = []
    for row in rows:
        doc_emb = row[0]
        doc_title = row[1]
        doc_filename = row[2]
        doc_created = row[3]

        if doc_emb.id in exclude_ids:
            continue

        emb = bytes_to_embedding(doc_emb.embedding)
        score = cosine_similarity(query_embedding, emb)
        scored.append((score, doc_emb, doc_title, doc_filename, doc_created))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top_results = scored[:limit]

    # Convert to GlobalSearchResult
    results = []
    for score, doc_emb, doc_title, doc_filename, doc_created in top_results:
        # Only include if similarity is above threshold
        if score < 0.3:
            continue
        results.append(
            GlobalSearchResult(
                type="document",
                id=doc_emb.id,
                title=doc_title,
                text=doc_emb.chunk_text,
                document_id=doc_emb.document_id,
                document_title=doc_title,
                chunk_index=doc_emb.chunk_index,
                chunk_metadata=doc_emb.chunk_metadata,
                created_at=doc_created,
                match_type="semantic",
            )
        )

    return results


class GlobalSearchResult(BaseModel):
    """A result from global search."""

    type: str  # "recording", "segment", "document", "note", or "conversation"
    id: str
    title: str | None
    text: str | None
    # Recording fields (for recording/segment results)
    recording_id: str | None = None
    recording_title: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    # Document fields (for document results)
    document_id: str | None = None
    document_title: str | None = None
    chunk_index: int | None = None
    chunk_metadata: dict | None = None
    # Note fields (for note results)
    note_id: str | None = None
    anchor_type: str | None = None
    anchor_data: dict | None = None
    # Conversation fields (for chat results)
    conversation_id: str | None = None
    conversation_title: str | None = None
    message_role: str | None = None  # "user" or "assistant"
    # Common fields
    created_at: datetime
    match_type: str | None = None  # "keyword" or "semantic"


class GlobalSearchResponse(BaseModel):
    """Global search results."""

    query: str
    results: list[GlobalSearchResult]
    total: int


@router.get("/global", response_model=GlobalSearchResponse)
async def global_search(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum results")] = 20,
    semantic: Annotated[bool, Query(description="Include semantic search results")] = True,
    save_history: Annotated[bool, Query(description="Save to search history")] = True,
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
    all_projects: Annotated[bool, Query(alias="all", description="Return all projects (ignore active project)")] = False,
) -> GlobalSearchResponse:
    """Search across recordings, segments, documents, notes, and conversations.

    Returns a combined list of matching recordings (by title),
    segments (by text content), documents (by OCR/extracted text),
    notes (by content), and conversations (by title and message content).

    Args:
        db: Database session.
        q: Search query string.
        limit: Maximum number of results.
        semantic: Whether to include semantic search results.
        save_history: Whether to save this search to history.

    Returns:
        Combined search results.
    """
    results: list[GlobalSearchResult] = []

    # Allocate limits: recordings, segments, documents, notes, conversations, semantic (1/6 each)
    keyword_limit = limit // 6 or 1

    # Search recordings by title
    recording_query = (
        select(Recording)
        .where(Recording.title.ilike(f"%{q}%"))
        .order_by(Recording.created_at.desc())
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        recording_query = recording_query.where(Recording.project_id == active_project_id)
    recording_result = await db.execute(recording_query)
    recordings = recording_result.scalars().all()

    for rec in recordings:
        results.append(
            GlobalSearchResult(
                type="recording",
                id=rec.id,
                title=rec.title,
                text=None,
                recording_id=rec.id,
                recording_title=rec.title,
                start_time=None,
                end_time=None,
                created_at=rec.created_at,
                match_type="keyword",
            )
        )

    # Search segments by text
    segment_query = (
        select(
            Segment,
            Transcript.recording_id,
            Recording.title.label("recording_title"),
            Recording.created_at.label("recording_created_at"),
        )
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
        .where(Segment.text.ilike(f"%{q}%"))
        .order_by(Recording.created_at.desc(), Segment.start_time)
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        segment_query = segment_query.where(Recording.project_id == active_project_id)
    segment_result = await db.execute(segment_query)
    segments = segment_result.all()

    for row in segments:
        segment = row[0]
        rec_id = row[1]
        rec_title = row[2]
        rec_created = row[3]

        results.append(
            GlobalSearchResult(
                type="segment",
                id=segment.id,
                title=None,
                text=segment.text,
                recording_id=rec_id,
                recording_title=rec_title,
                start_time=segment.start_time,
                end_time=segment.end_time,
                created_at=rec_created,
                match_type="keyword",
            )
        )

    # Search document chunks by text (OCR results) - if embeddings exist
    document_query = (
        select(
            DocumentEmbedding,
            Document.title.label("document_title"),
            Document.created_at.label("document_created_at"),
        )
        .join(Document, DocumentEmbedding.document_id == Document.id)
        .where(Document.status == "completed")
        .where(DocumentEmbedding.chunk_text.ilike(f"%{q}%"))
        .order_by(Document.created_at.desc(), DocumentEmbedding.chunk_index)
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        document_query = document_query.where(Document.project_id == active_project_id)
    document_result = await db.execute(document_query)
    document_chunks = document_result.all()

    seen_doc_ids = set()
    for row in document_chunks:
        doc_emb = row[0]
        doc_title = row[1]
        doc_created = row[2]
        seen_doc_ids.add(doc_emb.document_id)

        results.append(
            GlobalSearchResult(
                type="document",
                id=doc_emb.id,
                title=doc_title,
                text=doc_emb.chunk_text,
                document_id=doc_emb.document_id,
                document_title=doc_title,
                chunk_index=doc_emb.chunk_index,
                chunk_metadata=doc_emb.chunk_metadata,
                created_at=doc_created,
                match_type="keyword",
            )
        )

    # Also search document extracted_text directly (for documents without embeddings)
    direct_doc_query = (
        select(Document)
        .where(Document.status == "completed")
        .where(
            or_(
                Document.extracted_text.ilike(f"%{q}%"),
                Document.title.ilike(f"%{q}%"),
            )
        )
        .order_by(Document.created_at.desc())
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        direct_doc_query = direct_doc_query.where(Document.project_id == active_project_id)
    direct_doc_result = await db.execute(direct_doc_query)
    direct_docs = direct_doc_result.scalars().all()

    for doc in direct_docs:
        if doc.id in seen_doc_ids:
            continue  # Already found via chunk search
        seen_doc_ids.add(doc.id)

        # Extract a snippet around the match
        text_snippet = None
        if doc.extracted_text and q.lower() in doc.extracted_text.lower():
            idx = doc.extracted_text.lower().find(q.lower())
            start = max(0, idx - 50)
            end = min(len(doc.extracted_text), idx + len(q) + 100)
            text_snippet = ("..." if start > 0 else "") + doc.extracted_text[start:end] + ("..." if end < len(doc.extracted_text) else "")

        results.append(
            GlobalSearchResult(
                type="document",
                id=doc.id,
                title=doc.title,
                text=text_snippet or doc.title,
                document_id=doc.id,
                document_title=doc.title,
                created_at=doc.created_at,
                match_type="keyword",
            )
        )

    # Search notes by content
    note_query = (
        select(
            Note,
            Document.id.label("doc_id"),
            Document.title.label("doc_title"),
            Recording.id.label("rec_id"),
            Recording.title.label("rec_title"),
        )
        .outerjoin(Document, Note.document_id == Document.id)
        .outerjoin(Recording, Note.recording_id == Recording.id)
        .where(Note.content.ilike(f"%{q}%"))
        .order_by(Note.created_at.desc())
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        # Notes inherit project scope from their parent recording or document
        note_query = note_query.where(
            (Note.recording_id.in_(
                select(Recording.id).where(Recording.project_id == active_project_id)
            )) |
            (Note.document_id.in_(
                select(Document.id).where(Document.project_id == active_project_id)
            ))
        )
    note_result = await db.execute(note_query)
    notes = note_result.all()

    for row in notes:
        note = row[0]
        doc_id = row[1]
        doc_title = row[2]
        rec_id = row[3]
        rec_title = row[4]

        results.append(
            GlobalSearchResult(
                type="note",
                id=note.id,
                title=None,
                text=note.content,
                recording_id=rec_id,
                recording_title=rec_title,
                document_id=doc_id,
                document_title=doc_title,
                note_id=note.id,
                anchor_type=note.anchor_type,
                anchor_data=note.anchor_data,
                created_at=note.created_at,
                match_type="keyword",
            )
        )

    # Search conversation messages by content
    conversation_query = (
        select(
            ConversationMessage,
            Conversation.id.label("conv_id"),
            Conversation.title.label("conv_title"),
        )
        .join(Conversation, ConversationMessage.conversation_id == Conversation.id)
        .where(ConversationMessage.content.ilike(f"%{q}%"))
        .order_by(ConversationMessage.created_at.desc())
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        conversation_query = conversation_query.where(Conversation.project_id == active_project_id)
    conversation_result = await db.execute(conversation_query)
    conversations = conversation_result.all()

    seen_conv_ids = set()
    for row in conversations:
        message = row[0]
        conv_id = row[1]
        conv_title = row[2]
        seen_conv_ids.add(conv_id)

        # Extract a snippet around the match
        text_snippet = message.content
        if len(text_snippet) > 200:
            idx = text_snippet.lower().find(q.lower())
            if idx >= 0:
                start = max(0, idx - 50)
                end = min(len(text_snippet), idx + len(q) + 150)
                text_snippet = (
                    ("..." if start > 0 else "")
                    + text_snippet[start:end]
                    + ("..." if end < len(text_snippet) else "")
                )
            else:
                text_snippet = text_snippet[:200] + "..."

        results.append(
            GlobalSearchResult(
                type="conversation",
                id=message.id,
                title=conv_title,
                text=text_snippet,
                conversation_id=conv_id,
                conversation_title=conv_title,
                message_role=message.role,
                created_at=message.created_at,
                match_type="keyword",
            )
        )

    # Also search conversation titles directly
    conv_title_query = (
        select(Conversation)
        .where(Conversation.title.ilike(f"%{q}%"))
        .order_by(Conversation.updated_at.desc())
        .limit(keyword_limit)
    )
    if active_project_id and not all_projects:
        conv_title_query = conv_title_query.where(Conversation.project_id == active_project_id)
    conv_title_result = await db.execute(conv_title_query)
    conv_titles = conv_title_result.scalars().all()

    for conv in conv_titles:
        if conv.id in seen_conv_ids:
            continue  # Already found via message search
        seen_conv_ids.add(conv.id)

        results.append(
            GlobalSearchResult(
                type="conversation",
                id=conv.id,
                title=conv.title,
                text=conv.title,
                conversation_id=conv.id,
                conversation_title=conv.title,
                created_at=conv.created_at,
                match_type="keyword",
            )
        )

    # Semantic search (if enabled and available)
    if semantic and embedding_service.is_available():
        try:
            seen_ids = {r.id for r in results}
            query_embedding = await embedding_service.embed_query(q)

            # Semantic search on segments
            remaining_slots = limit - len(results)
            if remaining_slots > 0:
                segment_semantic_results = await _semantic_search(
                    db, query_embedding, remaining_slots // 2 or 1, seen_ids,
                    project_id=active_project_id if not all_projects else None,
                )
                results.extend(segment_semantic_results)
                seen_ids.update(r.id for r in segment_semantic_results)

            # Semantic search on documents
            remaining_slots = limit - len(results)
            if remaining_slots > 0:
                document_semantic_results = await _semantic_search_documents(
                    db, query_embedding, remaining_slots, seen_ids,
                    project_id=active_project_id if not all_projects else None,
                )
                results.extend(document_semantic_results)
        except Exception as e:
            logger.warning("Semantic search failed: %s", e)

    # Save to search history if enabled
    if save_history and q.strip():
        try:
            await _save_search_history(db, q.strip(), len(results))
            await broadcast("search_history", "created")
        except Exception as e:
            logger.warning("Failed to save search history: %s", e)

    return GlobalSearchResponse(
        query=q,
        results=results,
        total=len(results),
    )


class RebuildIndexResponse(BaseModel):
    """Response from rebuild index operation."""

    status: str
    transcripts_queued: int
    documents_queued: int
    message: str


@router.post("/rebuild-index", response_model=RebuildIndexResponse)
async def rebuild_search_index(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RebuildIndexResponse:
    """Rebuild the semantic search index by regenerating all embeddings.

    This queues embedding jobs for all transcripts and reprocess jobs for
    all completed documents. Use this after enabling semantic search or
    if embeddings are missing.

    Returns:
        Status with counts of queued jobs.
    """
    from services.embedding import embedding_service
    from services.jobs import job_queue

    if not embedding_service.is_available():
        return RebuildIndexResponse(
            status="error",
            transcripts_queued=0,
            documents_queued=0,
            message="Embedding service not available. Install sentence-transformers.",
        )

    # Get all transcripts
    transcript_query = select(Transcript.id)
    transcript_result = await db.execute(transcript_query)
    transcript_ids = [row[0] for row in transcript_result.all()]

    # Get all completed documents
    document_query = select(Document.id).where(Document.status == "completed")
    document_result = await db.execute(document_query)
    document_ids = [row[0] for row in document_result.all()]

    # Queue embedding jobs for transcripts
    transcripts_queued = 0
    for transcript_id in transcript_ids:
        try:
            await job_queue.enqueue("embed", {"transcript_id": transcript_id})
            transcripts_queued += 1
        except Exception as e:
            logger.warning("Failed to queue embed job for transcript %s: %s", transcript_id, e)

    # Queue reprocess jobs for documents (which regenerates embeddings)
    documents_queued = 0
    for document_id in document_ids:
        try:
            await job_queue.enqueue("process_document", {
                "document_id": document_id,
                "enable_ocr": False,  # Don't re-run OCR, just regenerate embeddings
            })
            documents_queued += 1
        except Exception as e:
            logger.warning("Failed to queue reprocess job for document %s: %s", document_id, e)

    return RebuildIndexResponse(
        status="queued",
        transcripts_queued=transcripts_queued,
        documents_queued=documents_queued,
        message=f"Queued {transcripts_queued} transcript and {documents_queued} document embedding jobs.",
    )


# Search history models and endpoints


class SearchHistoryEntry(BaseModel):
    """A search history entry."""

    id: str
    query: str
    result_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SearchHistoryListResponse(BaseModel):
    """List of search history entries."""

    items: list[SearchHistoryEntry]
    total: int


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
    id: str | None = None


@router.get("/history", response_model=SearchHistoryListResponse)
async def get_search_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50, description="Max entries")] = 20,
) -> SearchHistoryListResponse:
    """Get recent search history.

    Returns most recent searches, ordered by last searched time.

    Args:
        db: Database session.
        limit: Maximum number of entries to return.

    Returns:
        List of search history entries.
    """
    query = (
        select(SearchHistory)
        .order_by(SearchHistory.updated_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    count_query = select(func.count()).select_from(SearchHistory)
    total = await db.scalar(count_query) or 0

    return SearchHistoryListResponse(
        items=[SearchHistoryEntry.model_validate(item) for item in items],
        total=total,
    )


@router.delete("/history", response_model=MessageResponse)
async def clear_search_history(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Clear all search history.

    Args:
        db: Database session.

    Returns:
        Confirmation message.
    """
    await db.execute(delete(SearchHistory))
    await db.commit()
    await broadcast("search_history", "deleted")
    return MessageResponse(message="Search history cleared")


@router.delete("/history/{entry_id}", response_model=MessageResponse)
async def delete_search_history_entry(
    db: Annotated[AsyncSession, Depends(get_db)],
    entry_id: str,
) -> MessageResponse:
    """Delete a single search history entry.

    Args:
        db: Database session.
        entry_id: ID of the entry to delete.

    Returns:
        Confirmation message.
    """
    result = await db.execute(
        delete(SearchHistory).where(SearchHistory.id == entry_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    await db.commit()
    await broadcast("search_history", "deleted", entry_id)
    return MessageResponse(message="Entry deleted", id=entry_id)


async def _save_search_history(db: AsyncSession, query: str, result_count: int) -> None:
    """Save or update search history entry.

    Deduplicates by query (case-insensitive). If the query exists, updates
    the timestamp and result count. If new, creates entry and enforces
    50-entry limit by deleting oldest entries.

    Args:
        db: Database session.
        query: The search query.
        result_count: Number of results returned.
    """
    # Check for existing entry with same query (case-insensitive)
    existing_query = select(SearchHistory).where(
        func.lower(SearchHistory.query) == query.lower()
    )
    result = await db.execute(existing_query)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing entry (moves it to top via updated_at)
        existing.result_count = result_count
        # Force update of updated_at by setting it explicitly
        existing.updated_at = func.now()
    else:
        # Create new entry
        new_entry = SearchHistory(
            query=query,
            result_count=result_count,
        )
        db.add(new_entry)

        # Enforce limit of 50 entries (FIFO eviction)
        count_query = select(func.count()).select_from(SearchHistory)
        total = await db.scalar(count_query) or 0

        if total >= 50:
            # Delete oldest entries to make room
            oldest_query = (
                select(SearchHistory.id)
                .order_by(SearchHistory.updated_at.asc())
                .limit(total - 49)
            )
            oldest_result = await db.execute(oldest_query)
            oldest_ids = [row[0] for row in oldest_result.all()]
            if oldest_ids:
                await db.execute(
                    delete(SearchHistory).where(SearchHistory.id.in_(oldest_ids))
                )

    await db.commit()
