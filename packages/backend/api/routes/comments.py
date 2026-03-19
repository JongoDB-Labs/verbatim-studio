"""Comment API endpoints for transcript segments."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db
from persistence.models import Segment, SegmentComment

logger = logging.getLogger(__name__)

# Router for segment-scoped endpoints: /segments/{id}/comments
segment_comments_router = APIRouter(prefix="/segments", tags=["comments"])

# Router for comment-scoped endpoints: /comments/{id}
comments_router = APIRouter(prefix="/comments", tags=["comments"])


# Pydantic models
class CommentResponse(BaseModel):
    """Response model for a comment."""

    id: str
    segment_id: str
    text: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CommentCreateRequest(BaseModel):
    """Request model for creating a comment."""

    text: str


class CommentUpdateRequest(BaseModel):
    """Request model for updating a comment."""

    text: str


class CommentListResponse(BaseModel):
    """Response model for a list of comments."""

    items: list[CommentResponse]


class MessageResponse(BaseModel):
    """Simple response for delete operations."""

    message: str


# --- Segment-scoped endpoints ---


@segment_comments_router.get("/{segment_id}/comments", response_model=CommentListResponse)
async def list_comments(
    segment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommentListResponse:
    """List all comments for a segment."""
    # Verify segment exists
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment not found: {segment_id}",
        )

    result = await db.execute(
        select(SegmentComment)
        .where(SegmentComment.segment_id == segment_id)
        .order_by(SegmentComment.created_at)
    )
    comments = result.scalars().all()

    return CommentListResponse(
        items=[
            CommentResponse(
                id=c.id,
                segment_id=c.segment_id,
                text=c.text,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in comments
        ]
    )


@segment_comments_router.post(
    "/{segment_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    segment_id: str,
    request: CommentCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommentResponse:
    """Create a comment on a segment."""
    # Verify segment exists
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment not found: {segment_id}",
        )

    if not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Comment text cannot be empty",
        )

    comment = SegmentComment(segment_id=segment_id, text=request.text.strip())
    db.add(comment)
    await db.flush()

    logger.info("Created comment %s on segment %s", comment.id, segment_id)

    return CommentResponse(
        id=comment.id,
        segment_id=comment.segment_id,
        text=comment.text,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


# --- Comment-scoped endpoints ---


@comments_router.patch("/{comment_id}", response_model=CommentResponse)
async def update_comment(
    comment_id: str,
    request: CommentUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommentResponse:
    """Update a comment's text."""
    result = await db.execute(
        select(SegmentComment).where(SegmentComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()

    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment not found: {comment_id}",
        )

    if not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Comment text cannot be empty",
        )

    comment.text = request.text.strip()
    await db.commit()
    await db.refresh(comment)

    logger.info("Updated comment %s", comment_id)

    return CommentResponse(
        id=comment.id,
        segment_id=comment.segment_id,
        text=comment.text,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@comments_router.delete("/{comment_id}", response_model=MessageResponse)
async def delete_comment(
    comment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete a comment."""
    result = await db.execute(
        select(SegmentComment).where(SegmentComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()

    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment not found: {comment_id}",
        )

    await db.delete(comment)
    await db.flush()
    logger.info("Deleted comment %s", comment_id)

    return MessageResponse(message="Comment deleted successfully")
