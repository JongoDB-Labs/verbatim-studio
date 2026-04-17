"""Filler word detection API endpoints."""

import logging
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db
from persistence.models import Segment, Transcript
from services.filler_detection import detect_fillers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcripts", tags=["filler-detection"])


# --- Response models ---


class FillerMatchResponse(BaseModel):
    word: str
    start_char: int
    end_char: int
    type: str


class SegmentFillersResponse(BaseModel):
    segment_id: str
    segment_index: int
    text: str
    fillers: list[FillerMatchResponse]


class FillerSummaryResponse(BaseModel):
    total_fillers: int
    segments_with_fillers: int
    total_segments: int
    filler_counts: dict[str, int]


class FillerDetectionResponse(BaseModel):
    transcript_id: str
    segments: list[SegmentFillersResponse]
    summary: FillerSummaryResponse


# --- Endpoints ---


@router.post("/{transcript_id}/fillers", response_model=FillerDetectionResponse)
async def detect_transcript_fillers(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FillerDetectionResponse:
    """Analyze all segments of a transcript for filler words."""
    # Verify transcript exists
    result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transcript not found"
        )

    # Load segments ordered by segment_index
    seg_result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    db_segments = seg_result.scalars().all()

    # Build the list of dicts the service expects
    segment_dicts = [
        {"id": seg.id, "text": seg.text, "segment_index": seg.segment_index}
        for seg in db_segments
    ]

    # Run filler detection
    filler_map = detect_fillers(segment_dicts)

    # Build per-segment response (only segments with fillers)
    segments_response: list[SegmentFillersResponse] = []
    filler_counter: Counter[str] = Counter()

    # Create a lookup for segment metadata by id
    seg_lookup = {seg.id: seg for seg in db_segments}

    for seg_id, matches in filler_map.items():
        seg = seg_lookup[seg_id]
        fillers = [
            FillerMatchResponse(
                word=m.word,
                start_char=m.start_char,
                end_char=m.end_char,
                type=m.filler_type,
            )
            for m in matches
        ]
        segments_response.append(
            SegmentFillersResponse(
                segment_id=seg.id,
                segment_index=seg.segment_index,
                text=seg.text,
                fillers=fillers,
            )
        )
        for m in matches:
            filler_counter[m.word] += 1

    # Sort segments by segment_index for consistent output
    segments_response.sort(key=lambda s: s.segment_index)

    # Build summary with filler_counts ordered by most common
    total_fillers = sum(filler_counter.values())
    filler_counts = dict(filler_counter.most_common())

    summary = FillerSummaryResponse(
        total_fillers=total_fillers,
        segments_with_fillers=len(filler_map),
        total_segments=len(db_segments),
        filler_counts=filler_counts,
    )

    return FillerDetectionResponse(
        transcript_id=transcript_id,
        segments=segments_response,
        summary=summary,
    )
