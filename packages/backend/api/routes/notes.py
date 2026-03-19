"""Notes API endpoints for document and recording annotations."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.database import get_db
from persistence.models import Note, Document, Recording

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteCreate(BaseModel):
    """Request model for creating a note."""
    content: str
    recording_id: str | None = None
    document_id: str | None = None
    anchor_type: str  # 'timestamp', 'page', 'paragraph', 'selection'
    anchor_data: dict


class NoteUpdate(BaseModel):
    """Request model for updating a note."""
    content: str | None = None
    anchor_type: str | None = None
    anchor_data: dict | None = None


class NoteResponse(BaseModel):
    """Response model for a note."""
    id: str
    content: str
    recording_id: str | None
    document_id: str | None
    anchor_type: str
    anchor_data: dict
    created_at: str
    updated_at: str | None = None

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    """Response model for listing notes."""
    items: list[NoteResponse]
    total: int


def note_to_response(note: Note) -> NoteResponse:
    """Convert a Note model to a response."""
    return NoteResponse(
        id=note.id,
        content=note.content,
        recording_id=note.recording_id,
        document_id=note.document_id,
        anchor_type=note.anchor_type,
        anchor_data=note.anchor_data,
        created_at=note.created_at.isoformat(),
        updated_at=note.created_at.isoformat(),  # Note model doesn't have updated_at
    )


@router.get("", response_model=NoteListResponse)
async def list_notes(
    db: Annotated[AsyncSession, Depends(get_db)],
    recording_id: Annotated[str | None, Query()] = None,
    document_id: Annotated[str | None, Query()] = None,
) -> NoteListResponse:
    """List notes for a recording or document."""
    if not recording_id and not document_id:
        raise HTTPException(
            status_code=400,
            detail="Either recording_id or document_id must be provided"
        )

    query = select(Note).order_by(Note.created_at.desc())

    if recording_id:
        query = query.where(Note.recording_id == recording_id)
    if document_id:
        query = query.where(Note.document_id == document_id)

    result = await db.execute(query)
    notes = result.scalars().all()

    # Get total count
    count_query = select(func.count(Note.id))
    if recording_id:
        count_query = count_query.where(Note.recording_id == recording_id)
    if document_id:
        count_query = count_query.where(Note.document_id == document_id)

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    return NoteListResponse(
        items=[note_to_response(n) for n in notes],
        total=total,
    )


@router.post("", response_model=NoteResponse)
async def create_note(
    data: NoteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NoteResponse:
    """Create a new note."""
    if not data.recording_id and not data.document_id:
        raise HTTPException(
            status_code=400,
            detail="Either recording_id or document_id must be provided"
        )

    if data.recording_id and data.document_id:
        raise HTTPException(
            status_code=400,
            detail="Only one of recording_id or document_id can be provided"
        )

    # Validate parent exists
    if data.recording_id:
        recording = await db.get(Recording, data.recording_id)
        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

    if data.document_id:
        document = await db.get(Document, data.document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

    # Validate anchor_type
    valid_anchor_types = {'timestamp', 'page', 'paragraph', 'selection'}
    if data.anchor_type not in valid_anchor_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid anchor_type. Must be one of: {', '.join(valid_anchor_types)}"
        )

    note = Note(
        content=data.content,
        recording_id=data.recording_id,
        document_id=data.document_id,
        anchor_type=data.anchor_type,
        anchor_data=data.anchor_data,
    )

    db.add(note)
    await db.flush()
    await db.refresh(note)

    return note_to_response(note)


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(
    note_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NoteResponse:
    """Get a specific note."""
    note = await db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    return note_to_response(note)


@router.patch("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    data: NoteUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NoteResponse:
    """Update a note."""
    note = await db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if data.content is not None:
        note.content = data.content

    if data.anchor_type is not None:
        valid_anchor_types = {'timestamp', 'page', 'paragraph', 'selection'}
        if data.anchor_type not in valid_anchor_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid anchor_type. Must be one of: {', '.join(valid_anchor_types)}"
            )
        note.anchor_type = data.anchor_type

    if data.anchor_data is not None:
        note.anchor_data = data.anchor_data

    await db.flush()
    await db.refresh(note)

    return note_to_response(note)


@router.delete("/{note_id}")
async def delete_note(
    note_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete a note."""
    note = await db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    await db.delete(note)
    await db.flush()

    return {"status": "deleted", "id": note_id}
