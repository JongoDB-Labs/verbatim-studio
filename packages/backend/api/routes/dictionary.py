"""Custom dictionary CRUD endpoints.

Allows users to manage domain-specific terms that are passed to
Whisper's initial_prompt for improved transcription accuracy.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dictionary", tags=["dictionary"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class DictionaryEntryCreate(BaseModel):
    """Request body for creating a dictionary entry."""

    term: str
    category: str = "general"
    project_id: str | None = None


class DictionaryEntryResponse(BaseModel):
    """Response model for a single dictionary entry."""

    id: str
    term: str
    category: str
    project_id: str | None
    created_at: str | None


class DictionaryListResponse(BaseModel):
    """Response model for listing dictionary entries."""

    entries: list[DictionaryEntryResponse]


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=DictionaryListResponse)
async def list_entries(
    project_id: str | None = Query(default=None, description="Filter by project_id"),
    db: AsyncSession = Depends(get_db),
) -> DictionaryListResponse:
    """List all custom dictionary entries, optionally filtered by project."""
    try:
        if project_id:
            result = await db.execute(
                text(
                    "SELECT id, term, category, project_id, created_at "
                    "FROM custom_dictionary "
                    "WHERE project_id IS NULL OR project_id = :pid "
                    "ORDER BY created_at"
                ),
                {"pid": project_id},
            )
        else:
            result = await db.execute(
                text(
                    "SELECT id, term, category, project_id, created_at "
                    "FROM custom_dictionary "
                    "ORDER BY created_at"
                )
            )

        rows = result.fetchall()
        return DictionaryListResponse(
            entries=[
                DictionaryEntryResponse(
                    id=row[0],
                    term=row[1],
                    category=row[2],
                    project_id=row[3],
                    created_at=row[4],
                )
                for row in rows
            ]
        )
    except Exception as e:
        logger.warning("Could not query custom_dictionary table: %s", e)
        return DictionaryListResponse(entries=[])


@router.post("", response_model=DictionaryEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_entry(
    body: DictionaryEntryCreate,
    db: AsyncSession = Depends(get_db),
) -> DictionaryEntryResponse:
    """Add a new term to the custom dictionary."""
    entry_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    await db.execute(
        text(
            "INSERT INTO custom_dictionary (id, term, category, project_id, created_at) "
            "VALUES (:id, :term, :category, :project_id, :created_at)"
        ),
        {
            "id": entry_id,
            "term": body.term.strip(),
            "category": body.category,
            "project_id": body.project_id,
            "created_at": now,
        },
    )

    logger.info("Added dictionary entry: %s (category=%s, project=%s)", body.term, body.category, body.project_id)

    return DictionaryEntryResponse(
        id=entry_id,
        term=body.term.strip(),
        category=body.category,
        project_id=body.project_id,
        created_at=now,
    )


@router.delete("/{entry_id}", response_model=MessageResponse)
async def delete_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Remove a single dictionary entry by ID."""
    result = await db.execute(
        text("SELECT id FROM custom_dictionary WHERE id = :id"),
        {"id": entry_id},
    )
    if result.fetchone() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dictionary entry not found: {entry_id}",
        )

    await db.execute(
        text("DELETE FROM custom_dictionary WHERE id = :id"),
        {"id": entry_id},
    )
    logger.info("Deleted dictionary entry: %s", entry_id)

    return MessageResponse(message="Dictionary entry deleted")


@router.delete("", response_model=MessageResponse)
async def clear_entries(
    project_id: str | None = Query(default=None, description="Clear only entries for this project"),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Clear dictionary entries.

    If project_id is provided, only project-specific entries for that
    project are removed. Otherwise ALL entries are removed.
    """
    if project_id:
        await db.execute(
            text("DELETE FROM custom_dictionary WHERE project_id = :pid"),
            {"pid": project_id},
        )
        logger.info("Cleared dictionary entries for project %s", project_id)
        return MessageResponse(message=f"Cleared dictionary entries for project {project_id}")
    else:
        await db.execute(text("DELETE FROM custom_dictionary"))
        logger.info("Cleared all dictionary entries")
        return MessageResponse(message="All dictionary entries cleared")
