"""Highlight API endpoints for transcript segments."""

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db
from persistence.models import Segment, SegmentHighlight, Transcript

logger = logging.getLogger(__name__)

HighlightColor = Literal["yellow", "green", "blue", "red", "purple", "orange"]

# Router for segment-scoped endpoints: /segments/{id}/highlight
segment_highlights_router = APIRouter(prefix="/segments", tags=["highlights"])

# Router for transcript-scoped bulk endpoints: /transcripts/{id}/bulk-highlight
transcript_highlights_router = APIRouter(prefix="/transcripts", tags=["highlights"])


# Pydantic models
class HighlightResponse(BaseModel):
    """Response model for a highlight."""

    id: str
    segment_id: str
    color: str
    created_at: datetime

    class Config:
        from_attributes = True


class HighlightSetRequest(BaseModel):
    """Request model for setting a highlight."""

    color: HighlightColor


class BulkHighlightRequest(BaseModel):
    """Request model for bulk highlight operations."""

    segment_ids: list[str]
    color: HighlightColor | None = None  # None means remove
    remove: bool = False  # True = remove highlights instead of apply


class MessageResponse(BaseModel):
    """Simple response."""

    message: str


# --- Segment-scoped endpoints ---


@segment_highlights_router.put("/{segment_id}/highlight", response_model=HighlightResponse)
async def set_highlight(
    segment_id: str,
    request: HighlightSetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HighlightResponse:
    """Set or change the highlight color on a segment (upsert)."""
    # Verify segment exists
    result = await db.execute(select(Segment).where(Segment.id == segment_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment not found: {segment_id}",
        )

    # Check for existing highlight
    result = await db.execute(
        select(SegmentHighlight).where(SegmentHighlight.segment_id == segment_id)
    )
    highlight = result.scalar_one_or_none()

    if highlight is not None:
        highlight.color = request.color
    else:
        highlight = SegmentHighlight(segment_id=segment_id, color=request.color)
        db.add(highlight)

    await db.flush()
    logger.info("Set highlight %s on segment %s", request.color, segment_id)

    return HighlightResponse(
        id=highlight.id,
        segment_id=highlight.segment_id,
        color=highlight.color,
        created_at=highlight.created_at,
    )


@segment_highlights_router.delete("/{segment_id}/highlight", response_model=MessageResponse)
async def remove_highlight(
    segment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Remove the highlight from a segment."""
    result = await db.execute(
        select(SegmentHighlight).where(SegmentHighlight.segment_id == segment_id)
    )
    highlight = result.scalar_one_or_none()

    if highlight is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No highlight found on segment: {segment_id}",
        )

    await db.delete(highlight)
    await db.flush()
    logger.info("Removed highlight from segment %s", segment_id)

    return MessageResponse(message="Highlight removed successfully")


# --- Transcript-scoped bulk endpoints ---


@transcript_highlights_router.post(
    "/{transcript_id}/bulk-highlight", response_model=MessageResponse
)
async def bulk_highlight(
    transcript_id: str,
    request: BulkHighlightRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Apply or remove highlights on multiple segments at once."""
    # Verify transcript exists
    result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    if not request.segment_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="segment_ids must not be empty",
        )

    # Verify all segments belong to this transcript
    result = await db.execute(
        select(Segment.id).where(
            Segment.id.in_(request.segment_ids),
            Segment.transcript_id == transcript_id,
        )
    )
    valid_ids = {row[0] for row in result.all()}
    invalid_ids = set(request.segment_ids) - valid_ids

    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Segments not in transcript: {', '.join(invalid_ids)}",
        )

    if request.remove:
        # Remove highlights from selected segments
        await db.execute(
            delete(SegmentHighlight).where(
                SegmentHighlight.segment_id.in_(request.segment_ids)
            )
        )
        logger.info(
            "Bulk removed highlights from %d segments in transcript %s",
            len(request.segment_ids),
            transcript_id,
        )
        return MessageResponse(
            message=f"Removed highlights from {len(request.segment_ids)} segments"
        )
    else:
        # Apply highlight color
        if request.color is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="color is required when not removing highlights",
            )

        # Delete existing highlights for these segments first
        await db.execute(
            delete(SegmentHighlight).where(
                SegmentHighlight.segment_id.in_(request.segment_ids)
            )
        )

        # Create new highlights
        for segment_id in request.segment_ids:
            db.add(SegmentHighlight(segment_id=segment_id, color=request.color))

        logger.info(
            "Bulk applied %s highlight to %d segments in transcript %s",
            request.color,
            len(request.segment_ids),
            transcript_id,
        )
        return MessageResponse(
            message=f"Applied {request.color} highlight to {len(request.segment_ids)} segments"
        )
